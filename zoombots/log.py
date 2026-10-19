"""Logs structurés (une ligne JSON par événement) et journal d'événements par bot."""

import json
import logging
import os
import sys
import time
from pathlib import Path

_INSTANCE = os.environ.get("ZOOMBOTS_INSTANCE", "")
DATA_DIR = os.environ.get("ZOOMBOTS_DATA") or str(
    Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    / (f"zoombots-{_INSTANCE}" if _INSTANCE else "zoombots")
)  # absolu : la CLI et le serveur retrouvent les mêmes fichiers quel que soit le répertoire courant


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": round(record.created, 3), "level": record.levelname, "msg": record.getMessage()}
        payload.update(getattr(record, "fields", {}))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("zoombots")
    if not root.handlers:
        root.addHandler(handler)
    root.setLevel(os.environ.get("ZOOMBOTS_LOG_LEVEL", "INFO"))


def log_event(logger: logging.Logger, msg: str, **fields) -> dict:
    logger.info(msg, extra={"fields": fields})
    return {"ts": round(time.time(), 3), "msg": msg, **fields}
