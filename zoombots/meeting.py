import os
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, quote, urlparse

DEFAULT_HOST = "app.zoom.us"

# Domaines Zoom acceptés dans un lien (zoom.us, zoomgov, Chine, sous-domaines régionaux ou vanity).
# L'hôte du lien sert à construire l'URL de connexion : la liste blanche évite qu'un lien
# arbitraire fasse naviguer le navigateur du bot vers un site tiers.
_BASE_SUFFIXES = ("zoom.us", "zoomgov.com", "zoom.com", "zoom.com.cn", "zoomgov.cn")
_ID_IN_PATH = re.compile(r"/(?:j|s|wc/join|wc)/(\d{9,11})(?!\d)")


def _suffixes() -> tuple[str, ...]:
    extra = tuple(s.strip().lower() for s in os.environ.get("ZOOMBOTS_EXTRA_DOMAINS", "").split(",") if s.strip())
    return _BASE_SUFFIXES + extra  # domaines d'entreprise : ZOOMBOTS_EXTRA_DOMAINS=meet.example.com


def normalize_host(host: str) -> str:
    """Valide un hôte Zoom et le ramène à l'hôte du client web."""
    host = host.lower().rstrip(".")
    if not any(host == s or host.endswith("." + s) for s in _suffixes()):
        raise ValueError(f"domaine non autorisé : {host!r} (voir ZOOMBOTS_EXTRA_DOMAINS)")
    if host in ("zoom.us", "www.zoom.us"):
        return DEFAULT_HOST
    if host in ("zoomgov.com", "www.zoomgov.com"):
        return "www.zoomgov.com"
    return host


@dataclass
class Meeting:
    id: str
    passcode: str | None = None
    host: str = DEFAULT_HOST

    @property
    def join_url(self) -> str:
        url = f"https://{self.host}/wc/join/{self.id}"
        return f"{url}?pwd={quote(self.passcode, safe='')}" if self.passcode else url


def parse_meeting(value: str, passcode: str | None = None) -> Meeting:
    """Accepte un ID (avec ou sans espaces) ou un lien (zoom.us, zoomgov.com, sous-domaines…)."""
    value = value.strip()
    if "://" in value or "zoom" in value.lower():
        parsed = urlparse(value if "://" in value else f"https://{value}")
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise ValueError(f"lien invalide : {value!r}")
        host = normalize_host(parsed.hostname)
        match = _ID_IN_PATH.search(parsed.path)
        if not match:
            raise ValueError(f"ID de réunion introuvable dans {value!r}")
        pwd = parse_qs(parsed.query).get("pwd", [None])[0]
        return Meeting(match.group(1), passcode or pwd, host)
    digits = re.sub(r"[\s-]", "", value)
    if not re.fullmatch(r"\d{9,11}", digits):
        raise ValueError(f"ID de réunion invalide: {value!r}")
    return Meeting(digits, passcode)
