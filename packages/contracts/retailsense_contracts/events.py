"""Event envelope, ``Observation`` and every payload type.

Flow: the CV / rules code produces ``Observation``s (type + ts + payload); the
``EdgeStore.append()`` stamps them into ``Event``s (ULID event_id, per-device
gap-free ``seq``, HLC, ``cls``) inside one SQLite transaction together with the
outbox row.  The same ``Event`` JSON travels over HTTP batch sync or MQTT and is
stored unchanged on the cloud.

``Payload`` is a discriminated union on ``type`` so JSON round-trips into the
right class without guessing, and ``Observation``/``Event`` validators reject a
``type`` that disagrees with the payload (or with ``EVENT_CLASS``).
"""

import time
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .alerts import Alert, ImpactInr
from .enums import AckAction, AckBy, Direction, EventClass, LineKind, LinkState, ShelfState, ZoneKind
from .ids import new_ulid

EventType = Literal[
    "footfall.crossing",
    "zone.occupancy",
    "dwell.sample",
    "heatmap.tiles",
    "queue.snapshot",
    "queue.forecast",
    "shelf.scan",
    "shelf.state",
    "alert.raised",
    "alert.acked",
    "alert.resolved",
    "device.heartbeat",
    "stock.reconciled",
    "order.requested",
    "config.applied",
    "sim.truth",
]

EVENT_TYPES: tuple[str, ...] = EventType.__args__  # type: ignore[attr-defined]

EVENT_CLASS: dict[str, EventClass] = {
    "footfall.crossing": EventClass.AGGREGATE,
    "zone.occupancy": EventClass.AGGREGATE,
    "dwell.sample": EventClass.AGGREGATE,
    "heatmap.tiles": EventClass.TELEMETRY,
    "queue.snapshot": EventClass.AGGREGATE,
    "queue.forecast": EventClass.AGGREGATE,
    "shelf.scan": EventClass.AGGREGATE,
    "shelf.state": EventClass.AGGREGATE,
    "alert.raised": EventClass.ALERT,
    "alert.acked": EventClass.ALERT,
    "alert.resolved": EventClass.ALERT,
    "device.heartbeat": EventClass.TELEMETRY,
    "stock.reconciled": EventClass.TXN,
    "order.requested": EventClass.TXN,
    "config.applied": EventClass.CONFIG,
    "sim.truth": EventClass.TELEMETRY,
}

_FORBID = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# payloads
# ---------------------------------------------------------------------------


class FootfallCrossing(BaseModel):
    model_config = _FORBID
    type: Literal["footfall.crossing"] = "footfall.crossing"
    line_id: str
    line_kind: LineKind
    direction: Direction
    count: int = 1  # always 1 on edge; headless sims may batch


class ZoneOccupancy(BaseModel):
    model_config = _FORBID
    type: Literal["zone.occupancy"] = "zone.occupancy"
    zone_id: str
    zone_kind: ZoneKind
    count: int
    window_s: float


class DwellSample(BaseModel):
    """A finished visit to a zone. Deliberately carries no track id (privacy)."""

    model_config = _FORBID
    type: Literal["dwell.sample"] = "dwell.sample"
    zone_id: str
    dwell_s: float
    entered_ts: float
    exited_ts: float


class HeatmapTile(BaseModel):
    model_config = _FORBID
    cell_x: int
    cell_y: int
    hour_bucket: int  # floor(ts / 3600)
    dwell_s: float
    visits: int


class HeatmapTiles(BaseModel):
    """Deltas since the last flush, in floorplan cell coordinates."""

    model_config = _FORBID
    type: Literal["heatmap.tiles"] = "heatmap.tiles"
    cell_px: int
    width_cells: int
    height_cells: int
    tiles: list[HeatmapTile]


class QueueSnapshot(BaseModel):
    model_config = _FORBID
    type: Literal["queue.snapshot"] = "queue.snapshot"
    counter_id: str
    zone_id: str
    count: int
    avg_dwell_s: float
    max_dwell_s: float
    arrival_rate_pm: float
    service_rate_pm: float
    est_wait_s: float
    method: Literal["little_service", "observed_wait", "default_service"]
    served_window: int
    abandoned_window: int
    window_s: int = 300
    served_total: int  # cumulative since store-day start
    abandoned_total: int
    long_since_ts: float | None = None  # when count >= queue_long_count started, else None


