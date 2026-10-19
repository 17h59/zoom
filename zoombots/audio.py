"""Micro virtuel PulseAudio par bot : le soundboard joue dans un sink dédié,
dont le monitor est exposé comme source (micro) au navigateur du bot."""

import asyncio
import logging
import os
import re
import signal
import subprocess
from pathlib import Path

from . import net

logger = logging.getLogger("zoombots.audio")

MAX_PLAYERS = 4  # lectures simultanées par bot

# Préfixe propre à l'instance : ZOOMBOTS_INSTANCE=a donne zb_a_<id>, ce qui permet à plusieurs
# zoombots de partager un serveur PulseAudio sans décharger les modules les uns des autres.
INSTANCE = os.environ.get("ZOOMBOTS_INSTANCE", "")
if INSTANCE and not re.fullmatch(r"[a-z0-9]{1,16}", INSTANCE):
    raise SystemExit("ZOOMBOTS_INSTANCE : 1 à 16 caractères [a-z0-9]")
PREFIX = f"zb_{INSTANCE}_" if INSTANCE else "zb_"
NAME = rf"{PREFIX}[0-9a-f]{{8}}"  # nom de sink d'un bot de cette instance
ORPHAN = re.compile(rf"(?:sink_name|source_name)={NAME}(?:_mic)?(?:\s|$)")
OWN_SOURCE = re.compile(rf"{NAME}_mic")


PACTL_TIMEOUT_S = 15


async def _run(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=net.clean_env()
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), PACTL_TIMEOUT_S)  # un pactl figé ne bloque pas un nettoyage
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise RuntimeError(f"{args[0]} ne répond pas") from None
    if proc.returncode != 0:
        raise RuntimeError(f"{args[0]} a échoué: {err.decode().strip()}")
    return out.decode().strip()


class _Player:
    """Pipeline ffmpeg (décodage) → paplay (lecture), chacun dans son groupe de processus (arrêt propre via SIGTERM)."""

    def __init__(self, ffmpeg: asyncio.subprocess.Process, paplay: asyncio.subprocess.Process):
        self.ffmpeg, self.paplay = ffmpeg, paplay
        self.stopped = False  # arrêt demandé (stop, nettoyage, reconnexion) : la lecture n'est pas allée au bout

    @property
    def running(self) -> bool:
        return self.ffmpeg.returncode is None or self.paplay.returncode is None

    def kill(self) -> None:
        self.stopped = True
        for proc in (self.ffmpeg, self.paplay):
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

    async def wait(self, name: str) -> bool:
        """Attend la fin de la lecture. Renvoie False si elle a échoué ou a été interrompue (arrêt demandé)."""
        err = await self.ffmpeg.stderr.read() if self.ffmpeg.stderr else b""
        await asyncio.gather(self.ffmpeg.wait(), self.paplay.wait())
        if self.stopped:
            return False
        if self.ffmpeg.returncode not in (0, -signal.SIGTERM) or self.paplay.returncode not in (0, -signal.SIGTERM):
            logger.warning("lecture en échec", extra={"fields": {
                "sound": name, "ffmpeg": self.ffmpeg.returncode, "paplay": self.paplay.returncode,
                "stderr": err.decode(errors="replace")[-300:]}})
            return False
        return True


class VirtualMic:
    def __init__(self, bot_id: str):
        self.sink = f"{PREFIX}{bot_id}"
        self.source = f"{PREFIX}{bot_id}_mic"
        self._modules: list[str] = []
        self._players: set[_Player] = set()
        self._tasks: set[asyncio.Task] = set()

    async def create(self) -> None:
        try:
            self._modules.append(
                await _run(
                    "pactl", "load-module", "module-null-sink",
                    f"sink_name={self.sink}", f"sink_properties=device.description={self.sink}",
                )
            )
            self._modules.append(
                await _run(
                    "pactl", "load-module", "module-remap-source",
                    f"master={self.sink}.monitor", f"source_name={self.source}",
                )
            )
        except Exception:
            await self.destroy()  # pas de module à moitié chargé
            raise

    async def play(self, path: Path, volume: float = 1.0) -> "asyncio.Task[bool]":
        """Lance la lecture. La tâche renvoyée se termine avec la lecture (True = succès)."""
        if len(self._players) >= MAX_PLAYERS:
            raise RuntimeError(f"trop de lectures simultanées (max {MAX_PLAYERS})")
        # ffmpeg décode tous les formats ; paplay (contrairement à `ffmpeg -f pulse`) vide son buffer en fin de flux.
        read_fd, write_fd = os.pipe()
        ffmpeg = paplay = None
        try:
            ffmpeg = await asyncio.create_subprocess_exec(
                "ffmpeg", "-nostdin", "-loglevel", "error", "-protocol_whitelist", "file", "-i", str(path),
                "-af", f"volume={float(volume):.3f}", "-f", "wav", "-",
                stdout=write_fd, stderr=asyncio.subprocess.PIPE, start_new_session=True, env=net.clean_env(),
            )
            paplay = await asyncio.create_subprocess_exec(
                "paplay", f"--device={self.sink}", "--client-name=zoombots",
                stdin=read_fd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, env=net.clean_env(),
            )
        except BaseException:
            if ffmpeg and ffmpeg.returncode is None:
                ffmpeg.kill()
            raise
        finally:
            os.close(read_fd)
            os.close(write_fd)
        player = _Player(ffmpeg, paplay)
        self._players.add(player)
        task = asyncio.create_task(self._reap(player, path.name))
        self._tasks.add(task)  # référence forte : sinon la tâche peut être collectée avant la fin
        task.add_done_callback(self._tasks.discard)
        return task

    async def _reap(self, player: _Player, name: str) -> bool:
        try:
            return await player.wait(name)
        finally:
            self._players.discard(player)

    @property
    def playing(self) -> bool:
        return bool(self._players)

    def stop(self) -> None:
        for player in list(self._players):
            player.kill()

    async def destroy(self) -> None:
        self.stop()
        if self._tasks:
            await asyncio.wait(set(self._tasks), timeout=3)
        for module in reversed(self._modules):
            try:
                await _run("pactl", "unload-module", module)
            except RuntimeError as exc:
                logger.warning("unload-module a échoué", extra={"fields": {"module": module, "error": str(exc)}})
        self._modules.clear()


async def cleanup_orphans() -> int:
    """Décharge les modules de cette instance (zb_[<instance>_]<8 hex>) laissés par un process tué brutalement.

    Les autres instances et les autres applications ne sont pas touchées."""
    try:
        listing = await _run("pactl", "list", "short", "modules")
    except (RuntimeError, FileNotFoundError):
        return 0
    removed = 0
    for line in listing.splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and ORPHAN.search(parts[2]):
            try:
                await _run("pactl", "unload-module", parts[0])
                removed += 1
            except RuntimeError:
                pass
    return removed
