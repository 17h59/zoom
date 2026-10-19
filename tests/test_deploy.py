import json

import pytest

from zoombots import api, cli, deploy, gui_api
from zoombots import sessions as S


# -- configuration du serveur ----------------------------------------------

def test_update_config_generates_secrets_once():
    cfg = deploy.update_config({})
    assert len(cfg["code"]) >= 8 and len(cfg["token"]) >= 24 and cfg["port"] == 8000
    again = deploy.update_config(cfg, domain="Bots.Example.FR")
    assert again["code"] == cfg["code"] and again["token"] == cfg["token"] and again["domain"] == "bots.example.fr"
    assert "domain" not in deploy.update_config(again, no_domain=True)


@pytest.mark.parametrize("value,expected", [
    ("bots.example.fr", "bots.example.fr"), ("https://Bots.Example.fr/", "bots.example.fr"), ("exemple.com", "exemple.com")])
def test_domain_accepted(value, expected):
    assert deploy.validate_domain(value) == expected


@pytest.mark.parametrize("bad", ["", "localhost", "pas un domaine", "-x.com", "a..com", "http://", "exemple", "1.2.3.4", "x.com/chemin"])
def test_domain_rejected(bad):
    with pytest.raises(deploy.DeployError):
        deploy.validate_domain(bad)


def test_invalid_proxy_refused_at_setup():
    with pytest.raises(deploy.DeployError):
        deploy.update_config({}, proxy="pas-un-proxy")
    with pytest.raises(deploy.DeployError, match="SOCKS5"):
        deploy.update_config({}, proxy="socks5://user:pw@host:1080")


def test_config_file_is_private(tmp_path):
    path = tmp_path / "sub" / "server.json"
    deploy.save_config({"code": "x"}, path)
    assert oct(path.stat().st_mode & 0o777) == "0o600" and deploy.load_config(path) == {"code": "x"}
    path.write_text("n'importe quoi")
    assert deploy.load_config(path) == {}


def test_server_env_with_domain_listens_on_loopback_only():
    env = deploy.server_env(deploy.update_config({}, domain="bots.example.fr", proxy="http://p.example:3128"))
    assert env["ZOOMBOTS_HOST"] == "127.0.0.1" and env["ZOOMBOTS_ALLOWED_HOSTS"] == "bots.example.fr"
    assert env["ZOOMBOTS_PUBLIC_ONLY"] == "1" and env["ZOOMBOTS_MODE"] == "server"
    assert env["ZOOMBOTS_PROXY"] == "http://p.example:3128" and env["ZOOMBOTS_REQUIRE_PROXY"] == "1"


def test_server_env_without_domain_works_by_ip():
    env = deploy.server_env(deploy.update_config({}))
    assert env["ZOOMBOTS_HOST"] == "0.0.0.0" and env["ZOOMBOTS_ALLOWED_HOSTS"] == "*"
    assert env["ZOOMBOTS_TOKEN"] and env["ZOOMBOTS_ACCESS_CODE"] and "ZOOMBOTS_PROXY" not in env


def test_local_env():
    env = deploy.local_env(8123, None)
    assert env["ZOOMBOTS_MODE"] == "local" and env["ZOOMBOTS_HOST"] == "127.0.0.1" and env["ZOOMBOTS_PORT"] == "8123"
    assert "ZOOMBOTS_ACCESS_CODE" not in env and "ZOOMBOTS_PROXY" not in env
    assert deploy.local_env(8000, "http://p:1")["ZOOMBOTS_REQUIRE_PROXY"] == "1"


def test_caddyfile():
    text = deploy.caddyfile({"domain": "bots.example.fr", "port": 8123, "email": "moi@example.fr"})
    assert "bots.example.fr {" in text and "reverse_proxy 127.0.0.1:8123" in text
    assert "admin off" in text and "email moi@example.fr" in text and "-Server" in text and "max_size 30MB" in text


# -- DNS et IP ---------------------------------------------------------------

def test_dns_report_and_help(monkeypatch):
    monkeypatch.setattr(deploy, "public_ip", lambda proxy=None: "203.0.113.10")
    monkeypatch.setattr(deploy, "resolve", lambda d: [])
    report = deploy.dns_report("bots.example.fr")
    assert not report["ok"]
    text = deploy.dns_help(report)
    assert "Type : A" in text and "203.0.113.10" in text and "bots" in text and "ne résout encore" in text
    monkeypatch.setattr(deploy, "resolve", lambda d: ["203.0.113.10"])
    assert deploy.dns_report("bots.example.fr")["ok"]
    assert "pointe déjà vers ce serveur" in deploy.dns_help(deploy.dns_report("bots.example.fr"))
    monkeypatch.setattr(deploy, "resolve", lambda d: ["198.51.100.1"])
    assert "pas vers ce serveur" in deploy.dns_help(deploy.dns_report("bots.example.fr"))
    assert "@" in deploy.dns_help({"domain": "exemple.fr", "server_ip": None, "resolved": [], "ok": False})


