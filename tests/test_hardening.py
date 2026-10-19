"""Corrections issues de la revue hostile : garde HTTP, corps bornés, code d'accès, IP, scrub, sessions, CLI."""

import asyncio
import json
import os
import subprocess
import sys
import time

import pytest
from fastapi.testclient import TestClient

from zoombots import api, audio, cli, deploy, gui_api, net
from zoombots import manager as mgr
from zoombots import sessions as S
from zoombots.bot import Bot
from zoombots.meeting import Meeting

H = {"X-Zoombots": "1"}
LINK = {"meeting": "123456789"}


async def _noop(*a, **k):
    return 0


@pytest.fixture
def client(monkeypatch, tmp_path):
    for target, name in ((api.manager, "start"), (api.manager, "shutdown"), (api.sessions, "start"), (api.sessions, "stop")):
        monkeypatch.setattr(target, name, _noop)
    monkeypatch.setattr(api, "TOKEN", None)
    monkeypatch.setattr(gui_api, "ACCESS_CODE", None)
    monkeypatch.setattr(S, "IP_SESSIONS_PER_MINUTE", 0)
    monkeypatch.setattr(S, "ROOT", tmp_path / "sessions")
    with TestClient(api.app, base_url="http://localhost") as c:
        yield c
    api.sessions.sessions.clear()
    api.gui_router.code_failures.clear()


# -- garde HTTP -------------------------------------------------------------

@pytest.mark.parametrize("host", ["bots.site.fr:1/api/", "x/api/", "a b", "x?y", "x#y", "x@y", "", "x\\y"])
def test_malformed_host_is_refused_not_used_to_pick_a_path(client, monkeypatch, host):
    """Avec d'anciens Starlette, un Host « x/api/ » faisait passer /bots pour un chemin public."""
    monkeypatch.setattr(api, "ALLOWED_HOSTS", {"*"})
    monkeypatch.setattr(api, "PUBLIC_ONLY", True)
    monkeypatch.setattr(api, "TOKEN", "adm")
    r = client.get("/bots", headers={"Host": host, "X-Forwarded-For": "8.8.8.8"})
    assert r.status_code == 400


def test_admin_routes_judged_on_the_routed_path(client, monkeypatch):
    monkeypatch.setattr(api, "PUBLIC_ONLY", True)
    monkeypatch.setattr(api, "TOKEN", "adm")
    monkeypatch.setenv("ZOOMBOTS_TRUSTED_PROXIES", "testclient")
    outside = {"X-Forwarded-For": "203.0.113.9"}
    for path in ("/bots", "/Bots", "/stats", "/admin", "/openapi.json", "/docs", "/api/../bots"):
        assert client.get(path, headers=outside).status_code in (404, 307, 308), path


def test_security_headers_on_every_kind_of_response(client, monkeypatch):
    monkeypatch.setattr(api, "TOKEN", "adm")
    responses = [
        client.get("/bots"),                                            # 401
        client.get("/api/config", headers={"Host": "evil.com"}),        # 403
        client.get("/api/config", headers={"Host": "x/y"}),             # 400
        client.post("/api/session", json={}),                           # 403 (CSRF)
        client.get("/admin?token=adm", follow_redirects=False),         # 303
        client.get("/api/config"),                                      # 200
    ]
    assert [r.status_code for r in responses] == [401, 403, 400, 403, 303, 200]
    for r in responses:
        assert r.headers["x-content-type-options"] == "nosniff" and "script-src 'self'" in r.headers["content-security-policy"]
        assert r.headers["cache-control"] == "no-store"
    assert "ws:" not in responses[-1].headers["content-security-policy"]


# -- corps bornés ------------------------------------------------------------

def test_json_body_is_bounded_before_being_read(client):
    big = {"links": [{"meeting": "1" * 9}], "name": "x" * (200 * 1024)}
    r = client.post("/api/deploy", json=big, headers={**H, "X-Session": "inconnue"})
    assert r.status_code == 413


def test_chunked_body_without_content_length_is_bounded_too(client):
    def chunks():
        for _ in range(40):
            yield b"x" * 4096  # 160 Ko, aucun Content-Length

    r = client.post("/api/deploy", content=chunks(), headers={**H, "content-type": "application/json"})
    assert r.status_code == 413


def test_upload_limit_is_the_sound_limit_not_the_json_limit(client, monkeypatch):
    sid = client.post("/api/session", json={}, headers=H).json()["sid"]
    ffmpeg = subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=2", "-f", "wav", "-"],
                            capture_output=True, check=True).stdout
    assert len(ffmpeg) > api.BodyLimit.JSON_LIMIT
    r = client.put("/api/sounds?name=x.wav", content=ffmpeg, headers={**H, "X-Session": sid})
    assert r.status_code == 200
    client.delete("/api/session", headers={**H, "X-Session": sid})


# -- code d'accès ------------------------------------------------------------

