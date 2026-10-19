"""Micro virtuel PulseAudio par bot : le soundboard joue dans un sink dédié,
dont le monitor est exposé comme source (micro) au navigateur du bot."""

import asyncio
import os
import shlex
import signal
import subprocess
from pathlib import Path


async def _run(*args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"{args[0]} a échoué: {err.decode().strip()}")
    return out.decode().strip()


class VirtualMic:
    def __init__(self, bot_id: str):
        self.sink = f"zb_{bot_id}"
        self.source = f"zb_{bot_id}_mic"
        self._modules: list[str] = []
        self._players: set[asyncio.subprocess.Process] = set()

    async def create(self) -> None:
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

    async def play(self, path: Path, volume: float = 1.0) -> None:
        # ffmpeg décode tous les formats, paplay vide proprement le buffer (contrairement à -f pulse)
        cmd = (
            f"ffmpeg -loglevel error -i {shlex.quote(str(path))} -af volume={volume} -f wav - "
            f"| paplay --device={self.sink} --client-name=zoombots"
        )
        proc = await asyncio.create_subprocess_shell(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
        )
        self._players.add(proc)
        asyncio.create_task(self._reap(proc))

    async def _reap(self, proc: asyncio.subprocess.Process) -> None:
        await proc.wait()
        self._players.discard(proc)

    @property
    def playing(self) -> bool:
        return bool(self._players)

    def stop(self) -> None:
        for proc in list(self._players):
            if proc.returncode is None:
                os.killpg(proc.pid, signal.SIGTERM)

    async def destroy(self) -> None:
        self.stop()
        for module in reversed(self._modules):
            try:
                await _run("pactl", "unload-module", module)
            except RuntimeError:
                pass
        self._modules.clear()
