"""Réseau : sortie des bots via proxy (masquer l'IP du serveur), durcissement WebRTC, IP des clients du GUI."""

import ipaddress
import itertools
import os
import re
import secrets
from urllib.parse import unquote, urlparse

_counter = itertools.count()
_SCHEMES = {"http", "https", "socks5", "socks5h"}
# Variables qui ne doivent jamais être héritées par Chromium, ffmpeg, yt-dlp… (secrets de l'application)
SECRET_ENV = ("ZOOMBOTS_TOKEN", "ZOOMBOTS_ACCESS_CODE", "ZOOMBOTS_PROXY")


def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Environnement pour un sous-processus : celui du serveur, sans ses secrets."""
    env = {k: v for k, v in os.environ.items() if k not in SECRET_ENV}
    env.update(extra or {})
    return env


def secret_equal(a: str | None, b: str | None) -> bool:
    """Comparaison à temps constant, sûre pour les valeurs non ASCII (compare_digest lève sinon)."""
    return bool(a) and bool(b) and secrets.compare_digest(a.encode(), b.encode())


# -- proxy de sortie --------------------------------------------------------

def parse_proxy(url: str) -> dict:
    """Convertit `scheme://[user:pass@]hôte:port` au format Playwright."""
    parsed = urlparse(url.strip())
    try:
        port = parsed.port
    except ValueError:
        port = None
    if parsed.scheme not in _SCHEMES or not parsed.hostname or not port:
        raise ValueError(f"proxy invalide : {url!r} (attendu : http://hôte:port, https://… ou socks5://…)")
    scheme = "socks5" if parsed.scheme.startswith("socks5") else parsed.scheme  # Chromium résout alors les noms via le proxy
    host = f"[{parsed.hostname}]" if ":" in parsed.hostname else parsed.hostname  # IPv6
    proxy = {"server": f"{scheme}://{host}:{port}"}
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
    """Proxy du prochain lancement de navigateur, en rotation : chaque bot (et chaque reconnexion) prend le
    suivant, donc sort par une IP différente si plusieurs proxies sont donnés.

    Avec ZOOMBOTS_REQUIRE_PROXY=1, l'absence de proxy est une erreur : aucun bot ne sort avec l'IP du serveur."""
    pool = proxies()
    if not pool:
        if os.environ.get("ZOOMBOTS_REQUIRE_PROXY") == "1":
            raise RuntimeError("aucun proxy configuré (ZOOMBOTS_REQUIRE_PROXY=1) : le bot n'est pas lancé")
        return None
    return pool[next(_counter) % len(pool)]


def download_proxy_env() -> dict[str, str]:
    """Variables de proxy pour yt-dlp (proxy HTTP(S) du pool ; elles ne passent jamais par argv, donc pas par `ps`).

    Avec ZOOMBOTS_REQUIRE_PROXY=1, un téléchargement ne part pas en direct : sans proxy HTTP(S) utilisable, erreur."""
    urls = [p.strip() for p in os.environ.get("ZOOMBOTS_PROXY", "").split(",") if p.strip()]
    http = [u for u in urls if urlparse(u).scheme in ("http", "https")]
    if not http:
        if os.environ.get("ZOOMBOTS_REQUIRE_PROXY") == "1":
            raise RuntimeError("téléchargement refusé : proxy HTTP(S) requis (yt-dlp ne gère pas SOCKS ici)")
        return {}
    url = http[next(_counter) % len(http)]
    return {"HTTPS_PROXY": url, "HTTP_PROXY": url, "https_proxy": url, "http_proxy": url}


def privacy_args(proxied: bool) -> list[str]:
    """Options Chromium limitant les fuites d'adresse IP par WebRTC et QUIC."""
    if proxied:
        # pas de média UDP hors proxy : repli sur TCP via le proxy
        return ["--force-webrtc-ip-handling-policy=disable_non_proxied_udp", "--disable-quic"]
    return ["--force-webrtc-ip-handling-policy=default_public_interface_only"]


# -- nettoyage des messages -------------------------------------------------

_IPV4 = re.compile(r"(?<![\w.])\d{1,3}(?:\.\d{1,3}){3}(?![\w.])")
_IPV6_CANDIDATE = re.compile(r"(?<![\w:])\[?[0-9a-fA-F:.]*:[0-9a-fA-F:.]*:[0-9a-fA-F:.%\w]*\]?(?![\w:])")
_MIN_SECRET = 3  # un identifiant plus court détruirait des mots ; les plus longs ne sont masqués que comme mots entiers


