import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse


@dataclass
class Meeting:
    id: str
    passcode: str | None = None

    @property
    def join_url(self) -> str:
        url = f"https://app.zoom.us/wc/join/{self.id}"
        return f"{url}?pwd={self.passcode}" if self.passcode else url


def parse_meeting(value: str, passcode: str | None = None) -> Meeting:
    """Accepte un ID (avec ou sans espaces) ou un lien zoom.us/j/<id>?pwd=<pwd>."""
    value = value.strip()
    if "://" in value or "zoom" in value:
        parsed = urlparse(value if "://" in value else f"https://{value}")
        match = re.search(r"/(\d{9,11})", parsed.path)
        if not match:
            raise ValueError(f"ID de réunion introuvable dans {value!r}")
        pwd = parse_qs(parsed.query).get("pwd", [None])[0]
        return Meeting(match.group(1), passcode or pwd)
    digits = re.sub(r"[\s-]", "", value)
    if not re.fullmatch(r"\d{9,11}", digits):
        raise ValueError(f"ID de réunion invalide: {value!r}")
    return Meeting(digits, passcode)
