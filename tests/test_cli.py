import pytest

from zoombots import cli


def test_resolve_prefix(monkeypatch):
    monkeypatch.setattr(cli, "call", lambda *a, **k: [{"id": "abc123"}, {"id": "abd456"}])
    assert cli.resolve("abc") == "abc123"
    for bad in ("ab", "zzz", ""):
        with pytest.raises(cli.CliError):
            cli.resolve(bad)


def test_play_broadcast_quotes_sound_and_reports_failures(monkeypatch, capsys):
    calls = []

    def fake(m, p, b=None, raw=False):
        calls.append((m, p, b))
        return {"played": 1, "failed": [{"bot": "x", "error": "micro"}]}

    monkeypatch.setattr(cli, "call", fake)
    assert cli.main(["play", "a b.mp3", "-v", "0.5"]) == 1
    assert calls == [("POST", "/broadcast/sounds/a%20b.mp3", {"volume": 0.5})]
    assert "micro" in capsys.readouterr().err


def test_rm_requires_target():
    assert cli.main(["rm"]) == 2


def test_rm_continues_after_failure(monkeypatch, capsys):
    def fake(m, p, b=None, raw=False):
        if m == "GET":
            return [{"id": "a1"}, {"id": "b2"}]
        if p.endswith("a1"):
            raise cli.CliError("500: boom")
        return {}

    monkeypatch.setattr(cli, "call", fake)
    assert cli.main(["rm", "--all"]) == 1
    assert "supprimé : b2" in capsys.readouterr().out


def test_unreachable_server_is_clean_error(monkeypatch, capsys):
    monkeypatch.setattr(cli, "BASE", "http://127.0.0.1:1")
    assert cli.main(["list"]) == 1
    assert "injoignable" in capsys.readouterr().err


def test_proxy_env_is_ignored_for_api(monkeypatch):
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:9")
    monkeypatch.setattr(cli, "BASE", "http://127.0.0.1:1")
    with pytest.raises(cli.CliError) as exc:
        cli.call("GET", "/bots")
    assert "injoignable" in str(exc.value)


def test_validation_detail_list_is_readable():
    assert cli._detail(b'{"detail": [{"msg": "trop long"}, {"msg": "autre"}]}', "x") == "trop long; autre"
    assert cli._detail(b"<html>", "fallback") == "fallback"
    assert cli._detail(b'["x"]', "fallback") == "fallback"


def test_wait_exit_code(monkeypatch):
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    monkeypatch.setattr(cli, "call", lambda *a, **k: [{"id": "a", "name": "n", "status": "denied", "error": None}])
    assert cli.wait_for(5, ["a"]) == 1
    monkeypatch.setattr(cli, "call", lambda *a, **k: [])
    with pytest.raises(cli.CliError):
        cli.wait_for(5, ["a"])


def test_pid_ignores_recycled_pid(tmp_path, monkeypatch):
    pidfile = tmp_path / "server.pid"
    pidfile.write_text("1")  # PID 1 existe mais n'est pas zoombots
    monkeypatch.setattr(cli, "PID_FILE", pidfile)
    assert cli._pid() is None


def test_public_bind_requires_token(monkeypatch):
    monkeypatch.setattr(cli, "HOST", "0.0.0.0")
    monkeypatch.setattr(cli, "TOKEN", None)
    with pytest.raises(cli.CliError):
        cli._require_safe_bind()