def sensitive_strings() -> list[str]:
    """Hôtes, `hôte:port` et identifiants de proxy, à ne jamais montrer à un utilisateur du GUI."""
    found = []
    for raw in os.environ.get("ZOOMBOTS_PROXY", "").split(","):
        parsed = urlparse(raw.strip())
        try:
            port = parsed.port
        except ValueError:
            port = None
        host = parsed.hostname
        if host and port:
            found += [f"{host}:{port}", f"[{host}]:{port}"]
        for value in (host, parsed.username, parsed.password):
            if value:
                found.append(unquote(value))
    return sorted({s for s in found if len(s) >= _MIN_SECRET}, key=len, reverse=True)


def _scrub_ipv6(match: re.Match) -> str:
    token = match.group(0).strip("[]")
    token = token.split("%", 1)[0]
    try:
        ipaddress.IPv6Address(token)
    except ValueError:
        return match.group(0)  # une heure (12:34:56) ou un autre texte : pas une adresse
    return "[ip]"


_IPV6_BRACKETED = re.compile(r"\[([0-9a-fA-F:.%\w]+)\](?::\d{1,5})?")


def _scrub_bracketed(match: re.Match) -> str:
    try:
        ipaddress.IPv6Address(match.group(1).split("%", 1)[0])
    except ValueError:
        return match.group(0)
    return "[ip]"


def scrub(text: str) -> str:
    """Retire d'un message d'erreur les adresses IP et les informations de proxy."""
    for secret in sensitive_strings():
        pattern = rf"(?<!\w){re.escape(secret)}(?!\w)"  # mot entier : « bob » masqué, « Bobine » intact
        text = re.sub(pattern, "[proxy]", text, flags=re.I)
    text = _IPV6_BRACKETED.sub(_scrub_bracketed, text)  # [2001:db8::1]:3128
    return _IPV6_CANDIDATE.sub(_scrub_ipv6, _IPV4.sub("[ip]", text))


# -- adresse des clients ----------------------------------------------------

def trusted_proxies() -> list[str]:
    raw = os.environ.get("ZOOMBOTS_TRUSTED_PROXIES", "127.0.0.1,::1")
    return [p.strip() for p in raw.split(",") if p.strip()]


def _parse(value: str | None):
    """Adresse IP normalisée (IPv4-mappé ramené en IPv4), ou None."""
    try:
        ip = ipaddress.ip_address((value or "").strip())
    except ValueError:
        return None
    return ip.ipv4_mapped if getattr(ip, "ipv4_mapped", None) else ip


def _is_trusted(value: str) -> bool:
    ip = _parse(value)
    for entry in trusted_proxies():  # adresses, réseaux CIDR (172.16.0.0/12), ou noms littéraux
        if entry == value:
            return True
        if ip is not None:
            try:
                if ip in ipaddress.ip_network(entry, strict=False):
                    return True
            except ValueError:
                continue
    return False


def client_ip(peer: str | None, forwarded_for: str | None) -> str:
    """Adresse du client. `X-Forwarded-For` n'est cru que si la connexion vient d'un proxy de confiance
    (Caddy en local) ; on prend alors la première adresse non fiable en partant de la droite.
    `forwarded_for` : toutes les lignes de l'en-tête, jointes par des virgules."""
    peer = peer or "unknown"
    parsed_peer = _parse(peer)
    normalized_peer = str(parsed_peer) if parsed_peer else peer
    if not forwarded_for or not _is_trusted(peer):
        return normalized_peer
    hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
    for hop in reversed(hops):
        ip = _parse(hop)
        if ip is None:
            return "unknown"
        if not _is_trusted(str(ip)):
            return str(ip)
    first = _parse(hops[0]) if hops else None
    return str(first) if first else normalized_peer


def is_loopback(ip: str) -> bool:
    parsed = _parse(ip)
    return bool(parsed and parsed.is_loopback)


def ip_bucket(ip: str) -> str:
    """Clé des plafonds par IP : une adresse IPv4, ou le /64 d'une adresse IPv6 (un client IPv6 en reçoit 2^64)."""
    parsed = _parse(ip)
    if parsed is None:
        return ip
    if parsed.version == 6:
        return f"{ipaddress.ip_network(f'{parsed}/64', strict=False).network_address}/64"
    return str(parsed)
