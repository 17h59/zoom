"""Réseau : sortie des bots via proxy (masquer l'IP du serveur), durcissement WebRTC, IP des clients du GUI."""

import ipaddress
import itertools
import os
import re
from urllib.parse import unquote, urlparse

_counter = itertools.count()
_SCHEMES = {"http", "https", "socks5", "socks5h"}


def parse_proxy(url: str) -> dict:
    """Convertit `scheme://[user:pass@]hôte:port` au format Playwright."""
    parsed = urlparse(url.strip())
    if parsed.scheme not in _SCHEMES or not parsed.hostname or not parsed.port:
        raise ValueError(f"proxy invalide : {url!r} (attendu : http://hôte:port, https://… ou socks5://…)")
    scheme = "socks5" if parsed.scheme.startswith("socks5") else parsed.scheme  # Chromium résout alors les noms via le proxy
    proxy = {"server": f"{scheme}://{parsed.hostname}:{parsed.port}"}
    if parsed.username:
        if scheme == "socks5":
            raise ValueError("Chromium ne gère pas l'authentification SOCKS5 : utiliser un proxy http(s) avec "
                             "identifiants, ou un SOCKS5 sans mot de passe (ex. tunnel local `ssh -D`)")
        proxy["username"] = unquote(parsed.username)
        proxy["password"] = unquote(parsed.password or "")
    return proxy


def proxies() -> list[dict]:
    """Proxies configurés (ZOOMBOTS_PROXY, séparés par des virgules)."""
    raw = os.environ.get("ZOOMBOTS_PROXY", "")
    return [parse_proxy(p) for p in raw.split(",") if p.strip()]


def next_proxy() -> dict | None:
    """Proxy du prochain bot, en rotation : chaque bot sort par une IP différente si plusieurs proxies sont donnés.

    Avec ZOOMBOTS_REQUIRE_PROXY=1, l'absence de proxy est une erreur : aucun bot ne sort avec l'IP du serveur."""
    pool = proxies()
    if not pool:
        if os.environ.get("ZOOMBOTS_REQUIRE_PROXY") == "1":
            raise RuntimeError("aucun proxy configuré (ZOOMBOTS_REQUIRE_PROXY=1) : le bot n'est pas lancé")
        return None
    return pool[next(_counter) % len(pool)]


def privacy_args(proxied: bool) -> list[str]:
    """Options Chromium limitant les fuites d'adresse IP par WebRTC et QUIC."""
    if proxied:
        # pas de média UDP hors proxy : repli sur TCP via le proxy
        return ["--force-webrtc-ip-handling-policy=disable_non_proxied_udp", "--disable-quic"]
    return ["--force-webrtc-ip-handling-policy=default_public_interface_only"]


_IPV4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
_IPV6 = re.compile(r"\b(?:[0-9a-f]{1,4}:){3,7}[0-9a-f]{1,4}\b", re.I)


def sensitive_strings() -> list[str]:
    """Hôtes, ports et identifiants de proxy, à ne jamais montrer à un utilisateur du GUI."""
    found = []
    for raw in os.environ.get("ZOOMBOTS_PROXY", "").split(","):
        parsed = urlparse(raw.strip())
        for value in (parsed.hostname, parsed.username, parsed.password):
            if value:
                found.append(unquote(value))
    return found


def scrub(text: str) -> str:
    """Retire d'un message d'erreur les adresses IP et les informations de proxy."""
    for secret in sensitive_strings():
        text = text.replace(secret, "[proxy]")
    return _IPV6.sub("[ip]", _IPV4.sub("[ip]", text))


def trusted_proxies() -> set[str]:
    raw = os.environ.get("ZOOMBOTS_TRUSTED_PROXIES", "127.0.0.1,::1")
    return {p.strip() for p in raw.split(",") if p.strip()}


def client_ip(peer: str | None, forwarded_for: str | None) -> str:
    """Adresse du client. `X-Forwarded-For` n'est cru que si la connexion vient d'un proxy de confiance
    (Caddy en local) ; on prend alors la première adresse non fiable en partant de la droite."""
    peer = peer or "unknown"
    trusted = trusted_proxies()
    if peer not in trusted or not forwarded_for:
        return peer
    hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
    for hop in reversed(hops):
        if hop not in trusted:
            return normalize_ip(hop)
    return normalize_ip(hops[0]) if hops else peer


def normalize_ip(value: str) -> str:
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        return "unknown"


def is_loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        return False
