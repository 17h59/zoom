"""Déploiement : configuration du mode serveur public (nom de domaine, Caddy, secrets) et vérification des IP.

Tout est volontairement petit et testable sans réseau : les appels à `curl` et au DNS passent par `_curl` et
`resolve`, que les tests remplacent."""

import ipaddress
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
from pathlib import Path

from . import net

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "zoombots"
CONFIG_FILE = CONFIG_DIR / "server.json"
DOMAIN_RE = re.compile(r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
IP_SERVICES = ("https://api.ipify.org", "https://ifconfig.me/ip", "https://icanhazip.com")


class DeployError(Exception):
    pass


# -- configuration ----------------------------------------------------------

def load_config(path: Path | None = None) -> dict:
    path = path or CONFIG_FILE
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save_config(cfg: dict, path: Path | None = None) -> None:
    """Écriture atomique en 0600 : le fichier contient le code d'accès, le jeton et les identifiants du proxy."""
    path = path or CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(cfg, indent=2))
    tmp.replace(path)


def validate_domain(value: str) -> str:
    domain = value.strip().lower().removeprefix("https://").removeprefix("http://").rstrip("/")
    if not DOMAIN_RE.match(domain):
        raise DeployError(f"nom de domaine invalide : {value!r} (exemple : bots.monsite.fr)")
    return domain


def update_config(cfg: dict, *, domain: str | None = None, no_domain: bool = False, code: str | None = None,
                  proxy: str | None = None, port: int | None = None, email: str | None = None) -> dict:
    """Applique les options de la ligne de commande et génère les secrets manquants (code d'accès, jeton)."""
    cfg = dict(cfg)
    if no_domain:
        cfg.pop("domain", None)
    if domain:
        cfg["domain"] = validate_domain(domain)
    if proxy:
        for part in proxy.split(","):
            try:
                net.parse_proxy(part)
            except ValueError as exc:
                raise DeployError(str(exc)) from exc
        cfg["proxy"] = proxy
    if code:
        cfg["code"] = code
    if port:
        cfg["port"] = port
    if email:
        cfg["email"] = email
    cfg.setdefault("code", secrets.token_urlsafe(9))
    cfg.setdefault("token", secrets.token_urlsafe(24))
    cfg.setdefault("port", 8000)
    return cfg


def server_env(cfg: dict) -> dict[str, str]:
    """Variables d'environnement du serveur public.

    Avec un domaine, l'application n'écoute que sur 127.0.0.1 : Caddy est la seule porte d'entrée (HTTPS)."""
    domain = cfg.get("domain")
    env = {
        "ZOOMBOTS_MODE": "server",
        "ZOOMBOTS_PUBLIC_ONLY": "1",  # l'administration n'est joignable que depuis la machine
        "ZOOMBOTS_HOST": "127.0.0.1" if domain else "0.0.0.0",
        "ZOOMBOTS_PORT": str(cfg.get("port", 8000)),
        "ZOOMBOTS_ACCESS_CODE": cfg["code"],
        "ZOOMBOTS_TOKEN": cfg["token"],
        "ZOOMBOTS_ALLOWED_HOSTS": domain or "*",
    }
    if cfg.get("proxy"):
        env["ZOOMBOTS_PROXY"] = cfg["proxy"]
        env["ZOOMBOTS_REQUIRE_PROXY"] = "1"  # jamais de repli sur l'IP du serveur
    return env


def local_env(port: int, proxy: str | None) -> dict[str, str]:
    env = {"ZOOMBOTS_MODE": "local", "ZOOMBOTS_HOST": "127.0.0.1", "ZOOMBOTS_PORT": str(port),
           "ZOOMBOTS_SESSION_MAX_BOTS": os.environ.get("ZOOMBOTS_SESSION_MAX_BOTS", "20"),
           "ZOOMBOTS_MAX_SESSIONS": os.environ.get("ZOOMBOTS_MAX_SESSIONS", "10")}
    if proxy:
        env["ZOOMBOTS_PROXY"] = proxy
        env["ZOOMBOTS_REQUIRE_PROXY"] = "1"
    return env


