import asyncio
import subprocess
import sys
import textwrap
import time

import pytest

from zoombots import manager as mgr
from zoombots import sessions as S
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
def env(tmp_path, monkeypatch):
    async def fake_run(self, pw, gate=None):
        self._set_status("in_meeting")
        await asyncio.sleep(3600)

    monkeypatch.setattr(Bot, "run", fake_run)
    monkeypatch.setattr(mgr, "async_playwright", FakePlaywright)
    monkeypatch.setattr(mgr, "cleanup_orphans", _noop)
    monkeypatch.setattr(S, "ROOT", tmp_path / "sessions")
    manager = mgr.BotManager(state_path=tmp_path / "state.json")
    manager._pw = object()
    return manager, S.SessionManager(manager), tmp_path


def new(env):
    return env[1].create()


LINK = {"meeting": "123456789"}


@pytest.fixture
def wav_bytes(tmp_path):
    path = tmp_path / "x.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=0.3", str(path)], check=True)
    return path.read_bytes()


async def chunks(data, size=4096):
    for i in range(0, len(data), size):
        yield data[i:i + size]


# -- répartition -----------------------------------------------------------

def test_distribute():
    assert S.distribute(3, 10, "split") == [4, 3, 3]
    assert S.distribute(3, 2, "split") == [1, 1, 0]
    assert S.distribute(2, 3, "each") == [3, 3]
    assert sum(S.distribute(7, 50, "split")) == 50


async def test_deploy_spreads_bots_over_links_and_names_them(env):
    manager, _, _ = env
    s = new(env)
    bots = s.deploy([{"meeting": "111111111"}, {"meeting": "222222222"}, {"meeting": "333333333"}], 5, "split", "Bot", True)
    assert [b.meeting.id for b in bots] == ["111111111"] * 2 + ["222222222"] * 2 + ["333333333"]
    assert [b.name for b in bots] == [f"Bot {i}" for i in range(1, 6)]
    assert all(b.owner == s.id for b in bots)
    each = new(env).deploy([{"meeting": "111111111"}, {"meeting": "222222222"}], 2, "each", "X", True)
    assert [b.meeting.id for b in each] == ["111111111"] * 2 + ["222222222"] * 2
    single = new(env).deploy([LINK], 1, "split", "Solo", True)
    assert single[0].name == "Solo"


async def test_deploy_validation_and_quotas(env, monkeypatch):
    s = new(env)
    with pytest.raises(S.SessionError, match="lien 2"):
        s.deploy([LINK, {"meeting": "nope"}], 1, "split", "B", True)
    with pytest.raises(S.SessionError):
        s.deploy([LINK], 1, "bizarre", "B", True)
    monkeypatch.setattr(S, "SESSION_MAX_BOTS", 3)
    s.deploy([LINK], 3, "split", "B", True)
    with pytest.raises(S.SessionError) as exc:
        s.deploy([LINK], 1, "split", "B", True)
    assert exc.value.status == 429
    monkeypatch.setattr(mgr, "MAX_BOTS", 3)  # plafond global du serveur
    with pytest.raises(S.SessionError) as exc:
        new(env).deploy([LINK], 1, "split", "B", True)
    assert exc.value.status == 429


async def test_session_bots_are_not_persisted_nor_hit_by_admin_broadcast(env, tmp_path, monkeypatch):
    manager, _, _ = env
    s = new(env)
    s.deploy([LINK], 2, "split", "B", True)
    await asyncio.sleep(0)
    manager.flush()
    assert state.load(manager._state_path) == []
    monkeypatch.setattr(mgr, "SOUNDS_DIR", tmp_path)
    (tmp_path / "a.wav").write_bytes(b"x")
    assert (await manager.broadcast("a.wav"))["played"] == 0


# -- isolation -------------------------------------------------------------

