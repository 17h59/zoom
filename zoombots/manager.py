import asyncio
import os
import shutil
from pathlib import Path

from playwright.async_api import Playwright, async_playwright

from . import state, stats
from .audio import cleanup_orphans
from .bot import SCREENSHOT_DIR, Bot
from .meeting import Meeting, normalize_host

SOUNDS_DIR = Path(os.environ.get("ZOOMBOTS_SOUNDS", Path(__file__).parent.parent / "sounds"))
MAX_BOTS = int(os.environ.get("ZOOMBOTS_MAX_BOTS", "20"))
MAX_CONCURRENT_JOINS = int(os.environ.get("ZOOMBOTS_MAX_JOINS", "3"))  # lisse le pic CPU au démarrage
SOUND_EXTS = {".mp3", ".wav", ".ogg", ".flac", ".m4a", ".opus"}


def list_sounds() -> list[str]:
    return sorted(p.name for p in SOUNDS_DIR.glob("*") if p.suffix.lower() in SOUND_EXTS)


def sound_path(name: str) -> Path:
    if not name or "\0" in name or "/" in name or "\\" in name or Path(name).suffix.lower() not in SOUND_EXTS:
        raise FileNotFoundError(name)
    try:
        path = (SOUNDS_DIR / name).resolve()
        if path.parent != SOUNDS_DIR.resolve() or not path.is_file():
            raise FileNotFoundError(name)
    except (OSError, ValueError):
        raise FileNotFoundError(name) from None
    return path


class BotManager:
    def __init__(self, state_path=None) -> None:
        self.bots: dict[str, Bot] = {}
        self._pw_cm = None
        self._pw: Playwright | None = None
        self._join_gate = asyncio.Semaphore(MAX_CONCURRENT_JOINS)
        self._state_path = state_path or state.STATE_FILE
        self._stopping = False
        self._dirty = False
        self._saved: list | None = None

    async def start(self) -> None:
        self._pw_cm = async_playwright()
        self._pw = await self._pw_cm.__aenter__()
        await cleanup_orphans()  # micros laissés par un process tué brutalement
        shutil.rmtree(SCREENSHOT_DIR, ignore_errors=True)  # captures d'erreur d'une exécution précédente
        for entry in state.load(self._state_path):
            if len(self._active()) >= MAX_BOTS or entry["id"] in self.bots:
                continue
            try:
                meeting = Meeting(entry["meeting"], entry.get("passcode"), normalize_host(entry.get("host") or "app.zoom.us"))
                if not (meeting.id.isdigit() and 9 <= len(meeting.id) <= 11):
                    raise ValueError(meeting.id)
            except ValueError:
                continue  # entrée altérée : ignorée plutôt que de bloquer le démarrage
            bot = Bot(meeting, entry["name"][:64], bot_id=entry["id"],
                      auto_reconnect=bool(entry.get("auto_reconnect", True)), on_change=self._persist)
            self._register(bot)

    async def shutdown(self) -> None:
        self.flush()  # la liste est figée : les `left` de l'arrêt ne doivent pas la vider
        self._stopping = True
        await asyncio.gather(*(b.leave() for b in list(self.bots.values())), return_exceptions=True)
        if self._pw_cm:
            await self._pw_cm.__aexit__(None, None, None)

    def _active(self) -> list[Bot]:
        return [b for b in self.bots.values() if not b.finished]

    def capacity_left(self) -> int:
        return max(0, MAX_BOTS - len(self._active()))

    def _persist(self) -> None:
        """Planifie une écriture groupée : plusieurs changements dans le même tour de boucle = un seul fichier."""
        if self._stopping or self._dirty:
            return
        self._dirty = True
        try:
            asyncio.get_running_loop().call_soon(self.flush)
        except RuntimeError:
            self.flush()

    def flush(self) -> None:
        self._dirty = False
        if self._stopping:
            return
        entries = [b.to_state() for b in self.bots.values() if not b.finished and b.owner is None]
        if entries != self._saved:  # pas de fsync pour les changements qui ne touchent que des bots de session
            state.save(entries, self._state_path)
            self._saved = entries

    def _register(self, bot: Bot) -> None:
        self.bots[bot.id] = bot
        bot.start(self._pw, self._join_gate)

    def bots_of(self, owner: str) -> list[Bot]:
        return [b for b in list(self.bots.values()) if b.owner == owner]

    def spawn(self, meeting: Meeting, names: list[str], auto_reconnect: bool = True,
              owner: str | None = None) -> list[Bot]:
        assert self._pw is not None
        if len(self._active()) + len(names) > MAX_BOTS:
            raise OverflowError(f"limite de {MAX_BOTS} bots actifs atteinte (ZOOMBOTS_MAX_BOTS)")
        bots = [Bot(meeting, n, auto_reconnect=auto_reconnect, on_change=self._persist, owner=owner) for n in names]
        for bot in bots:
            self._register(bot)
        self.flush()
        return bots

    def get(self, bot_id: str) -> Bot:
        try:
            return self.bots[bot_id]
        except KeyError:
            raise KeyError(f"bot {bot_id} inconnu") from None

    async def remove(self, bot_id: str) -> None:
        bot = self.get(bot_id)
        await bot.leave()
        self.bots.pop(bot_id, None)
        self.flush()

    def purge_finished(self) -> int:
        """Retire les bots d'administration terminés (ended, denied, error, left) ; ceux des sessions GUI sont à leur utilisateur."""
        done = [i for i, b in self.bots.items() if b.finished and b.owner is None]
        for bot_id in done:
            del self.bots[bot_id]
        self.flush()
        return len(done)

    async def broadcast(self, sound: str, volume: float = 1.0) -> dict:
        path = sound_path(sound)
        ready = [b for b in self.bots.values() if b.status == "in_meeting" and b.owner is None]  # pas les bots des sessions GUI
        results = await asyncio.gather(*(b.play(path, volume) for b in ready), return_exceptions=True)
        failed = [{"bot": b.id, "error": str(r)} for b, r in zip(ready, results) if isinstance(r, Exception)]
        return {"played": len(ready) - len(failed), "failed": failed}

    def stats(self) -> dict:
        """Ressources par bot et capacité estimée de la machine (mémoire disponible / RSS moyen)."""
        processes = stats.scan_bot_processes()  # un seul balayage de /proc pour tous les bots
        per_bot = {b.id: stats.process_usage(processes.get(b.mic.source, [])) for b in list(self.bots.values())}
        active = [r for r in per_bot.values() if r["processes"]]
        system = stats.system_usage()
        total_rss = sum(r["rss_mb"] for r in active)
        avg = total_rss / len(active) if active else None
        capacity = int((system["mem_available_mb"] + total_rss) / avg) if avg else None
        return {"bots": per_bot, "system": system, "total_rss_mb": round(total_rss, 1),
                "avg_rss_mb_per_bot": round(avg, 1) if avg else None, "estimated_capacity": capacity,
                "max_bots": MAX_BOTS}
