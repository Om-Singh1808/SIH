"""Deterministic fakes for every Protocol in ``interfaces.py`` plus sample builders.

Why this module exists: fourteen agents build fourteen packages in parallel.
``registry.resolve()`` hands out these fakes whenever the real package is not
installed, so SenseEdge boots, SenseCloud ingests and every test suite runs with
*only* ``retailsense-contracts`` installed.  The fakes are intentionally small
but *behaviourally honest*:

* ``FakeFrameSource`` paints magenta shoppers on a floor-coloured canvas, so even
  the real HSV ``SyntheticDetector`` works on its frames.
* ``FakeDetector`` without a script detects those magenta blobs (pure numpy).
* ``InMemoryEdgeStore`` implements the *whole* ``EdgeStore`` protocol: seq/HLC
  stamping, outbox expiry/eviction policy, views and ``kpi_today``.
* ``FakeUplink`` acknowledges batches exactly like the cloud (idempotent on
  ``event_id``, per-device seq check) and can drop acks to test resend paths.

Everything is seeded or scripted - no randomness without an explicit ``seed``.
"""

import asyncio
import csv
import datetime as _dt
import json
import math
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from .alerts import (
    ACTIONS_BY_KIND,
    Alert,
    CameraAlertDetails,
    DeviceAlertDetails,
    FootfallAlertDetails,
    ImpactInr,
    QueueAlertDetails,
    ShrinkAlertDetails,
    StockoutAlert,
    SyncAlertDetails,
)
from .api import (
    Command,
    DeliveryReceipt,
    FitReport,
    FootfallForecastDay,
    HeatCell,
    HeatmapResponse,
    IngestAck,
    IngestBatch,
    KpiDaily,
    KpiToday,
    OndcAck,
    OutboundMessage,
    ReconcileReport,
    ReconcileRow,
    ReorderSuggestion,
    ScenarioStatus,
    ShelfStateView,
    SyncStatus,
)
from .clock import date_to_ts, day_start_ts, hour_bucket, store_date
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
    load_store_config,
)
from .enums import (
    AckAction,
    AckBy,
    AlertKind,
    AlertStatus,
    Direction,
    EventClass,
    LineKind,
    LinkState,
    Origin,
    Severity,
    ShelfState,
    UplinkMode,
    ZoneKind,
)
from .events import (
    EVENT_TYPES,
    AlertAcked,
    AlertRaised,
    AlertResolved,
    CameraHealth,
    ConfigApplied,
    DeviceHeartbeat,
    DwellSample,
    Event,
    FootfallCrossing,
    HeatmapTile,
    HeatmapTiles,
    Observation,
    OrderRequested,
    QueueForecast,
    QueueSnapshot,
    ShelfScan,
    ShelfStateChange,
    SimTruth,
    StockReconciled,
    ZoneOccupancy,
    make_event,
)
from .geometry import point_in_polygon, polygon_bbox, polygon_long_axis, side_of_line
from .hlc import HLC
from .i18n import action_labels, render
from .ids import new_ulid
from .impact import lost_sales, queue_abandon_risk, recovered, zero_impact
from .interfaces import (
    AnalyticsUpdate,
    CoverageResult,
    Crossing,
    Detection,
    Frame,
    Track,
)
from .manifest import ModelManifest
from .synthetic import SyntheticPalette
from .topics import EVICTABLE, expires_ts
from .version import VERSION

if TYPE_CHECKING:
    import pandas as pd

EXAMPLES_DIR = Path(__file__).resolve().parent / "examples"

# A fixed "now" for samples: 2026-08-23 17:00 IST (demo start_time on today's date at spec freeze).
SAMPLE_TS: float = date_to_ts("2026-08-23", "Asia/Kolkata", "17:00")


# ===========================================================================
# examples + sample builders
# ===========================================================================


def example_path(name: str) -> Path:
    return EXAMPLES_DIR / name


def sample_store_config() -> StoreConfig:
    """The canonical demo store (``examples/store_demo.yaml``)."""
    return load_store_config(example_path("store_demo.yaml"))


def sample_manifest() -> ModelManifest:
    with open(example_path("manifest_demo.json"), encoding="utf-8") as fh:
        return ModelManifest.model_validate(json.load(fh))