async def test_sessions_cannot_touch_each_others_bots(env):
    a, b = new(env), new(env)
    bot_a = a.deploy([LINK], 1, "split", "A", True)[0]
    b.deploy([LINK], 1, "split", "B", True)
    assert [x.name for x in b.bots] == ["B"] and [x.name for x in a.bots] == ["A"]
    with pytest.raises(S.SessionError) as exc:
        b.bot(bot_a.id)
    assert exc.value.status == 404
    with pytest.raises(S.SessionError):
        await b.remove_bot(bot_a.id)
    with pytest.raises(S.SessionError):
        b.stop_actions([bot_a.id])
    with pytest.raises(S.SessionError):
        b.run_action([bot_a.id], "lib:x.wav", 1, 0, 1, 0)
    assert bot_a.id in env[0].bots


# -- actions ---------------------------------------------------------------

def fake_play(monkeypatch, played, fail=None):
    async def play_wait(self, path, volume=1.0):
        if fail:
            raise RuntimeError(fail)
        played.append((self.id, path.name, volume))
        await asyncio.sleep(0.01)

    monkeypatch.setattr(Bot, "play_wait", play_wait)


async def finish(session, bot_id, timeout=5):
    deadline = time.monotonic() + timeout
    while session.actions[bot_id].status in ("pending", "running"):
        assert time.monotonic() < deadline, "action jamais terminée"
        await asyncio.sleep(0.01)


async def add_sound(session, wav_bytes, name="beep.wav"):
    return await session.add_upload(name, chunks(wav_bytes))


async def test_action_repeats_sound_n_times_sequentially(env, wav_bytes, monkeypatch):
    played = []
    fake_play(monkeypatch, played)
    s = new(env)
    bots = s.deploy([LINK], 2, "split", "B", True)
    sound = await add_sound(s, wav_bytes)
    await asyncio.sleep(0)
    s.run_action(None, sound.id, 3, 0, 0.5, 0)
    for b in bots:
        await finish(s, b.id)
        assert (s.actions[b.id].done, s.actions[b.id].status) == (3, "done")
    assert len(played) == 6 and {p[2] for p in played} == {0.5}


async def test_action_waits_for_bot_to_join(env, wav_bytes, monkeypatch):
    played = []
    fake_play(monkeypatch, played)
    s = new(env)
    bot = s.deploy([LINK], 1, "split", "B", True)[0]
    sound = await add_sound(s, wav_bytes)
    await asyncio.sleep(0)
    bot.status = "joining"
    s.run_action([bot.id], sound.id, 1, 0, 1, 0)
    await asyncio.sleep(0.2)
    assert played == [] and s.actions[bot.id].status == "running"
    bot.status = "in_meeting"
    await finish(s, bot.id)
    assert len(played) == 1


async def test_stop_cancels_action_and_silences_bot(env, wav_bytes, monkeypatch):
    stopped = []

    async def slow(self, path, volume=1.0):
        await asyncio.sleep(3600)

    monkeypatch.setattr(Bot, "play_wait", slow)
    monkeypatch.setattr(Bot, "stop_sounds", lambda self: stopped.append(self.id))
    s = new(env)
    bot = s.deploy([LINK], 1, "split", "B", True)[0]
    sound = await add_sound(s, wav_bytes)
    await asyncio.sleep(0)
    s.run_action(None, sound.id, 5, 0, 1, 0)
    await asyncio.sleep(0.05)
    s.stop_actions()
    await asyncio.gather(s.actions[bot.id].task, return_exceptions=True)
    assert s.actions[bot.id].status == "stopped" and bot.id in stopped


async def test_action_stops_after_repeated_failures(env, wav_bytes, monkeypatch):
    monkeypatch.setattr(asyncio, "sleep", _fast_sleep)
    fake_play(monkeypatch, [], fail="micro")
    s = new(env)
    bot = s.deploy([LINK], 1, "split", "B", True)[0]
    sound = await add_sound(s, wav_bytes)
    await _real_sleep(0)
    s.run_action(None, sound.id, 3, 0, 1, 0)
    await finish(s, bot.id)
    assert s.actions[bot.id].status == "error" and "micro" in s.actions[bot.id].error


_real_sleep = asyncio.sleep


async def _fast_sleep(*_):
    await _real_sleep(0)


