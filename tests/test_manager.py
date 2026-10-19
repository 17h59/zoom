import asyncio

import pytest

from zoombots import manager as mgr
from zoombots import state
from zoombots.bot import Bot
from zoombots.meeting import Meeting


@pytest.fixture
def manager(tmp_path, monkeypatch):
    async def fake_run(self, pw, gate=None):
        self._set_status("in_meeting")
        await asyncio.sleep(3600)

    monkeypatch.setattr(Bot, "run", fake_run)
    monkeypatch.setattr(Bot, "leave", _fake_leave)
    monkeypatch.setattr(mgr, "cleanup_orphans", _noop)
    m = mgr.BotManager(state_path=tmp_path / "state.json")
    m._pw = object()
    return m


async def _noop():
    return 0


async def _fake_leave(self):
    self._closing = True
    self._set_status("left")


async def test_spawn_persists_and_remove_clears(manager):
    bots = manager.spawn(Meeting("123456789", "pw"), ["a", "b"])
    saved = state.load(manager._state_path)
    assert {e["id"] for e in saved} == {b.id for b in bots}
    assert saved[0]["passcode"] == "pw"
    await manager.remove(bots[0].id)
    assert [e["id"] for e in state.load(manager._state_path)] == [bots[1].id]


async def test_restore_relaunches_saved_bots(manager, monkeypatch):
    bots = manager.spawn(Meeting("123456789"), ["a"])
    fresh = mgr.BotManager(state_path=manager._state_path)
    fresh._pw = object()
    for entry in state.load(fresh._state_path):
        fresh._register(Bot(Meeting(entry["meeting"], entry.get("passcode")), entry["name"], bot_id=entry["id"]))
    assert list(fresh.bots) == [bots[0].id]


async def test_max_bots(manager, monkeypatch):
    monkeypatch.setattr(mgr, "MAX_BOTS", 1)
    manager.spawn(Meeting("123456789"), ["a"])
    with pytest.raises(OverflowError):
        manager.spawn(Meeting("123456789"), ["b"])


def test_sound_path_rejects_traversal(tmp_path, monkeypatch):
    monkeypatch.setattr(mgr, "SOUNDS_DIR", tmp_path)
    (tmp_path / "ok.wav").write_bytes(b"x")
    assert mgr.sound_path("ok.wav").name == "ok.wav"
    with pytest.raises(FileNotFoundError):
        mgr.sound_path("../etc/passwd")


def test_state_load_ignores_garbage(tmp_path):
    p = tmp_path / "s.json"
    p.write_text("not json")
    assert state.load(p) == []
