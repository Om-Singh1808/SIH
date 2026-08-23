"""Closed vocabularies shared by every RetailSense component.

All enums are ``StrEnum`` so they serialise to plain strings in JSON, SQLite and
the TypeScript mirror, and compare equal to their string value
(``AlertKind.SHELF_GAP == "shelf_gap"``).  Never rename a member: the values are
wire format.
"""

from enum import StrEnum


class EventClass(StrEnum):
    """Storage/sync class of an event; drives outbox expiry + eviction (topics.EXPIRY_S)."""

    TELEMETRY = "telemetry"
    AGGREGATE = "aggregate"
    ALERT = "alert"
    TXN = "txn"
    CONFIG = "config"


class Severity(StrEnum):
    INFO = "info"
    WARN = "warn"
    HIGH = "high"
    CRITICAL = "critical"


class AlertKind(StrEnum):
    SHELF_GAP = "shelf_gap"
    QUEUE_LONG = "queue_long"
    QUEUE_FORECAST = "queue_forecast"
    CAMERA_DOWN = "camera_down"
    SYNC_BACKLOG = "sync_backlog"
    DEVICE_OFFLINE = "device_offline"
    SHRINK_SUSPECT = "shrink_suspect"
    FOOTFALL_SPIKE = "footfall_spike"


class AlertStatus(StrEnum):
    OPEN = "open"
    ACKED = "acked"
    RESOLVED = "resolved"


class AckAction(StrEnum):
    """What the shopkeeper did. On WhatsApp digit *i* maps to ``Alert.actions[i-1]``."""

    RESTOCKED = "restocked"
    ORDER = "order"
    FALSE_POSITIVE = "false_positive"
    OPENED_COUNTER = "opened_counter"
    IGNORE = "ignore"
    CHECKED = "checked"
    INVESTIGATE = "investigate"


class AckBy(StrEnum):
    WHATSAPP = "whatsapp"
    WHATSAPP_SIM = "whatsapp_sim"
    BOARD = "board"
    AUTO = "auto"
    TELEGRAM = "telegram"


class ShelfState(StrEnum):
    STOCKED = "stocked"
    PARTIAL = "partial"
    EMPTY = "empty"
    UNKNOWN = "unknown"


class LinkState(StrEnum):
    UP = "up"
    DOWN = "down"


class UplinkMode(StrEnum):
    HTTP = "http"
    MQTT = "mqtt"
    NONE = "none"


class ZoneKind(StrEnum):
    AISLE = "aisle"
    QUEUE = "queue"
    ENTRANCE = "entrance"
    COUNTER = "counter"
    STORE = "store"
    CUSTOM = "custom"


class LineKind(StrEnum):
    ENTRANCE = "entrance"
    COUNTER = "counter"
    CUSTOM = "custom"


class Direction(StrEnum):
    """Line-crossing direction; see geometry.side_of_line for the normative rule."""

    IN = "in"
    OUT = "out"


class DetectorKind(StrEnum):
    AUTO = "auto"
    SYNTHETIC = "synthetic"
    ONNX = "onnx"
    ULTRALYTICS = "ultralytics"
    FAKE = "fake"


class Anchor(StrEnum):
    """Which point of a track's bbox represents the person on the floor."""

    BOTTOM_CENTER = "bottom_center"
    CENTER = "center"


class Lang(StrEnum):
    HI = "hi"
    EN = "en"


class Origin(StrEnum):
    EDGE = "edge"
    CLOUD = "cloud"


__all__ = [
    "AckAction",
    "AckBy",
    "AlertKind",
    "AlertStatus",
    "Anchor",
    "DetectorKind",
    "Direction",
    "EventClass",
    "Lang",
    "LineKind",
    "LinkState",
    "Origin",
    "Severity",
    "ShelfState",
    "UplinkMode",
    "ZoneKind",
]
