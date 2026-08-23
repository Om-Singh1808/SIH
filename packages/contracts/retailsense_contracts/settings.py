"""Environment variable names and defaults shared by the apps and tools.

Keeping the names here (instead of in each app) means ``tools/demo.py``,
``docker-compose.yml`` and the apps can never disagree about spelling.
Values are strings; use ``get_int`` / ``get_float`` / ``get_bool`` for typed reads.
"""

import os

DEFAULTS: dict[str, str] = {
    # --- SenseEdge ---------------------------------------------------------
    "RS_CONFIG": "packages/contracts/retailsense_contracts/examples/store_demo.yaml",
    "RS_EDGE_PORT": "8001",
    "RS_CLOUD_URL": "http://localhost:8000",
    "RS_DB_PATH": "var/senseedge.db",
    "RS_CLOCK_FACTOR": "10",
    "RS_UPLINK": "http",  # http | mqtt | none
    "RS_DETECTOR": "auto",  # auto | synthetic | onnx | ultralytics | fake
    "RS_LOG_LEVEL": "INFO",
    "RS_LOG_PLAIN": "0",
    # --- SenseCloud --------------------------------------------------------
    "SENSECLOUD_DB_URL": "sqlite:///var/sensecloud.db",
    "SENSECLOUD_DEV": "0",  # "1" accepts any device token (demo/dev only)
    "SENSECLOUD_SEED_HISTORY": "0",  # "1" seeds 30 days of KPIs from fake/sim history at boot
    "SENSECLOUD_NOTIFIER": "simulator",  # simulator | telegram | cloud_api
    "SENSECLOUD_MQTT_HOST": "",  # empty = MQTT bridge disabled
    "SENSECLOUD_PORT": "8000",
    # --- Integrations secrets (never logged) -------------------------------
    "WHATSAPP_TOKEN": "",
    "TELEGRAM_TOKEN": "",
}

SECRET_NAMES = frozenset({"WHATSAPP_TOKEN", "TELEGRAM_TOKEN"})


def get(name: str, default: str | None = None) -> str:
    """Read ``name`` from the environment, falling back to ``default`` or the table above."""
    if default is None:
        default = DEFAULTS.get(name, "")
    return os.environ.get(name, default)


def get_int(name: str, default: int | None = None) -> int:
    raw = get(name, None if default is None else str(default))
    try:
        return int(raw)
    except ValueError:
        return int(DEFAULTS.get(name, "0") or 0) if default is None else default


def get_float(name: str, default: float | None = None) -> float:
    raw = get(name, None if default is None else str(default))
    try:
        return float(raw)
    except ValueError:
        return float(DEFAULTS.get(name, "0") or 0) if default is None else default


def get_bool(name: str, default: bool | None = None) -> bool:
    raw = get(name, None if default is None else ("1" if default else "0")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def snapshot(*, redact_secrets: bool = True) -> dict[str, str]:
    """Effective settings (for ``/health`` or a startup banner)."""
    out = {}
    for name in DEFAULTS:
        value = get(name)
        if redact_secrets and name in SECRET_NAMES and value:
            value = "***"
        out[name] = value
    return out


__all__ = ["DEFAULTS", "SECRET_NAMES", "get", "get_bool", "get_float", "get_int", "snapshot"]