async def test_new_action_replaces_previous_one(env, wav_bytes, monkeypatch):
    fake_play(monkeypatch, [])
    s = new(env)
    bot = s.deploy([LINK], 1, "split", "B", True)[0]
    sound = await add_sound(s, wav_bytes)
    await asyncio.sleep(0)
    s.run_action(None, sound.id, 1000, 5, 1, 0)
    first = s.actions[bot.id].task
    s.run_action(None, sound.id, 1, 0, 1, 0)
    await asyncio.gather(first, return_exceptions=True)
    assert first.cancelled() or first.done()
    await finish(s, bot.id)
    assert s.actions[bot.id].total == 1


async def test_action_validation(env, wav_bytes):
    s = new(env)
    sound = await add_sound(s, wav_bytes)
    with pytest.raises(S.SessionError, match="aucun bot"):
        s.run_action(None, sound.id, 1, 0, 1, 0)
    s.deploy([LINK], 1, "split", "B", True)
    with pytest.raises(S.SessionError) as exc:
        s.run_action(None, "s_inconnu", 1, 0, 1, 0)
    assert exc.value.status == 404
    with pytest.raises(S.SessionError):
        s.run_action(None, sound.id, 0, 0, 1, 0)


# -- sons ------------------------------------------------------------------

async def test_upload_ok_and_sanitised(env, wav_bytes):
    s = new(env)
    sound = await add_sound(s, wav_bytes, "../../evil.wav")
    assert sound.name == "evil.wav" and sound.path.parent == s.dir and sound.duration > 0.2
    assert sound.path.exists()
    s.delete_sound(sound.id)
    assert not sound.path.exists()


async def test_upload_rejects_garbage_big_and_unknown_formats(env, wav_bytes, monkeypatch):
    s = new(env)
    with pytest.raises(S.SessionError, match="illisible"):
        await add_sound(s, b"pas de l'audio" * 100)
    assert list(s.dir.iterdir()) == []  # pas de fichier résiduel
    with pytest.raises(S.SessionError, match="format"):
        await add_sound(s, wav_bytes, "script.sh")
    monkeypatch.setattr(S, "MAX_UPLOAD_BYTES", 1000)
    with pytest.raises(S.SessionError) as exc:
        await add_sound(s, wav_bytes)
    assert exc.value.status == 413 and list(s.dir.iterdir()) == []
    monkeypatch.setattr(S, "MAX_UPLOAD_BYTES", 10**7)
    monkeypatch.setattr(S, "MAX_SOUND_SECONDS", 0.1)
    with pytest.raises(S.SessionError, match="trop long"):
        await add_sound(s, wav_bytes)


async def test_sound_count_limit(env, wav_bytes, monkeypatch):
    monkeypatch.setattr(S, "MAX_SOUNDS", 1)
    s = new(env)
    await add_sound(s, wav_bytes)
    with pytest.raises(S.SessionError) as exc:
        await add_sound(s, wav_bytes)
    assert exc.value.status == 429


def test_youtube_url_whitelist():
    assert S.youtube_url("https://www.youtube.com/watch?v=abc") .startswith("https://www.youtube.com")
    assert S.youtube_url("https://youtu.be/abc")
    for bad in ("http://evil.com/watch?v=1", "https://youtube.com.evil.com/x", "file:///etc/passwd",
                "https://169.254.169.254/", "javascript:alert(1)", "https://notyoutube.com/x"):
        with pytest.raises(S.SessionError):
            S.youtube_url(bad)


FAKE_YTDLP = textwrap.dedent('''
    import subprocess, sys
    args = sys.argv[1:]
    if "FAIL" in args[-1]:
        sys.stderr.write("ERROR: Video unavailable\\n"); sys.exit(1)
    out = args[args.index("-o") + 1].replace("%(ext)s", "mp3")
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=0.3", "-f", "wav", out], check=True)
    print("Ma vidéo\\t" + out)
''')


@pytest.fixture
def fake_ytdlp(tmp_path, monkeypatch):
    script = tmp_path / "fake_ytdlp.py"
    script.write_text(FAKE_YTDLP)
    monkeypatch.setattr(S, "ytdlp_command", lambda: [sys.executable, str(script)])


async def wait_job(session, job_id, timeout=15):
    deadline = time.monotonic() + timeout
    while session.jobs.get(job_id, {}).get("status") == "downloading":
        assert time.monotonic() < deadline
        await asyncio.sleep(0.05)


