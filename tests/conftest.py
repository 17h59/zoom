import asyncio

import pytest

_real_sleep = asyncio.sleep


@pytest.fixture
def fast_sleep(monkeypatch):
    async def sleep(*_):
        await _real_sleep(0)

    monkeypatch.setattr(asyncio, "sleep", sleep)
