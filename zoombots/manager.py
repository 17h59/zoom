import asyncio
import os
from pathlib import Path

from playwright.async_api import Playwright, async_playwright

from . import state, stats
from .audio import cleanup_orphans
from .bot import Bot
from .meeting import Meeting

SOUNDS_DIR = Path(os.environ.get("ZOOMBOTS_SOUNDS", Path(__file__).parent.parent / "sounds"))
MAX_BOTS = int(os.environ.get("ZOOMBOTS_MAX_BOTS", "20"))
MAX_CONCURRENT_JOINS = int(os.environ.get("ZOOMBOTS_MAX_JOINS", "3"))  # lisse le pic CPU au démarrage
SOUND_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".opus"}


def list_sounds() -> list[str]:
    return sorted(p.name for p in SOUNDS_DIR.glob("*") if p.suffix.lower() in SOUND_EXTS)


def sound_path(name: str) -> Path:
    path = (SOUNDS_DIR / name).resolve()
    if path.parent != SOUNDS_DIR.resolve() or not path.is_file():
        raise FileNotFoundError(name)
    return path


class BotManager:
    def __init__(self, state_path=None) -> None:
        self.bots: dict[str, Bot] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._pw_cm = None
        self._pw: Playwright | None = None
        self._join_gate = asyncio.Semaphore(MAX_CONCURRENT_JOINS)
        self._state_path = state_path or state.STATE_FILE

    async def start(self) -> None:
        self._pw_cm = async_playwright()
        self._pw = await self._pw_cm.__aenter__()
        await cleanup_orphans()  # micros laissés par un process tué brutalement
        for entry in state.load(self._state_path):
            bot = Bot(Meeting(entry["meeting"], entry.get("passcode")), entry["name"], bot_id=entry["id"],
                      auto_reconnect=entry.get("auto_reconnect", True), on_change=self._persist)
            self._register(bot)

    async def shutdown(self) -> None:
        self._persist()  # on garde la liste : les bots seront relancés au prochain démarrage
        await asyncio.gather(*(b.leave() for b in self.bots.values()), return_exceptions=True)
        if self._pw_cm:
            await self._pw_cm.__aexit__(None, None, None)

    def _persist(self) -> None:
        state.save([b.to_state() for b in self.bots.values() if b.status != "left"], self._state_path)

    def _register(self, bot: Bot) -> None:
        self.bots[bot.id] = bot
        self._tasks[bot.id] = asyncio.create_task(bot.run(self._pw, self._join_gate))

    def spawn(self, meeting: Meeting, names: list[str], auto_reconnect: bool = True) -> list[Bot]:
        assert self._pw is not None
        if len(self.bots) + len(names) > MAX_BOTS:
            raise OverflowError(f"limite de {MAX_BOTS} bots atteinte (ZOOMBOTS_MAX_BOTS)")
        bots = [Bot(meeting, n, auto_reconnect=auto_reconnect, on_change=self._persist) for n in names]
        for bot in bots:
            self._register(bot)
        self._persist()
        return bots

    def get(self, bot_id: str) -> Bot:
        try:
            return self.bots[bot_id]
        except KeyError:
            raise KeyError(f"bot {bot_id} inconnu") from None

    async def remove(self, bot_id: str) -> None:
        bot = self.get(bot_id)
        await bot.leave()
        task = self._tasks.pop(bot_id, None)
        if task:
            task.cancel()
        del self.bots[bot_id]
        self._persist()

    async def broadcast(self, sound: str, volume: float = 1.0) -> int:
        path = sound_path(sound)
        ready = [b for b in self.bots.values() if b.status == "in_meeting"]
        await asyncio.gather(*(b.play(path, volume) for b in ready))
        return len(ready)

    def stats(self) -> dict:
        """Ressources par bot et capacité estimée de la machine (mémoire disponible / RSS moyen)."""
        per_bot = {b.id: b.resources() for b in self.bots.values()}
        active = [r for r in per_bot.values() if r["processes"]]
        system = stats.system_usage()
        total_rss = sum(r["rss_mb"] for r in active)
        avg = total_rss / len(active) if active else None
        capacity = int((system["mem_available_mb"] + total_rss) / avg) if avg else None
        return {"bots": per_bot, "system": system, "total_rss_mb": round(total_rss, 1),
                "avg_rss_mb_per_bot": round(avg, 1) if avg else None, "estimated_capacity": capacity,
                "max_bots": MAX_BOTS}
