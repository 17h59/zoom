import asyncio

from zoombots import bot as botmod
from zoombots.bot import Bot, JoinDenied
from zoombots.meeting import Meeting


_sleep = asyncio.sleep


async def _fast_sleep(*_):
    await _sleep(0)


class Harness(Bot):
    def __init__(self, script, **kw):
        super().__init__(Meeting("123456789"), "t", **kw)
        self.script, self.connects = list(script), 0

    async def _connect(self, pw):
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


async def test_reconnects_after_drop_then_ends(monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    bot = Harness(["dropped", "ended"])
    await bot.run(None)
    assert bot.connects == 2 and bot.reconnects == 1 and bot.status == "ended"


async def test_denied_is_not_retried():
    bot = Harness([JoinDenied("non")])
    await bot.run(None)
    assert bot.connects == 1 and bot.status == "denied"


async def test_gives_up_after_max_reconnects(monkeypatch):
    monkeypatch.setattr(botmod, "MAX_RECONNECTS", 2)
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    bot = Harness([RuntimeError("x")] * 5)
    await bot.run(None)
    assert bot.connects == 3 and bot.status == "error"


async def test_no_reconnect_when_disabled():
    bot = Harness(["dropped"], auto_reconnect=False)
    await bot.run(None)
    assert bot.connects == 1 and bot.status == "error"