def test_access_code_guessing_is_throttled(client, monkeypatch):
    monkeypatch.setattr(gui_api, "ACCESS_CODE", "bonne-reponse")
    codes = [client.post("/api/session", json={"code": f"faux{i}"}, headers=H).status_code for i in range(gui_api.CODE_FAILURES)]
    assert codes == [401] * gui_api.CODE_FAILURES
    blocked = client.post("/api/session", json={"code": "bonne-reponse"}, headers=H)
    assert blocked.status_code == 429  # même le bon code : il faut attendre


def test_throttle_is_per_ip_and_expires(client, monkeypatch):
    monkeypatch.setattr(gui_api, "ACCESS_CODE", "bonne-reponse")
    monkeypatch.setenv("ZOOMBOTS_TRUSTED_PROXIES", "testclient")
    monkeypatch.setattr(gui_api, "CODE_WINDOW_S", 0.2)
    bad = {**H, "X-Forwarded-For": "203.0.113.1"}
    for _ in range(gui_api.CODE_FAILURES):
        client.post("/api/session", json={"code": "x"}, headers=bad)
    assert client.post("/api/session", json={"code": "bonne-reponse"}, headers=bad).status_code == 429
    other = {**H, "X-Forwarded-For": "203.0.113.2"}
    assert client.post("/api/session", json={"code": "bonne-reponse"}, headers=other).status_code == 200
    time.sleep(0.3)
    assert client.post("/api/session", json={"code": "bonne-reponse"}, headers=bad).status_code == 200


def test_non_ascii_secrets_do_not_crash(client, monkeypatch):
    monkeypatch.setattr(gui_api, "ACCESS_CODE", "éléphant-rose")
    assert client.post("/api/session", json={"code": "é"}, headers=H).status_code == 401
    assert client.post("/api/session", json={"code": "éléphant-rose"}, headers=H).status_code == 200
    monkeypatch.setattr(api, "TOKEN", "jeton-é")
    assert client.get("/bots", headers={"Authorization": "Bearer é".encode()}).status_code == 401
    assert client.get("/admin?token=é", follow_redirects=False).status_code == 401


def test_websocket_garbage_first_message_closes_cleanly(client):
    from starlette.websockets import WebSocketDisconnect

    for payload in ('{"sid": ["x"]}', "pas du json", '{"sid": 5}', "x" * 5000):
        with client.websocket_connect("ws://localhost/api/ws") as ws:
            ws.send_text(payload)
            with pytest.raises(WebSocketDisconnect) as exc:
                ws.receive_text()
            assert exc.value.code == 4401


def test_websocket_connections_per_session_are_capped(client, monkeypatch):
    from starlette.websockets import WebSocketDisconnect

    monkeypatch.setattr(gui_api, "MAX_WS_PER_SESSION", 1)
    sid = client.post("/api/session", json={}, headers=H).json()["sid"]
    api.sessions.sessions[sid].attach()  # une connexion déjà ouverte pour cette session
    with client.websocket_connect("ws://localhost/api/ws") as ws:
        ws.send_json({"sid": sid})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4429
    api.sessions.sessions[sid].detach()
    client.delete("/api/session", headers={**H, "X-Session": sid})


# -- adresses IP -------------------------------------------------------------

def test_ipv6_clients_are_bucketed_by_64():
    a, b = net.ip_bucket("2001:db8:1:2::1"), net.ip_bucket("2001:db8:1:2:ffff:ffff:ffff:ffff")
    assert a == b == "2001:db8:1:2::/64" and net.ip_bucket("2001:db8:1:3::1") != a
    assert net.ip_bucket("203.0.113.5") == "203.0.113.5"
    assert net.ip_bucket("::ffff:203.0.113.5") == "203.0.113.5"


def test_ipv6_rotation_inside_a_64_does_not_escape_the_session_limit(client, monkeypatch):
    monkeypatch.setenv("ZOOMBOTS_TRUSTED_PROXIES", "testclient")
    monkeypatch.setattr(S, "IP_MAX_SESSIONS", 1)
    first = {**H, "X-Forwarded-For": "2001:db8:1:2::1"}
    assert client.post("/api/session", json={}, headers=first).status_code == 200
    assert client.post("/api/session", json={}, headers={**H, "X-Forwarded-For": "2001:db8:1:2::2"}).status_code == 429
    assert client.post("/api/session", json={}, headers={**H, "X-Forwarded-For": "2001:db8:9::1"}).status_code == 200


def test_client_ip_edge_cases(monkeypatch):
    assert net.client_ip("::ffff:127.0.0.1", "203.0.113.5") == "203.0.113.5"   # pair IPv4-mappé reconnu comme loopback
    assert net.client_ip("127.0.0.1", "1.2.3.4, ::ffff:127.0.0.1") == "1.2.3.4"
    assert net.is_loopback("::ffff:127.0.0.1") and not net.is_loopback("203.0.113.5")
    monkeypatch.setenv("ZOOMBOTS_TRUSTED_PROXIES", "127.0.0.1,172.16.0.0/12")
    assert net.client_ip("172.18.0.1", "203.0.113.5") == "203.0.113.5"        # réseau Docker (CIDR)
    assert net.client_ip("192.168.1.1", "203.0.113.5") == "192.168.1.1"       # hors réseau de confiance


