import asyncio

import pytest

from zoombots import manager as mgr
from zoombots import sessions as S
from zoombots.bot import Bot

LINK = {"meeting": "123456789"}


@pytest.fixture
def reg(tmp_path, monkeypatch):
    async def fake_run(self, pw, gate=None):
        self._set_status("in_meeting")
        await asyncio.sleep(3600)

    monkeypatch.setattr(Bot, "run", fake_run)
    monkeypatch.setattr(S, "ROOT", tmp_path / "sessions")
    monkeypatch.setattr(S, "SESSION_MAX_BOTS", 50)
    manager = mgr.BotManager(state_path=tmp_path / "state.json")
    manager._pw = object()
    return S.SessionManager(manager)


def test_sessions_per_ip(reg, monkeypatch):
    monkeypatch.setattr(S, "IP_MAX_SESSIONS", 2)
    monkeypatch.setattr(S, "IP_SESSIONS_PER_MINUTE", 0)
    reg.create("203.0.113.1")
    reg.create("203.0.113.1")
    with pytest.raises(S.SessionError) as exc:
        reg.create("203.0.113.1")
    assert exc.value.status == 429 and "adresse IP" in str(exc.value)
    reg.create("203.0.113.2")  # une autre adresse n'est pas touchée


def test_session_creation_rate_per_ip(reg, monkeypatch):
    monkeypatch.setattr(S, "IP_MAX_SESSIONS", 0)
    monkeypatch.setattr(S, "IP_SESSIONS_PER_MINUTE", 3)
    for _ in range(3):
        reg.create("203.0.113.1")
    with pytest.raises(S.SessionError) as exc:
        reg.create("203.0.113.1")
    assert exc.value.status == 429 and "minute" in str(exc.value)
    reg.create("203.0.113.2")


async def test_bots_per_ip_span_all_sessions_of_that_ip(reg, monkeypatch):
    monkeypatch.setattr(S, "IP_MAX_BOTS", 5)
    monkeypatch.setattr(S, "IP_MAX_SESSIONS", 0)
    monkeypatch.setattr(S, "IP_SESSIONS_PER_MINUTE", 0)
    a, b = reg.create("203.0.113.1"), reg.create("203.0.113.1")
    a.deploy([LINK], 3, "split", "A", True)
    with pytest.raises(S.SessionError, match="adresse IP"):
        b.deploy([LINK], 3, "split", "B", True)  # 3 + 3 > 5, même depuis une autre session
    b.deploy([LINK], 2, "split", "B", True)
    other = reg.create("203.0.113.2")
    other.deploy([LINK], 5, "split", "C", True)
    assert reg.active_bots("203.0.113.1") == 5


async def test_finished_bots_free_the_ip_quota(reg, monkeypatch):
    monkeypatch.setattr(S, "IP_MAX_BOTS", 2)
    monkeypatch.setattr(S, "IP_SESSIONS_PER_MINUTE", 0)
    s = reg.create("203.0.113.1")
    bots = s.deploy([LINK], 2, "split", "A", True)
    bots[0]._set_status("left")
    s.deploy([LINK], 1, "split", "B", True)


def test_local_mode_has_no_ip_limits(monkeypatch):
    import importlib

    monkeypatch.setenv("ZOOMBOTS_MODE", "local")
    for name in ("ZOOMBOTS_IP_MAX_SESSIONS", "ZOOMBOTS_IP_MAX_BOTS", "ZOOMBOTS_IP_SESSIONS_PER_MINUTE"):
        monkeypatch.delenv(name, raising=False)
    try:
        local = importlib.reload(S)
        assert (local.IP_MAX_SESSIONS, local.IP_MAX_BOTS, local.IP_SESSIONS_PER_MINUTE) == (0, 0, 0)
        monkeypatch.delenv("ZOOMBOTS_MODE")
        server = importlib.reload(S)
        assert server.IP_MAX_SESSIONS > 0 and server.IP_MAX_BOTS > 0 and server.IP_SESSIONS_PER_MINUTE > 0
    finally:
        monkeypatch.delenv("ZOOMBOTS_MODE", raising=False)
        importlib.reload(S)
