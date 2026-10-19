"""Persistance de la liste des bots (JSON) pour survivre à un redémarrage du serveur."""

import json
import os
import re
from pathlib import Path

from .log import DATA_DIR

STATE_FILE = Path(os.environ.get("ZOOMBOTS_STATE") or Path(DATA_DIR) / "state.json")
BOT_ID = re.compile(r"[0-9a-f]{8}")


def save(entries: list[dict], path: Path = STATE_FILE) -> None:
    """Écriture atomique, lisible par le seul propriétaire (le fichier contient les passcodes)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(entries, indent=2))
        fh.flush()
        os.fsync(fh.fileno())
    tmp.replace(path)


def load(path: Path = STATE_FILE) -> list[dict]:
    """Renvoie les entrées valides. Un fichier illisible est conservé en .bak au lieu d'être écrasé."""
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, list):
            raise ValueError("liste attendue")
    except FileNotFoundError:
        return []
    except (ValueError, OSError):
        try:
            path.replace(path.with_suffix(".bak"))
        except OSError:
            pass
        return []
    return [
        e for e in data
        if isinstance(e, dict) and isinstance(e.get("id"), str) and BOT_ID.fullmatch(e["id"])
        and isinstance(e.get("meeting"), str) and isinstance(e.get("name"), str)
    ]
