import asyncio
import os
import re
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import net
from .gui_api import build_router
from .log import setup_logging
from .manager import BotManager, list_sounds, sound_path
from .meeting import parse_meeting
from . import sessions as sessions_module
from .sessions import SessionManager

setup_logging()
manager = BotManager()
sessions = SessionManager(manager)

TOKEN = os.environ.get("ZOOMBOTS_TOKEN") or None
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1",
                 *(h.strip().lower() for h in os.environ.get("ZOOMBOTS_ALLOWED_HOSTS", "").split(",") if h.strip())}
# `*` : tout nom d'hôte accepté (serveur public sans nom de domaine, joint par son adresse IP)
PUBLIC_ONLY = os.environ.get("ZOOMBOTS_PUBLIC_ONLY") == "1"  # l'administration n'est joignable que depuis la machine elle-même
CSRF_HEADER = "x-zoombots"  # un en-tête personnalisé force un preflight CORS, que le serveur ne satisfait jamais

CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' blob: data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")

# Chemins du GUI utilisateur : accessibles sans jeton d'administration (les sessions ont leur propre secret).
PUBLIC_PREFIXES = ("/api/", "/static/")
PUBLIC_PATHS = {"/", "/health"}


@asynccontextmanager
async def lifespan(_: FastAPI):
    await manager.start()
    await sessions.start()
    yield
    await sessions.stop()  # retire les bots de toutes les sessions avant l'arrêt du serveur
    await manager.shutdown()


app = FastAPI(title="zoombots", lifespan=lifespan)
STATIC = Path(__file__).parent / "static"


_HOST_RE = re.compile(r"^(?:[A-Za-z0-9._-]+|\[[0-9A-Fa-f:.]+\])(?::\d{1,5})?$")


def _hostname(header: str) -> str:
    header = header.strip().lower()
    return header[: header.index("]") + 1] if header.startswith("[") else header.split(":")[0]


def host_allowed(header: str) -> bool:
    return "*" in ALLOWED_HOSTS or _hostname(header) in ALLOWED_HOSTS


def _reject(status: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status)


def _harden(response):
    """En-têtes de sécurité sur TOUTES les réponses, refus et redirections compris."""
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def guard(request: Request, call_next):
    return _harden(await _guard(request, call_next))


async def _guard(request: Request, call_next):
    """Protège une API locale sans compte : DNS rebinding (Host), CSRF (en-tête + Sec-Fetch-Site), jeton optionnel."""
    host_header = request.headers.get("host", "")
    # Un Host mal formé (ex. « x/api/ ») fausse `request.url.path` dans les anciennes versions de Starlette :
    # on le refuse, et on ne juge que sur le chemin réellement routé (scope["path"]).
    if not _HOST_RE.match(host_header):
        return _reject(400, "en-tête Host invalide")
    if not host_allowed(host_header) and request.scope["path"] != "/health":
        return _reject(403, "hôte non autorisé (ZOOMBOTS_ALLOWED_HOSTS)")
    if request.method not in ("GET", "HEAD"):
        if CSRF_HEADER not in request.headers or request.headers.get("sec-fetch-site") == "cross-site":
            return _reject(403, "en-tête X-Zoombots requis")
    path = request.scope["path"]
    public = path in PUBLIC_PATHS or path.startswith(PUBLIC_PREFIXES)
    if PUBLIC_ONLY and not public:
        ip = net.client_ip(request.client.host if request.client else None, ", ".join(request.headers.getlist("x-forwarded-for")))
        if not net.is_loopback(ip):
            return _reject(404, "introuvable")  # l'existence de l'administration n'est pas révélée à l'extérieur
    if TOKEN and not public:
        supplied = request.cookies.get("zb_token") or request.headers.get("authorization", "").removeprefix("Bearer ")
        if path == "/admin" and request.query_params.get("token"):
            if net.secret_equal(request.query_params["token"], TOKEN):
                resp = RedirectResponse("/admin", status_code=303)
                resp.set_cookie("zb_token", TOKEN, httponly=True, samesite="strict")
                return resp
        if not net.secret_equal(supplied, TOKEN):
            return _reject(401, "jeton requis (Authorization: Bearer, ou /admin?token=…)")
    return await call_next(request)


