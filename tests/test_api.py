from fastapi.testclient import TestClient

from zoombots import api, state


def test_stats_and_validation(monkeypatch):
    monkeypatch.setattr(api.manager, "start", _noop)
    monkeypatch.setattr(api.manager, "shutdown", _noop)
    with TestClient(api.app) as client:
        assert client.get("/bots").json() == []
        s = client.get("/stats").json()
        assert s["system"]["mem_total_mb"] > 0 and s["estimated_capacity"] is None
        assert client.post("/bots", json={"meeting": "nope"}).status_code == 422
        assert client.get("/bots/zzz/events").status_code == 404


async def _noop():
    pass
