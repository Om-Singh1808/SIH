"""JSON-ish structured logging that is safe on Windows consoles.

``get_logger("senseedge.sync")`` returns a standard ``logging.Logger`` whose
records are rendered as one JSON object per line (``ts``, ``level``, ``logger``,
``msg`` plus any ``extra=`` fields).  Hindi alert text and the rupee sign are
common in our logs, and a cp1252 console would otherwise raise
``UnicodeEncodeError`` - so the first call reconfigures ``stdout``/``stderr`` to
UTF-8 with ``errors="replace"``.

Set ``RS_LOG_LEVEL`` (``DEBUG``/``INFO``/...) to change the default level and
``RS_LOG_PLAIN=1`` for human-readable lines during development.
"""

import datetime as _dt
import json
import logging
import os
import sys

_CONFIGURED = False
_RESERVED = set(logging.LogRecord("x", 0, "x", 0, "", (), None).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        doc = {
            "ts": _dt.datetime.fromtimestamp(record.created, _dt.UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                doc[key] = value
        if record.exc_info:
            doc["exc"] = self.formatException(record.exc_info)
        return json.dumps(doc, ensure_ascii=False, default=str)


class PlainFormatter(logging.Formatter):
    def __init__(self) -> None:
        super().__init__("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%H:%M:%S")


def _utf8_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:  # pragma: no cover - exotic streams
                pass


def configure(level: str | int | None = None, *, plain: bool | None = None) -> None:
    """Install the root handler once. Safe to call repeatedly."""
    global _CONFIGURED
    _utf8_console()
    if _CONFIGURED:
        if level is not None:
            logging.getLogger().setLevel(level)
        return
    if plain is None:
        plain = os.environ.get("RS_LOG_PLAIN", "0") == "1"
    if level is None:
        level = os.environ.get("RS_LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(PlainFormatter() if plain else JsonFormatter())
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(level)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a logger that emits JSON lines, configuring the root once."""
    configure()
    return logging.getLogger(name)


__all__ = ["JsonFormatter", "PlainFormatter", "configure", "get_logger"]
