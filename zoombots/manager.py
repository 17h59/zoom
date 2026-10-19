import asyncio
import os
from pathlib import Path

from playwright.async_api import Playwright, async_playwright

from .bot import Bot
from .meeting import Meeting

SOUNDS_DIR = Path(os.environ.get("ZOOMBOTS_SOUNDS", Path(__file__).parent.parent / "sounds"))
SOUND_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".opus"}


def list_sounds() -> list[str]:
    return sorted(p.name for p in SOUNDS_DIR.glob("*") if p.suffix.lower() in SOUND_EXTS)


def sound_path(name: str) -> Path:
    path = (SOUNDS_DIR / name).resolve()
    if path.parent != SOUNDS_DIR.resolve() or not path.is_file():
        raise FileNotFoundError(name)
    return path


class BotManager:
    def __init__(self) -> None:
        self.bots: dict[str, Bot] = {}
        self._pw_cm = None
        self._pw: Playwright | None = None

    async def start(self) -> None:
        self._pw_cm = async_playwright()
        self._pw = await self._pw_cm.__aenter__()

    async def shutdown(self) -> None:
        await asyncio.gather(*(b.leave() for b in self.bots.values()), return_exceptions=True)
        if self._pw_cm:
            await self._pw_cm.__aexit__(None, None, None)

    def spawn(self, meeting: Meeting, names: list[str]) -> list[Bot]:
        assert self._pw is not None
        bots = [Bot(meeting, name) for name in names]
        for bot in bots:
            self.bots[bot.id] = bot
            asyncio.create_task(bot.join(self._pw))
        return bots

    def get(self, bot_id: str) -> Bot:
        try:
            return self.bots[bot_id]
        except KeyError:
            raise KeyError(f"bot {bot_id} inconnu") from None

    async def remove(self, bot_id: str) -> None:
        bot = self.get(bot_id)
        await bot.leave()
        del self.bots[bot_id]

    async def broadcast(self, sound: str, volume: float = 1.0) -> int:
        path = sound_path(sound)
        ready = [b for b in self.bots.values() if b.status == "in_meeting"]
        await asyncio.gather(*(b.play(path, volume) for b in ready))
        return len(ready)
