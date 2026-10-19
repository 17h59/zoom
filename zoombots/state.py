"""Persistance de la liste des bots (JSON) pour survivre à un redémarrage du serveur."""

import json
import os
from pathlib import Path

from .log import DATA_DIR

STATE_FILE = Path(os.environ.get("ZOOMBOTS_STATE", Path(DATA_DIR) / "state.json"))


def save(entries: list[dict], path: Path = STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(entries, indent=2))
    tmp.replace(path)  # écriture atomique


def load(path: Path = STATE_FILE) -> list[dict]:
    try:
        data = json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    return [e for e in data if isinstance(e, dict) and {"id", "meeting", "name"} <= e.keys()]
