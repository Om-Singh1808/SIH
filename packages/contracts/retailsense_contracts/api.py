"""REST request/response models for SenseEdge (:8001) and SenseCloud (:8000).

See IMPLEMENTATION_SPEC C.16 for the endpoint table.  These are the *only*
shapes the dashboard knows about (via the generated TypeScript mirror), so every
endpoint must return exactly one of them.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .alerts import Alert, ImpactInr
from .config import Counter, Line, ShelfPolygon, StoreConfig, Zone
from .enums import Lang, LinkState, ShelfState, UplinkMode
from .events import CameraHealth, Event, QueueForecast, QueueSnapshot
from .manifest import ModelManifest

_FORBID = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# edge: health / sync / kpis
# ---------------------------------------------------------------------------


class SyncStatus(BaseModel):
    model_config = _FORBID
    link: LinkState
    uplink: UplinkMode
    cloud_reachable: bool
    backlog: int
    backlog_by_class: dict[str, int]
    last_ack_ts: float | None
    last_ack_seq: int | None
    replayed_since_restore: int
    replay_total_at_restore: int
    seq_ok: bool
    down_since_ts: float | None


class HealthStatus(BaseModel):
    model_config = _FORBID
    status: Literal["ok", "degraded", "starting"]
    store_id: str
    device_id: str
    uptime_s: float
    contracts_version: str
    detector: str
    model_version: str
    cameras: list[CameraHealth]
    sync: SyncStatus
    sim_ts: float | None
    clock_factor: float
    fps: float
    infer_ms_p50: float


class KpiToday(BaseModel):
    model_config = _FORBID
    store_id: str
    date: str
    as_of_ts: float
    footfall_in: int
    footfall_out: int
    occupancy_now: int
    visual_transactions: int
    conversion_pct: float | None
    atv_inr: float | None
    osa_pct: float
    gap_minutes_total: float
    avg_wait_s: float | None
    max_wait_s: float | None
    abandoned: int
    lost_sales_inr: float
    lost_margin_inr: float
    recovered_inr: float
    alerts_open: int
    alerts_today: int
    deltas: dict[str, float | None] = Field(default_factory=dict)  # metric -> delta vs yesterday


class KpiDaily(BaseModel):
    model_config = _FORBID
    store_id: str
    date: str
    footfall_in: int
    footfall_out: int
    visual_transactions: int
    conversion_pct: float | None
    atv_inr: float | None
    osa_pct: float
    gap_minutes_total: float
    avg_wait_s: float | None
    max_wait_s: float | None
    abandoned: int
    lost_sales_inr: float
    recovered_inr: float
    shrink_inr: float
    alerts_total: int


class SeriesPoint(BaseModel):
    model_config = _FORBID
    ts: float
    value: float


class Series(BaseModel):
    model_config = _FORBID
    metric: str
    bucket_s: int
    points: list[SeriesPoint]


# ---------------------------------------------------------------------------
# edge: views
# ---------------------------------------------------------------------------


class ShelfStateView(BaseModel):
    model_config = _FORBID
    shelf_id: str
    name: str
    sku_id: str | None
    sku_name: str
    state: ShelfState
    coverage: float
    facings: int
    capacity_facings: int
    min_facings: int
    consecutive_empty_scans: int
    persistence_required: int
    gap_started_ts: float | None
    gap_minutes: float | None
    last_scan_ts: float | None
    occluded: bool
    impact_open: ImpactInr | None
    has_reference: bool


class QueueView(BaseModel):
    model_config = _FORBID
    counter_id: str
    name: str
    snapshot: QueueSnapshot | None
    forecast: QueueForecast | None
    open_alert_id: str | None


class HeatCell(BaseModel):
    model_config = _FORBID
    x: int
    y: int
    dwell_s: float
    visits: int


class HeatmapResponse(BaseModel):
    model_config = _FORBID
    camera_id: str | None
    cell_px: int
    width_cells: int
    height_cells: int
    from_ts: float
    to_ts: float
    cells: list[HeatCell]
    max_dwell_s: float


class ZonesUpdate(BaseModel):
    model_config = _FORBID
    zones: list[Zone]
    lines: list[Line]
    counters: list[Counter]


class ShelvesUpdate(BaseModel):
    model_config = _FORBID
    shelves: list[ShelfPolygon]


class LinkRequest(BaseModel):
    model_config = _FORBID
    state: LinkState


class ScenarioRequest(BaseModel):
    model_config = _FORBID
    name: str
    params: dict[str, Any] = Field(default_factory=dict)


class ScenarioStatus(BaseModel):
    model_config = _FORBID
    active: str
    since_ts: float
    params: dict[str, Any]
    available: list[str]
    clock_factor: float
    sim_ts: float


class ChaosRequest(BaseModel):
    model_config = _FORBID
    kind: Literal["freeze", "drop", "blackout", "noise"]
    enabled: bool
    seconds: float | None = None
    p: float | None = None


class WhatsAppReply(BaseModel):
    model_config = _FORBID
    alert_id: str
    digit: int
    from_number: str | None = None


class SkuEnrolResponse(BaseModel):
    model_config = _FORBID
    sku_id: str
    enrolled: int
    backend: str


class DailySummary(BaseModel):
    model_config = _FORBID
    store_id: str
    date: str
    lang: Lang
    text: str
    kpis: KpiToday


class ModelStatus(BaseModel):
    model_config = _FORBID
    local: ModelManifest | None
    remote: ModelManifest | None
    active_model_id: str
    active_version: str
    update_available: bool
    assigned_version: str | None


# ---------------------------------------------------------------------------
# sync protocol (edge -> cloud)
# ---------------------------------------------------------------------------


class Command(BaseModel):
    """Cloud -> device instruction, piggybacked on IngestAck (HTTP) or cmd topic (MQTT)."""

    model_config = _FORBID
    command_id: str
    device_id: str
    kind: Literal["ack_alert", "apply_config", "set_link", "set_scenario", "model_update", "ping"]
    payload: dict[str, Any]
    created_ts: float


class IngestBatch(BaseModel):
    model_config = _FORBID
    batch_id: str
    device_id: str
    store_id: str
    sent_ts: float
    cursor: int
    events: list[Event] = Field(max_length=500)
    backlog: int
    contracts_version: str


class IngestAck(BaseModel):
    model_config = _FORBID
    batch_id: str
    accepted: int
    duplicates: int
    rejected: list[dict[str, str]]
    last_seq: int | None
    seq_ok: bool
    seq_gaps: list[int]
    commands: list[Command]
    server_ts: float


# ---------------------------------------------------------------------------
# cloud: stores / fleet / chain
# ---------------------------------------------------------------------------


class Store(BaseModel):
    model_config = _FORBID
    store_id: str
    name: str
    tier: str
    lang: Lang
    tz: str
    device_ids: list[str]
    registered_ts: float
    config: StoreConfig | None = None


class DeviceStatus(BaseModel):
    model_config = _FORBID
    device_id: str
    store_id: str
    status: Literal["online", "offline", "never"]
    last_seen_ts: float | None
    model_version: str | None
    assigned_version: str | None
    version_drift: bool
    fps: float | None
    backlog: int | None
    link: LinkState | None
    uptime_s: float | None


class FleetView(BaseModel):
    model_config = _FORBID
    devices: list[DeviceStatus]
    online: int
    offline: int
    manifest_version: str | None


class ChainRankRow(BaseModel):
    model_config = _FORBID
    store_id: str
    name: str
    value: float
    rank: int
    footfall_in: int
    normalised: float | None


class ChainRank(BaseModel):
    model_config = _FORBID
    metric: str
    date: str
    rows: list[ChainRankRow]


class KpiRange(BaseModel):
    model_config = _FORBID
    today: KpiToday
    daily: list[KpiDaily]


# ---------------------------------------------------------------------------
# cloud: forecasting / reorder / reconcile / ondc
# ---------------------------------------------------------------------------


class FitReport(BaseModel):
    model_config = _FORBID
    model: str
    target: str
    trained_ts: float
    n_rows: int
    mae_holdout: float
    mae_baseline: float
    features: list[str]
    horizons: list[int] = Field(default_factory=list)


class FootfallForecastDay(BaseModel):
    model_config = _FORBID
    date: str
    predicted: float
    lower: float
    upper: float
    is_festival: bool
    festival_name: str | None
    days_to_festival: int | None


class FootfallForecast(BaseModel):
    model_config = _FORBID
    store_id: str
    made_ts: float
    days: list[FootfallForecastDay]
    mae_holdout: float | None


class ReorderSuggestion(BaseModel):
    model_config = _FORBID
    sku_id: str
    name_en: str
    name_hi: str
    system_units: int | None
    visual_units: int | None
    forecast_units_lead: float
    safety_stock: float
    suggest_qty: int
    est_cost_inr: float
    reason: str


class ReconcileRow(BaseModel):
    model_config = _FORBID
    sku_id: str
    name: str
    shelf_id: str | None
    visual_units: int
    system_units: int
    delta_units: int
    delta_inr: float
    flagged: bool


class ReconcileReport(BaseModel):
    model_config = _FORBID
    store_id: str
    ts: float
    source: str
    rows: list[ReconcileRow]
    shrink_inr_total: float
    alerts_raised: int


class OndcPublishRequest(BaseModel):
    model_config = _FORBID
    sku_id: str
    available: bool
    qty: int | None = None


class OndcAck(BaseModel):
    model_config = _FORBID
    ok: bool
    message_id: str
    item_id: str
    available: bool
    ts: float
    signed: bool = False


# ---------------------------------------------------------------------------
# cloud: notifications / reports / integrations / fleet admin
# ---------------------------------------------------------------------------


class OutboundMessage(BaseModel):
    model_config = _FORBID
    message_id: str
    channel: str
    to: str
    text: str
    buttons: list[str]
    alert_id: str | None
    store_id: str
    created_ts: float
    status: Literal["queued", "sent", "delivered", "failed"]
    delivered_ts: float | None = None


class DeliveryReceipt(BaseModel):
    model_config = _FORBID
    message_id: str
    status: Literal["sent", "failed"]
    detail: str | None = None


class DailyReport(BaseModel):
    model_config = _FORBID
    store_id: str
    date: str
    kpis: KpiDaily
    top_alerts: list[Alert]
    gap_minutes_by_shelf: dict[str, float]
    queue_by_hour: dict[str, float]
    forecast_mae: float | None
    whatsapp_text_hi: str
    whatsapp_text_en: str


class IntegrationsStatus(BaseModel):
    model_config = _FORBID
    tally: dict[str, Any]
    ondc: dict[str, Any]
    whatsapp: dict[str, Any]


class ManifestPublishRequest(BaseModel):
    model_config = _FORBID
    manifest: ModelManifest


class RolloutRequest(BaseModel):
    model_config = _FORBID
    model_id: str
    version: str
    canary_pct: int


class ErrorResponse(BaseModel):
    """Uniform error body (FastAPI ``detail`` compatible)."""

    model_config = _FORBID
    detail: str
    code: str | None = None


__all__ = [
    "ChainRank",
    "ChainRankRow",
    "ChaosRequest",
    "Command",
    "DailyReport",
    "DailySummary",
    "DeliveryReceipt",
    "DeviceStatus",
    "ErrorResponse",
    "FitReport",
    "FleetView",
    "FootfallForecast",
    "FootfallForecastDay",
    "HealthStatus",
    "HeatCell",
    "HeatmapResponse",
    "IngestAck",
    "IngestBatch",
    "IntegrationsStatus",
    "KpiDaily",
    "KpiRange",
    "KpiToday",
    "LinkRequest",
    "ManifestPublishRequest",
    "ModelStatus",
    "OndcAck",
    "OndcPublishRequest",
    "OutboundMessage",
    "QueueView",
    "ReconcileReport",
    "ReconcileRow",
    "ReorderSuggestion",
    "RolloutRequest",
    "ScenarioRequest",
    "ScenarioStatus",
    "Series",
    "SeriesPoint",
    "ShelfStateView",
    "ShelvesUpdate",
    "SkuEnrolResponse",
    "Store",
    "SyncStatus",
    "WhatsAppReply",
    "ZonesUpdate",
]