def test_check_egress_masks_credentials_and_compares(monkeypatch):
    ips = {None: "203.0.113.10", "http://u:p@a.example:1": "198.51.100.7", "http://b.example:2": "203.0.113.10"}
    monkeypatch.setattr(deploy, "public_ip", lambda proxy=None: ips.get(proxy))
    report = deploy.check_egress(["http://u:p@a.example:1", "http://b.example:2"])
    assert report["direct"] == "203.0.113.10"
    assert report["proxies"][0] == {"proxy": "http://***@a.example:1", "ip": "198.51.100.7", "masked": True}
    assert report["proxies"][1]["masked"] is False  # même IP que la machine : pas masquée


def test_curl_rejects_non_ip_output(monkeypatch):
    class R:
        stdout = "<html>erreur</html>"

    monkeypatch.setattr(deploy.shutil, "which", lambda n: "/usr/bin/curl")
    monkeypatch.setattr(deploy.subprocess, "run", lambda *a, **k: R())
    assert deploy._curl("https://x") is None


# -- CLI ---------------------------------------------------------------------

def test_cli_check_ip(monkeypatch, capsys):
    monkeypatch.setattr(deploy, "public_ip", lambda proxy=None: "198.51.100.7" if proxy else "203.0.113.10")
    assert cli.main(["check-ip", "--proxy", "http://p.example:1"]) == 0
    out = capsys.readouterr().out
    assert "203.0.113.10" in out and "198.51.100.7" in out and "masquée" in out
    monkeypatch.setattr(deploy, "public_ip", lambda proxy=None: "203.0.113.10")
    assert cli.main(["check-ip", "--proxy", "http://p.example:1"]) == 1
    monkeypatch.delenv("ZOOMBOTS_PROXY", raising=False)
    assert cli.main(["check-ip"]) == 1 and "Aucun proxy" in capsys.readouterr().out


def test_cli_gui_starts_local_server_and_opens_browser(monkeypatch, capsys):
    started, opened = {}, []
    monkeypatch.setattr(cli, "_start_server", lambda host, port, env, headed=False: started.update(host=host, port=port, env=env) or 4242)
    monkeypatch.setattr(cli, "_open_browser", opened.append)
    monkeypatch.setattr(cli, "_pid", lambda: None)
    assert cli.main(["gui", "--port", "8123", "--proxy", "http://p.example:1"]) == 0
    assert started["host"] == "127.0.0.1" and started["port"] == 8123
    assert started["env"]["ZOOMBOTS_MODE"] == "local" and started["env"]["ZOOMBOTS_PROXY"] == "http://p.example:1"
    assert opened == ["http://127.0.0.1:8123/"]
    assert cli.main(["gui", "--no-open"]) == 0 and len(opened) == 1


def test_cli_gui_reuses_running_local_server(monkeypatch, capsys, tmp_path):
    cli.RUN_FILE.write_text(json.dumps({"mode": "local", "host": "127.0.0.1", "port": 8000}))
    monkeypatch.setattr(cli, "_pid", lambda: 99)
    monkeypatch.setattr(cli, "_start_server", lambda *a, **k: pytest.fail("ne doit pas relancer"))
    opened = []
    monkeypatch.setattr(cli, "_open_browser", opened.append)
    assert cli.main(["gui"]) == 0 and opened == ["http://127.0.0.1:8000/"]
    assert "tourne déjà" in capsys.readouterr().out


def test_cli_server_start_with_domain(monkeypatch, capsys):
    calls = {}
    monkeypatch.setattr(cli, "_start_server", lambda host, port, env, headed=False: calls.update(host=host, port=port, env=env) or 7)
    monkeypatch.setattr(cli, "_start_caddy", lambda cfg: calls.update(caddy=cfg["domain"]))
    assert cli.main(["server", "start", "--domain", "bots.example.fr", "--code", "sesame", "--proxy", "http://p.example:1"]) == 0
    assert calls["host"] == "127.0.0.1" and calls["caddy"] == "bots.example.fr"
    assert calls["env"]["ZOOMBOTS_ACCESS_CODE"] == "sesame" and calls["env"]["ZOOMBOTS_PUBLIC_ONLY"] == "1"
    out = capsys.readouterr().out
    assert "https://bots.example.fr/" in out and "sesame" in out
    saved = deploy.load_config()
    assert saved["domain"] == "bots.example.fr" and saved["token"]
    # relancer garde le même code et le même jeton
    token = saved["token"]
    assert cli.main(["server", "start"]) == 0 and deploy.load_config()["token"] == token


def test_cli_server_start_without_domain_and_warns_about_ip(monkeypatch, capsys):
    calls = {}
    monkeypatch.setattr(cli, "_start_server", lambda host, port, env, headed=False: calls.update(host=host, env=env) or 7)
    monkeypatch.setattr(deploy, "public_ip", lambda proxy=None: "203.0.113.10")
    assert cli.main(["server", "start"]) == 0
    out = capsys.readouterr().out
    assert calls["host"] == "0.0.0.0" and "http://203.0.113.10:8000/" in out and "Aucun proxy" in out and "--domain" in out