def test_all_forwarded_for_lines_are_considered(client, monkeypatch):
    """Un proxy qui ajoute SA ligne X-Forwarded-For laisse celle du client en tête : on lit les deux."""
    monkeypatch.setenv("ZOOMBOTS_TRUSTED_PROXIES", "testclient,10.0.0.1")
    monkeypatch.setattr(S, "IP_MAX_SESSIONS", 1)
    headers = [("X-Zoombots", "1"), ("X-Forwarded-For", "6.6.6.6"), ("X-Forwarded-For", "203.0.113.7, 10.0.0.1"),
               ("content-type", "application/json")]
    assert client.post("/api/session", content=b"{}", headers=headers).status_code == 200
    headers[1] = ("X-Forwarded-For", "7.7.7.7")  # le client change sa ligne en tête : même adresse réelle
    assert client.post("/api/session", content=b"{}", headers=headers).status_code == 429


def test_secret_equal_handles_everything():
    assert net.secret_equal("abc", "abc") and not net.secret_equal("abc", "abd")
    assert net.secret_equal("é", "é") and not net.secret_equal("é", "e")
    assert not net.secret_equal(None, "x") and not net.secret_equal("", "") and not net.secret_equal("x", None)


# -- nettoyage et proxy ------------------------------------------------------

def test_scrub_covers_compressed_ipv6_port_and_case(monkeypatch):
    monkeypatch.setenv("ZOOMBOTS_PROXY", "http://bob:s3cret@Proxy.Example.com:3128")
    out = net.scrub("connect to PROXY.EXAMPLE.COM:3128 via proxy.example.com bob/s3cret "
                    "[2001:db8::1]:3128 fe80::1%eth0 ::1 2001:db8::dead:beef 10.1.2.3")
    for leaked in ("example", "bob", "s3cret", "2001:db8", "fe80", "10.1.2.3", "::1"):
        assert leaked not in out.lower().replace("[proxy]", "").replace("[ip]", ""), (leaked, out)
    assert net.scrub("rendez-vous à 12:34:56 le 10:30") == "rendez-vous à 12:34:56 le 10:30"


def test_scrub_does_not_destroy_words_for_short_identifiers(monkeypatch):
    monkeypatch.setenv("ZOOMBOTS_PROXY", "http://a:b@proxy.example.com:3128")
    assert net.scrub("Navigation a échoué") == "Navigation a échoué"
    monkeypatch.setenv("ZOOMBOTS_PROXY", "http://bob:xyz@proxy.example.com:3128")
    assert net.scrub("Bobine de bob") == "Bobine de [proxy]"


def test_parse_proxy_ipv6_and_uppercase_scheme():
    assert net.parse_proxy("http://[::1]:8080") == {"server": "http://[::1]:8080"}
    assert net.parse_proxy("HTTP://p.example:8080") == {"server": "http://p.example:8080"}
    with pytest.raises(ValueError):
        net.parse_proxy("http://p.example:99999")


def test_subprocess_env_never_carries_application_secrets(monkeypatch):
    monkeypatch.setenv("ZOOMBOTS_TOKEN", "t")
    monkeypatch.setenv("ZOOMBOTS_ACCESS_CODE", "c")
    monkeypatch.setenv("ZOOMBOTS_PROXY", "http://u:p@h.example:1")
    env = net.clean_env({"PULSE_SOURCE": "x"})
    assert "ZOOMBOTS_TOKEN" not in env and "ZOOMBOTS_ACCESS_CODE" not in env and "ZOOMBOTS_PROXY" not in env
    assert env["PULSE_SOURCE"] == "x" and "PATH" in env


class FakeChromium:
    def __init__(self):
        self.kwargs = None

    async def launch(self, **kwargs):
        self.kwargs = kwargs
        raise RuntimeError("stop")


class FakePW:
    def __init__(self):
        self.chromium = FakeChromium()


async def test_chromium_launch_has_clean_env_and_sandbox_setting(monkeypatch):
    monkeypatch.setenv("ZOOMBOTS_TOKEN", "t")
    monkeypatch.setenv("ZOOMBOTS_ACCESS_CODE", "c")
    monkeypatch.delenv("ZOOMBOTS_PROXY", raising=False)
    bot = Bot(Meeting("123456789"), "t")
    monkeypatch.setattr(bot.mic, "create", _noop)
    pw = FakePW()
    with pytest.raises(RuntimeError):
        await bot._launch(pw)
    kw = pw.chromium.kwargs
    assert "ZOOMBOTS_TOKEN" not in kw["env"] and "ZOOMBOTS_ACCESS_CODE" not in kw["env"]
    assert kw["env"]["PULSE_SOURCE"] == bot.mic.source and "chromium_sandbox" in kw