def load_festivals_csv(path: str | Path | None = None) -> list[dict[str, Any]]:
    """Rows of ``festivals_in.csv`` with ``weight`` as float and ``verified`` as bool."""
    p = Path(path) if path else example_path("festivals_in.csv")
    out: list[dict[str, Any]] = []
    with open(p, encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
            out.append(
                {
                    "date": row["date"],
                    "name": row["name"],
                    "region": row["region"],
                    "weight": float(row["weight"]),
                    "verified": row["verified"].strip().lower() in {"yes", "true", "1"},
                }
            )
    return out


def _sku(cfg: StoreConfig, sku_id: str = "AMUL-TAAZA-500") -> SKU:
    s = cfg.sku(sku_id)
    assert s is not None
    return s


def sample_alert(
    kind: AlertKind | str = AlertKind.SHELF_GAP,
    *,
    ts: float | None = None,
    cfg: StoreConfig | None = None,
    **overrides: Any,
) -> Alert:
    """A fully rendered Alert of ``kind`` for the demo store (hi + en text, impact, actions)."""
    kind = AlertKind(str(kind))
    cfg = cfg or sample_store_config()
    ts = SAMPLE_TS if ts is None else ts
    store_id, device_id = cfg.store.store_id, cfg.device.device_id
    impact: ImpactInr | None = None
    origin = Origin.EDGE
    severity = Severity.WARN
    params: dict[str, Any]
    if kind == AlertKind.SHELF_GAP:
        sku = _sku(cfg)
        gap_min = 3.0
        impact = lost_sales(sku, gap_min, cfg.impact)
        details: Any = StockoutAlert(
            shelf_id="shelf-A",
            sku_id=sku.sku_id,
            sku_name=sku.name_en,
            gap_minutes=gap_min,
            coverage=0.12,
            facings=0,
            min_facings=2,
            consecutive_empty_scans=3,
        )
        subject = "shelf-A"
        severity = Severity.HIGH
        params = {"sku_name": sku.name_en, "gap_min": gap_min, "lost_inr": impact.lost_sales_inr, "basis": impact.basis}
        params_hi = dict(params, sku_name=sku.name_hi)
    elif kind in (AlertKind.QUEUE_LONG, AlertKind.QUEUE_FORECAST):
        counter = cfg.counters[0]
        count, threshold = 6, cfg.rules.queue_long_count
        impact = queue_abandon_risk(count, threshold, cfg.impact)
        fc = 7.0 if kind == AlertKind.QUEUE_FORECAST else None
        details = QueueAlertDetails(
            counter_id=counter.counter_id,
            counter_name=counter.name,
            count=count,
            est_wait_s=count * counter.default_service_s,
            forecast=fc,
            horizon_min=15 if fc else None,
            threshold=threshold,
        )
        subject = counter.counter_id
        severity = Severity.WARN if kind == AlertKind.QUEUE_LONG else Severity.INFO
        params = {
            "counter_name": counter.name,
            "count": count,
            "wait_min": count * counter.default_service_s / 60,
            "risk_inr": impact.lost_sales_inr,
            "forecast": fc,
            "horizon": 15,
        }
        params_hi = params
    elif kind == AlertKind.CAMERA_DOWN:
        cam = cfg.cameras[0].camera_id
        details = CameraAlertDetails(camera_id=cam, status="stale", last_frame_age_s=20.0)
        subject = cam
        severity = Severity.HIGH
        params = {"camera_id": cam}
        params_hi = params
    elif kind == AlertKind.SYNC_BACKLOG:
        details = SyncAlertDetails(backlog=1200, down_since_ts=ts - 600)
        subject = device_id
        severity = Severity.INFO
        params = {"minutes": 10, "backlog": 1200}
        params_hi = params
    elif kind == AlertKind.DEVICE_OFFLINE:
        details = DeviceAlertDetails(device_id=device_id, last_seen_ts=ts - 120)
        subject = device_id
        origin = Origin.CLOUD
        severity = Severity.HIGH
        params = {"device_id": device_id, "since": store_date(ts - 120, cfg.store.tz)}
        params_hi = params
    elif kind == AlertKind.SHRINK_SUSPECT:
        sku = _sku(cfg)
        details = ShrinkAlertDetails(
            sku_id=sku.sku_id, sku_name=sku.name_en, visual_units=41, system_units=48, delta_units=7, delta_inr=189.0
        )
        subject = sku.sku_id
        origin = Origin.CLOUD
        params = {"sku_name": sku.name_en, "system_units": 48, "visual_units": 41, "delta_inr": 189.0}
        params_hi = dict(params, sku_name=sku.name_hi)
    elif kind == AlertKind.FOOTFALL_SPIKE:
        details = FootfallAlertDetails(count=30, baseline=10.0, factor=3.0)
        subject = "entrance"
        severity = Severity.INFO
        params = {"count": 30, "factor": 3.0}
        params_hi = params
    else:  # pragma: no cover
        raise ValueError(kind)
    alert = Alert(
        alert_id=new_ulid(ts),
        store_id=store_id,
        device_id=device_id,
        origin=origin,
        kind=kind,
        severity=severity,
        subject_id=subject,
        title_en=render(f"{kind}.title", "en", **params),
        title_hi=render(f"{kind}.title", "hi", **params_hi),
        message_en=render(f"{kind}.msg", "en", **params),
        message_hi=render(f"{kind}.msg", "hi", **params_hi),
        details=details,
        impact=impact,
        actions=list(ACTIONS_BY_KIND[kind]),
        raised_ts=ts,
    )
    if overrides:
        alert = alert.model_copy(update=overrides)
    return alert


def sample_payload(type_: str, *, ts: float | None = None, cfg: StoreConfig | None = None) -> Any:
    """A valid payload instance for any EventType."""
    cfg = cfg or sample_store_config()
    ts = SAMPLE_TS if ts is None else ts
    sku = _sku(cfg)
    if type_ == "footfall.crossing":
        return FootfallCrossing(line_id="entrance", line_kind=LineKind.ENTRANCE, direction=Direction.IN)
    if type_ == "zone.occupancy":
        return ZoneOccupancy(zone_id="aisle-1", zone_kind=ZoneKind.AISLE, count=2, window_s=10.0)
    if type_ == "dwell.sample":
        return DwellSample(zone_id="aisle-1", dwell_s=12.5, entered_ts=ts - 12.5, exited_ts=ts)
    if type_ == "heatmap.tiles":
        return HeatmapTiles(
            cell_px=20,
            width_cells=32,
            height_cells=18,
            tiles=[HeatmapTile(cell_x=10, cell_y=5, hour_bucket=hour_bucket(ts), dwell_s=4.5, visits=2)],
        )
    if type_ == "queue.snapshot":
        return QueueSnapshot(
            counter_id="counter-1",
            zone_id="queue-1",
            count=5,
            avg_dwell_s=90.0,
            max_dwell_s=180.0,
            arrival_rate_pm=1.4,
            service_rate_pm=1.3,
            est_wait_s=230.0,
            method="little_service",
            served_window=7,
            abandoned_window=1,
            window_s=300,
            served_total=40,
            abandoned_total=3,
            long_since_ts=ts - 70,
        )
    if type_ == "queue.forecast":
        return QueueForecast(
            counter_id="counter-1",
            made_ts=ts,
            horizons={"5": 5.2, "10": 6.1, "15": 7.0, "30": 6.4},
            model="edge_trend",
            mae_recent=0.8,
        )
    if type_ == "shelf.scan":
        return ShelfScan(
            shelf_id="shelf-A",
            sku_id=sku.sku_id,
            coverage=0.12,
            facings=1,
            capacity_facings=9,
            state_raw=ShelfState.EMPTY,
            occluded=False,
            method="classical",
        )
    if type_ == "shelf.state":
        return ShelfStateChange(
            shelf_id="shelf-A",
            sku_id=sku.sku_id,
            from_state=ShelfState.PARTIAL,
            to_state=ShelfState.EMPTY,
            gap_started_ts=ts - 90,
            gap_minutes=1.5,
            consecutive_empty_scans=3,
            impact=lost_sales(sku, 1.5, cfg.impact),
        )
    if type_ == "alert.raised":
        return AlertRaised(alert=sample_alert(AlertKind.SHELF_GAP, ts=ts, cfg=cfg))
    if type_ == "alert.acked":
        return AlertAcked(alert_id=new_ulid(ts), action=AckAction.RESTOCKED, by=AckBy.WHATSAPP_SIM, note=None)
    if type_ == "alert.resolved":
        return AlertResolved(
            alert_id=new_ulid(ts),
            reason="restocked_observed",
            final_gap_minutes=4.0,
            impact_final=lost_sales(sku, 4.0, cfg.impact),
            recovered=recovered(sku, 4.0, cfg.impact),
        )
    if type_ == "device.heartbeat":
        return DeviceHeartbeat(
            uptime_s=3600.0,
            fps=4.0,
            infer_ms_p50=9.5,
            infer_ms_p95=14.2,
            detector="synthetic",
            model_version="hsv-1.0",
            backlog=0,
            link=LinkState.UP,
            cameras=[
                CameraHealth(
                    camera_id=cfg.cameras[0].camera_id,
                    status="ok",
                    fps=4.0,
                    last_frame_age_s=0.25,
                    detector="synthetic",
                )
            ],
            contracts_version=VERSION,
            clock_factor=cfg.demo.clock_factor,
            sim_ts=ts,
            cpu_pct=23.0,
            mem_mb=310.0,
        )
    if type_ == "stock.reconciled":
        return StockReconciled(
            sku_id=sku.sku_id,
            shelf_id="shelf-A",
            visual_units=41,
            system_units=48,
            delta_units=-7,
            delta_inr=-189.0,
            source="mock",
        )
    if type_ == "order.requested":
        return OrderRequested(
            sku_id=sku.sku_id,
            qty=24,
            channel=AckBy.WHATSAPP_SIM,
            alert_id=new_ulid(ts),
            est_cost_inr=24 * sku.mrp_inr * 0.9,
        )
    if type_ == "config.applied":
        return ConfigApplied(config_version=cfg.config_version, config_hash=cfg.config_hash())
    if type_ == "sim.truth":
        return SimTruth(
            in_store=7,
            queue_counts={"counter-1": 3},
            shelf_units={"shelf-A": 12, "shelf-B": 36, "shelf-C": 10},
            shelf_facings={"shelf-A": 3, "shelf-B": 9, "shelf-C": 5},
            served_total=40,
            abandoned_total=3,
            footfall_in_total=55,
            scenario="baseline",
        )
    raise ValueError(f"unknown event type {type_!r}")


def sample_observation(type_: str, *, ts: float | None = None, cfg: StoreConfig | None = None) -> Observation:
    cfg = cfg or sample_store_config()
    ts = SAMPLE_TS if ts is None else ts
    return Observation(
        type=type_, ts=ts, camera_id=cfg.cameras[0].camera_id, payload=sample_payload(type_, ts=ts, cfg=cfg)
    )  # type: ignore[arg-type]


def sample_event(
    type_: str, *, seq: int = 1, ts: float | None = None, cfg: StoreConfig | None = None, **overrides: Any
) -> Event:
    """A valid, stamped Event of ``type_`` for the demo store/device."""
    cfg = cfg or sample_store_config()
    ts = SAMPLE_TS if ts is None else ts
    obs = sample_observation(type_, ts=ts, cfg=cfg)
    hlc = f"{int(ts * 1000):013d}-{seq % 10000:04d}-{cfg.device.device_id.replace('-', '_')}"
    ev = make_event(
        obs,
        store_id=cfg.store.store_id,
        device_id=cfg.device.device_id,
        seq=seq,
        hlc=hlc,
        created_ts=ts + 0.01,
        event_id=new_ulid(ts),
    )
    return ev.model_copy(update=overrides) if overrides else ev


def sample_events_all(*, cfg: StoreConfig | None = None) -> list[Event]:
    """One Event per EventType with consecutive seq - handy fixture for ingest tests."""
    cfg = cfg or sample_store_config()
    return [sample_event(t, seq=i + 1, ts=SAMPLE_TS + i, cfg=cfg) for i, t in enumerate(EVENT_TYPES)]


# ===========================================================================
# drawing helpers (pure numpy)
# ===========================================================================


def draw_rect(image: np.ndarray, bbox: tuple[float, float, float, float], colour: tuple[int, int, int]) -> None:
    """Fill an xyxy rectangle in-place (clipped to the image)."""
    h, w = image.shape[:2]
    x0, y0, x1, y1 = (int(round(v)) for v in bbox)
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    if x1 > x0 and y1 > y0:
        image[y0:y1, x0:x1] = colour


def draw_polygon_fill(image: np.ndarray, poly: list[list[float]], colour: tuple[int, int, int]) -> None:
    """Axis-aligned fill of a polygon's bbox (fakes only draw rectangles)."""
    x0, y0, x1, y1 = polygon_bbox(poly)
    draw_rect(image, (x0, y0, x1, y1), colour)


def magenta_mask(image: np.ndarray) -> np.ndarray:
    """Pixels that are SyntheticPalette.SHOPPER-ish (BGR test equivalent to the HSV window)."""
    b = image[..., 0].astype(np.int16)
    g = image[..., 1].astype(np.int16)
    r = image[..., 2].astype(np.int16)
    return (r >= 150) & (b >= 150) & (g <= 80)


def blobs_from_mask(mask: np.ndarray, min_area: int = 120) -> list[tuple[float, float, float, float]]:
    """Connected components (8-connected via run overlap) of a boolean mask -> xyxy boxes.

    Run-length union-find: O(runs), fast enough for 640x360 frames with a few shoppers.
    """
    parent: dict[int, int] = {}

    def find(a: int) -> int:
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    runs: list[tuple[int, int, int, int]] = []  # (y, x0, x1, label)
    prev: list[tuple[int, int, int]] = []
    next_label = 0
    h = mask.shape[0]
    for y in range(h):
        row = mask[y]
        if not row.any():
            prev = []
            continue
        padded = np.concatenate(([False], row, [False])).astype(np.int8)
        d = np.diff(padded)
        starts = np.flatnonzero(d == 1)
        ends = np.flatnonzero(d == -1)
        cur: list[tuple[int, int, int]] = []
        for x0, x1 in zip(starts.tolist(), ends.tolist(), strict=True):
            label = -1
            for px0, px1, pl in prev:
                if px0 <= x1 and x0 <= px1:  # touching counts (8-connectivity)
                    if label < 0:
                        label = find(pl)
                    else:
                        union(label, pl)
            if label < 0:
                label = next_label
                parent[label] = label
                next_label += 1
            cur.append((x0, x1, label))
            runs.append((y, x0, x1, label))
        prev = cur
    boxes: dict[int, list[float]] = {}
    for y, x0, x1, label in runs:
        root = find(label)
        box = boxes.get(root)
        if box is None:
            boxes[root] = [x0, y, x1, y + 1, x1 - x0]
        else:
            box[0] = min(box[0], x0)
            box[1] = min(box[1], y)
            box[2] = max(box[2], x1)
            box[3] = max(box[3], y + 1)
            box[4] += x1 - x0
    out = [(float(b[0]), float(b[1]), float(b[2]), float(b[3])) for b in boxes.values() if b[4] >= min_area]
    out.sort()
    return out


# ===========================================================================
# capture / perception fakes
# ===========================================================================


class FakeFrameSource:
    """Scripted frames: ``script=[(ts, [bbox, ...]), ...]`` draws magenta shoppers on a floor canvas.

    Frames beyond the script are empty (no shoppers) with ts continuing at ``fps``.
    ``background`` (optional) is an HxWx3 image to draw on instead of a plain floor,
    e.g. a rendered floorplan with shelves.
    """

    def __init__(
        self,
        n_frames: int = 10,
        size: tuple[int, int] = (640, 360),
        script: list[tuple[float, list[tuple[float, float, float, float]]]] | None = None,
        *,
        camera_id: str = "cam-fake",
        fps: float = 4.0,
        start_ts: float | None = None,
        background: np.ndarray | None = None,
    ):
        self.camera_id = camera_id
        self.n_frames = int(n_frames)
        self._size = (int(size[0]), int(size[1]))
        self.script = list(script or [])
        self._fps = float(fps)
        self._start_ts = SAMPLE_TS if start_ts is None else float(start_ts)
        self._background = background
        self._i = 0
        self._open = False

    # FrameSource -----------------------------------------------------------
    def open(self) -> None:
        self._open = True
        self._i = 0

    def read(self) -> Frame | None:
        if not self._open:
            self.open()
        if self._i >= self.n_frames:
            return None
        i = self._i
        self._i += 1
        w, h = self._size
        if self._background is not None:
            image = self._background.copy()
        else:
            image = np.empty((h, w, 3), dtype=np.uint8)
            image[:] = SyntheticPalette.FLOOR
        if i < len(self.script):
            ts, boxes = self.script[i]
            for bbox in boxes:
                draw_rect(image, bbox, SyntheticPalette.SHOPPER)
        else:
            ts = self._start_ts + i / self._fps
        return Frame(ts=float(ts), camera_id=self.camera_id, image=image, seq=i)

    def close(self) -> None:
        self._open = False

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    @property
    def nominal_fps(self) -> float:
        return self._fps

    def frames(self) -> list[Frame]:
        """Convenience: read everything."""
        self.open()
        out = []
        while (f := self.read()) is not None:
            out.append(f)
        return out


class FakeDetector:
    """Scripted detections per call index; with no script, detects magenta blobs in the image."""

    def __init__(
        self,
        script: list[list[Detection | tuple[float, float, float, float]]] | None = None,
        *,
        name: str = "fake",
        model_version: str = "fake-1.0",
        min_area: int = 120,
    ):
        self.name = name
        self.model_version = model_version
        self.script = script
        self.min_area = min_area
        self.calls = 0
        self.warmed = False

    def detect(self, image: np.ndarray) -> list[Detection]:
        idx = self.calls
        self.calls += 1
        if self.script is not None:
            if not self.script:
                return []
            frame = self.script[idx % len(self.script)]
            return [
                d if isinstance(d, Detection) else Detection(bbox=tuple(float(v) for v in d), conf=0.99) for d in frame
            ]  # type: ignore[arg-type]
        boxes = blobs_from_mask(magenta_mask(image), self.min_area)
        return [Detection(bbox=b, conf=0.99, cls=0) for b in boxes]

    def warmup(self) -> None:
        self.warmed = True


class FakeTracker:
    """Nearest-centroid tracker: ids never reused, confirmed after ``min_hits``."""

    def __init__(self, max_dist: float = 60.0, max_age: int = 5, min_hits: int = 1):
        self.max_dist = float(max_dist)
        self.max_age = int(max_age)
        self.min_hits = int(min_hits)
        self._tracks: dict[int, Track] = {}
        self._next_id = 1

    def reset(self) -> None:
        self._tracks.clear()
        self._next_id = 1

    @staticmethod
    def _centre(b: tuple[float, float, float, float]) -> tuple[float, float]:
        return ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)

    def update(self, detections: list[Detection], ts: float) -> list[Track]:
        unmatched = list(range(len(detections)))
        assigned: dict[int, int] = {}
        # greedy nearest-centroid matching, deterministic order by track id
        for tid in sorted(self._tracks):
            tr = self._tracks[tid]
            cx, cy = self._centre(tr.bbox)
            best, best_d = -1, self.max_dist
            for j in unmatched:
                dx, dy = self._centre(detections[j].bbox)
                d = math.hypot(dx - cx, dy - cy)
                if d < best_d:
                    best, best_d = j, d
            if best >= 0:
                unmatched.remove(best)
                assigned[tid] = best
        out: list[Track] = []
        for tid in sorted(self._tracks):
            tr = self._tracks[tid]
            if tid in assigned:
                det = detections[assigned[tid]]
                tr.bbox = tuple(float(v) for v in det.bbox)  # type: ignore[assignment]
                tr.conf = det.conf
                tr.hits += 1
                tr.time_since_update = 0
            else:
                tr.time_since_update += 1
            tr.age += 1
            tr.confirmed = tr.hits >= self.min_hits
            if tr.time_since_update > self.max_age:
                del self._tracks[tid]
            else:
                out.append(tr)
        for j in unmatched:
            det = detections[j]
            tr = Track(
                track_id=self._next_id,
                bbox=tuple(float(v) for v in det.bbox),
                conf=det.conf,
                age=1,
                hits=1,
                time_since_update=0,
                confirmed=1 >= self.min_hits,
            )  # type: ignore[arg-type]
            self._next_id += 1
            self._tracks[tr.track_id] = tr
            out.append(tr)
        return out


