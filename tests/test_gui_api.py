"""API du GUI de bout en bout (TestClient : HTTP + WebSocket) avec de faux bots."""

import asyncio
import subprocess
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from zoombots import api, gui_api
from zoombots import manager as mgr
from zoombots import sessions as S
from zoombots.bot import Bot

H = {"X-Zoombots": "1"}
WS = "ws://localhost/api/ws"


class FakePlaywright:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *a):
        pass


@pytest.fixture
def client(tmp_path, monkeypatch):
    async def fake_run(self, pw, gate=None):
        self._set_status("in_meeting")
        await asyncio.sleep(3600)

    async def fake_play_wait(self, path, volume=1.0):
        await asyncio.sleep(0.01)

    monkeypatch.setattr(Bot, "run", fake_run)
    monkeypatch.setattr(Bot, "play_wait", fake_play_wait)
    monkeypatch.setattr(mgr, "async_playwright", FakePlaywright)

    async def noop(*a, **k):
        return 0

    monkeypatch.setattr(mgr, "cleanup_orphans", noop)
    monkeypatch.setattr(S, "ROOT", tmp_path / "sessions")
    monkeypatch.setattr(api.manager, "_state_path", tmp_path / "state.json")
    monkeypatch.setattr(api, "TOKEN", None)
    monkeypatch.setattr(gui_api, "ACCESS_CODE", None)
    monkeypatch.setattr(S, "GRACE_S", 0.3)
    with TestClient(api.app, base_url="http://localhost") as c:
        yield c
    assert api.manager.bots == {}


def make_session(client, **extra):
    r = client.post("/api/session", json=extra, headers=H)
    assert r.status_code == 200, r.text
    return r.json()["sid"]


def hdr(sid):
    return {**H, "X-Session": sid}


@pytest.fixture
def wav(tmp_path):
    path = tmp_path / "x.wav"
    subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=d=0.3", str(path)], check=True)
    return path.read_bytes()


def test_page_is_empty_for_a_new_visitor(client):
    sid = make_session(client)
    state = client.get("/api/state", headers=hdr(sid)).json()
    assert state["bots"] == [] and state["sounds"] == [] and state["jobs"] == []


def test_requests_without_valid_session_are_refused(client):
    assert client.get("/api/state").status_code == 401
    assert client.get("/api/state", headers=hdr("nope")).status_code == 401
    assert client.post("/api/deploy", json={"links": [{"meeting": "123456789"}]}, headers=hdr("nope")).status_code == 401


def test_two_users_are_isolated(client):
    a, b = make_session(client), make_session(client)
    r = client.post("/api/deploy", json={"links": [{"meeting": "123456789"}], "count": 2, "name": "A"}, headers=hdr(a))
    ids = r.json()["bots"]
    assert len(ids) == 2
    assert len(client.get("/api/state", headers=hdr(a)).json()["bots"]) == 2
    assert client.get("/api/state", headers=hdr(b)).json()["bots"] == []
    assert client.delete(f"/api/bots/{ids[0]}", headers=hdr(b)).status_code == 404
    assert client.get(f"/api/bots/{ids[0]}/screenshot", headers=hdr(b)).status_code == 404
    assert client.post(f"/api/bots/{ids[0]}/mic", json={"on": True}, headers=hdr(b)).status_code == 404
    assert client.post("/api/actions/stop", json={"bots": ids}, headers=hdr(b)).status_code == 404
    assert client.delete("/api/bots", headers=hdr(b)).json() == {"removed": 0}
    assert len(client.get("/api/state", headers=hdr(a)).json()["bots"]) == 2
    client.delete("/api/session", headers=hdr(a))
    client.delete("/api/session", headers=hdr(b))


def test_deploy_validation_errors_are_readable(client):
    sid = make_session(client)
    r = client.post("/api/deploy", json={"links": [{"meeting": "pas un lien"}]}, headers=hdr(sid))
    assert r.status_code == 422 and "lien 1" in r.json()["detail"]
    assert client.post("/api/deploy", json={"links": []}, headers=hdr(sid)).status_code == 422
    assert client.post("/api/deploy", json={"links": [{"meeting": "123456789"}], "count": 999}, headers=hdr(sid)).status_code == 422
    assert client.post("/api/deploy", json={"links": [{"meeting": "https://evil.com/j/123456789"}]}, headers=hdr(sid)).status_code == 422
    client.delete("/api/session", headers=hdr(sid))


def test_websocket_pushes_state_and_closing_it_closes_the_bots(client):
    sid = make_session(client)
    client.post("/api/deploy", json={"links": [{"meeting": "123456789"}], "count": 2}, headers=hdr(sid))
    with client.websocket_connect(WS, headers={"origin": "http://localhost"}) as ws:
        ws.send_json({"sid": sid})
        snap = ws.receive_json()
        assert len(snap["bots"]) == 2
        assert len(api.manager.bots) == 2
        ws.send_text("ping")
    # onglet fermé : après le délai de grâce, la session et ses bots disparaissent
    deadline = time.monotonic() + 5
    while api.manager.bots and time.monotonic() < deadline:
        time.sleep(0.05)
    assert api.manager.bots == {}
    assert client.get("/api/state", headers=hdr(sid)).status_code == 401


