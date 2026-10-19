import pytest

from zoombots import cli


def test_resolve_prefix(monkeypatch):
    monkeypatch.setattr(cli, "call", lambda *a, **k: [{"id": "abc123"}, {"id": "abd456"}])
    assert cli.resolve("abc") == "abc123"
    with pytest.raises(cli.CliError):
        cli.resolve("ab")  # ambigu
    with pytest.raises(cli.CliError):
        cli.resolve("zzz")


def test_play_broadcast_quotes_sound(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr(cli, "call", lambda m, p, b=None, raw=False: calls.append((m, p, b)) or {"bots": 2})
    assert cli.main(["play", "a b.mp3", "-v", "0.5"]) == 0
    assert calls == [("POST", "/broadcast/sounds/a%20b.mp3", {"volume": 0.5})]


def test_rm_requires_target(capsys):
    assert cli.main(["rm"]) == 2


def test_unreachable_server_is_clean_error(monkeypatch, capsys):
    monkeypatch.setattr(cli, "BASE", "http://127.0.0.1:1")
    assert cli.main(["list"]) == 1
    assert "injoignable" in capsys.readouterr().err