class IdentityMapper:
    """PointMapper where image pixels == floorplan pixels."""

    def to_floor(self, pts: np.ndarray) -> np.ndarray:
        return np.asarray(pts, dtype=np.float64).reshape(-1, 2).copy()

    def to_image(self, pts: np.ndarray) -> np.ndarray:
        return np.asarray(pts, dtype=np.float64).reshape(-1, 2).copy()


# ===========================================================================
# analytics fakes
# ===========================================================================

_DWELL_KINDS = {ZoneKind.AISLE, ZoneKind.CUSTOM, ZoneKind.QUEUE}


class FakeZoneEngine:
    """Zone membership, line crossings (normative side rule), dwell, occupancy and heat - no inertia/cooldown."""

    def __init__(
        self,
        camera: CameraConfig,
        zones: list[Zone],
        lines: list[Line],
        mapper: Any,
        rules: RulesConfig,
        floorplan: Floorplan,
    ):
        self.camera = camera
        self.zones = list(zones)
        self.lines = list(lines)
        self.mapper = mapper
        self.rules = rules
        self.floorplan = floorplan
        self._members: dict[str, dict[int, float]] = {z.zone_id: {} for z in zones}
        self._sides: dict[tuple[int, str], int] = {}
        self._heat: dict[tuple[int, int, int], list[float]] = {}
        self._last_cell: dict[int, tuple[int, int]] = {}
        self._last_ts: float | None = None
        self._last_occ_ts: float | None = None
        self._last_flush_ts: float | None = None
        self.in_total = 0
        self.out_total = 0

    def reload(self, zones: list[Zone], lines: list[Line]) -> None:
        self.zones = list(zones)
        self.lines = list(lines)
        for z in zones:
            self._members.setdefault(z.zone_id, {})

    @property
    def width_cells(self) -> int:
        return math.ceil(self.floorplan.width_px / self.floorplan.heat_cell_px)

    @property
    def height_cells(self) -> int:
        return math.ceil(self.floorplan.height_px / self.floorplan.heat_cell_px)

    def _flush_heat(self) -> HeatmapTiles:
        tiles = [
            HeatmapTile(cell_x=cx, cell_y=cy, hour_bucket=hb, dwell_s=round(v[0], 3), visits=int(v[1]))
            for (cx, cy, hb), v in sorted(self._heat.items())
        ]
        self._heat.clear()
        return HeatmapTiles(
            cell_px=self.floorplan.heat_cell_px,
            width_cells=self.width_cells,
            height_cells=self.height_cells,
            tiles=tiles,
        )

    def update(self, tracks: list[Track], ts: float) -> AnalyticsUpdate:
        upd = AnalyticsUpdate(ts=ts, camera_id=self.camera.camera_id)
        dt = 0.0 if self._last_ts is None else max(0.0, ts - self._last_ts)
        self._last_ts = ts
        if self._last_occ_ts is None:
            self._last_occ_ts = ts
        if self._last_flush_ts is None:
            self._last_flush_ts = ts
        present: set[int] = set()
        for tr in tracks:
            if not tr.confirmed:
                continue
            present.add(tr.track_id)
            pt = tr.anchor(self.camera.anchor)
            for z in self.zones:
                if point_in_polygon(pt, z.polygon):
                    self._members[z.zone_id].setdefault(tr.track_id, ts)
                    upd.zone_members.setdefault(z.zone_id, []).append(tr.track_id)
            for ln in self.lines:
                side = side_of_line(pt, (ln.start[0], ln.start[1]), (ln.end[0], ln.end[1]))
                key = (tr.track_id, ln.line_id)
                prev = self._sides.get(key)
                if side != 0:
                    if prev is not None and prev != side:
                        direction = Direction.IN if (prev == -1 and side == 1) else Direction.OUT
                        upd.crossings.append(
                            Crossing(
                                line_id=ln.line_id, line_kind=ln.kind, track_id=tr.track_id, direction=direction, ts=ts
                            )
                        )
                        upd.footfall.append(
                            FootfallCrossing(line_id=ln.line_id, line_kind=ln.kind, direction=direction)
                        )
                        if ln.kind == LineKind.ENTRANCE:
                            if direction == Direction.IN:
                                self.in_total += 1
                            else:
                                self.out_total += 1
                    self._sides[key] = side
            floor = self.mapper.to_floor(np.asarray([pt]))[0]
            cell = (int(floor[0] // self.floorplan.heat_cell_px), int(floor[1] // self.floorplan.heat_cell_px))
            hkey = (cell[0], cell[1], hour_bucket(ts))
            acc = self._heat.setdefault(hkey, [0.0, 0.0])
            acc[0] += dt
            if self._last_cell.get(tr.track_id) != cell:
                acc[1] += 1
                self._last_cell[tr.track_id] = cell
        # exits -> dwell samples
        for z in self.zones:
            members = self._members[z.zone_id]
            for tid in list(members):
                if tid not in upd.zone_members.get(z.zone_id, []):
                    entered = members.pop(tid)
                    if z.kind in _DWELL_KINDS and ts - entered >= 1.0:
                        upd.dwell_samples.append(
                            DwellSample(
                                zone_id=z.zone_id, dwell_s=round(ts - entered, 3), entered_ts=entered, exited_ts=ts
                            )
                        )
        for key in [k for k in self._sides if k[0] not in present]:
            del self._sides[key]
        for tid in [t for t in self._last_cell if t not in present]:
            del self._last_cell[tid]
        # occupancy cadence
        if ts - self._last_occ_ts >= self.rules.occupancy_interval_s or dt == 0.0:
            self._last_occ_ts = ts
            for z in self.zones:
                count = len(upd.zone_members.get(z.zone_id, []))
                if z.kind == ZoneKind.STORE:
                    count = max(count, self.in_total - self.out_total)
                upd.occupancy.append(
                    ZoneOccupancy(
                        zone_id=z.zone_id, zone_kind=z.kind, count=count, window_s=self.rules.occupancy_interval_s
                    )
                )
        if ts - self._last_flush_ts >= self.rules.heat_flush_s and self._heat:
            self._last_flush_ts = ts
            upd.heat = self._flush_heat()
        return upd

    def flush(self, ts: float) -> AnalyticsUpdate:
        upd = AnalyticsUpdate(ts=ts, camera_id=self.camera.camera_id)
        for z in self.zones:
            for _tid, entered in list(self._members[z.zone_id].items()):
                if z.kind in _DWELL_KINDS and ts - entered >= 1.0:
                    upd.dwell_samples.append(
                        DwellSample(zone_id=z.zone_id, dwell_s=round(ts - entered, 3), entered_ts=entered, exited_ts=ts)
                    )
            self._members[z.zone_id].clear()
        if self._heat:
            upd.heat = self._flush_heat()
        self._sides.clear()
        self._last_cell.clear()
        return upd


class FakeQueueAnalyzer:
    """Queue joins/served/abandoned from AnalyticsUpdates; est_wait via default service time."""

    def __init__(self, counter: Counter, rules: RulesConfig, day_start_ts: float):
        self.counter = counter
        self.rules = rules
        self.day_start_ts = day_start_ts
        self._entry: dict[int, float] = {}
        self._served: list[float] = []
        self._joins: list[float] = []
        self._abandoned: list[float] = []
        self.served_total = 0
        self.abandoned_total = 0
        self._last_snapshot: QueueSnapshot | None = None
        self._last_count = 0
        self._long_since: float | None = None

    def reset_day(self, day_start_ts: float) -> None:
        self.day_start_ts = day_start_ts
        self.served_total = 0
        self.abandoned_total = 0
        self._served.clear()
        self._joins.clear()
        self._abandoned.clear()
        self._last_snapshot = None

    def _window(self, xs: list[float], ts: float) -> int:
        return sum(1 for x in xs if ts - x <= self.rules.queue_window_s)

    def _snapshot(self, ts: float, count: int, dwells: list[float]) -> QueueSnapshot:
        w = self.rules.queue_window_s
        served_w = self._window(self._served, ts)
        elapsed_min = max(1.0, min(w, ts - self.day_start_ts) / 60.0)
        service_pm = served_w / elapsed_min
        arrival_pm = self._window(self._joins, ts) / elapsed_min
        if service_pm >= 0.2:
            est, method = count * 60.0 / service_pm, "little_service"
        else:
            est, method = count * self.counter.default_service_s, "default_service"
        if count >= self.rules.queue_long_count:
            if self._long_since is None:
                self._long_since = ts
        else:
            self._long_since = None
        return QueueSnapshot(
            counter_id=self.counter.counter_id,
            zone_id=self.counter.queue_zone_id,
            count=count,
            avg_dwell_s=round(sum(dwells) / len(dwells), 2) if dwells else 0.0,
            max_dwell_s=round(max(dwells), 2) if dwells else 0.0,
            arrival_rate_pm=round(arrival_pm, 3),
            service_rate_pm=round(service_pm, 3),
            est_wait_s=round(est, 1),
            method=method,  # type: ignore[arg-type]
            served_window=served_w,
            abandoned_window=self._window(self._abandoned, ts),
            window_s=w,
            served_total=self.served_total,
            abandoned_total=self.abandoned_total,
            long_since_ts=self._long_since,
        )

    def update(self, upd: AnalyticsUpdate) -> QueueSnapshot | None:
        ts = upd.ts
        members = set(upd.zone_members.get(self.counter.queue_zone_id, []))
        served_ids = {
            c.track_id
            for c in upd.crossings
            if c.line_id == self.counter.counter_line_id and c.direction == Direction.IN
        }
        for tid in members:
            if tid not in self._entry:
                self._entry[tid] = ts
                self._joins.append(ts)
        for tid in served_ids:
            if tid in self._entry:
                del self._entry[tid]
            self._served.append(ts)
            self.served_total += 1
        left_now = False
        for tid in list(self._entry):
            if tid not in members and tid not in served_ids:
                age = ts - self._entry.pop(tid)
                if age >= self.rules.queue_min_age_s:
                    self._abandoned.append(ts)
                    self.abandoned_total += 1
                    left_now = True
        count = len(self._entry)
        dwells = [ts - t0 for t0 in self._entry.values()]
        due = (
            self._last_snapshot is None
            or ts - self._last_snapshot_ts >= self.rules.snapshot_interval_s
            or abs(count - self._last_count) >= 2
            or bool(served_ids)
            or left_now
        )
        if not due:
            return None
        snap = self._snapshot(ts, count, dwells)
        self._last_snapshot = snap
        self._last_snapshot_ts = ts
        self._last_count = count
        return snap

    _last_snapshot_ts: float = 0.0

    def state(self) -> QueueSnapshot:
        if self._last_snapshot is not None:
            return self._last_snapshot
        return self._snapshot(self.day_start_ts, 0, [])


class FakeEdgeForecaster:
    """Persistence forecast (all horizons = last count); cloud forecast overrides when set."""

    HORIZONS = ("5", "10", "15", "30")

    def __init__(self, cloud_ttl_s: float = 120.0):
        self._last: QueueSnapshot | None = None
        self._cloud: QueueForecast | None = None
        self._cloud_set_ts: float | None = None
        self.cloud_ttl_s = cloud_ttl_s
        self._history: list[tuple[float, int]] = []

    def observe(self, snap: QueueSnapshot) -> None:
        self._last = snap
        self._history.append((time.time(), snap.count))
        self._history = self._history[-200:]

    def predict(self, ts: float) -> QueueForecast | None:
        if (
            self._cloud is not None
            and self._cloud_set_ts is not None
            and time.time() - self._cloud_set_ts < self.cloud_ttl_s
        ):
            return self._cloud
        if self._last is None:
            return None
        return QueueForecast(
            counter_id=self._last.counter_id,
            made_ts=ts,
            horizons={h: float(self._last.count) for h in self.HORIZONS},
            model="edge_trend",
            mae_recent=None,
        )

    def set_cloud_forecast(self, fc: QueueForecast) -> None:
        self._cloud = fc
        self._cloud_set_ts = time.time()


# ===========================================================================
# shelf fakes
# ===========================================================================


class FakeCoverageEstimator:
    """Backing-colour difference along the shelf's long axis. Works on palette-rendered shelves."""

    def __init__(
        self, backing_bgr: tuple[int, int, int] | None = None, diff_tau: int = 40, covered_col_frac: float = 0.35
    ):
        self.backing = tuple(int(v) for v in (backing_bgr or SyntheticPalette.SHELF_BACKING))
        self.diff_tau = diff_tau
        self.covered_col_frac = covered_col_frac

    def _raw(self, image: np.ndarray, shelf: ShelfPolygon, backing: tuple[int, ...]) -> tuple[float, np.ndarray]:
        x0, y0, x1, y1 = polygon_bbox(shelf.polygon)
        h, w = image.shape[:2]
        crop = image[max(0, y0) : min(h, y1), max(0, x0) : min(w, x1)].astype(np.int16)
        if crop.size == 0:
            return 0.0, np.zeros(0)
        diff = np.abs(crop - np.asarray(backing, dtype=np.int16)).sum(axis=2)
        product = diff > self.diff_tau
        axis = 0 if polygon_long_axis(shelf.polygon) == "x" else 1
        profile = product.mean(axis=axis)  # per column (x-axis shelf) or per row (y-axis shelf)
        covered = profile > self.covered_col_frac
        return float(covered.mean()), profile.astype(np.float64)

    def calibrate(self, image: np.ndarray, shelf: ShelfPolygon) -> ShelfReference:
        raw, profile = self._raw(image, shelf, self.backing)
        return ShelfReference(
            shelf_id=shelf.shelf_id,
            calibrated_ts=time.time(),
            raw_coverage_full=max(raw, 0.05),
            backing_bgr=list(self.backing),
            profile=[round(float(v), 3) for v in profile],
            method="fake",
        )

    def estimate(self, image: np.ndarray, shelf: ShelfPolygon, ref: ShelfReference | None) -> CoverageResult:
        backing = tuple(ref.backing_bgr) if ref is not None and ref.backing_bgr else self.backing
        raw, _ = self._raw(image, shelf, backing)
        full = max(ref.raw_coverage_full, 0.05) if ref is not None else 1.0
        coverage = float(min(1.0, max(0.0, raw / full)))
        facings = int(round(coverage * shelf.capacity_facings))
        return CoverageResult(
            coverage=round(coverage, 3),
            facings=facings,
            raw_coverage=round(raw, 3),
            method="fake",
            debug={"full": full},
        )


class FakeShelfStateMachine:
    """Persistence-filtered shelf states: ``persistence_scans`` consecutive empty scans confirm a gap."""

    def __init__(self, shelves: list[ShelfPolygon], skus: list[SKU], rules: RulesConfig, impact: ImpactConfig):
        self.shelves = {s.shelf_id: s for s in shelves}
        self.skus = {s.sku_id: s for s in skus}
        self.rules = rules
        self.impact = impact
        self._st: dict[str, dict[str, Any]] = {}
        self._day_start: float | None = None
        for s in shelves:
            self._st[s.shelf_id] = {
                "state": ShelfState.UNKNOWN,
                "coverage": 0.0,
                "facings": 0,
                "consecutive_empty_scans": 0,
                "fp_count": 0,
                "gap_started_ts": None,
                "last_scan_ts": None,
                "gap_minutes_today": 0.0,
                "occluded": False,
                "reference": s.reference,
            }

    # -- helpers ---------------------------------------------------------
    def _required(self, st: dict[str, Any]) -> int:
        return min(self.rules.persistence_scans + st["fp_count"], self.rules.max_persistence_scans)

    def _raw_state(self, scan: ShelfScan, shelf: ShelfPolygon) -> ShelfState:
        if scan.coverage >= self.rules.shelf_partial_coverage and scan.facings >= shelf.min_facings:
            return ShelfState.STOCKED
        if scan.coverage > self.rules.shelf_empty_coverage and scan.facings >= shelf.min_facings:
            return ShelfState.PARTIAL
        return ShelfState.EMPTY

    def _impact(self, shelf: ShelfPolygon, gap_minutes: float) -> ImpactInr:
        sku = self.skus.get(shelf.sku_id or "")
        return lost_sales(sku, gap_minutes, self.impact) if sku else zero_impact(self.impact)

    # -- protocol --------------------------------------------------------
    def apply(self, scan: ShelfScan, ts: float) -> ShelfStateChange | None:
        shelf = self.shelves.get(scan.shelf_id)
        if shelf is None:
            return None
        st = self._st[scan.shelf_id]
        if self._day_start is None:
            self._day_start = day_start_ts(ts)
        if scan.occluded:
            st["occluded"] = True
            return None
        st["occluded"] = False
        st["coverage"], st["facings"], st["last_scan_ts"] = scan.coverage, scan.facings, ts
        raw = self._raw_state(scan, shelf)
        prev: ShelfState = st["state"]
        if raw == ShelfState.EMPTY:
            st["consecutive_empty_scans"] += 1
            if st["gap_started_ts"] is None:
                st["gap_started_ts"] = ts
            if prev != ShelfState.EMPTY and st["consecutive_empty_scans"] >= self._required(st):
                st["state"] = ShelfState.EMPTY
                gap_min = (ts - st["gap_started_ts"]) / 60.0
                return ShelfStateChange(
                    shelf_id=shelf.shelf_id,
                    sku_id=shelf.sku_id,
                    from_state=prev,
                    to_state=ShelfState.EMPTY,
                    gap_started_ts=st["gap_started_ts"],
                    gap_minutes=round(gap_min, 2),
                    consecutive_empty_scans=st["consecutive_empty_scans"],
                    impact=self._impact(shelf, gap_min),
                )
            return None
        # stocked / partial
        st["consecutive_empty_scans"] = 0
        gap_started = st["gap_started_ts"]
        st["gap_started_ts"] = None
        if prev == ShelfState.EMPTY and gap_started is not None:
            gap_min = (ts - gap_started) / 60.0
            st["gap_minutes_today"] += gap_min
            st["state"] = raw
            return ShelfStateChange(
                shelf_id=shelf.shelf_id,
                sku_id=shelf.sku_id,
                from_state=prev,
                to_state=raw,
                gap_started_ts=gap_started,
                gap_minutes=round(gap_min, 2),
                consecutive_empty_scans=0,
                impact=self._impact(shelf, gap_min),
            )
        if prev != raw:
            st["state"] = raw
            return ShelfStateChange(
                shelf_id=shelf.shelf_id,
                sku_id=shelf.sku_id,
                from_state=prev,
                to_state=raw,
                consecutive_empty_scans=0,
                impact=None,
            )
        return None

    def view(self, shelf_id: str) -> ShelfStateView:
        shelf = self.shelves[shelf_id]
        st = self._st[shelf_id]
        sku = self.skus.get(shelf.sku_id or "")
        gap_min = None
        impact_open = None
        if st["state"] == ShelfState.EMPTY and st["gap_started_ts"] is not None and st["last_scan_ts"] is not None:
            gap_min = round((st["last_scan_ts"] - st["gap_started_ts"]) / 60.0, 2)
            impact_open = self._impact(shelf, gap_min)
        return ShelfStateView(
            shelf_id=shelf_id,
            name=shelf.name,
            sku_id=shelf.sku_id,
            sku_name=sku.name_en if sku else shelf.name,
            state=st["state"],
            coverage=st["coverage"],
            facings=st["facings"],
            capacity_facings=shelf.capacity_facings,
            min_facings=shelf.min_facings,
            consecutive_empty_scans=st["consecutive_empty_scans"],
            persistence_required=self._required(st),
            gap_started_ts=st["gap_started_ts"],
            gap_minutes=gap_min,
            last_scan_ts=st["last_scan_ts"],
            occluded=st["occluded"],
            impact_open=impact_open,
            has_reference=st["reference"] is not None,
        )

    def views(self) -> list[ShelfStateView]:
        return [self.view(sid) for sid in self.shelves]

    def feedback_false_positive(self, shelf_id: str) -> int:
        st = self._st[shelf_id]
        st["fp_count"] += 1
        st["consecutive_empty_scans"] = 0
        st["gap_started_ts"] = None
        if st["state"] == ShelfState.EMPTY:
            st["state"] = ShelfState.UNKNOWN
        return self._required(st)

    def restore(self, rows: list[dict]) -> None:
        for row in rows:
            sid = row.get("shelf_id")
            if sid in self._st:
                st = self._st[sid]
                for k in (
                    "coverage",
                    "facings",
                    "consecutive_empty_scans",
                    "fp_count",
                    "gap_started_ts",
                    "last_scan_ts",
                    "gap_minutes_today",
                ):
                    if k in row and row[k] is not None:
                        st[k] = row[k]
                if row.get("state"):
                    st["state"] = ShelfState(row["state"])
                if row.get("reference"):
                    ref = row["reference"]
                    st["reference"] = ref if isinstance(ref, ShelfReference) else ShelfReference.model_validate(ref)

    def set_reference(self, ref: ShelfReference) -> None:
        if ref.shelf_id in self._st:
            self._st[ref.shelf_id]["reference"] = ref

    def gap_minutes_today(self, ts: float) -> float:
        total = 0.0
        for st in self._st.values():
            total += st["gap_minutes_today"]
            if st["state"] == ShelfState.EMPTY and st["gap_started_ts"] is not None:
                total += max(0.0, ts - st["gap_started_ts"]) / 60.0
        return round(total, 2)

    def osa_pct(self, ts: float) -> float:
        if not self._st:
            return 100.0
        start = self._day_start if self._day_start is not None else day_start_ts(ts)
        minutes = max(1.0, (ts - start) / 60.0)
        return round(100.0 * max(0.0, 1.0 - self.gap_minutes_today(ts) / (len(self._st) * minutes)), 2)


class FakeSkuIdentifier:
    backend = "fake"

    def __init__(self) -> None:
        self.enrolled: dict[str, int] = {}

    def enrol(self, sku_id: str, images: list[np.ndarray]) -> int:
        self.enrolled[sku_id] = self.enrolled.get(sku_id, 0) + len(images)
        return self.enrolled[sku_id]

    def identify(self, crop: np.ndarray, hint_sku_id: str | None) -> tuple[str | None, float]:
        return (hint_sku_id, 1.0 if hint_sku_id else 0.0)


def fake_shelf_thumbnail(image: np.ndarray, shelf: ShelfPolygon) -> str | None:
    """No JPEG encoder without cv2: fakes return None (a valid value per the contract)."""
    return None


def fake_annotate_frame(frame: np.ndarray, tracks: list[Track], cfg_view: dict, *, blur_people: bool) -> np.ndarray:
    """Draw 2-px boxes around tracks (and pixelate their interiors when ``blur_people``)."""
    out = frame.copy()
    for tr in tracks:
        x0, y0, x1, y1 = (int(round(v)) for v in tr.bbox)
        if blur_people and x1 > x0 and y1 > y0:
            region = out[max(0, y0) : y1, max(0, x0) : x1]
            if region.size:
                region[:] = region.reshape(-1, 3).mean(axis=0).astype(np.uint8)
        for x in range(max(0, x0), min(out.shape[1], x1)):
            for yy in (y0, y1 - 1):
                if 0 <= yy < out.shape[0]:
                    out[yy, x] = (0, 255, 0)
    return out


def fake_render_floorplan(cfg: StoreConfig, *, with_zones: bool = True) -> np.ndarray:
    """Floor-coloured canvas with shelf backings, counter and (optionally) zone outlines."""
    fp = cfg.floorplan
    img = np.empty((fp.height_px, fp.width_px, 3), dtype=np.uint8)
    img[:] = SyntheticPalette.FLOOR
    for s in cfg.shelves:
        draw_polygon_fill(img, s.polygon, SyntheticPalette.SHELF_BACKING)
    for z in cfg.zones:
        if z.kind == ZoneKind.COUNTER:
            draw_polygon_fill(img, z.polygon, SyntheticPalette.COUNTER)
    return img


# ===========================================================================
# rules fake
# ===========================================================================


class FakeRuleEngine:
    """Minimal RuleEngine: one open alert per (kind, subject), i18n rendering, impact from contracts.impact."""

    def __init__(self, cfg: StoreConfig):
        self.cfg = cfg
        self._alerts: dict[str, Alert] = {}
        self.feedback: Callable[[str], Any] | None = None  # called with shelf_id on false_positive

    # helpers ---------------------------------------------------------------
    def _open(self, kind: AlertKind, subject: str) -> Alert | None:
        for a in self._alerts.values():
            if a.kind == kind and a.subject_id == subject and a.status != AlertStatus.RESOLVED:
                return a
        return None

    def _raise(
        self,
        kind: AlertKind,
        subject: str,
        severity: Severity,
        details: Any,
        impact: ImpactInr | None,
        params_en: dict,
        params_hi: dict,
        ts: float,
        origin: Origin = Origin.EDGE,
    ) -> list[Observation]:
        if self._open(kind, subject) is not None:
            return []
        a = Alert(
            alert_id=new_ulid(ts),
            store_id=self.cfg.store.store_id,
            device_id=self.cfg.device.device_id,
            origin=origin,
            kind=kind,
            severity=severity,
            subject_id=subject,
            title_en=render(f"{kind}.title", "en", **params_en),
            title_hi=render(f"{kind}.title", "hi", **params_hi),
            message_en=render(f"{kind}.msg", "en", **params_en),
            message_hi=render(f"{kind}.msg", "hi", **params_hi),
            details=details,
            impact=impact,
            actions=list(ACTIONS_BY_KIND[kind]),
            raised_ts=ts,
        )
        self._alerts[a.alert_id] = a
        return [Observation.of(AlertRaised(alert=a), ts)]

    def _resolve(self, a: Alert, reason: str, ts: float, **extra: Any) -> list[Observation]:
        a.status = AlertStatus.RESOLVED
        a.resolved_ts = ts
        return [Observation.of(AlertResolved(alert_id=a.alert_id, reason=reason, **extra), ts)]  # type: ignore[arg-type]

    # protocol --------------------------------------------------------------
    def on_shelf_change(self, ch: ShelfStateChange, view: ShelfStateView, ts: float) -> list[Observation]:
        sku = self.cfg.sku(ch.sku_id)
        if ch.to_state == ShelfState.EMPTY:
            gap = ch.gap_minutes or 0.0
            impact = ch.impact or (lost_sales(sku, gap, self.cfg.impact) if sku else zero_impact(self.cfg.impact))
            details = StockoutAlert(
                shelf_id=ch.shelf_id,
                sku_id=ch.sku_id,
                sku_name=view.sku_name,
                gap_minutes=gap,
                coverage=view.coverage,
                facings=view.facings,
                min_facings=view.min_facings,
                consecutive_empty_scans=ch.consecutive_empty_scans,
            )
            pe = {
                "sku_name": sku.name_en if sku else view.sku_name,
                "gap_min": gap,
                "lost_inr": impact.lost_sales_inr,
                "basis": impact.basis,
            }
            ph = dict(pe, sku_name=sku.name_hi if sku else view.sku_name)
            return self._raise(AlertKind.SHELF_GAP, ch.shelf_id, Severity.HIGH, details, impact, pe, ph, ts)
        a = self._open(AlertKind.SHELF_GAP, ch.shelf_id)
        if a is not None and ch.from_state == ShelfState.EMPTY:
            gap = ch.gap_minutes or 0.0
            rec = recovered(sku, gap, self.cfg.impact) if (sku and a.ack_action == AckAction.RESTOCKED) else None
            return self._resolve(
                a, "restocked_observed", ts, final_gap_minutes=gap, impact_final=ch.impact, recovered=rec
            )
        return []

    def on_queue(self, snap: QueueSnapshot, forecast: QueueForecast | None, ts: float) -> list[Observation]:
        counter = self.cfg.counter(snap.counter_id)
        name = counter.name if counter else snap.counter_id
        threshold = self.cfg.rules.queue_long_count
        out: list[Observation] = []
        if (
            snap.count >= threshold
            and snap.long_since_ts is not None
            and ts - snap.long_since_ts >= self.cfg.rules.queue_long_s
        ):
            impact = queue_abandon_risk(snap.count, threshold, self.cfg.impact)
            details = QueueAlertDetails(
                counter_id=snap.counter_id,
                counter_name=name,
                count=snap.count,
                est_wait_s=snap.est_wait_s,
                threshold=threshold,
            )
            p = {
                "counter_name": name,
                "count": snap.count,
                "wait_min": snap.est_wait_s / 60.0,
                "risk_inr": impact.lost_sales_inr,
            }
            sev = Severity.CRITICAL if counter and snap.count >= counter.max_queue else Severity.WARN
            out += self._raise(AlertKind.QUEUE_LONG, snap.counter_id, sev, details, impact, p, p, ts)
        elif snap.count < threshold - 1:
            a = self._open(AlertKind.QUEUE_LONG, snap.counter_id)
            if a is not None:
                out += self._resolve(a, "condition_cleared", ts)
        if forecast is not None:
            h = str(self.cfg.rules.queue_forecast_horizon_min)
            fc = forecast.horizons.get(h)
            if (
                fc is not None
                and fc >= self.cfg.rules.queue_forecast_threshold
                and self._open(AlertKind.QUEUE_LONG, snap.counter_id) is None
            ):
                details = QueueAlertDetails(
                    counter_id=snap.counter_id,
                    counter_name=name,
                    count=snap.count,
                    est_wait_s=snap.est_wait_s,
                    forecast=fc,
                    horizon_min=int(h),
                    threshold=self.cfg.rules.queue_forecast_threshold,
                )
                p = {"counter_name": name, "forecast": round(fc), "horizon": int(h)}
                out += self._raise(AlertKind.QUEUE_FORECAST, snap.counter_id, Severity.INFO, details, None, p, p, ts)
            elif fc is not None and fc < self.cfg.rules.queue_forecast_threshold - 1:
                a = self._open(AlertKind.QUEUE_FORECAST, snap.counter_id)
                if a is not None:
                    out += self._resolve(a, "condition_cleared", ts)
        return out

    def on_health(self, hb: DeviceHeartbeat, ts: float) -> list[Observation]:
        out: list[Observation] = []
        for cam in hb.cameras:
            if cam.status in ("stale", "black", "error"):
                details = CameraAlertDetails(
                    camera_id=cam.camera_id, status=cam.status, last_frame_age_s=cam.last_frame_age_s
                )
                p = {"camera_id": cam.camera_id}
                out += self._raise(AlertKind.CAMERA_DOWN, cam.camera_id, Severity.HIGH, details, None, p, p, ts)
            else:
                a = self._open(AlertKind.CAMERA_DOWN, cam.camera_id)
                if a is not None:
                    out += self._resolve(a, "device_back", ts)
        return out

    def on_sync(self, sync: SyncStatus, ts: float) -> list[Observation]:
        subject = self.cfg.device.device_id
        if sync.link == LinkState.DOWN and sync.down_since_ts is not None:
            down_for = ts - sync.down_since_ts
            if down_for >= self.cfg.rules.sync_backlog_after_s and sync.backlog >= self.cfg.rules.sync_backlog_warn:
                details = SyncAlertDetails(backlog=sync.backlog, down_since_ts=sync.down_since_ts)
                p = {"minutes": round(down_for / 60.0), "backlog": sync.backlog}
                return self._raise(AlertKind.SYNC_BACKLOG, subject, Severity.INFO, details, None, p, p, ts)
            return []
        a = self._open(AlertKind.SYNC_BACKLOG, subject)
        return self._resolve(a, "condition_cleared", ts) if a is not None else []

    def on_ack(self, alert_id: str, action: AckAction, by: AckBy, ts: float) -> list[Observation]:
        a = self._alerts.get(alert_id)
        if a is None:
            return []
        a.ack_action, a.ack_by, a.acked_ts = action, by, ts
        out = [Observation.of(AlertAcked(alert_id=alert_id, action=action, by=by), ts)]
        if a.status == AlertStatus.OPEN:
            a.status = AlertStatus.ACKED
        if action == AckAction.FALSE_POSITIVE:
            out += self._resolve(a, "false_positive", ts)
            if self.feedback is not None and a.kind == AlertKind.SHELF_GAP:
                self.feedback(a.subject_id)
        elif (
            action == AckAction.ORDER
            and a.kind == AlertKind.SHELF_GAP
            and isinstance(a.details, StockoutAlert)
            and a.details.sku_id
        ):
            sku = self.cfg.sku(a.details.sku_id)
            if sku:
                qty = int(math.ceil(sku.velocity_units_per_hr * 14 * sku.lead_time_days))
                out.append(
                    Observation.of(
                        OrderRequested(
                            sku_id=sku.sku_id,
                            qty=qty,
                            channel=by,
                            alert_id=alert_id,
                            est_cost_inr=round(qty * sku.mrp_inr * (1 - sku.margin_pct / 100), 2),
                        ),
                        ts,
                    )
                )
        elif action in (AckAction.IGNORE, AckAction.CHECKED):
            out += self._resolve(a, "condition_cleared", ts)
        return out

    def open_alerts(self) -> list[Alert]:
        return [a for a in self._alerts.values() if a.status != AlertStatus.RESOLVED]

    def get(self, alert_id: str) -> Alert | None:
        return self._alerts.get(alert_id)

    def restore(self, alerts: list[Alert]) -> None:
        for a in alerts:
            self._alerts[a.alert_id] = a


# ===========================================================================
# in-memory EdgeStore
# ===========================================================================


class InMemoryEdgeStore:
    """Dict-backed EdgeStore implementing the full protocol with the real semantics:

    * ``append`` stamps seq (from 1, gap-free), HLC, ULID and creates one outbox row per event
      with the class expiry from ``topics.EXPIRY_S``;
    * ``pending`` returns unsent, unexpired, unevicted rows in id order;
    * ``evict_overflow`` only touches TELEMETRY/AGGREGATE; ``expire`` never touches ALERT/TXN;
    * ``kpi_today`` is computed from stored events/alerts/shelves/queues.
    """

    def __init__(self, cfg: StoreConfig, *, clock: Callable[[], float] | None = None):
        self.cfg = cfg
        self.store_id = cfg.store.store_id
        self.device_id = cfg.device.device_id
        self.tz = cfg.store.tz
        self._clock = clock or time.time
        self._hlc = HLC(self.device_id)
        self._seq_next = 1
        self.events: dict[str, Event] = {}
        self.outbox: list[dict[str, Any]] = []
        self._outbox_next = 1
        self._state: dict[str, str] = {}
        self._alerts: dict[str, Alert] = {}
        self._shelves: dict[str, dict[str, Any]] = {}
        self._queues: dict[str, dict[str, Any]] = {}
        self._heat: dict[tuple[str, int, int, int], list[float]] = {}
        self._kpi_daily: dict[str, KpiDaily] = {}
        self.closed = False

    # -- append / outbox ----------------------------------------------------
    def append(self, observations: list[Observation]) -> list[Event]:
        now = self._clock()
        out: list[Event] = []
        for obs in observations:
            ev = make_event(
                obs,
                store_id=self.store_id,
                device_id=self.device_id,
                seq=self._seq_next,
                hlc=self._hlc.now(),
                created_ts=now,
            )
            self._seq_next += 1
            self.events[ev.event_id] = ev
            self.outbox.append(
                {
                    "id": self._outbox_next,
                    "event_id": ev.event_id,
                    "cls": str(ev.cls),
                    "enqueued_ts": now,
                    "expires_ts": expires_ts(ev.cls, now),
                    "sent_ts": None,
                    "attempts": 0,
                    "last_error": None,
                    "evicted_ts": None,
                }
            )
            self._outbox_next += 1
            out.append(ev)
        self._state["seq_next"] = str(self._seq_next)
        self._state["hlc_last"] = self._hlc.last
        return out

    def _pending_rows(self) -> list[dict[str, Any]]:
        return [r for r in self.outbox if r["sent_ts"] is None and r["evicted_ts"] is None]

    def pending(self, limit: int) -> list[tuple[int, Event]]:
        rows = self._pending_rows()[: max(0, int(limit))]
        return [(r["id"], self.events[r["event_id"]]) for r in rows]

    def _by_id(self, ids: list[int]) -> list[dict[str, Any]]:
        wanted = set(ids)
        return [r for r in self.outbox if r["id"] in wanted]

    def mark_sent(self, outbox_ids: list[int], ts: float) -> None:
        for r in self._by_id(outbox_ids):
            r["sent_ts"] = ts

    def mark_failed(self, outbox_ids: list[int], error: str) -> None:
        for r in self._by_id(outbox_ids):
            r["attempts"] += 1
            r["last_error"] = error

    def backlog(self) -> dict[str, int]:
        counts = {str(c): 0 for c in EventClass}
        for r in self._pending_rows():
            counts[r["cls"]] += 1
        return counts

    def evict_overflow(self, max_rows: int) -> int:
        pending = self._pending_rows()
        excess = len(pending) - int(max_rows)
        if excess <= 0:
            return 0
        now = self._clock()
        evicted = 0
        for r in pending:  # oldest first
            if excess <= 0:
                break
            if EventClass(r["cls"]) in EVICTABLE:
                r["evicted_ts"] = now
                evicted += 1
                excess -= 1
        return evicted

    def expire(self, now_ts: float) -> int:
        n = 0
        for r in self._pending_rows():
            if r["expires_ts"] is not None and r["expires_ts"] <= now_ts:
                r["evicted_ts"] = now_ts
                n += 1
        return n

    # -- state --------------------------------------------------------------
    def get_state(self, key: str, default: str | None = None) -> str | None:
        return self._state.get(key, default)

    def set_state(self, key: str, value: str) -> None:
        self._state[key] = str(value)

    # -- alerts -------------------------------------------------------------
    def upsert_alert(self, a: Alert) -> None:
        self._alerts[a.alert_id] = a.model_copy(deep=True)

    def alerts(self, status: AlertStatus | None, limit: int = 100) -> list[Alert]:
        rows = [a for a in self._alerts.values() if status is None or a.status == status]
        rows.sort(key=lambda a: a.raised_ts, reverse=True)
        return rows[:limit]

    def alert(self, alert_id: str) -> Alert | None:
        return self._alerts.get(alert_id)

    # -- shelves / queues / heat -------------------------------------------
    def upsert_shelf(self, v: ShelfStateView, reference: ShelfReference | None) -> None:
        prev = self._shelves.get(v.shelf_id, {})
        row = v.model_dump(mode="json")
        row["fp_count"] = max(0, v.persistence_required - self.cfg.rules.persistence_scans)
        row["gap_minutes_today"] = prev.get("gap_minutes_today", 0.0)
        row["reference"] = reference.model_dump(mode="json") if reference is not None else prev.get("reference")
        self._shelves[v.shelf_id] = row

    def shelves(self) -> list[dict]:
        return [dict(r) for r in self._shelves.values()]

    def upsert_queue(self, counter_id: str, snap: QueueSnapshot | None, fc: QueueForecast | None) -> None:
        row = self._queues.setdefault(
            counter_id, {"counter_id": counter_id, "snapshot": None, "forecast": None, "updated_ts": None}
        )
        if snap is not None:
            row["snapshot"] = snap.model_dump(mode="json")
        if fc is not None:
            row["forecast"] = fc.model_dump(mode="json")
        row["updated_ts"] = self._clock()

    def queues(self) -> list[dict]:
        return [dict(r) for r in self._queues.values()]

    def heat_add(self, camera_id: str, tiles: HeatmapTiles) -> None:
        for t in tiles.tiles:
            acc = self._heat.setdefault((camera_id, t.cell_x, t.cell_y, t.hour_bucket), [0.0, 0.0])
            acc[0] += t.dwell_s
            acc[1] += t.visits

    def heat_query(self, camera_id: str | None, from_ts: float, to_ts: float) -> HeatmapResponse:
        cell_px = self.cfg.floorplan.heat_cell_px
        agg: dict[tuple[int, int], list[float]] = {}
        lo, hi = int(from_ts // 3600), int(to_ts // 3600)
        for (cam, cx, cy, hb), (dwell, visits) in self._heat.items():
            if camera_id is not None and cam != camera_id:
                continue
            if not (lo <= hb <= hi):
                continue
            a = agg.setdefault((cx, cy), [0.0, 0.0])
            a[0] += dwell
            a[1] += visits
        cells = [HeatCell(x=cx, y=cy, dwell_s=round(v[0], 3), visits=int(v[1])) for (cx, cy), v in sorted(agg.items())]
        return HeatmapResponse(
            camera_id=camera_id,
            cell_px=cell_px,
            width_cells=math.ceil(self.cfg.floorplan.width_px / cell_px),
            height_cells=math.ceil(self.cfg.floorplan.height_px / cell_px),
            from_ts=from_ts,
            to_ts=to_ts,
            cells=cells,
            max_dwell_s=max((c.dwell_s for c in cells), default=0.0),
        )

    # -- KPIs ---------------------------------------------------------------
    def kpi_today(self, ts: float) -> KpiToday:
        date = store_date(ts, self.tz)
        start = day_start_ts(ts, self.tz)
        todays = [e for e in self.events.values() if start <= e.ts <= ts]
        fin = fout = tx = 0
        for e in todays:
            if e.type == "footfall.crossing":
                p = e.payload
                if p.line_kind == LineKind.ENTRANCE:  # type: ignore[union-attr]
                    if p.direction == Direction.IN:  # type: ignore[union-attr]
                        fin += p.count  # type: ignore[union-attr]
                    else:
                        fout += p.count  # type: ignore[union-attr]
                elif p.line_kind == LineKind.COUNTER and p.direction == Direction.IN:  # type: ignore[union-attr]
                    tx += p.count  # type: ignore[union-attr]
        waits: list[float] = []
        abandoned = 0
        for q in self._queues.values():
            s = q.get("snapshot")
            if s:
                waits.append(float(s["est_wait_s"]))
                abandoned = max(abandoned, int(s["abandoned_total"]))
        gap_total = 0.0
        empty = 0
        for sh in self._shelves.values():
            gap_total += float(sh.get("gap_minutes_today") or 0.0) + float(sh.get("gap_minutes") or 0.0)
            if sh.get("state") == "empty":
                empty += 1
        osa = 100.0 if not self._shelves else round(100.0 * (1 - empty / len(self._shelves)), 2)
        lost = margin = rec = 0.0
        alerts_open = alerts_today = 0
        for a in self._alerts.values():
            if a.status != AlertStatus.RESOLVED:
                alerts_open += 1
            if start <= a.raised_ts <= ts:
                alerts_today += 1
                if a.impact is not None:
                    lost += a.impact.lost_sales_inr
                    margin += a.impact.lost_margin_inr
        for e in todays:
            if e.type == "alert.resolved" and e.payload.recovered is not None:  # type: ignore[union-attr]
                rec += e.payload.recovered.lost_sales_inr  # type: ignore[union-attr]
        conversion = round(100.0 * tx / fin, 2) if fin else None
        today = KpiToday(
            store_id=self.store_id,
            date=date,
            as_of_ts=ts,
            footfall_in=fin,
            footfall_out=fout,
            occupancy_now=max(0, fin - fout),
            visual_transactions=tx,
            conversion_pct=conversion,
            atv_inr=self.cfg.impact.atv_inr,
            osa_pct=osa,
            gap_minutes_total=round(gap_total, 2),
            avg_wait_s=round(sum(waits) / len(waits), 1) if waits else None,
            max_wait_s=max(waits) if waits else None,
            abandoned=abandoned,
            lost_sales_inr=round(lost, 2),
            lost_margin_inr=round(margin, 2),
            recovered_inr=round(rec, 2),
            alerts_open=alerts_open,
            alerts_today=alerts_today,
        )
        yesterday = (_dt.date.fromisoformat(date) - _dt.timedelta(days=1)).isoformat()
        prev = self._kpi_daily.get(yesterday)
        if prev is not None:
            today.deltas = {
                "footfall_in": float(today.footfall_in - prev.footfall_in),
                "visual_transactions": float(today.visual_transactions - prev.visual_transactions),
                "osa_pct": round(today.osa_pct - prev.osa_pct, 2),
                "lost_sales_inr": round(today.lost_sales_inr - prev.lost_sales_inr, 2),
                "recovered_inr": round(today.recovered_inr - prev.recovered_inr, 2),
                "avg_wait_s": None
                if today.avg_wait_s is None or prev.avg_wait_s is None
                else round(today.avg_wait_s - prev.avg_wait_s, 1),
            }
        return today

    def upsert_kpi_daily(self, row: KpiDaily) -> None:
        self._kpi_daily[row.date] = row

    def kpi_daily(self, date: str) -> KpiDaily | None:
        return self._kpi_daily.get(date)

    # -- retention ----------------------------------------------------------
    def purge(self, policy: RetentionPolicy, now_ts: float) -> dict[str, int]:
        counts = {"telemetry_events": 0, "aggregate_events": 0, "thumbnails": 0, "sent_outbox": 0, "heatmap_cells": 0}
        tel_cut = now_ts - policy.telemetry_hours * 3600
        agg_cut = now_ts - policy.aggregate_days * 86400
        thumb_cut = now_ts - policy.thumbnails_days * 86400
        referenced = {r["event_id"] for r in self._pending_rows()}
        for eid, ev in list(self.events.items()):
            if eid in referenced:
                continue
            if ev.cls == EventClass.TELEMETRY and ev.ts < tel_cut:
                del self.events[eid]
                counts["telemetry_events"] += 1
            elif ev.cls == EventClass.AGGREGATE and ev.ts < agg_cut:
                del self.events[eid]
                counts["aggregate_events"] += 1
            elif ev.type == "shelf.scan" and ev.ts < thumb_cut and ev.payload.thumb_b64 is not None:  # type: ignore[union-attr]
                self.events[eid] = ev.model_copy(update={"payload": ev.payload.model_copy(update={"thumb_b64": None})})
                counts["thumbnails"] += 1
        sent_cut = now_ts - policy.sent_outbox_hours * 3600
        before = len(self.outbox)
        self.outbox = [
            r
            for r in self.outbox
            if not (r["sent_ts"] is not None and r["sent_ts"] < sent_cut)
            and not (r["evicted_ts"] is not None and r["evicted_ts"] < sent_cut)
        ]
        counts["sent_outbox"] = before - len(self.outbox)
        keep = {eid for eid in self.events} | {r["event_id"] for r in self.outbox}
        self.events = {eid: ev for eid, ev in self.events.items() if eid in keep}
        heat_cut = int((now_ts - policy.heatmap_days * 86400) // 3600)
        for key in [k for k in self._heat if k[3] < heat_cut]:
            del self._heat[key]
            counts["heatmap_cells"] += 1
        return counts

    def close(self) -> None:
        self.closed = True


class FakeRetentionJob:
    """Runs ``store.purge`` - the real job adds scheduling and logging."""

    def __init__(self, store: Any, policy: RetentionPolicy):
        self.store = store
        self.policy = policy
        self.runs: list[dict[str, int]] = []

    def run(self, now_ts: float | None = None) -> dict[str, int]:
        result = self.store.purge(self.policy, time.time() if now_ts is None else now_ts)
        self.runs.append(result)
        return result


# ===========================================================================
# uplink fakes
# ===========================================================================


class FakeUplink:
    """Records batches and acks them like SenseCloud would (idempotent on event_id, per-device seq check).

    ``fail=True`` -> every send raises ConnectionError.  ``drop_every=N`` -> every Nth send
    raises TimeoutError *after* recording the events, so the resend shows up as duplicates.
    """

    mode = UplinkMode.HTTP

    def __init__(self, fail: bool = False, drop_every: int = 0, *, latency_s: float = 0.0):
        self.fail = fail
        self.drop_every = int(drop_every)
        self.latency_s = latency_s
        self.batches: list[IngestBatch] = []
        self.acks: list[IngestAck] = []
        self.seen: set[str] = set()
        self.events: list[Event] = []
        self.last_seq: dict[str, int] = {}
        self.commands: list[Command] = []  # queued, returned in the next ack
        self.sends = 0
        self._connected = False

    async def connect(self) -> None:
        if self.fail:
            raise ConnectionError("fake uplink: cloud unreachable")
        self._connected = True

    async def send(self, batch: IngestBatch) -> IngestAck:
        self.sends += 1
        if self.fail:
            self._connected = False
            raise ConnectionError("fake uplink: link down")
        if self.latency_s:
            await asyncio.sleep(self.latency_s)
        self._connected = True
        self.batches.append(batch)
        accepted = duplicates = 0
        gaps: list[int] = []
        last_seq = self.last_seq.get(batch.device_id)
        for ev in batch.events:
            if ev.event_id in self.seen:
                duplicates += 1
                continue
            self.seen.add(ev.event_id)
            self.events.append(ev)
            accepted += 1
            if last_seq is not None and ev.seq > last_seq + 1:
                gaps.extend(range(last_seq + 1, ev.seq))
            last_seq = ev.seq if last_seq is None else max(last_seq, ev.seq)
        if last_seq is not None:
            self.last_seq[batch.device_id] = last_seq
        cmds, self.commands = self.commands, []
        ack = IngestAck(
            batch_id=batch.batch_id,
            accepted=accepted,
            duplicates=duplicates,
            rejected=[],
            last_seq=last_seq,
            seq_ok=not gaps,
            seq_gaps=gaps,
            commands=cmds,
            server_ts=time.time(),
        )
        self.acks.append(ack)
        if self.drop_every and self.sends % self.drop_every == 0:
            raise TimeoutError("fake uplink: ack lost")
        return ack

    async def close(self) -> None:
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected and not self.fail

    def queue_command(self, cmd: Command) -> None:
        self.commands.append(cmd)


class SimpleLinkController:
    """Reference LinkController: ``cut()``/``restore()`` flip state and notify subscribers."""

    def __init__(self, initial: LinkState = LinkState.UP):
        self._state = initial
        self._subs: list[Callable[[LinkState], None]] = []
        self.down_since_ts: float | None = None

    @property
    def state(self) -> LinkState:
        return self._state

    @property
    def up(self) -> bool:
        return self._state == LinkState.UP

    def _set(self, s: LinkState) -> None:
        if s == self._state:
            return
        self._state = s
        self.down_since_ts = time.time() if s == LinkState.DOWN else None
        for cb in list(self._subs):
            cb(s)

    def cut(self) -> None:
        self._set(LinkState.DOWN)

    def restore(self) -> None:
        self._set(LinkState.UP)

    def subscribe(self, cb: Callable[[LinkState], None]) -> None:
        self._subs.append(cb)


class FakeSyncWorker:
    """Reference sync loop: pending -> IngestBatch -> uplink.send -> mark_sent, with replay stats.

    ``tick()`` does one round (tests call it directly); ``run(stop)`` loops every
    ``cfg.device.uplink.interval_s`` until ``stop`` is set.  Mirrors the SyncStatus
    semantics the SyncBadge shows: after ``link.restore()`` the worker counts
    ``replayed_since_restore`` against ``replay_total_at_restore`` until the backlog is 0.
    """

    def __init__(
        self,
        store: Any,
        uplink: Any,
        link: Any,
        cfg: StoreConfig,
        on_status: Callable[[SyncStatus], None] | None = None,
        on_command: Callable[[Command], None] | None = None,
    ):
        self.store, self.uplink, self.link, self.cfg = store, uplink, link, cfg
        self.on_status, self.on_command = on_status, on_command
        self.last_ack_ts: float | None = None
        self.last_ack_seq: int | None = None
        self.replayed_since_restore = 0
        self.replay_total_at_restore = 0
        self.seq_ok = True
        self.cloud_reachable = False
        self.errors = 0
        self._cursor = 0
        link.subscribe(self._on_link)

    def _on_link(self, state: LinkState) -> None:
        if state == LinkState.UP:
            self.replay_total_at_restore = sum(self.store.backlog().values())
            self.replayed_since_restore = 0
        self._emit()

    def status(self) -> SyncStatus:
        by_cls = self.store.backlog()
        return SyncStatus(
            link=self.link.state,
            uplink=getattr(self.uplink, "mode", UplinkMode.HTTP),
            cloud_reachable=self.cloud_reachable,
            backlog=sum(by_cls.values()),
            backlog_by_class=by_cls,
            last_ack_ts=self.last_ack_ts,
            last_ack_seq=self.last_ack_seq,
            replayed_since_restore=self.replayed_since_restore,
            replay_total_at_restore=self.replay_total_at_restore,
            seq_ok=self.seq_ok,
            down_since_ts=getattr(self.link, "down_since_ts", None),
        )

    def _emit(self) -> None:
        if self.on_status is not None:
            self.on_status(self.status())

    async def tick(self) -> IngestAck | None:
        if self.link.state != LinkState.UP:
            self.cloud_reachable = False
            self._emit()
            return None
        rows = self.store.pending(self.cfg.device.uplink.batch_size)
        ids = [oid for oid, _ in rows]
        events = [ev for _, ev in rows]
        batch = IngestBatch(
            batch_id=new_ulid(),
            device_id=self.cfg.device.device_id,
            store_id=self.cfg.store.store_id,
            sent_ts=time.time(),
            cursor=self._cursor,
            events=events,
            backlog=sum(self.store.backlog().values()),
            contracts_version=VERSION,
        )
        try:
            ack = await self.uplink.send(batch)
        except Exception as exc:  # ConnectionError / TimeoutError from the uplink
            self.cloud_reachable = False
            self.errors += 1
            if ids:
                self.store.mark_failed(ids, str(exc))
            self._emit()
            return None
        self.cloud_reachable = True
        if ids:
            self.store.mark_sent(ids, ack.server_ts)
            self._cursor = events[-1].seq
        self.last_ack_ts = ack.server_ts
        self.last_ack_seq = ack.last_seq
        self.seq_ok = ack.seq_ok
        if self.replayed_since_restore < self.replay_total_at_restore:
            self.replayed_since_restore = min(
                self.replay_total_at_restore, self.replayed_since_restore + ack.accepted + ack.duplicates
            )
        for cmd in ack.commands:
            if self.on_command is not None:
                self.on_command(cmd)
        self._emit()
        return ack

    async def run(self, stop: asyncio.Event) -> None:
        interval = max(0.05, float(self.cfg.device.uplink.interval_s))
        while not stop.is_set():
            await self.tick()
            try:
                await asyncio.wait_for(stop.wait(), timeout=interval)
            except TimeoutError:
                pass


# ===========================================================================
# cloud-side fakes
# ===========================================================================


class FakeNotifier:
    channel = "fake"

    def __init__(self, *, fail: bool = False, channel: str = "fake"):
        self.channel = channel
        self.fail = fail
        self.sent: list[OutboundMessage] = []

    async def send(self, msg: OutboundMessage) -> DeliveryReceipt:
        if self.fail:
            return DeliveryReceipt(message_id=msg.message_id, status="failed", detail="fake notifier failure")
        self.sent.append(msg)
        return DeliveryReceipt(message_id=msg.message_id, status="sent", detail=None)

    def outbox(self, store_id: str | None = None) -> list[OutboundMessage]:
        return [m for m in self.sent if store_id is None or m.store_id == store_id]


class FakeErp:
    """Tally stand-in with the stage numbers: Amul 48 / Parle-G 120 / Fortune 18 (by tally_item_name)."""

    source = "fake"

    def __init__(self, stock: dict[str, int] | None = None, sales_inr: float = 12600.0, transactions: int = 70):
        self.stock = (
            dict(stock)
            if stock is not None
            else {"Amul Taaza 500ml": 48, "Parle-G 70g": 120, "Fortune Sunflower 1L": 18}
        )
        self._sales = {"sales_inr": float(sales_inr), "transactions": float(transactions)}
        self.journals: list[dict[str, int]] = []
        self.purchase_orders: list[list[ReorderSuggestion]] = []

    def stock_summary(self) -> dict[str, int]:
        return dict(self.stock)

    def sales_today(self) -> dict[str, float]:
        return dict(self._sales)

    def post_stock_journal(self, adjustments: dict[str, int]) -> bool:
        self.journals.append(dict(adjustments))
        for k, v in adjustments.items():
            self.stock[k] = self.stock.get(k, 0) + v
        return True

    def post_purchase_order(self, lines: list[ReorderSuggestion]) -> str:
        self.purchase_orders.append(list(lines))
        return f"PO-FAKE-{len(self.purchase_orders):04d}"


class FakeOndc:
    def __init__(self) -> None:
        self.published: list[dict[str, Any]] = []

    async def publish_availability(self, store_id: str, item_id: str, available: bool, qty: int | None) -> OndcAck:
        ts = time.time()
        mid = new_ulid(ts)
        self.published.append(
            {"store_id": store_id, "item_id": item_id, "available": available, "qty": qty, "message_id": mid, "ts": ts}
        )
        return OndcAck(ok=True, message_id=mid, item_id=item_id, available=available, ts=ts, signed=False)


class FakeForecaster:
    """Persistence forecaster implementing both CloudQueueForecaster and CloudFootfallForecaster."""

    HORIZONS = (5, 10, 15, 30)

    def __init__(self, *, mae: float = 0.8):
        self._report: FitReport | None = None
        self._daily_mean: float = 250.0
        self._mae = mae
        self._festivals = {f["date"]: f for f in load_festivals_csv()}

    def fit(self, history: "pd.DataFrame") -> FitReport:
        cols = list(history.columns)
        target = "queue_count" if "queue_count" in cols else "footfall_in"
        if target == "footfall_in" and len(history):
            self._daily_mean = float(history["footfall_in"].mean())
        self._report = FitReport(
            model="fake_persistence",
            target=target,
            trained_ts=time.time(),
            n_rows=int(len(history)),
            mae_holdout=self._mae,
            mae_baseline=self._mae * 1.5,
            features=[c for c in cols if c not in ("ts", "date", "store_id", "counter_id", target)],
            horizons=list(self.HORIZONS) if target == "queue_count" else [],
        )
        return self._report

    def predict(self, recent: "pd.DataFrame", now_ts: float) -> dict[str, float]:
        last = float(recent["queue_count"].iloc[-1]) if len(recent) and "queue_count" in recent.columns else 0.0
        return {str(h): max(0.0, last) for h in self.HORIZONS}

    def report(self) -> FitReport | None:
        return self._report

    def predict_days(self, start_date: str, n: int) -> list[FootfallForecastDay]:
        start = _dt.date.fromisoformat(start_date)
        out = []
        for i in range(n):
            d = start + _dt.timedelta(days=i)
            iso = d.isoformat()
            fest = self._festivals.get(iso)
            weight = fest["weight"] if fest else 0.0
            pred = self._daily_mean * (1.0 + 0.5 * weight) * (1.1 if d.weekday() >= 5 else 1.0)
            band = 1.28 * self._mae * 10
            nxt = next((f for f in sorted(self._festivals) if f >= iso), None)
            days_to = (_dt.date.fromisoformat(nxt) - d).days if nxt else None
            out.append(
                FootfallForecastDay(
                    date=iso,
                    predicted=round(pred, 1),
                    lower=round(max(0.0, pred - band), 1),
                    upper=round(pred + band, 1),
                    is_festival=fest is not None,
                    festival_name=fest["name"] if fest else None,
                    days_to_festival=days_to,
                )
            )
        return out


# ===========================================================================
# history / reconcile / reorder fakes
# ===========================================================================


def _hour_curve(hour: float) -> float:
    """Arrivals per minute for a kirana: peaks 08-10 and 17-21, ~0 overnight."""
    morning = 1.0 * math.exp(-((hour - 9.0) ** 2) / (2 * 1.2**2))
    evening = 1.4 * math.exp(-((hour - 19.0) ** 2) / (2 * 1.8**2))
    base = 0.25 if 8 <= hour < 22 else 0.02
    return base + morning + evening


def fake_history(
    days: int = 30,
    cfg: StoreConfig | None = None,
    seed: int = 42,
    *,
    end_date: str | None = None,
    festivals: list[dict[str, Any]] | None = None,
    counter_id: str = "counter-1",
) -> tuple["pd.DataFrame", "pd.DataFrame"]:
    """Synthetic (minute_df, daily_df) per the History DataFrame contract in interfaces.py.

    Minute rows = days x 1440 (43,200 for 30 days); festival flags come from festivals_in.csv,
    salary week = days 1-7 of the month. Queue counts follow an AR process so a forecaster can beat
    persistence. Deterministic for a given (seed, end_date).
    """
    import pandas as pd

    cfg = cfg or sample_store_config()
    rng = np.random.default_rng(seed)
    tz_name = cfg.store.tz
    fest_rows = festivals if festivals is not None else load_festivals_csv()
    fest_by_date = {f["date"]: float(f["weight"]) for f in fest_rows}
    fest_dates = sorted(fest_by_date)
    end = (
        _dt.date.fromisoformat(end_date)
        if end_date
        else _dt.datetime.now(_dt.UTC).astimezone(_dt.timezone(_dt.timedelta(hours=5, minutes=30))).date()
    )
    start = end - _dt.timedelta(days=days - 1)
    dates = [start + _dt.timedelta(days=i) for i in range(days)]

    def days_to(d: _dt.date) -> int:
        iso = d.isoformat()
        nxt = next((f for f in fest_dates if f >= iso), None)
        return (_dt.date.fromisoformat(nxt) - d).days if nxt else 365

    minute_rows = []
    daily_rows = []
    store_id = cfg.store.store_id
    service_pm_base = 60.0 / cfg.counters[0].default_service_s if cfg.counters else 1.33
    for d in dates:
        iso = d.isoformat()
        day_ts = date_to_ts(iso, tz_name)
        weight = fest_by_date.get(iso, 0.0)
        is_fest = iso in fest_by_date
        dtf = days_to(d)
        salary = d.day <= 7
        mult = (1.0 + 0.6 * weight) * (1.15 if salary else 1.0) * (1.1 if d.weekday() >= 5 else 1.0)
        if 0 < dtf <= 2:
            mult *= 1.2
        q = 0.0
        occupancy = 0.0
        fin_15 = []
        daily_in = 0
        daily_tx = 0
        noise = rng.normal(0, 1, 1440)
        for m in range(1440):
            hour = m / 60.0
            arrivals = _hour_curve(hour) * mult * max(0.2, 1 + 0.25 * noise[m])
            open_now = (
                cfg.store.open_hours[0] <= f"{int(hour):02d}:{int((hour % 1) * 60):02d}" < cfg.store.open_hours[1]
            )
            if not open_now:
                arrivals = 0.0
            service = service_pm_base * (1.0 if q > 0 else 0.0)
            q = max(0.0, 0.85 * q + arrivals - service * 0.6 + 0.3 * noise[m])
            served = min(q, service) if open_now else 0.0
            occupancy = max(0.0, 0.9 * occupancy + arrivals * 2.5 - served)
            fin_15.append(arrivals)
            fin_15 = fin_15[-15:]
            daily_in += arrivals
            daily_tx += served
            minute_rows.append(
                (
                    day_ts + m * 60,
                    store_id,
                    counter_id,
                    int(round(q)),
                    round(arrivals, 3),
                    round(service, 3),
                    int(round(sum(fin_15))),
                    int(round(occupancy)),
                    int(hour),
                    d.weekday(),
                    m,
                    is_fest,
                    weight,
                    dtf,
                    salary,
                )
            )
        daily_rows.append(
            (
                iso,
                store_id,
                int(round(daily_in)),
                int(round(daily_tx)),
                d.weekday(),
                is_fest,
                weight,
                dtf,
                salary,
                bool(rng.random() < 0.1),
            )
        )
    minute_df = pd.DataFrame(
        minute_rows,
        columns=[
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
        ],
    )
    daily_df = pd.DataFrame(
        daily_rows,
        columns=[
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
        ],
    )
    return minute_df, daily_df


def fake_reconcile(
    store_cfg: StoreConfig, erp: Any, shelf_views: list[ShelfStateView], rules: RulesConfig, impact: ImpactConfig
) -> ReconcileReport:
    """Visual (facings x units_per_facing) vs ERP stock; flags shrink per rules thresholds."""
    stock = erp.stock_summary()
    rows: list[ReconcileRow] = []
    total = 0.0
    for v in shelf_views:
        sku = store_cfg.sku(v.sku_id)
        if sku is None:
            continue
        visual = int(v.facings * sku.units_per_facing)
        key = sku.tally_item_name or sku.sku_id
        system = int(stock.get(key, stock.get(sku.sku_id, 0)))
        delta = system - visual
        delta_inr = round(delta * sku.mrp_inr, 2)
        flagged = delta >= rules.shrink_min_units and delta_inr >= rules.shrink_min_inr
        if flagged:
            total += delta_inr
        rows.append(
            ReconcileRow(
                sku_id=sku.sku_id,
                name=sku.name_en,
                shelf_id=v.shelf_id,
                visual_units=visual,
                system_units=system,
                delta_units=delta,
                delta_inr=delta_inr,
                flagged=flagged,
            )
        )
    return ReconcileReport(
        store_id=store_cfg.store.store_id,
        ts=time.time(),
        source=getattr(erp, "source", "fake"),
        rows=rows,
        shrink_inr_total=round(total, 2),
        alerts_raised=sum(r.flagged for r in rows),
    )


def fake_suggest_reorder(
    cfg: StoreConfig, footfall_fc: Any, system_stock: dict[str, int] | None, visual: dict[str, int] | None
) -> list[ReorderSuggestion]:
    """demand = velocity x open_hours x lead_time_days; safety = 0.5 day; suggest = ceil(demand + safety - stock)."""
    h0, h1 = (int(x.split(":")[0]) for x in cfg.store.open_hours)
    open_hours = max(1, h1 - h0)
    out = []
    for sku in cfg.skus:
        demand = sku.velocity_units_per_hr * open_hours * sku.lead_time_days
        safety = 0.5 * sku.velocity_units_per_hr * open_hours
        stock = (system_stock or {}).get(sku.tally_item_name or sku.sku_id, (visual or {}).get(sku.sku_id, 0))
        qty = max(0, math.ceil(demand + safety - stock))
        out.append(
            ReorderSuggestion(
                sku_id=sku.sku_id,
                name_en=sku.name_en,
                name_hi=sku.name_hi,
                system_units=(system_stock or {}).get(sku.tally_item_name or sku.sku_id),
                visual_units=(visual or {}).get(sku.sku_id),
                forecast_units_lead=round(demand, 1),
                safety_stock=round(safety, 1),
                suggest_qty=qty,
                est_cost_inr=round(qty * sku.mrp_inr * (1 - sku.margin_pct / 100), 2),
                reason=f"velocity {sku.velocity_units_per_hr}/hr × {open_hours} h × {sku.lead_time_days} d lead + 0.5 day safety − stock {stock}",
            )
        )
    return out


def whatsapp_message_for(alert: Alert, lang: str = "hi") -> OutboundMessage:
    """Build the OutboundMessage the dispatcher would send for an alert (buttons = action labels)."""
    return OutboundMessage(
        message_id=new_ulid(),
        channel="whatsapp_sim",
        to="+919999900001",
        text=alert.message(lang),
        buttons=action_labels(alert.actions, lang),
        alert_id=alert.alert_id,
        store_id=alert.store_id,
        created_ts=alert.raised_ts,
        status="queued",
    )


def sample_scenario_status(ts: float | None = None) -> ScenarioStatus:
    ts = SAMPLE_TS if ts is None else ts
    return ScenarioStatus(
        active="baseline",
        since_ts=ts,
        params={},
        available=["baseline", "quiet", "evening_rush", "diwali", "stockout", "restock"],
        clock_factor=10.0,
        sim_ts=ts,
    )


__all__ = [
    "EXAMPLES_DIR",
    "SAMPLE_TS",
    "FakeCoverageEstimator",
    "FakeDetector",
    "FakeEdgeForecaster",
    "FakeErp",
    "FakeForecaster",
    "FakeFrameSource",
    "FakeNotifier",
    "FakeOndc",
    "FakeQueueAnalyzer",
    "FakeRetentionJob",
    "FakeRuleEngine",
    "FakeShelfStateMachine",
    "FakeSkuIdentifier",
    "FakeSyncWorker",
    "FakeTracker",
    "FakeUplink",
    "FakeZoneEngine",
    "IdentityMapper",
    "InMemoryEdgeStore",
    "SimpleLinkController",
    "blobs_from_mask",
    "draw_polygon_fill",
    "draw_rect",
    "example_path",
    "fake_annotate_frame",
    "fake_history",
    "fake_reconcile",
    "fake_render_floorplan",
    "fake_shelf_thumbnail",
    "fake_suggest_reorder",
    "load_festivals_csv",
    "magenta_mask",
    "sample_alert",
    "sample_event",
    "sample_events_all",
    "sample_manifest",
    "sample_observation",
    "sample_payload",
    "sample_scenario_status",
    "sample_store_config",
    "whatsapp_message_for",
]