def test_reload_within_grace_keeps_the_session(client):
    sid = make_session(client)
    client.post("/api/deploy", json={"links": [{"meeting": "123456789"}]}, headers=hdr(sid))
    with client.websocket_connect(WS) as ws:
        ws.send_json({"sid": sid})
        ws.receive_json()
    time.sleep(0.1)  # < GRACE_S
    with client.websocket_connect(WS) as ws:
        ws.send_json({"sid": sid})
        assert len(ws.receive_json()["bots"]) == 1
        time.sleep(0.6)  # connecté : jamais expirée
        assert client.get("/api/state", headers=hdr(sid)).status_code == 200
    client.delete("/api/session", headers=hdr(sid))


def test_websocket_rejects_bad_session_host_and_origin(client):
    with client.websocket_connect(WS) as ws:
        ws.send_json({"sid": "inconnue"})
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4401
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(WS, headers={"origin": "https://evil.com"}) as ws:
            ws.receive_json()
    assert exc.value.code == 4403
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect(WS, headers={"host": "evil.com"}) as ws:
            ws.receive_json()
    assert exc.value.code == 4403


def test_upload_play_and_delete_sound(client, wav):
    sid = make_session(client)
    client.post("/api/deploy", json={"links": [{"meeting": "123456789"}]}, headers=hdr(sid))
    r = client.put("/api/sounds?name=beep.wav", content=wav, headers=hdr(sid))
    assert r.status_code == 200, r.text
    sound = r.json()
    assert sound["name"] == "beep.wav" and sound["duration"] > 0
    state = client.get("/api/state", headers=hdr(sid)).json()
    assert [s["id"] for s in state["sounds"]] == [sound["id"]]
    r = client.post("/api/actions", json={"sound": sound["id"], "count": 3, "gap": 0}, headers=hdr(sid))
    assert r.status_code == 200 and len(r.json()["bots"]) == 1
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        bot = client.get("/api/state", headers=hdr(sid)).json()["bots"][0]
        if bot["action"]["status"] == "done":
            break
        time.sleep(0.05)
    assert bot["action"]["done"] == 3
    assert client.delete(f"/api/sounds/{sound['id']}", headers=hdr(sid)).status_code == 200
    assert client.delete(f"/api/sounds/{sound['id']}", headers=hdr(sid)).status_code == 404
    client.delete("/api/session", headers=hdr(sid))


def test_a_sound_of_another_session_cannot_be_played(client, wav):
    a, b = make_session(client), make_session(client)
    sound = client.put("/api/sounds?name=beep.wav", content=wav, headers=hdr(a)).json()
    client.post("/api/deploy", json={"links": [{"meeting": "123456789"}]}, headers=hdr(b))
    r = client.post("/api/actions", json={"sound": sound["id"]}, headers=hdr(b))
    assert r.status_code == 404
    client.delete("/api/session", headers=hdr(a))
    client.delete("/api/session", headers=hdr(b))


def test_upload_limits(client, wav, monkeypatch):
    sid = make_session(client)
    assert client.put("/api/sounds?name=x.exe", content=wav, headers=hdr(sid)).status_code == 422
    assert client.put("/api/sounds?name=x.wav", content=b"garbage", headers=hdr(sid)).status_code == 422
    monkeypatch.setattr(S, "MAX_UPLOAD_BYTES", 100)
    assert client.put("/api/sounds?name=x.wav", content=wav, headers=hdr(sid)).status_code == 413
    assert client.put("/api/sounds?name=x.wav", content=wav).status_code == 403  # sans en-tête CSRF
    client.delete("/api/session", headers=hdr(sid))


def test_youtube_rejects_foreign_urls(client):
    sid = make_session(client)
    r = client.post("/api/youtube", json={"url": "http://169.254.169.254/latest"}, headers=hdr(sid))
    assert r.status_code == 422 and "YouTube" in r.json()["detail"]
    client.delete("/api/session", headers=hdr(sid))


def test_access_code(client, monkeypatch):
    monkeypatch.setattr(gui_api, "ACCESS_CODE", "sesame")
    assert client.get("/api/config").json()["access_code_required"] is True
    assert client.post("/api/session", json={}, headers=H).status_code == 401
    assert client.post("/api/session", json={"code": "faux"}, headers=H).status_code == 401
    sid = make_session(client, code="sesame")
    client.delete("/api/session", headers=hdr(sid))


def test_session_limit_returns_503(client, monkeypatch):
    monkeypatch.setattr(S, "MAX_SESSIONS", 1)
    sid = make_session(client)
    assert client.post("/api/session", json={}, headers=H).status_code == 503
    client.delete("/api/session", headers=hdr(sid))


def test_csrf_and_host_guard_apply_to_gui_api(client):
    assert client.post("/api/session", json={}).status_code == 403
    assert client.get("/api/config", headers={"Host": "evil.com"}).status_code == 403
    assert client.get("/api/config").status_code == 200


def test_admin_api_does_not_expose_other_users_to_broadcast(client):
    sid = make_session(client)
    client.post("/api/deploy", json={"links": [{"meeting": "123456789"}]}, headers=hdr(sid))
    assert client.delete("/bots?finished=true", headers=H).json() == {"removed": 0}
    assert len(client.get("/api/state", headers=hdr(sid)).json()["bots"]) == 1
    client.delete("/api/session", headers=hdr(sid))