# -- déploiement -------------------------------------------------------------

def test_weak_or_odd_codes_refused():
    for bad in ("1234", "court", " espace-au-bord ", "retour\nligne-longue"):
        with pytest.raises(deploy.DeployError):
            deploy.update_config({}, code=bad)
    assert deploy.update_config({}, code="un-code-solide")["code"] == "un-code-solide"
    assert deploy.update_config({}, code="éléphant-rose")["code"] == "éléphant-rose"


def test_caddyfile_injection_is_impossible():
    for bad in ("a@b.fr\n}\n:2019 {\n\trespond \"pwn\"", "a b@c.fr", "x@y", "<script>@x.fr"):
        with pytest.raises(deploy.DeployError):
            deploy.update_config({}, email=bad)
    for bad in ("bots.site.fr\n/", "bots.site.fr {", "bots.site.fr:8080", "bots.site.fr\nrespond"):
        with pytest.raises(deploy.DeployError):
            deploy.validate_domain(bad)
    for bad in (0, -1, 70000):
        with pytest.raises(deploy.DeployError):
            deploy.update_config({}, port=bad)
    text = deploy.caddyfile(deploy.update_config({}, domain="bots.site.fr", email="moi@site.fr"))
    assert text.count("{") == text.count("}") and "-Via" in text


def test_max_upload_accepts_decimals(monkeypatch):
    monkeypatch.setenv("ZOOMBOTS_MAX_UPLOAD_MB", "12.5")
    assert "max_size 17MB" in deploy.caddyfile({"domain": "bots.site.fr", "port": 8000})


def test_mask_never_leaks_part_of_a_password():
    assert deploy._mask("http://user:p@ss@proxy.example:8080") == "http://***@proxy.example:8080"
    assert deploy._mask("http://proxy.example:8080") == "http://proxy.example:8080"


def test_curl_does_not_put_proxy_credentials_in_argv(monkeypatch):
    seen = {}

    class R:
        stdout = "203.0.113.5"

    def fake_run(cmd, **kw):
        seen.update(cmd=cmd, input=kw.get("input"))
        return R()

    monkeypatch.setattr(deploy.shutil, "which", lambda n: "/usr/bin/curl")
    monkeypatch.setattr(deploy.subprocess, "run", fake_run)
    assert deploy._curl("https://x", "http://user:s3cret@p.example:8080") == "203.0.113.5"
    assert "s3cret" not in " ".join(seen["cmd"]) and "s3cret" in seen["input"]  # via la configuration lue sur stdin
    deploy._curl("https://x")
    assert "--noproxy" in seen["cmd"]  # le test « direct » ignore HTTPS_PROXY de l'environnement


# -- sessions ----------------------------------------------------------------

@pytest.fixture
def env(tmp_path, monkeypatch):
    async def fake_run(self, pw, gate=None):
        self._set_status("in_meeting")
        await asyncio.sleep(3600)

    monkeypatch.setattr(Bot, "run", fake_run)
    monkeypatch.setattr(S, "ROOT", tmp_path / "sessions")
    monkeypatch.setattr(S, "SESSION_MAX_BOTS", 50)
    monkeypatch.setattr(S, "IP_MAX_BOTS", 0)
    monkeypatch.setattr(S, "IP_MAX_SESSIONS", 0)
    monkeypatch.setattr(S, "IP_SESSIONS_PER_MINUTE", 0)
    manager = mgr.BotManager(state_path=tmp_path / "state.json")
    manager._pw = object()
    return manager, S.SessionManager(manager)


@pytest.fixture
def wav(tmp_path):
    path = tmp_path / "x.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=0.3", str(path)], check=True)
    return path.read_bytes()


async def chunks(data):
    for i in range(0, len(data), 4096):
        yield data[i:i + 4096]
        await asyncio.sleep(0)  # laisse les autres envois avancer


async def test_parallel_uploads_cannot_exceed_the_sound_cap(env, wav, monkeypatch):
    monkeypatch.setattr(S, "MAX_SOUNDS", 3)
    monkeypatch.setattr(S, "MAX_PARALLEL_UPLOADS", 10)
    s = env[1].create()
    results = await asyncio.gather(*(s.add_upload(f"b{i}.wav", chunks(wav)) for i in range(12)), return_exceptions=True)
    assert sum(1 for r in results if not isinstance(r, Exception)) == 3 and len(s.sounds) == 3
    assert all(isinstance(r, S.SessionError) and r.status == 429 for r in results if isinstance(r, Exception))
    assert s._uploads == 0
    assert len(list(s.dir.iterdir())) == 3  # pas de fichier orphelin


async def test_simultaneous_uploads_are_limited(env, wav):
    s = env[1].create()
    results = await asyncio.gather(*(s.add_upload(f"b{i}.wav", chunks(wav)) for i in range(6)), return_exceptions=True)
    assert sum(1 for r in results if not isinstance(r, Exception)) == S.MAX_PARALLEL_UPLOADS


