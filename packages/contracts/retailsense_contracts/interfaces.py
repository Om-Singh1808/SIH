"""Every pluggable component of RetailSense, as a ``typing.Protocol``.

Apps never import sibling packages; they ask ``registry.resolve(key)`` for an
object satisfying one of these Protocols and get either the real implementation
or the deterministic fake from ``testing.py``.  All Protocols are
``runtime_checkable`` so conformance can be spot-checked with ``isinstance``.

Dataclasses at the top (``Frame``, ``Detection``, ``Track``...) are the hot-path
in-process types: plain dataclasses rather than pydantic models because the CV
thread creates thousands per second.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import numpy as np

from .alerts import Alert
from .api import (
    ChaosRequest,
    DeliveryReceipt,
    FitReport,
    FootfallForecastDay,
    HeatmapResponse,
    IngestAck,
    IngestBatch,
    KpiDaily,
    KpiToday,
    OndcAck,
    OutboundMessage,
    ReorderSuggestion,
    ScenarioStatus,
    ShelfStateView,
    SyncStatus,
)
from .config import (
    SKU,
    CameraConfig,
    Counter,
    Floorplan,
    ImpactConfig,
    Line,
    RetentionPolicy,
    RulesConfig,
    ShelfPolygon,
    ShelfReference,
    StoreConfig,
    Zone,
)
from .enums import AckAction, AckBy, AlertStatus, Anchor, Direction, LineKind, LinkState, UplinkMode
from .events import (
    DeviceHeartbeat,
    DwellSample,
    Event,
    FootfallCrossing,
    HeatmapTiles,
    Observation,
    QueueForecast,
    QueueSnapshot,
    ShelfScan,
    ShelfStateChange,
    SimTruth,
    ZoneOccupancy,
)

if TYPE_CHECKING:  # pandas is NOT a dependency of contracts; only forecasting/sim use it
    import pandas as pd

BBox = tuple[float, float, float, float]  # xyxy image pixels


# ---------------------------------------------------------------------------
# hot-path dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Frame:
    ts: float
    camera_id: str
    image: np.ndarray  # BGR uint8 HxWx3
    seq: int


@dataclass
class Detection:
    bbox: BBox
    conf: float
    cls: int = 0


@dataclass
class Track:
    track_id: int
    bbox: BBox
    conf: float
    age: int
    hits: int
    time_since_update: int
    confirmed: bool

    def anchor(self, kind: Anchor | str) -> tuple[float, float]:
        x0, y0, x1, y1 = self.bbox
        if str(kind) == "center":
            return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)
        return ((x0 + x1) / 2.0, float(y1))

    @property
    def center(self) -> tuple[float, float]:
        return self.anchor(Anchor.CENTER)


@dataclass
class Crossing:
    line_id: str
    line_kind: LineKind
    track_id: int
    direction: Direction
    ts: float


@dataclass
class AnalyticsUpdate:
    ts: float
    camera_id: str
    zone_members: dict[str, list[int]] = field(default_factory=dict)  # zone_id -> track ids inside
    crossings: list[Crossing] = field(default_factory=list)
    dwell_samples: list[DwellSample] = field(default_factory=list)
    occupancy: list[ZoneOccupancy] = field(default_factory=list)
    footfall: list[FootfallCrossing] = field(default_factory=list)
    heat: HeatmapTiles | None = None

    def observations(self) -> list[Observation]:
        """Everything in this update that should be persisted, as Observations."""
        out: list[Observation] = []
        for f in self.footfall:
            out.append(Observation.of(f, self.ts, self.camera_id))
        for o in self.occupancy:
            out.append(Observation.of(o, self.ts, self.camera_id))
        for d in self.dwell_samples:
            out.append(Observation.of(d, self.ts, self.camera_id))
        if self.heat is not None:
            out.append(Observation.of(self.heat, self.ts, self.camera_id))
        return out


@dataclass
class CoverageResult:
    coverage: float
    facings: int
    raw_coverage: float
    method: str
    debug: dict[str, float] = field(default_factory=dict)


class SourceError(Exception):
    """Fatal frame-source failure (device gone, file unreadable)."""


# ---------------------------------------------------------------------------
# capture / perception
# ---------------------------------------------------------------------------


@runtime_checkable
class FrameSource(Protocol):
    camera_id: str

    def open(self) -> None: ...
    def read(self) -> Frame | None: ...  # blocks; None = end of stream; raises SourceError if fatal
    def close(self) -> None: ...
    @property
    def size(self) -> tuple[int, int]: ...
    @property
    def nominal_fps(self) -> float: ...


@runtime_checkable
class SyntheticControl(Protocol):
    """Also implemented by the synthetic FrameSource; drives the demo buttons."""

    def apply_scenario(self, name: str, params: dict) -> ScenarioStatus: ...
    def scenario_status(self) -> ScenarioStatus: ...
    def restock(self, shelf_id: str, units: int | None = None) -> None: ...
    def set_clock_factor(self, factor: float) -> None: ...
    def chaos(self, req: ChaosRequest) -> None: ...
    def truth(self) -> SimTruth: ...


@runtime_checkable
class Detector(Protocol):
    name: str
    model_version: str

    def detect(self, image: np.ndarray) -> list[Detection]: ...
    def warmup(self) -> None: ...


@runtime_checkable
class Tracker(Protocol):
    def update(self, detections: list[Detection], ts: float) -> list[Track]: ...  # confirmed + tentative
    def reset(self) -> None: ...


@runtime_checkable
class PointMapper(Protocol):
    def to_floor(self, pts: np.ndarray) -> np.ndarray: ...  # [N,2] image px -> floor px
    def to_image(self, pts: np.ndarray) -> np.ndarray: ...


# ---------------------------------------------------------------------------
# analytics
# ---------------------------------------------------------------------------


@runtime_checkable
class ZoneEngine(Protocol):
    def __init__(
        self,
        camera: CameraConfig,
        zones: list[Zone],
        lines: list[Line],
        mapper: PointMapper,
        rules: RulesConfig,
        floorplan: Floorplan,
    ) -> None: ...
    def update(self, tracks: list[Track], ts: float) -> AnalyticsUpdate: ...
    def flush(self, ts: float) -> AnalyticsUpdate: ...  # end-of-day / exit


@runtime_checkable
class QueueAnalyzer(Protocol):
    def __init__(self, counter: Counter, rules: RulesConfig, day_start_ts: float) -> None: ...
    def update(self, upd: AnalyticsUpdate) -> QueueSnapshot | None: ...
    def state(self) -> QueueSnapshot: ...
    def reset_day(self, day_start_ts: float) -> None: ...


@runtime_checkable
class EdgeQueueForecaster(Protocol):
    def observe(self, snap: QueueSnapshot) -> None: ...
    def predict(self, ts: float) -> QueueForecast | None: ...
    def set_cloud_forecast(self, fc: QueueForecast) -> None: ...


# ---------------------------------------------------------------------------
# shelf
# ---------------------------------------------------------------------------


@runtime_checkable
class CoverageEstimator(Protocol):
    def calibrate(self, image: np.ndarray, shelf: ShelfPolygon) -> ShelfReference: ...
    def estimate(self, image: np.ndarray, shelf: ShelfPolygon, ref: ShelfReference | None) -> CoverageResult: ...


@runtime_checkable
class ShelfStateMachine(Protocol):
    def __init__(
        self, shelves: list[ShelfPolygon], skus: list[SKU], rules: RulesConfig, impact: ImpactConfig
    ) -> None: ...
    def apply(self, scan: ShelfScan, ts: float) -> ShelfStateChange | None: ...
    def view(self, shelf_id: str) -> ShelfStateView: ...
    def views(self) -> list[ShelfStateView]: ...
    def feedback_false_positive(self, shelf_id: str) -> int: ...
    def restore(self, rows: list[dict]) -> None: ...
    def osa_pct(self, ts: float) -> float: ...
    def gap_minutes_today(self, ts: float) -> float: ...


@runtime_checkable
class SkuIdentifier(Protocol):
    backend: str

    def enrol(self, sku_id: str, images: list[np.ndarray]) -> int: ...
    def identify(self, crop: np.ndarray, hint_sku_id: str | None) -> tuple[str | None, float]: ...


# ---------------------------------------------------------------------------
# rules / store / uplink
# ---------------------------------------------------------------------------


@runtime_checkable
class RuleEngine(Protocol):
    def __init__(self, cfg: StoreConfig) -> None: ...
    def on_shelf_change(self, ch: ShelfStateChange, view: ShelfStateView, ts: float) -> list[Observation]: ...
    def on_queue(self, snap: QueueSnapshot, forecast: QueueForecast | None, ts: float) -> list[Observation]: ...
    def on_health(self, hb: DeviceHeartbeat, ts: float) -> list[Observation]: ...
    def on_sync(self, sync: SyncStatus, ts: float) -> list[Observation]: ...
    def on_ack(self, alert_id: str, action: AckAction, by: AckBy, ts: float) -> list[Observation]: ...
    def open_alerts(self) -> list[Alert]: ...
    def get(self, alert_id: str) -> Alert | None: ...
    def restore(self, alerts: list[Alert]) -> None: ...


@runtime_checkable
class EdgeStore(Protocol):
    def __init__(self, cfg: StoreConfig) -> None: ...
    def append(self, observations: list[Observation]) -> list[Event]: ...  # ONE transaction
    def pending(self, limit: int) -> list[tuple[int, Event]]: ...
    def mark_sent(self, outbox_ids: list[int], ts: float) -> None: ...
    def mark_failed(self, outbox_ids: list[int], error: str) -> None: ...
    def backlog(self) -> dict[str, int]: ...
    def evict_overflow(self, max_rows: int) -> int: ...
    def expire(self, now_ts: float) -> int: ...
    def get_state(self, key: str, default: str | None = None) -> str | None: ...
    def set_state(self, key: str, value: str) -> None: ...
    def upsert_alert(self, a: Alert) -> None: ...
    def alerts(self, status: AlertStatus | None, limit: int = 100) -> list[Alert]: ...
    def alert(self, alert_id: str) -> Alert | None: ...
    def upsert_shelf(self, v: ShelfStateView, reference: ShelfReference | None) -> None: ...
    def shelves(self) -> list[dict]: ...
    def upsert_queue(self, counter_id: str, snap: QueueSnapshot | None, fc: QueueForecast | None) -> None: ...
    def queues(self) -> list[dict]: ...
    def heat_add(self, camera_id: str, tiles: HeatmapTiles) -> None: ...
    def heat_query(self, camera_id: str | None, from_ts: float, to_ts: float) -> HeatmapResponse: ...
    def kpi_today(self, ts: float) -> KpiToday: ...
    def upsert_kpi_daily(self, row: KpiDaily) -> None: ...
    def kpi_daily(self, date: str) -> KpiDaily | None: ...
    def purge(self, policy: RetentionPolicy, now_ts: float) -> dict[str, int]: ...
    def close(self) -> None: ...


@runtime_checkable
class Uplink(Protocol):
    mode: UplinkMode

    async def connect(self) -> None: ...
    async def send(self, batch: IngestBatch) -> IngestAck: ...
    async def close(self) -> None: ...
    @property
    def connected(self) -> bool: ...


@runtime_checkable
class LinkController(Protocol):
    @property
    def state(self) -> LinkState: ...
    def cut(self) -> None: ...
    def restore(self) -> None: ...
    def subscribe(self, cb: Callable[[LinkState], None]) -> None: ...


# ---------------------------------------------------------------------------
# cloud-side integrations
# ---------------------------------------------------------------------------


@runtime_checkable
class Notifier(Protocol):
    channel: str

    async def send(self, msg: OutboundMessage) -> DeliveryReceipt: ...


@runtime_checkable
class ErpClient(Protocol):
    source: str

    def stock_summary(self) -> dict[str, int]: ...
    def sales_today(self) -> dict[str, float]: ...  # {"sales_inr":.., "transactions":..}
    def post_stock_journal(self, adjustments: dict[str, int]) -> bool: ...
    def post_purchase_order(self, lines: list[ReorderSuggestion]) -> str: ...


@runtime_checkable
class OndcPublisher(Protocol):
    async def publish_availability(self, store_id: str, item_id: str, available: bool, qty: int | None) -> OndcAck: ...


@runtime_checkable
class CloudQueueForecaster(Protocol):
    def fit(self, history: "pd.DataFrame") -> FitReport: ...
    def predict(self, recent: "pd.DataFrame", now_ts: float) -> dict[str, float]: ...
    def report(self) -> FitReport | None: ...


@runtime_checkable
class CloudFootfallForecaster(Protocol):
    def fit(self, daily: "pd.DataFrame") -> FitReport: ...
    def predict_days(self, start_date: str, n: int) -> list[FootfallForecastDay]: ...


# History DataFrame contract (sim -> forecasting). Both sides code to these columns.
HISTORY_MINUTE_COLUMNS: tuple[str, ...] = (
    "ts",
    "store_id",
    "counter_id",
    "queue_count",
    "arrivals_pm",
    "service_pm",
    "footfall_in_15m",
    "occupancy",
    "hour",
    "dow",
    "minute_of_day",
    "is_festival",
    "festival_weight",
    "days_to_festival",
    "is_salary_week",
)
HISTORY_DAILY_COLUMNS: tuple[str, ...] = (
    "date",
    "store_id",
    "footfall_in",
    "transactions",
    "dow",
    "is_festival",
    "festival_weight",
    "days_to_festival",
    "is_salary_week",
    "rain_flag",
)

__all__ = [
    "HISTORY_DAILY_COLUMNS",
    "HISTORY_MINUTE_COLUMNS",
    "AnalyticsUpdate",
    "BBox",
    "CloudFootfallForecaster",
    "CloudQueueForecaster",
    "CoverageEstimator",
    "CoverageResult",
    "Crossing",
    "Detection",
    "Detector",
    "EdgeQueueForecaster",
    "EdgeStore",
    "ErpClient",
    "Frame",
    "FrameSource",
    "LinkController",
    "Notifier",
    "OndcPublisher",
    "PointMapper",
    "QueueAnalyzer",
    "RuleEngine",
    "ShelfStateMachine",
    "SkuIdentifier",
    "SourceError",
    "SyntheticControl",
    "Track",
    "Tracker",
    "Uplink",
    "ZoneEngine",
]