class BodyLimit:
    """Borne la taille des corps de requête avant que FastAPI ne les lise en mémoire : 64 Ko pour le JSON,
    la taille maximale d'un son (+ 1 Mo) pour `PUT /api/sounds`. Valable sans Caddy devant.

    Un corps JSON est lu ici (au plus 64 Ko en mémoire) puis rejoué à l'application ; l'envoi d'un son est
    compté au fil du flux, sans être gardé en mémoire."""

    JSON_LIMIT = 64 * 1024

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            return await self.app(scope, receive, send)
        upload = scope["method"] == "PUT" and scope["path"] == "/api/sounds"
        limit = (sessions_module.MAX_UPLOAD_BYTES + 2**20) if upload else self.JSON_LIMIT
        declared = dict(scope["headers"]).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > limit:
            return await self._too_large(scope, receive, send)
        if upload:
            return await self._stream_upload(scope, receive, send, limit)
        body, more = b"", True
        while more:  # corps JSON : lu au plus jusqu'à la limite
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body += message.get("body", b"")
            if len(body) > limit:
                return await self._too_large(scope, receive, send)
            more = message.get("more_body", False)
        replayed = False

        async def replay():
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    async def _stream_upload(self, scope, receive, send, limit):
        received = 0
        started = False

        async def counting_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise _TooLarge
            return message

        async def tracking_send(message):
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _TooLarge:
            if not started:
                await self._too_large(scope, receive, send)

    @staticmethod
    async def _too_large(scope, receive, send):
        response = _harden(JSONResponse({"detail": "corps de requête trop volumineux"}, status_code=413))
        await response(scope, receive, send)


class _TooLarge(Exception):
    pass




app.add_middleware(BodyLimit)
gui_router = build_router(sessions, host_allowed)
app.include_router(gui_router)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class SpawnRequest(BaseModel):
    meeting: str = Field(max_length=500, description="ID de réunion ou lien Zoom")
    passcode: str | None = Field(None, max_length=64)
    name: str = Field("Bot", min_length=1, max_length=60)
    count: int = Field(1, ge=1, le=20)
    auto_reconnect: bool = True
    respawn_on_ban: bool = True


class PlayRequest(BaseModel):
    volume: float = Field(1.0, ge=0, le=2)


class MicRequest(BaseModel):
    on: bool


def _bot(bot_id: str):
    try:
        return manager.get(bot_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/health")
async def health():
    return {"ok": True}


@app.get("/")
async def gui():
    return FileResponse(STATIC / "gui.html")


@app.get("/admin")
async def dashboard():
    """Tableau de bord d'administration : tous les bots du serveur (jeton ZOOMBOTS_TOKEN si défini)."""
    return FileResponse(STATIC / "index.html")


@app.get("/bots")
async def list_bots():
    return [b.to_dict() for b in list(manager.bots.values())]


@app.post("/bots")
async def spawn(req: SpawnRequest):
    try:
        meeting = parse_meeting(req.meeting, req.passcode)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    names = [req.name] if req.count == 1 else [f"{req.name} {i + 1}" for i in range(req.count)]
    try:
        return [b.to_dict() for b in manager.spawn(meeting, names, req.auto_reconnect, respawn_on_ban=req.respawn_on_ban)]
    except OverflowError as exc:
        raise HTTPException(429, str(exc)) from exc


@app.delete("/bots")
async def purge(finished: bool = False):
    if not finished:
        raise HTTPException(422, "utiliser ?finished=true (ou DELETE /bots/{id})")
    return {"removed": manager.purge_finished()}


@app.delete("/bots/{bot_id}")
async def remove(bot_id: str):
    _bot(bot_id)
    await manager.remove(bot_id)
    return {"ok": True}


@app.post("/bots/{bot_id}/mic")
async def mic(bot_id: str, req: MicRequest):
    try:
        changed = await _bot(bot_id).set_mic(req.on)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True, "changed": changed}


@app.get("/sounds")
async def sounds():
    return list_sounds()


@app.post("/bots/{bot_id}/sounds/{sound}")
async def play(bot_id: str, sound: str, req: PlayRequest = PlayRequest()):
    try:
        await _bot(bot_id).play(sound_path(sound), req.volume)
    except FileNotFoundError as exc:
        raise HTTPException(404, f"son {sound!r} introuvable") from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}


@app.post("/bots/{bot_id}/stop")
async def stop(bot_id: str):
    _bot(bot_id).stop_sounds()
    return {"ok": True}


@app.post("/broadcast/sounds/{sound}")
async def broadcast(sound: str, req: PlayRequest = PlayRequest()):
    try:
        result = await manager.broadcast(sound, req.volume)
    except FileNotFoundError as exc:
        raise HTTPException(404, f"son {sound!r} introuvable") from exc
    return {"bots": result["played"], **result}


@app.get("/stats")
async def stats():
    return await asyncio.to_thread(manager.stats)


@app.get("/bots/{bot_id}/events")
async def events(bot_id: str):
    return list(_bot(bot_id).events)


@app.get("/bots/{bot_id}/screenshot")
async def screenshot(bot_id: str):
    try:
        return Response(await _bot(bot_id).screenshot(), media_type="image/png")
    except RuntimeError as exc:
        raise HTTPException(404, str(exc)) from exc
