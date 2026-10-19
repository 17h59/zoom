"""API du GUI multi-utilisateurs : tout est rattaché à une session (en-tête X-Session, secret de 192 bits)."""

import asyncio
import os
import secrets
from collections.abc import Callable

from fastapi import APIRouter, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import sessions as S

ACCESS_CODE = os.environ.get("ZOOMBOTS_ACCESS_CODE") or None
PUSH_INTERVAL_S = 1.0


class Link(BaseModel):
    meeting: str = Field(max_length=500, description="ID de réunion ou lien Zoom")
    passcode: str | None = Field(None, max_length=64)


class DeployRequest(BaseModel):
    links: list[Link] = Field(min_length=1, max_length=S.MAX_LINKS)
    count: int = Field(1, ge=1, le=S.MAX_DEPLOY)
    mode: str = "split"  # split : `count` bots répartis sur les liens ; each : `count` bots par lien
    name: str = Field("Bot", min_length=1, max_length=60)
    auto_reconnect: bool = True


class ActionRequest(BaseModel):
    bots: list[str] | None = Field(None, max_length=S.MAX_DEPLOY * 2)  # None : tous les bots actifs
    sound: str = Field(max_length=200)
    count: int = Field(1, ge=1, le=S.MAX_ACTION_COUNT)
    gap: float = Field(0, ge=0, le=3600)
    volume: float = Field(1.0, ge=0, le=2)
    stagger: float = Field(0, ge=0, le=60)


class StopRequest(BaseModel):
    bots: list[str] | None = Field(None, max_length=S.MAX_DEPLOY * 2)


class MicRequest(BaseModel):
    on: bool


class YoutubeRequest(BaseModel):
    url: str = Field(max_length=300)


class SessionRequest(BaseModel):
    code: str | None = Field(None, max_length=200)