def caddyfile(cfg: dict) -> str:
    """Caddyfile : HTTPS automatique (Let's Encrypt), taille des envois bornée, pas d'en-tête Server."""
    max_mb = int(os.environ.get("ZOOMBOTS_MAX_UPLOAD_MB", "25")) + 5
    email = f"\temail {cfg['email']}\n" if cfg.get("email") else ""
    return (
        "{\n\tadmin off\n" + email + "}\n\n"
        f"{cfg['domain']} {{\n"
        f"\trequest_body {{\n\t\tmax_size {max_mb}MB\n\t}}\n"
        "\theader {\n\t\t-Server\n\t\tStrict-Transport-Security \"max-age=31536000\"\n"
        "\t\tReferrer-Policy \"no-referrer\"\n\t}\n"
        f"\treverse_proxy 127.0.0.1:{cfg.get('port', 8000)}\n"
        "}\n"
    )


def find_caddy() -> str | None:
    return shutil.which("caddy") or next(
        (str(p) for p in (Path.home() / ".local" / "bin" / "caddy",) if p.exists()), None)


# -- adresses IP et DNS -----------------------------------------------------

def _curl(url: str, proxy: str | None = None) -> str | None:
    if not shutil.which("curl"):
        raise DeployError("curl est requis pour cette vérification")
    cmd = ["curl", "-s", "--max-time", "15", "-4"]
    if proxy:
        cmd += ["-x", proxy.replace("socks5://", "socks5h://", 1)]
    result = subprocess.run(cmd + [url], capture_output=True, text=True)
    out = result.stdout.strip()
    try:
        ipaddress.ip_address(out)
    except ValueError:
        return None
    return out


def public_ip(proxy: str | None = None) -> str | None:
    """Adresse IP vue de l'extérieur (directement, ou via un proxy)."""
    for service in IP_SERVICES:
        ip = _curl(service, proxy)
        if ip:
            return ip
    return None


def resolve(domain: str) -> list[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(domain, None, socket.AF_INET)})
    except OSError:
        return []


def dns_report(domain: str) -> dict:
    """Le domaine pointe-t-il vers ce serveur ? (enregistrement A à créer chez le registrar sinon)"""
    ip = public_ip()
    resolved = resolve(domain)
    return {"domain": domain, "server_ip": ip, "resolved": resolved, "ok": bool(ip and ip in resolved)}


def dns_help(report: dict) -> str:
    ip = report["server_ip"] or "<IP-publique-du-serveur>"
    lines = [f"Chez le registrar du domaine, crée cet enregistrement DNS :",
             f"    Type : A     Nom : {_record_name(report['domain'])}     Valeur : {ip}",
             "    (TTL : le plus court proposé ; la propagation prend de quelques minutes à quelques heures)"]
    if report["ok"]:
        lines.append(f"✔ {report['domain']} pointe déjà vers ce serveur ({ip}).")
    elif report["resolved"]:
        lines.append(f"✘ {report['domain']} pointe vers {', '.join(report['resolved'])}, pas vers ce serveur ({ip}).")
    else:
        lines.append(f"✘ {report['domain']} ne résout encore vers aucune adresse.")
    lines.append("Les ports 80 et 443 doivent être ouverts (pare-feu et box/hébergeur) pour le certificat HTTPS.")
    return "\n".join(lines)


def _record_name(domain: str) -> str:
    parts = domain.split(".")
    return "@  (domaine racine)" if len(parts) <= 2 else parts[0] + "  (sous-domaine)"


def check_egress(proxy_urls: list[str]) -> dict:
    """Compare l'IP directe du serveur à celle vue à travers chaque proxy configuré."""
    direct = public_ip()
    rows = []
    for url in proxy_urls:
        via = public_ip(url)
        rows.append({"proxy": _mask(url), "ip": via,
                     "masked": bool(via and direct and via != direct)})
    return {"direct": direct, "proxies": rows}


def _mask(url: str) -> str:
    """URL de proxy sans identifiants, pour l'affichage."""
    return re.sub(r"//[^@/]*@", "//***@", url)