class QueueForecast(BaseModel):
    model_config = _FORBID
    type: Literal["queue.forecast"] = "queue.forecast"
    counter_id: str
    made_ts: float
    horizons: dict[str, float]  # keys "5","10","15","30" -> expected count
    model: Literal["edge_trend", "cloud_gbm"]
    mae_recent: float | None = None


class ShelfScan(BaseModel):
    model_config = _FORBID
    type: Literal["shelf.scan"] = "shelf.scan"
    shelf_id: str
    sku_id: str | None
    coverage: float
    facings: int
    capacity_facings: int
    state_raw: ShelfState
    occluded: bool = False
    method: str = "classical"
    thumb_b64: str | None = None  # JPEG <= 96x96, shelf polygon only

    @field_validator("thumb_b64")
    @classmethod
    def _thumb_size(cls, v: str | None) -> str | None:
        if v is not None and len(v) > 16384:
            raise ValueError("thumb_b64 must be <= 16384 chars (96x96 JPEG)")
        return v


class ShelfStateChange(BaseModel):
    model_config = _FORBID
    type: Literal["shelf.state"] = "shelf.state"
    shelf_id: str
    sku_id: str | None
    from_state: ShelfState
    to_state: ShelfState
    gap_started_ts: float | None = None
    gap_minutes: float | None = None
    consecutive_empty_scans: int
    impact: ImpactInr | None = None


class AlertRaised(BaseModel):
    model_config = _FORBID
    type: Literal["alert.raised"] = "alert.raised"
    alert: Alert


class AlertAcked(BaseModel):
    model_config = _FORBID
    type: Literal["alert.acked"] = "alert.acked"
    alert_id: str
    action: AckAction
    by: AckBy
    note: str | None = None


class AlertResolved(BaseModel):
    model_config = _FORBID
    type: Literal["alert.resolved"] = "alert.resolved"
    alert_id: str
    reason: Literal["condition_cleared", "restocked_observed", "false_positive", "superseded", "timeout", "device_back"]
    final_gap_minutes: float | None = None
    impact_final: ImpactInr | None = None
    recovered: ImpactInr | None = None


class CameraHealth(BaseModel):
    model_config = _FORBID
    camera_id: str
    status: Literal["ok", "stale", "black", "error"]
    fps: float
    last_frame_age_s: float
    detector: str


class DeviceHeartbeat(BaseModel):
    model_config = _FORBID
    type: Literal["device.heartbeat"] = "device.heartbeat"
    uptime_s: float
    fps: float
    infer_ms_p50: float
    infer_ms_p95: float
    detector: str
    model_version: str
    backlog: int
    link: LinkState
    cameras: list[CameraHealth]
    contracts_version: str
    clock_factor: float = 1.0
    sim_ts: float | None = None
    cpu_pct: float | None = None
    mem_mb: float | None = None


class StockReconciled(BaseModel):
    model_config = _FORBID
    type: Literal["stock.reconciled"] = "stock.reconciled"
    sku_id: str
    shelf_id: str | None
    visual_units: int
    system_units: int
    delta_units: int
    delta_inr: float
    source: Literal["tally", "zoho", "manual", "mock"]


class OrderRequested(BaseModel):
    model_config = _FORBID
    type: Literal["order.requested"] = "order.requested"
    sku_id: str
    qty: int
    channel: AckBy
    alert_id: str | None = None
    est_cost_inr: float | None = None


class ConfigApplied(BaseModel):
    model_config = _FORBID
    type: Literal["config.applied"] = "config.applied"
    config_version: int
    config_hash: str


class SimTruth(BaseModel):
    """Ground truth emitted by the synthetic store so tests can assert accuracy."""

    model_config = _FORBID
    type: Literal["sim.truth"] = "sim.truth"
    in_store: int
    queue_counts: dict[str, int]
    shelf_units: dict[str, int]
    shelf_facings: dict[str, int]
    served_total: int
    abandoned_total: int
    footfall_in_total: int
    scenario: str


Payload = Annotated[
    Union[
        FootfallCrossing,
        ZoneOccupancy,
        DwellSample,
        HeatmapTiles,
        QueueSnapshot,
        QueueForecast,
        ShelfScan,
        ShelfStateChange,
        AlertRaised,
        AlertAcked,
        AlertResolved,
        DeviceHeartbeat,
        StockReconciled,
        OrderRequested,
        ConfigApplied,
        SimTruth,
    ],
    Field(discriminator="type"),
]