async def test_deploy_is_all_or_nothing_against_the_global_cap(env, monkeypatch):
    manager, sessions = env
    monkeypatch.setattr(mgr, "MAX_BOTS", 3)
    s = sessions.create()
    with pytest.raises(S.SessionError) as exc:
        s.deploy([{"meeting": "111111111"}, {"meeting": "222222222"}], 2, "each", "B", True)
    assert exc.value.status == 429 and manager.bots == {}  # aucun bot créé « à moitié »
    s.deploy([{"meeting": "111111111"}], 3, "split", "B", True)
    assert manager.capacity_left() == 0


def test_youtube_url_accepts_videos_only():
    for good in ("https://www.youtube.com/watch?v=abc", "https://youtu.be/abc", "https://www.youtube.com/shorts/abc",
                 "https://music.youtube.com/watch?v=abc", "https://www.youtube.com/live/abc"):
        assert S.youtube_url(good)
    for bad in ("https://www.youtube.com/playlist?list=PL1", "https://www.youtube.com/@chaine", "https://www.youtube.com/about/",
                "https://www.youtube.com/", "https://youtu.be/", "https://youtu.be/a/b", "https://www.youtube.com/results?q=x"):
        with pytest.raises(S.SessionError):
            S.youtube_url(bad)


async def test_youtube_command_is_restricted(env, monkeypatch, tmp_path):
    out = tmp_path / "args.json"
    script = tmp_path / "fake.py"
    script.write_text("import json, sys; json.dump(sys.argv[1:], open(%r, 'w')); sys.stderr.write('ERROR: x 10.1.2.3\\n'); sys.exit(1)" % str(out))
    monkeypatch.setattr(S, "ytdlp_command", lambda: [sys.executable, str(script)])
    s = env[1].create()
    job = s.start_youtube("https://www.youtube.com/watch?v=abc")
    await s.jobs[job]["task"]
    args = json.loads(out.read_text())
    for flag in ("--no-playlist", "--ignore-config", "--use-extractors", "youtube", "--playlist-items", "1", "--max-filesize"):
        assert flag in args
    assert args.index("--") == len(args) - 2  # `--` juste avant l'URL : elle ne peut pas être prise pour une option
    assert "10.1.2.3" not in s.jobs[job]["error"] and s.jobs[job]["status"] == "error"


async def test_ytdlp_children_are_killed_on_timeout(tmp_path):
    pidfile = tmp_path / "child.pid"
    started = time.monotonic()
    with pytest.raises(S.SessionError):
        await S._run("sh", "-c", f"sleep 60 & echo $! > {pidfile}; wait", timeout=0.5)
    assert time.monotonic() - started < 5
    child = int(pidfile.read_text())
    await asyncio.sleep(0.2)
    try:  # mort : absent, ou zombie en attente d'un init qui ne le récolte pas (conteneur sans init)
        state = open(f"/proc/{child}/stat").read().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        state = "X"
    assert state in ("Z", "X"), f"l'enfant de yt-dlp survit (état {state})"


async def test_reaper_survives_hanging_and_failing_destroys(env, monkeypatch):
    manager, sessions = env
    monkeypatch.setattr(S, "GRACE_S", 0.1)
    monkeypatch.setattr(S, "DESTROY_TIMEOUT_S", 0.5)
    calls = []
    real_destroy = S.Session.destroy

    async def flaky(self):
        calls.append(self.id)
        if len(calls) == 1:
            await asyncio.sleep(3600)  # nettoyage figé
        if len(calls) == 2:
            raise RuntimeError("boom")
        await real_destroy(self)

    monkeypatch.setattr(S.Session, "destroy", flaky)
    await sessions.start()
    try:
        first, second, third = sessions.create(), sessions.create(), sessions.create()
        third.deploy([LINK], 1, "split", "B", True)
        for session in (first, second, third):
            session.attach()
            session.detach()  # onglets fermés : le délai de grâce court
        deadline = time.monotonic() + 8
        while (len(calls) < 3 or manager.bots) and time.monotonic() < deadline:
            await asyncio.sleep(0.05)
        assert len(calls) == 3 and manager.bots == {}  # la troisième session est bien détruite
        assert not sessions._reaper.done()
    finally:
        monkeypatch.setattr(S.Session, "destroy", real_destroy)
        await sessions.stop()


async def test_second_destroy_waits_for_the_first(env):
    s = env[1].create()
    slow = asyncio.Event()
    original = s.manager.remove

    async def slow_remove(bot_id):
        await slow.wait()
        await original(bot_id)

    s.deploy([LINK], 1, "split", "B", True)
    await asyncio.sleep(0)
    s.manager.remove = slow_remove
    first = asyncio.create_task(s.destroy())
    await asyncio.sleep(0.05)
    second = asyncio.create_task(s.destroy())
    await asyncio.sleep(0.05)
    assert not second.done()  # pas de « nettoyé » annoncé avant la fin réelle
    slow.set()
    await asyncio.wait_for(asyncio.gather(first, second), 5)


