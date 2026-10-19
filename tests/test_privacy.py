"""Masquage de l'IP du serveur : proxy de sortie, durcissement WebRTC, nettoyage des messages, IP des clients."""

import asyncio

import pytest

from zoombots import net
from zoombots.bot import Bot
from zoombots.meeting import Meeting


def test_parse_proxy_formats():
    assert net.parse_proxy("http://proxy.example:8080") == {"server": "http://proxy.example:8080"}
    assert net.parse_proxy("http://us%40er:p%3Ass@proxy.example:8080") == {
        "server": "http://proxy.example:8080", "username": "us@er", "password": "p:ss"}
    assert net.parse_proxy("socks5h://127.0.0.1:1080") == {"server": "socks5://127.0.0.1:1080"}  # DNS résolu par le proxy


@pytest.mark.parametrize("bad", ["", "proxy:8080", "ftp://x:1", "http://x", "socks5://u:p@x:1080"])
def test_parse_proxy_rejects(bad):
    with pytest.raises(ValueError):
        net.parse_proxy(bad)


def test_proxies_rotate_per_bot(monkeypatch):
    monkeypatch.setenv("ZOOMBOTS_PROXY", "http://a.example:1, http://b.example:2")
    seen = [net.next_proxy()["server"] for _ in range(4)]
    assert set(seen) == {"http://a.example:1", "http://b.example:2"} and seen[0] != seen[1] and seen[0] == seen[2]


def test_require_proxy_fails_closed(monkeypatch):
    monkeypatch.delenv("ZOOMBOTS_PROXY", raising=False)
    assert net.next_proxy() is None
    monkeypatch.setenv("ZOOMBOTS_REQUIRE_PROXY", "1")
    with pytest.raises(RuntimeError, match="proxy"):
        net.next_proxy()


def test_privacy_args():
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in net.privacy_args(True)
    assert "--disable-quic" in net.privacy_args(True)
    assert any("default_public_interface_only" in a for a in net.privacy_args(False))


def test_scrub_removes_ips_and_proxy_details(monkeypatch):
    monkeypatch.setenv("ZOOMBOTS_PROXY", "http://bob:s3cret@proxy.example.net:8080")
    text = ("net::ERR_PROXY_CONNECTION_FAILED at proxy.example.net bob s3cret ; "
            "connect 203.0.113.7:443 ; [2001:db8:85a3:0:0:8a2e:370:7334] ; 12:34:56 ok")
    out = net.scrub(text)
    for leaked in ("proxy.example.net", "bob", "s3cret", "203.0.113.7", "2001:db8"):
        assert leaked not in out
    assert "12:34:56" in out  # une heure n'est pas une adresse


def test_bot_errors_never_show_server_ip(monkeypatch):
    bot = Bot(Meeting("123456789"), "t")
    bot._set_status("error", "Timeout connecting to 198.51.100.23:443 from 192.0.2.1")
    assert "198.51" not in bot.error and "192.0.2.1" not in bot.error
    assert "198.51" not in str(list(bot.events))


class FakeChromium:
    def __init__(self):
        self.kwargs = None

    async def launch(self, **kwargs):
        self.kwargs = kwargs
        raise RuntimeError("stop")  # on ne vérifie que les options de lancement


class FakePW:
    def __init__(self):
        self.chromium = FakeChromium()


async def test_launch_uses_proxy_and_webrtc_hardening(monkeypatch):
    async def no_audio(*a):
        return None

    monkeypatch.setenv("ZOOMBOTS_PROXY", "http://u:p@proxy.example:8080")
    bot = Bot(Meeting("123456789"), "t")
    monkeypatch.setattr(bot.mic, "create", no_audio)
    pw = FakePW()
    with pytest.raises(RuntimeError):
        await bot._launch(pw)
    kw = pw.chromium.kwargs
    assert kw["proxy"] == {"server": "http://proxy.example:8080", "username": "u", "password": "p"}
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in kw["args"] and "--disable-quic" in kw["args"]


async def test_launch_without_required_proxy_starts_nothing(monkeypatch):
    created = []

    async def create():
        created.append(1)

    monkeypatch.delenv("ZOOMBOTS_PROXY", raising=False)
    monkeypatch.setenv("ZOOMBOTS_REQUIRE_PROXY", "1")
    bot = Bot(Meeting("123456789"), "t")
    monkeypatch.setattr(bot.mic, "create", create)
    pw = FakePW()
    with pytest.raises(RuntimeError, match="proxy"):
        await bot._launch(pw)
    assert pw.chromium.kwargs is None and created == []  # ni navigateur ni micro lancés


# -- IP des clients ---------------------------------------------------------

def test_forwarded_for_trusted_only_from_local_proxy():
    assert net.client_ip("127.0.0.1", "203.0.113.5") == "203.0.113.5"
    assert net.client_ip("203.0.113.9", "1.2.3.4") == "203.0.113.9"  # pas un proxy de confiance : en-tête ignoré
    assert net.client_ip("127.0.0.1", "6.6.6.6, 203.0.113.5") == "203.0.113.5"  # falsifié à gauche : on prend la droite
    assert net.client_ip("127.0.0.1", "203.0.113.5, 127.0.0.1") == "203.0.113.5"
    assert net.client_ip("127.0.0.1", None) == "127.0.0.1"
    assert net.client_ip("127.0.0.1", "pas une ip") == "unknown"
    assert net.is_loopback("127.0.0.1") and net.is_loopback("::1") and not net.is_loopback("203.0.113.5")