def test_cli_server_start_rejects_bad_domain(capsys):
    assert cli.main(["server", "start", "--domain", "pas un domaine"]) == 1
    assert "invalide" in capsys.readouterr().err


def test_cli_server_dns(monkeypatch, capsys):
    monkeypatch.setattr(deploy, "public_ip", lambda proxy=None: "203.0.113.10")
    monkeypatch.setattr(deploy, "resolve", lambda d: [])
    assert cli.main(["server", "dns", "--domain", "bots.example.fr"]) == 1
    assert "203.0.113.10" in capsys.readouterr().out
    monkeypatch.setattr(deploy, "resolve", lambda d: ["203.0.113.10"])
    assert cli.main(["server", "dns", "--domain", "bots.example.fr"]) == 0
    assert cli.main(["server", "dns"]) == 1  # pas de domaine configuré


def test_cli_adopts_token_and_port_of_running_server(monkeypatch):
    deploy.save_config(deploy.update_config({}, domain="bots.example.fr", port=8123))
    cli.RUN_FILE.write_text(json.dumps({"mode": "server", "host": "127.0.0.1", "port": 8123}))
    monkeypatch.delenv("ZOOMBOTS_TOKEN", raising=False)
    monkeypatch.delenv("ZOOMBOTS_URL", raising=False)
    monkeypatch.setattr(cli, "TOKEN", None)
    seen = {}
    monkeypatch.setattr(cli, "call", lambda *a, **k: seen.update(base=cli.BASE, token=cli.TOKEN) or [])
    cli.main(["list"])
    assert seen["base"] == "http://127.0.0.1:8123" and seen["token"] == deploy.load_config()["token"]


# -- API : accès public, administration réservée à la machine ------------------

@pytest.fixture
def client(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    async def noop(*a, **k):
        return 0

    for target, name in ((api.manager, "start"), (api.manager, "shutdown"), (api.sessions, "start"), (api.sessions, "stop")):
        monkeypatch.setattr(target, name, noop)
    monkeypatch.setattr(api, "TOKEN", None)
    monkeypatch.setattr(gui_api, "ACCESS_CODE", None)
    monkeypatch.setattr(S, "IP_SESSIONS_PER_MINUTE", 0)
    monkeypatch.setattr(S, "ROOT", tmp_path / "sessions")
    with TestClient(api.app, base_url="http://localhost") as c:
        yield c
    api.sessions.sessions.clear()


H = {"X-Zoombots": "1"}


def test_public_only_hides_admin_from_the_outside(client, monkeypatch):
    monkeypatch.setattr(api, "PUBLIC_ONLY", True)
    monkeypatch.setattr(api, "TOKEN", "adm")
    outside = {"X-Forwarded-For": "203.0.113.50"}
    # le proxy local (peer 127.0.0.1 / « testclient ») relaie un client extérieur
    monkeypatch.setattr(net_trusted(), "trusted_proxies", lambda: {"testclient"})
    bearer = {"Authorization": "Bearer adm"}
    assert client.get("/bots", headers={**outside, **bearer}).status_code == 404  # même avec le bon jeton
    assert client.get("/admin", headers=outside).status_code == 404
    assert client.get("/api/config", headers=outside).status_code == 200  # le GUI reste public
    assert client.get("/", headers=outside).status_code == 200
    assert client.get("/bots", headers={"X-Forwarded-For": "127.0.0.1", **bearer}).status_code == 200  # depuis la machine


def net_trusted():
    from zoombots import net

    return net


def test_any_host_accepted_when_wildcard(client, monkeypatch):
    assert client.get("/api/config", headers={"Host": "203.0.113.10:8000"}).status_code == 403
    monkeypatch.setattr(api, "ALLOWED_HOSTS", {"*"})
    assert client.get("/api/config", headers={"Host": "203.0.113.10:8000"}).status_code == 200


def test_sessions_are_counted_per_real_client_ip(client, monkeypatch):
    monkeypatch.setattr(net_trusted(), "trusted_proxies", lambda: {"testclient"})
    monkeypatch.setattr(S, "IP_MAX_SESSIONS", 1)
    first = {**H, "X-Forwarded-For": "203.0.113.1"}
    assert client.post("/api/session", json={}, headers=first).status_code == 200
    assert client.post("/api/session", json={}, headers=first).status_code == 429
    assert client.post("/api/session", json={}, headers={**H, "X-Forwarded-For": "203.0.113.2"}).status_code == 200


def test_forwarded_for_is_ignored_from_untrusted_peers(client, monkeypatch):
    monkeypatch.setattr(S, "IP_MAX_SESSIONS", 1)
    assert client.post("/api/session", json={}, headers={**H, "X-Forwarded-For": "203.0.113.1"}).status_code == 200
    # impossible de se faire passer pour une autre adresse en changeant l'en-tête
    assert client.post("/api/session", json={}, headers={**H, "X-Forwarded-For": "203.0.113.2"}).status_code == 429


def test_no_server_banner(client):
    assert "server" not in {k.lower() for k in client.get("/api/config").headers}
