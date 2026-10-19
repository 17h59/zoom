import pytest
from fastapi.testclient import TestClient

from zoombots import api

H = {"X-Zoombots": "1"}


async def _noop():
    pass


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(api.manager, "start", _noop)
    monkeypatch.setattr(api.manager, "shutdown", _noop)
    monkeypatch.setattr(api, "TOKEN", None)
    with TestClient(api.app, base_url="http://localhost") as c:
        yield c


def test_stats_and_validation(client):
    assert client.get("/bots").json() == []
    s = client.get("/stats").json()
    assert s["system"]["mem_total_mb"] > 0 and s["estimated_capacity"] is None
    assert client.post("/bots", json={"meeting": "nope"}, headers=H).status_code == 422
    assert client.post("/bots", json={"meeting": "123456789", "name": "x" * 200}, headers=H).status_code == 422
    assert client.get("/bots/zzz/events").status_code == 404
    assert client.post("/broadcast/sounds/a%00b", headers=H).status_code == 404


def test_foreign_host_is_rejected_dns_rebinding(client):
    assert client.get("/bots", headers={"Host": "evil.com"}).status_code == 403
    assert client.get("/bots", headers={"Host": "localhost:8000"}).status_code == 200
    assert client.get("/bots", headers={"Host": "[::1]:8000"}).status_code == 200


def test_mutations_need_custom_header_csrf(client):
    assert client.post("/broadcast/sounds/x.wav").status_code == 403
    assert client.post("/bots/abc/stop", headers={"Sec-Fetch-Site": "cross-site", **H}).status_code == 403
    assert client.post("/bots/abc/stop", headers=H).status_code == 404  # passe la garde


def test_csp_on_responses(client):
    r = client.get("/")
    assert "script-src 'self'" in r.headers["content-security-policy"]
    assert "inline" not in r.text.split("<script")[1].split("</script>")[0]
    assert client.get("/static/app.js").status_code == 200


def test_token_required_when_configured(client, monkeypatch):
    monkeypatch.setattr(api, "TOKEN", "s3cret")
    assert client.get("/bots").status_code == 401
    assert client.get("/bots", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/bots", headers={"Authorization": "Bearer s3cret"}).status_code == 200
    assert client.get("/health").status_code == 200
    r = client.get("/?token=s3cret", follow_redirects=False)
    assert r.status_code == 303 and "httponly" in r.headers["set-cookie"].lower()
    assert client.get("/bots").status_code == 200  # cookie conservé


def test_purge_requires_flag(client):
    assert client.delete("/bots", headers=H).status_code == 422
    assert client.delete("/bots?finished=true", headers=H).json() == {"removed": 0}