def build_router(sessions: S.SessionManager, host_ok: Callable[[str], bool]) -> APIRouter:
    router = APIRouter(prefix="/api")

    def fail(exc: S.SessionError):
        raise HTTPException(exc.status, str(exc)) from exc

    def current(x_session: str | None) -> S.Session:
        try:
            return sessions.get(x_session)
        except S.SessionError as exc:
            fail(exc)

    @router.get("/config")
    async def config():
        return {
            "access_code_required": bool(ACCESS_CODE),
            "grace_seconds": S.GRACE_S,
            "limits": {
                "session_bots": S.SESSION_MAX_BOTS, "deploy": S.MAX_DEPLOY, "links": S.MAX_LINKS,
                "upload_mb": S.MAX_UPLOAD_BYTES // 2**20, "sounds": S.MAX_SOUNDS,
                "sound_seconds": S.MAX_SOUND_SECONDS, "action_count": S.MAX_ACTION_COUNT,
            },
        }

    @router.post("/session")
    async def create_session(req: SessionRequest = SessionRequest()):
        if ACCESS_CODE and not (req.code and secrets.compare_digest(req.code, ACCESS_CODE)):
            raise HTTPException(401, "code d'accès incorrect")
        try:
            return {"sid": sessions.create().id}
        except S.SessionError as exc:
            fail(exc)

    @router.delete("/session")
    async def close_session(x_session: str | None = Header(None)):
        current(x_session)
        await sessions.close(x_session)
        return {"ok": True}

    @router.get("/state")
    async def state(x_session: str | None = Header(None)):
        return current(x_session).snapshot()

    @router.post("/deploy")
    async def deploy(req: DeployRequest, x_session: str | None = Header(None)):
        session = current(x_session)
        try:
            bots = session.deploy([l.model_dump() for l in req.links], req.count, req.mode, req.name, req.auto_reconnect)
        except S.SessionError as exc:
            fail(exc)
        return {"bots": [b.id for b in bots]}

    @router.delete("/bots")
    async def remove_bots(finished: bool = False, x_session: str | None = Header(None)):
        return {"removed": await current(x_session).remove_bots(finished_only=finished)}

    @router.delete("/bots/{bot_id}")
    async def remove_bot(bot_id: str, x_session: str | None = Header(None)):
        try:
            await current(x_session).remove_bot(bot_id)
        except S.SessionError as exc:
            fail(exc)
        return {"ok": True}

    @router.post("/bots/{bot_id}/mic")
    async def mic(bot_id: str, req: MicRequest, x_session: str | None = Header(None)):
        try:
            changed = await current(x_session).bot(bot_id).set_mic(req.on)
        except S.SessionError as exc:
            fail(exc)
        except RuntimeError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"ok": True, "changed": changed}

    @router.get("/bots/{bot_id}/events")
    async def events(bot_id: str, x_session: str | None = Header(None)):
        try:
            return list(current(x_session).bot(bot_id).events)
        except S.SessionError as exc:
            fail(exc)

    @router.get("/bots/{bot_id}/screenshot")
    async def screenshot(bot_id: str, x_session: str | None = Header(None)):
        try:
            return Response(await current(x_session).bot(bot_id).screenshot(), media_type="image/png")
        except S.SessionError as exc:
            fail(exc)
        except RuntimeError as exc:
            raise HTTPException(404, str(exc)) from exc

    @router.put("/sounds")
    async def upload(request: Request, name: str, x_session: str | None = Header(None)):
        session = current(x_session)
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > S.MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"fichier trop gros (max {S.MAX_UPLOAD_BYTES // 2**20} Mo)")
        try:
            return (await session.add_upload(name, request.stream())).to_dict()
        except S.SessionError as exc:
            fail(exc)

    @router.delete("/sounds/{sound_id}")
    async def delete_sound(sound_id: str, x_session: str | None = Header(None)):
        try:
            current(x_session).delete_sound(sound_id)
        except S.SessionError as exc:
            fail(exc)
        return {"ok": True}

    @router.post("/youtube")
    async def youtube(req: YoutubeRequest, x_session: str | None = Header(None)):
        try:
            return {"job": current(x_session).start_youtube(req.url)}
        except S.SessionError as exc:
            fail(exc)

    @router.post("/actions")
    async def action(req: ActionRequest, x_session: str | None = Header(None)):
        try:
            ids = current(x_session).run_action(req.bots, req.sound, req.count, req.gap, req.volume, req.stagger)
        except S.SessionError as exc:
            fail(exc)
        return {"bots": ids}

    @router.post("/actions/stop")
    async def stop(req: StopRequest = StopRequest(), x_session: str | None = Header(None)):
        try:
            return {"stopped": current(x_session).stop_actions(req.bots)}
        except S.SessionError as exc:
            fail(exc)

    @router.websocket("/ws")
    async def ws(websocket: WebSocket):
        """Présence + état en direct. La session est détruite quand le WebSocket reste fermé trop longtemps."""
        origin = websocket.headers.get("origin")
        origin_host = origin.split("://", 1)[-1] if origin else None
        host = websocket.headers.get("host", "")
        if not host_ok(host) or (origin_host is not None and origin_host.lower() != host.lower()):
            await websocket.close(code=4403)
            return
        await websocket.accept()
        try:
            first = await asyncio.wait_for(websocket.receive_json(), 5)
            session = sessions.get(first.get("sid") if isinstance(first, dict) else None)
        except (S.SessionError, asyncio.TimeoutError, ValueError, WebSocketDisconnect):
            await websocket.close(code=4401)
            return
        session.attach()

        async def reader():  # les messages du client (ping) ne servent qu'à détecter la déconnexion
            while True:
                await websocket.receive_text()

        async def writer():
            last = None
            while True:
                snapshot = session.snapshot()
                if snapshot != last:
                    await websocket.send_json(snapshot)
                    last = snapshot
                await asyncio.sleep(PUSH_INTERVAL_S)

        tasks = [asyncio.create_task(reader()), asyncio.create_task(writer())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            session.detach()

    return router
