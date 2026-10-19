import asyncio

import pytest

from zoombots import bot as botmod
from zoombots.bot import Bot, JoinDenied
from zoombots.meeting import Meeting

pytestmark = pytest.mark.usefixtures("fast_sleep")


class Harness(Bot):
    def __init__(self, script, **kw):
        super().__init__(Meeting("123456789"), "t", **kw)
        self.script, self.connects = list(script), 0

    async def _connect(self, pw, gate=None):
        self.connects += 1
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        self._set_status("in_meeting")
        self._watch_result = step

    async def _watch(self):
        return self._watch_result

    async def _cleanup(self):
        pass


async def test_reconnects_after_drop_then_ends():
    bot = Harness(["dropped", "ended"])
    await bot.run(None)
    assert bot.connects == 2 and bot.reconnects == 1 and bot.status == "ended"


async def test_denied_is_not_retried():
    bot = Harness([JoinDenied("non")])
    await bot.run(None)
    assert bot.connects == 1 and bot.status == "denied"


async def test_gives_up_after_max_reconnects(monkeypatch):
    monkeypatch.setattr(botmod, "MAX_RECONNECTS", 2)
    bot = Harness([RuntimeError("x")] * 5)
    await bot.run(None)
    assert bot.connects == 3 and bot.status == "error"


async def test_connect_then_immediate_drop_loop_is_bounded(monkeypatch):
    """Session éphémère : le compteur ne doit pas être remis à zéro."""
    monkeypatch.setattr(botmod, "MAX_RECONNECTS", 2)
    bot = Harness(["dropped"] * 20)
    await bot.run(None)
    assert bot.connects == 3 and bot.status == "error"


async def test_stable_session_resets_counter(monkeypatch):
    monkeypatch.setattr(botmod, "MAX_RECONNECTS", 1)
    monkeypatch.setattr(botmod, "STABLE_SESSION_S", 0)
    bot = Harness(["dropped", "dropped", "ended"])
    await bot.run(None)
    assert bot.connects == 3 and bot.status == "ended"


async def test_no_reconnect_when_disabled():
    bot = Harness(["dropped"], auto_reconnect=False)
    await bot.run(None)
    assert bot.connects == 1 and bot.status == "error"


async def test_cancel_cleans_up():
    cleaned = []

    class Slow(Harness):
        async def _connect(self, pw, gate=None):
            await asyncio.sleep(3600)

        async def _cleanup(self):
            cleaned.append(True)

    bot = Slow([])
    task = asyncio.create_task(bot.run(None))
    await asyncio.sleep(0.01)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert cleaned


async def test_join_gate_released_before_admission():
    """Un bot en salle d'attente ne doit pas bloquer le sémaphore des autres."""
    gate = asyncio.Semaphore(1)
    waiting = asyncio.Event()

    class Waiting(Bot):
        async def _open_and_submit(self, pw):
            return object()

        async def _await_admission(self, page):
            assert not gate.locked()
            waiting.set()
            await asyncio.sleep(3600)

    bot = Waiting(Meeting("123456789"), "t")
    task = asyncio.create_task(bot._connect(None, gate))
    await asyncio.wait_for(waiting.wait(), 1)
    assert not gate.locked()
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
