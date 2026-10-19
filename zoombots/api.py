from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .manager import BotManager, list_sounds, sound_path
from .meeting import parse_meeting

manager = BotManager()


@asynccontextmanager
async def lifespan(_: FastAPI):
    await manager.start()
    yield
    await manager.shutdown()


app = FastAPI(title="zoombots", lifespan=lifespan)
STATIC = Path(__file__).parent / "static"


class SpawnRequest(BaseModel):
    meeting: str = Field(description="ID de réunion ou lien Zoom")
    passcode: str | None = None
    name: str = "Bot"
    count: int = Field(1, ge=1, le=20)


class PlayRequest(BaseModel):
    volume: float = Field(1.0, ge=0, le=2)


class MicRequest(BaseModel):
    on: bool


def _bot(bot_id: str):
    try:
        return manager.get(bot_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.get("/")
def dashboard():
    return FileResponse(STATIC / "index.html")


@app.get("/bots")
def list_bots():
    return [b.to_dict() for b in manager.bots.values()]


@app.post("/bots")
async def spawn(req: SpawnRequest):
    try:
        meeting = parse_meeting(req.meeting, req.passcode)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    names = [req.name] if req.count == 1 else [f"{req.name} {i + 1}" for i in range(req.count)]
    return [b.to_dict() for b in manager.spawn(meeting, names)]


@app.delete("/bots/{bot_id}")
async def remove(bot_id: str):
    _bot(bot_id)
    await manager.remove(bot_id)
    return {"ok": True}


@app.post("/bots/{bot_id}/mic")
async def mic(bot_id: str, req: MicRequest):
    try:
        await _bot(bot_id).set_mic(req.on)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"ok": True}


@app.get("/sounds")
def sounds():
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
def stop(bot_id: str):
    _bot(bot_id).stop_sounds()
    return {"ok": True}


@app.post("/broadcast/sounds/{sound}")
async def broadcast(sound: str, req: PlayRequest = PlayRequest()):
    try:
        return {"bots": await manager.broadcast(sound, req.volume)}
    except FileNotFoundError as exc:
        raise HTTPException(404, f"son {sound!r} introuvable") from exc
