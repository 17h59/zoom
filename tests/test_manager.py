import asyncio

import pytest

from zoombots import manager as mgr
from zoombots import state
from zoombots.bot import Bot
from zoombots.meeting import Meeting


class FakePlaywright:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *a):
        pass


async def _noop(*a, **k):
    return 0


@pytest.fixture
def manager(tmp_path, monkeypatch):
    async def fake_run(self, pw, gate=None):
        self._set_status("in_meeting")
        await asyncio.sleep(3600)

    monkeypatch.setattr(Bot, "run", fake_run)
    monkeypatch.setattr(mgr, "async_playwright", FakePlaywright)
    monkeypatch.setattr(mgr, "cleanup_orphans", _noop)
    return lambda: mgr.BotManager(state_path=tmp_path / "state.json")


async def test_spawn_persists_and_remove_clears(manager):
    m = manager()
    await m.start()
    bots = m.spawn(Meeting("123456789", "pw"), ["a", "b"])
    saved = state.load(m._state_path)
    assert {e["id"] for e in saved} == {b.id for b in bots}
    assert saved[0]["passcode"] == "pw"
    await m.remove(bots[0].id)
    assert [e["id"] for e in state.load(m._state_path)] == [bots[1].id]
    await m.shutdown()


async def test_clean_shutdown_keeps_state_and_restart_restores_it(manager):
    m = manager()
    await m.start()
    bots = m.spawn(Meeting("123456789", "pw", "acme.zoom.us"), ["a", "b"])
    await asyncio.sleep(0)
    await m.shutdown()
    assert {e["id"] for e in state.load(m._state_path)} == {b.id for b in bots}

    again = manager()
    await again.start()
    assert set(again.bots) == {b.id for b in bots}
    assert again.bots[bots[0].id].meeting.host == "acme.zoom.us"
    assert again.bots[bots[0].id].meeting.passcode == "pw"
    await again.shutdown()


async def test_finished_bots_are_not_persisted_nor_counted(manager, monkeypatch):
    monkeypatch.setattr(mgr, "MAX_BOTS", 1)
    m = manager()
    await m.start()
    bot = m.spawn(Meeting("123456789"), ["a"])[0]
    await asyncio.sleep(0)
    with pytest.raises(OverflowError):
        m.spawn(Meeting("123456789"), ["b"])
    bot._set_status("denied", "x")
    await asyncio.sleep(0)
    assert state.load(m._state_path) == []
    m.spawn(Meeting("123456789"), ["b"])  # le bot terminé ne compte plus
    assert m.purge_finished() == 1
    await m.shutdown()


async def test_corrupt_state_entries_do_not_block_start(manager, tmp_path):
    (tmp_path / "state.json").write_text(
        '[{"id": "../../x", "meeting": "123456789", "name": "a"},'
        ' {"id": "abcdef01", "meeting": "12", "name": "b"},'
        ' {"id": "abcdef02", "meeting": "123456789", "name": "ok", "host": "evil.com"},'
        ' {"id": "abcdef03", "meeting": "123456789", "name": "ok"}]')
    m = manager()
    await m.start()
    assert list(m.bots) == ["abcdef03"]
    await m.shutdown()


async def test_broadcast_reports_partial_failures(manager, monkeypatch, tmp_path):
    monkeypatch.setattr(mgr, "SOUNDS_DIR", tmp_path)
    (tmp_path / "ok.wav").write_bytes(b"x")
    m = manager()
    await m.start()
    good, bad = m.spawn(Meeting("123456789"), ["a", "b"])
    await asyncio.sleep(0)

    async def boom(self, *a, **k):
        raise RuntimeError("micro")

    played = []

    async def ok(self, *a, **k):
        played.append(self.id)

    monkeypatch.setattr(good, "play", ok.__get__(good))
    monkeypatch.setattr(bad, "play", boom.__get__(bad))
    result = await m.broadcast("ok.wav")
    assert result["played"] == 1 and result["failed"] == [{"bot": bad.id, "error": "micro"}]
    await m.shutdown()


@pytest.mark.parametrize("name", ["../etc/passwd", "a\0b.wav", "sub/x.wav", "", ".gitkeep", "x.txt", "..\\x.wav"])
def test_sound_path_rejects_bad_names(tmp_path, monkeypatch, name):
    monkeypatch.setattr(mgr, "SOUNDS_DIR", tmp_path)
    (tmp_path / ".gitkeep").write_bytes(b"")
    (tmp_path / "x.txt").write_bytes(b"")
    with pytest.raises(FileNotFoundError):
        mgr.sound_path(name)


def test_sound_path_accepts_sound(tmp_path, monkeypatch):
    monkeypatch.setattr(mgr, "SOUNDS_DIR", tmp_path)
    (tmp_path / "ok.WAV").write_bytes(b"x")
    assert mgr.sound_path("ok.WAV").name == "ok.WAV"


def test_state_file_is_private_and_corrupt_file_is_kept(tmp_path):
    p = tmp_path / "s.json"
    state.save([{"id": "abcdef01", "meeting": "1", "name": "a"}], p)
    assert oct(p.stat().st_mode & 0o777) == "0o600"
    p.write_text("not json")
    assert state.load(p) == []
    assert (tmp_path / "s.bak").read_text() == "not json"
