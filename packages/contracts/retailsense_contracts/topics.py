"""MQTT topic builders (optional uplink) and the outbox expiry / eviction policy.

The policy table is used by *both* uplinks: HTTP sync consults ``EXPIRY_S`` when
it enqueues an outbox row; the MQTT uplink maps the same number to the MQTT 5
``MessageExpiryInterval``.  ALERT and TXN never expire and are never evicted
(RejectNewData semantics: when the outbox overflows, the oldest telemetry /
aggregate rows go first and alerts always survive a long outage).
"""

from .enums import EventClass

ROOT = "rs/v1"


def topic(store_id: str, device_id: str, cls: EventClass | str) -> str:
    """``rs/v1/{store}/{device}/{cls}`` - payload is one Event JSON per message."""
    return f"{ROOT}/{store_id}/{device_id}/{str(cls)}"


def status_topic(store_id: str, device_id: str) -> str:
    """``rs/v1/{store}/{device}/status`` - retained LWT ``"online"`` | ``"offline"``."""
    return f"{ROOT}/{store_id}/{device_id}/status"


def cmd_topic(store_id: str, device_id: str) -> str:
    """``rs/v1/{store}/{device}/cmd`` - payload is a Command JSON."""
    return f"{ROOT}/{store_id}/{device_id}/cmd"


def subscribe_all_events(store_id: str = "+", device_id: str = "+") -> str:
    """Wildcard subscription for the cloud bridge (``rs/v1/+/+/+``)."""
    return f"{ROOT}/{store_id}/{device_id}/+"


def parse_topic(t: str) -> tuple[str, str, str] | None:
    """``rs/v1/{store}/{device}/{leaf}`` -> (store, device, leaf); None if not ours."""
    parts = t.split("/")
    if len(parts) != 5 or parts[0] != "rs" or parts[1] != "v1":
        return None
    return parts[2], parts[3], parts[4]


EXPIRY_S: dict[EventClass, int | None] = {
    EventClass.TELEMETRY: 3600,
    EventClass.AGGREGATE: 86400,
    EventClass.ALERT: None,  # never
    EventClass.TXN: None,  # never
    EventClass.CONFIG: 86400,
}

EVICTABLE: tuple[EventClass, ...] = (EventClass.TELEMETRY, EventClass.AGGREGATE)

QOS = 1
MQTT_VERSION = 5
CLEAN_START = False


def expires_ts(cls: EventClass | str, enqueued_ts: float) -> float | None:
    """Absolute expiry for an outbox row, or None for never."""
    ttl = EXPIRY_S[EventClass(str(cls))]
    return None if ttl is None else enqueued_ts + ttl


__all__ = [
    "CLEAN_START",
    "EVICTABLE",
    "EXPIRY_S",
    "MQTT_VERSION",
    "QOS",
    "ROOT",
    "cmd_topic",
    "expires_ts",
    "parse_topic",
    "status_topic",
    "subscribe_all_events",
    "topic",
]
