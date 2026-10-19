import asyncio
import os
import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .log import setup_logging
from .manager import BotManager, list_sounds, sound_path
from .meeting import parse_meeting

setup_logging()
manager = BotManager()

TOKEN = os.environ.get("ZOOMBOTS_TOKEN") or None
ALLOWED_HOSTS = {"localhost", "127.0.0.1", "[::1]", "::1", *filter(None, os.environ.get("ZOOMBOTS_ALLOWED_HOSTS", "").split(","))}
CSRF_HEADER = "x-zoombots"  # un en-tête personnalisé force un preflight CORS, que le serveur ne satisfait jamais

CSP = ("default-src 'none'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
       "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")


@asynccontextmanager
async def lifespan(_: FastAPI):
    await manager.start()
    yield
    await manager.shutdown()


app = FastAPI(title="zoombots", lifespan=lifespan)
STATIC = Path(__file__).parent / "static"


def _hostname(header: str) -> str:
    header = header.strip().lower()
    return header[: header.index("]") + 1] if header.startswith("[") else header.split(":")[0]


def _reject(status: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status, headers={"Content-Security-Policy": CSP})


@app.middleware("http")
async def guard(request: Request, call_next):
    """Protège une API locale sans compte : DNS rebinding (Host), CSRF (en-tête + Sec-Fetch-Site), jeton optionnel."""
    if _hostname(request.headers.get("host", "")) not in ALLOWED_HOSTS and request.url.path != "/health":
        return _reject(403, "hôte non autorisé (ZOOMBOTS_ALLOWED_HOSTS)")
    if request.method not in ("GET", "HEAD"):
        if CSRF_HEADER not in request.headers or request.headers.get("sec-fetch-site") == "cross-site":
            return _reject(403, "en-tête X-Zoombots requis")
    if TOKEN and request.url.path != "/health":
        supplied = request.cookies.get("zb_token") or request.headers.get("authorization", "").removeprefix("Bearer ")
        if request.url.path == "/" and request.query_params.get("token"):
            if secrets.compare_digest(request.query_params["token"], TOKEN):
                resp = RedirectResponse("/", status_code=303)
                resp.set_cookie("zb_token", TOKEN, httponly=True, samesite="strict")
                return resp
        if not supplied or not secrets.compare_digest(supplied, TOKEN):
            return _reject(401, "jeton requis (Authorization: Bearer, ou /?token=…)")
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    return response


app.mount("/static", StaticFiles(directory=STATIC), name="static")


class SpawnRequest(BaseModel):
    meeting: str = Field(max_length=500, description="ID de réunion ou lien Zoom")
    passcode: str | None = Field(None, max_length=64)
    name: str = Field("Bot", min_length=1, max_length=60)
    count: int = Field(1, ge=1, le=20)
    auto_reconnect: bool = True


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
async def dashboard():
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
        return [b.to_dict() for b in manager.spawn(meeting, names, req.auto_reconnect)]
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