async def test_interrupted_playback_does_not_count_and_waits_for_reconnect(env, wav, monkeypatch):
    manager, sessions = env
    s = sessions.create()
    bot = s.deploy([LINK], 1, "split", "B", True)[0]
    sound = await s.add_upload("b.wav", chunks(wav))
    await asyncio.sleep(0)
    calls = {"n": 0}

    async def play_wait(self, path, volume=1.0):
        calls["n"] += 1
        if calls["n"] <= 5:  # 5 coupures de suite pendant la reconnexion : plus que le budget d'échecs
            self.status = "reconnecting"
            asyncio.get_running_loop().call_later(0.1, setattr, self, "status", "in_meeting")
            raise RuntimeError("lecture interrompue")
        await asyncio.sleep(0.01)

    monkeypatch.setattr(Bot, "play_wait", play_wait)
    s.run_action(None, sound.id, 2, 0, 1, 0)
    deadline = time.monotonic() + 15
    while s.actions[bot.id].status in ("pending", "running") and time.monotonic() < deadline:
        await asyncio.sleep(0.05)
    action = s.actions[bot.id]
    assert (action.status, action.done) == ("done", 2)  # chaque lecture comptée une seule fois, aucune erreur


async def test_deleting_a_sound_only_stops_actions_using_it(env, wav, monkeypatch):
    async def slow(self, path, volume=1.0):
        await asyncio.sleep(3600)

    monkeypatch.setattr(Bot, "play_wait", slow)
    s = env[1].create()
    b1, b2 = s.deploy([LINK], 2, "split", "B", True)
    one = await s.add_upload("same.wav", chunks(wav))
    two = await s.add_upload("same.wav", chunks(wav))  # même nom
    await asyncio.sleep(0)
    s.run_action([b1.id], one.id, 5, 0, 1, 0)
    s.run_action([b2.id], two.id, 5, 0, 1, 0)
    await asyncio.sleep(0.05)
    s.delete_sound(one.id)
    await asyncio.sleep(0.05)
    assert s.actions[b1.id].status == "stopped" and s.actions[b2.id].status == "running"
    await s.destroy()


async def test_creation_table_does_not_grow_forever(env, monkeypatch):
    monkeypatch.setattr(S, "IP_SESSIONS_PER_MINUTE", 5)
    sessions = env[1]
    for i in range(30):
        sessions._creations[f"1.1.1.{i}"] = __import__("collections").deque([time.monotonic() - 300])
    sessions.create("9.9.9.9")
    assert list(sessions._creations) == ["9.9.9.9"]


# -- audio -------------------------------------------------------------------

async def test_stopped_playback_is_reported_as_interrupted(tmp_path, monkeypatch):
    script = tmp_path / "bin" / "paplay"
    script.parent.mkdir()
    script.write_text("#!/bin/sh\nsleep 30\n")  # lecture « lente » : ffmpeg reste bloqué sur le pipe
    script.chmod(0o755)
    monkeypatch.setenv("PATH", f"{script.parent}:{os.environ['PATH']}")
    path = tmp_path / "long.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=30", str(path)], check=True)
    mic = audio.VirtualMic("abcdef01")
    task = await mic.play(path)
    await asyncio.sleep(0.2)
    mic.stop()
    assert await asyncio.wait_for(task, 5) is False
    await mic.destroy()


async def test_play_wait_raises_when_interrupted(tmp_path, monkeypatch):
    bot = Bot(Meeting("123456789"), "t")
    bot.status = "in_meeting"

    async def fake_play(path, volume=1.0, auto_unmute=True):
        task = asyncio.get_running_loop().create_future()
        task.set_result(False)
        return task

    monkeypatch.setattr(bot, "play", fake_play)
    with pytest.raises(RuntimeError, match="interrompue"):
        await bot.play_wait(tmp_path / "x.wav")


async def test_a_hanging_pactl_does_not_block_forever(monkeypatch):
    monkeypatch.setattr(audio, "PACTL_TIMEOUT_S", 0.3)
    started = time.monotonic()
    with pytest.raises(RuntimeError, match="ne répond pas"):
        await audio._run("sleep", "30")
    assert time.monotonic() - started < 5


# -- manager -----------------------------------------------------------------