PAYLOAD_CLASSES: dict[str, type[BaseModel]] = {
    "footfall.crossing": FootfallCrossing,
    "zone.occupancy": ZoneOccupancy,
    "dwell.sample": DwellSample,
    "heatmap.tiles": HeatmapTiles,
    "queue.snapshot": QueueSnapshot,
    "queue.forecast": QueueForecast,
    "shelf.scan": ShelfScan,
    "shelf.state": ShelfStateChange,
    "alert.raised": AlertRaised,
    "alert.acked": AlertAcked,
    "alert.resolved": AlertResolved,
    "device.heartbeat": DeviceHeartbeat,
    "stock.reconciled": StockReconciled,
    "order.requested": OrderRequested,
    "config.applied": ConfigApplied,
    "sim.truth": SimTruth,
}


# ---------------------------------------------------------------------------
# envelope
# ---------------------------------------------------------------------------


class Observation(BaseModel):
    """What a producer hands to ``EdgeStore.append()``; not yet stamped."""

    model_config = _FORBID
    type: EventType
    ts: float
    camera_id: str | None = None
    payload: Payload

    @model_validator(mode="after")
    def _same_type(self) -> "Observation":
        if self.type != self.payload.type:
            raise ValueError(f"Observation.type {self.type!r} != payload.type {self.payload.type!r}")
        return self

    @classmethod
    def of(cls, payload: BaseModel, ts: float, camera_id: str | None = None) -> "Observation":
        """Build an Observation from a payload, inferring ``type``."""
        return cls(type=payload.type, ts=ts, camera_id=camera_id, payload=payload)  # type: ignore[arg-type]

    @property
    def cls(self) -> EventClass:
        return EVENT_CLASS[self.type]


class Event(BaseModel):
    """The wire/storage envelope. Immutable once stamped."""

    model_config = _FORBID
    event_id: str  # ULID
    store_id: str
    device_id: str
    camera_id: str | None = None
    ts: float  # observation time (frame ts / sim ts)
    hlc: str
    seq: int  # per-device monotonic, gap-free, starts at 1
    type: EventType
    cls: EventClass
    version: int = 1
    payload: Payload
    created_ts: float  # wall clock when stamped

    @model_validator(mode="after")
    def _check(self) -> "Event":
        if self.type != self.payload.type:
            raise ValueError(f"Event.type {self.type!r} != payload.type {self.payload.type!r}")
        if self.cls != EVENT_CLASS[self.type]:
            raise ValueError(f"Event.cls {self.cls!r} != EVENT_CLASS[{self.type!r}] = {EVENT_CLASS[self.type]!r}")
        return self

    def to_observation(self) -> Observation:
        return Observation(type=self.type, ts=self.ts, camera_id=self.camera_id, payload=self.payload)


def make_event(
    obs: Observation,
    *,
    store_id: str,
    device_id: str,
    seq: int,
    hlc: str,
    created_ts: float | None = None,
    event_id: str | None = None,
) -> Event:
    """Stamp an Observation into an Event (what EdgeStore.append does per row)."""
    return Event(
        event_id=event_id or new_ulid(),
        store_id=store_id,
        device_id=device_id,
        camera_id=obs.camera_id,
        ts=obs.ts,
        hlc=hlc,
        seq=seq,
        type=obs.type,
        cls=EVENT_CLASS[obs.type],
        payload=obs.payload,
        created_ts=time.time() if created_ts is None else created_ts,
    )


__all__ = [
    "EVENT_CLASS",
    "EVENT_TYPES",
    "PAYLOAD_CLASSES",
    "AlertAcked",
    "AlertRaised",
    "AlertResolved",
    "CameraHealth",
    "ConfigApplied",
    "DeviceHeartbeat",
    "DwellSample",
    "Event",
    "EventType",
    "FootfallCrossing",
    "HeatmapTile",
    "HeatmapTiles",
    "Observation",
    "OrderRequested",
    "Payload",
    "QueueForecast",
    "QueueSnapshot",
    "ShelfScan",
    "ShelfStateChange",
    "SimTruth",
    "StockReconciled",
    "ZoneOccupancy",
    "make_event",
]