async def test_youtube_download_becomes_a_session_sound(env, fake_ytdlp):
    s = new(env)
    job = s.start_youtube("https://www.youtube.com/watch?v=abc")
    await wait_job(s, job)
    assert s.jobs[job]["status"] == "ready"
    sound = s.sounds[job]
    assert sound.name == "Ma vidéo.mp3" and sound.source == "youtube" and sound.path.parent == s.dir
    assert s.snapshot()["jobs"] == []  # un job terminé n'encombre pas l'interface


async def test_youtube_failure_is_reported(env, fake_ytdlp):
    s = new(env)
    job = s.start_youtube("https://www.youtube.com/watch?v=FAIL")
    await wait_job(s, job)
    assert s.jobs[job]["status"] == "error" and "unavailable" in s.jobs[job]["error"]
    assert s.snapshot()["jobs"][0]["status"] == "error"


async def test_youtube_concurrency_limit(env, fake_ytdlp, monkeypatch):
    async def never(*a, **k):
        await asyncio.sleep(3600)

    monkeypatch.setattr(S.Session, "_download", never)
    s = new(env)
    s.start_youtube("https://youtu.be/a")
    s.start_youtube("https://youtu.be/b")
    with pytest.raises(S.SessionError) as exc:
        s.start_youtube("https://youtu.be/c")
    assert exc.value.status == 429
    await s.destroy()


async def test_library_sound_resolution(env, tmp_path, monkeypatch):
    monkeypatch.setattr(mgr, "SOUNDS_DIR", tmp_path)
    (tmp_path / "jingle.mp3").write_bytes(b"x")
    s = new(env)
    assert s.resolve_sound("lib:jingle.mp3")[0] == "jingle.mp3"
    for bad in ("lib:../x.mp3", "lib:nope.mp3"):
        with pytest.raises(S.SessionError):
            s.resolve_sound(bad)
    assert s.snapshot()["library"] == ["jingle.mp3"]


# -- cycle de vie ----------------------------------------------------------

async def test_destroy_removes_everything(env, wav_bytes, monkeypatch):
    manager, _, _ = env
    fake_play(monkeypatch, [])
    s = new(env)
    s.deploy([LINK], 2, "split", "B", True)
    sound = await add_sound(s, wav_bytes)
    await asyncio.sleep(0)
    s.run_action(None, sound.id, 1000, 5, 1, 0)
    await asyncio.sleep(0.05)
    await s.destroy()
    assert manager.bots == {} and not s.dir.exists()
    assert all(a.task.done() for a in s.actions.values())
    with pytest.raises(S.SessionError):
        s.deploy([LINK], 1, "split", "B", True)
    await s.destroy()  # idempotent


def test_expiry_rules(env, monkeypatch):
    monkeypatch.setattr(S, "GRACE_S", 10)
    s = new(env)
    now = time.monotonic()
    assert not s.expired(now) and s.expired(now + 31)  # jamais connectée : 30 s
    s.attach()
    assert not s.expired(now + 1000)
    s.detach()
    assert not s.expired(time.monotonic() + 5) and s.expired(time.monotonic() + 11)
    s.attach()  # reconnexion dans le délai : la session continue
    assert not s.expired(time.monotonic() + 1000)


async def test_reaper_destroys_disconnected_sessions(env, monkeypatch):
    manager, sessions, _ = env
    monkeypatch.setattr(S, "GRACE_S", 0.2)
    await sessions.start()
    try:
        s = sessions.create()
        s.deploy([LINK], 2, "split", "B", True)
        s.attach()
        s.detach()
        await asyncio.sleep(0.2)
        assert s.id in sessions.sessions and len(manager.bots) == 2
        deadline = time.monotonic() + 5
        while sessions.sessions and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert not sessions.sessions and manager.bots == {}
    finally:
        await sessions.stop()


async def test_session_limit(env, monkeypatch):
    monkeypatch.setattr(S, "MAX_SESSIONS", 2)
    new(env), new(env)
    with pytest.raises(S.SessionError) as exc:
        new(env)
    assert exc.value.status == 503
    with pytest.raises(S.SessionError) as exc:
        env[1].get("inconnue")
    assert exc.value.status == 401