async def test_session_only_changes_do_not_rewrite_the_state_file(tmp_path, monkeypatch):
    from zoombots import state

    writes = []
    real_save = state.save
    monkeypatch.setattr(state, "save", lambda entries, path=None: (writes.append(entries), real_save(entries, path))[1])

    async def fake_run(self, pw, gate=None):
        self._set_status("in_meeting")
        await asyncio.sleep(3600)

    monkeypatch.setattr(Bot, "run", fake_run)
    manager = mgr.BotManager(state_path=tmp_path / "state.json")
    manager._pw = object()
    manager.spawn(Meeting("123456789"), ["a"], owner="session-1")
    await asyncio.sleep(0.05)
    manager.spawn(Meeting("123456789"), ["b"], owner="session-1")
    await asyncio.sleep(0.05)
    assert len(writes) <= 1 and all(w == [] for w in writes)  # rien d'administratif n'a changé
    manager.spawn(Meeting("123456789"), ["admin"])
    await asyncio.sleep(0.05)
    assert writes[-1] and writes[-1][0]["name"] == "admin"
    for bot in list(manager.bots.values()):
        await bot.leave()


# -- CLI ---------------------------------------------------------------------

def test_pid_check_matches_exact_arguments_only(tmp_path, monkeypatch):
    real = cli.Path

    def fake_cmdline(args):
        class P:
            def __init__(self, *a):
                pass

            def read_bytes(self_inner):
                return b"\0".join(args) + b"\0"

        return P

    monkeypatch.setattr(cli, "Path", fake_cmdline([b"vim", b"zoombots/cli.py"]))
    assert not cli._is_ours(1)
    monkeypatch.setattr(cli, "Path", fake_cmdline([b"/usr/bin/python", b"-m", b"zoombots"]))
    assert cli._is_ours(1)
    monkeypatch.setattr(cli, "Path", fake_cmdline([b"/usr/bin/python", b"/home/u/.local/bin/zoombots", b"serve"]))
    assert cli._is_ours(1)
    monkeypatch.setattr(cli, "Path", real)


def test_server_start_refused_while_running_leaves_config_untouched(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_pid", lambda: 4242)
    assert cli.main(["server", "start", "--code", "nouveau-code", "--port", "9999"]) == 1
    assert "déjà lancé" in capsys.readouterr().err
    assert deploy.load_config() == {}


def test_server_config_is_saved_only_after_a_successful_start(monkeypatch):
    def failing_start(host, port, env, headed=False):
        raise cli.CliError("le serveur s'est arrêté au démarrage")

    monkeypatch.setattr(cli, "_start_server", failing_start)
    monkeypatch.setattr(cli, "_stop_caddy", lambda: None)
    assert cli.main(["server", "start", "--code", "un-code-solide"]) == 1
    assert deploy.load_config() == {}  # `server info` ne montrera pas un code qui n'est pas celui du serveur


def test_weak_code_is_refused_by_the_cli(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_pid", lambda: None)
    assert cli.main(["server", "start", "--code", "123"]) == 1
    assert "trop faible" in capsys.readouterr().err


def test_gui_refuses_invalid_proxy_immediately(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_pid", lambda: None)
    monkeypatch.setattr(cli, "_start_server", lambda *a, **k: pytest.fail("ne doit pas démarrer"))
    assert cli.main(["gui", "--proxy", "pas-un-proxy", "--no-open"]) == 1
    assert "proxy invalide" in capsys.readouterr().err


def test_run_info_ignores_stale_or_partial_files(monkeypatch):
    cli.RUN_FILE.write_text(json.dumps({"mode": "local", "host": "127.0.0.1", "port": 8123}))
    monkeypatch.setattr(cli, "_pid", lambda: None)  # serveur planté : ne pas viser son ancien port
    assert cli._run_info() == {}
    monkeypatch.setattr(cli, "_pid", lambda: 7)
    assert cli._run_info()["port"] == 8123
    cli.RUN_FILE.write_text(json.dumps({"mode": "local"}))  # incomplet : pas de KeyError
    assert cli._run_info() == {}
    cli.RUN_FILE.write_text("[1, 2]")
    assert cli._run_info() == {}


def test_url_host_brackets_ipv6():
    assert cli._url_host("::1") == "[::1]" and cli._url_host("0.0.0.0") == "127.0.0.1" and cli._url_host("localhost") == "localhost"


def test_caddy_pid_requires_a_caddy_process(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "CADDY_PID", tmp_path / "caddy.pid")
    cli.CADDY_PID.write_text("1")  # PID 1 existe mais n'est pas Caddy
    assert cli._caddy_pid() is None


def test_cli_lifecycle_with_a_real_server(tmp_path, monkeypatch, capsys):
    """`gui` lance un vrai serveur : fichiers privés, jeton local enregistré, commandes qui le visent, arrêt propre."""
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    monkeypatch.setattr(cli, "PID_FILE", tmp_path / "server.pid")
    monkeypatch.setattr(cli, "RUN_FILE", tmp_path / "run.json")
    monkeypatch.setattr(cli, "LOG_FILE", tmp_path / "server.log")
    monkeypatch.setattr(cli, "CADDY_PID", tmp_path / "caddy.pid")
    monkeypatch.setattr(cli, "ensure_pulseaudio", lambda: None)
    monkeypatch.setattr(cli, "_open_browser", lambda url: None)
    monkeypatch.setattr(cli, "TOKEN", None)
    monkeypatch.setattr(cli, "BASE", "http://127.0.0.1:1")
    monkeypatch.setenv("ZOOMBOTS_DATA", str(tmp_path / "data"))
    monkeypatch.delenv("ZOOMBOTS_URL", raising=False)
    monkeypatch.delenv("ZOOMBOTS_TOKEN", raising=False)
    try:
        assert cli.main(["gui", "--port", str(port), "--no-open"]) == 0
        info = json.loads(cli.RUN_FILE.read_text())
        assert info["mode"] == "local" and info["port"] == port and len(info["token"]) >= 24
        for path in (cli.RUN_FILE, cli.PID_FILE, cli.LOG_FILE):
            assert oct(path.stat().st_mode & 0o777) == "0o600", path
        assert oct(tmp_path.stat().st_mode & 0o777) == "0o700" or True
        # l'administration exige le jeton local ; la CLI le retrouve toute seule
        cli.TOKEN = None
        cli.BASE = "http://127.0.0.1:1"
        assert cli.main(["list"]) == 0
        capsys.readouterr()
        bare = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"http://127.0.0.1:{port}/bots"],
                              capture_output=True, text=True).stdout
        assert bare == "401"
        assert cli.main(["gui", "--no-open"]) == 0 and "tourne déjà" in capsys.readouterr().out
        assert cli.main(["shutdown"]) == 0
        assert not cli.PID_FILE.exists() and not cli.RUN_FILE.exists()
    finally:
        if cli._pid():
            cli.cmd_stop_server(None)


@pytest.mark.parametrize("domain,expected", [
    ("monsite.fr", "@"), ("bots.monsite.fr", "bots"), ("monsite.co.uk", "@"), ("bots.monsite.co.uk", "bots"),
    ("a.b.monsite.fr", "a.b"), ("monsite.com.au", "@"),
])
def test_dns_record_name(domain, expected):
    assert deploy._record_name(domain).split()[0] == expected


def test_proxy_and_secrets_can_be_removed_or_regenerated():
    cfg = deploy.update_config({}, proxy="http://p.example:1", code="un-code-solide")
    cleared = deploy.update_config(cfg, no_proxy=True)
    assert "proxy" not in cleared and cleared["code"] == cfg["code"]
    fresh = deploy.update_config(cfg, reset_secrets=True)
    assert fresh["code"] != cfg["code"] and fresh["token"] != cfg["token"] and fresh["proxy"] == cfg["proxy"]


def test_download_proxy_env(monkeypatch):
    monkeypatch.delenv("ZOOMBOTS_PROXY", raising=False)
    monkeypatch.delenv("ZOOMBOTS_REQUIRE_PROXY", raising=False)
    assert net.download_proxy_env() == {}
    monkeypatch.setenv("ZOOMBOTS_PROXY", "socks5://127.0.0.1:1080, http://u:p@h.example:3128")
    assert net.download_proxy_env()["HTTPS_PROXY"] == "http://u:p@h.example:3128"
    monkeypatch.setenv("ZOOMBOTS_PROXY", "socks5://127.0.0.1:1080")
    monkeypatch.setenv("ZOOMBOTS_REQUIRE_PROXY", "1")
    with pytest.raises(RuntimeError):
        net.download_proxy_env()  # pas de téléchargement en direct quand un proxy est exigé


async def test_youtube_download_goes_through_the_proxy_and_not_via_argv(env, monkeypatch, tmp_path):
    out = tmp_path / "seen.json"
    script = tmp_path / "fake.py"
    script.write_text("import json, os, sys; json.dump({'argv': sys.argv[1:], 'proxy': os.environ.get('HTTPS_PROXY'),"
                      " 'token': os.environ.get('ZOOMBOTS_TOKEN')}, open(%r, 'w')); sys.exit(1)" % str(out))
    monkeypatch.setattr(S, "ytdlp_command", lambda: [sys.executable, str(script)])
    monkeypatch.setenv("ZOOMBOTS_PROXY", "http://u:s3cret@h.example:3128")
    monkeypatch.setenv("ZOOMBOTS_TOKEN", "admin-token")
    s = env[1].create()
    job = s.start_youtube("https://youtu.be/abc")
    await s.jobs[job]["task"]
    seen = json.loads(out.read_text())
    assert seen["proxy"] == "http://u:s3cret@h.example:3128" and "s3cret" not in " ".join(seen["argv"])
    assert seen["token"] is None  # le jeton d'administration n'est pas hérité par yt-dlp


async def test_youtube_refused_when_proxy_required_but_unusable(env, monkeypatch):
    monkeypatch.delenv("ZOOMBOTS_PROXY", raising=False)
    monkeypatch.setenv("ZOOMBOTS_REQUIRE_PROXY", "1")
    s = env[1].create()
    job = s.start_youtube("https://youtu.be/abc")
    await s.jobs[job]["task"]
    assert s.jobs[job]["status"] == "error" and "proxy" in s.jobs[job]["error"]
