"""Process-wide state shared by the routers and the background tasks.

Single-writer rule: :class:`EdgeState.ingest` is the *only* path that writes
events to the EdgeStore, and it always runs on the asyncio loop (routers and
tasks are coroutines; camera threads only enqueue).  That is what makes the
SQLite store safe without locks and what keeps ``seq`` gap-free.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

import numpy as np

from retailsense_contracts import VERSION
from retailsense_contracts.alerts import Alert
from retailsense_contracts.api import HealthStatus, ScenarioStatus, Series, SeriesPoint, SyncStatus
from retailsense_contracts.clock import SimClock
from retailsense_contracts.config import StoreConfig, dump_store_config
from retailsense_contracts.enums import EventClass, LinkState, UplinkMode
from retailsense_contracts.events import CameraHealth, ConfigApplied, Event, Observation
from retailsense_contracts.ws import WsMessage

from senseedge.preview import PreviewStreamer
from senseedge.wiring import Wiring
from senseedge.workers import BoundedResultQueue, CameraWorker, LatestFrame
from senseedge.ws import WsManager

log = logging.getLogger("senseedge.state")

SERIES_METRICS = ("queue_count", "est_wait_s", "footfall_in", "occupancy", "osa_pct")

DEFAULT_INTERVALS: dict[str, float] = {
    "heartbeat": 10.0,
    "kpi": 5.0,
    "retention": 3600.0,
    "forecast": 30.0,
    "model_check": 300.0,
    "consumer_idle": 0.02,
}


class SeriesBuffer:
    """In-RAM ring buffer feeding ``GET /kpis/series`` (1-minute buckets, last 24 h).

    The EdgeStore protocol exposes KPIs and views but no time-series query, so the
    edge keeps the last day of samples itself - enough for the board's sparklines.
    """

    def __init__(self, bucket_s: int = 60, keep_s: float = 86400.0):
        self.bucket_s = bucket_s
        self.keep_s = keep_s
        self._data: dict[str, deque[tuple[float, float]]] = {m: deque() for m in SERIES_METRICS}

    def record(self, metric: str, ts: float, value: float) -> None:
        d = self._data.setdefault(metric, deque())
        d.append((float(ts), float(value)))
        while d and d[0][0] < ts - self.keep_s:
            d.popleft()

    def series(self, metric: str, now: float, minutes: int) -> Series:
        d = self._data.get(metric, deque())
        start = now - minutes * 60
        buckets: dict[float, list[float]] = {}
        for ts, v in d:
            if ts >= start:
                b = ts - (ts % self.bucket_s)
                buckets.setdefault(b, []).append(v)
        points = [SeriesPoint(ts=b, value=round(sum(vs) / len(vs), 3)) for b, vs in sorted(buckets.items())]
        return Series(metric=metric, bucket_s=self.bucket_s, points=points)


class EdgeState:
    """Everything the app needs at runtime (attached as ``app.state.edge``)."""

    def __init__(
        self,
        cfg: StoreConfig,
        wiring: Wiring,
        *,
        config_path: str | Path | None = None,
        intervals: dict[str, float] | None = None,
        models_dir: str | Path | None = None,
    ):
        self.cfg = cfg
        self.config_path = Path(config_path) if config_path else None
        self.wiring = wiring
        self.clock = wiring.clock
        self.store = wiring.store
        self.rules = wiring.rule_engine
        self.shelf_machine = wiring.shelf_machine
        self.forecasters = wiring.forecasters
        self.intervals = {**DEFAULT_INTERVALS, **(intervals or {})}
        self.models_dir = Path(models_dir) if models_dir else None
        self.latest = LatestFrame()
        self.results = BoundedResultQueue(maxsize=1000)
        self.workers: list[CameraWorker] = []
        self.stop_threads = threading.Event()
        self.stopping = asyncio.Event()
        self.ws = WsManager()
        self.preview = PreviewStreamer(self)
        self.series = SeriesBuffer()
        self.start_wall = time.time()
        self.thumbs: dict[str, str] = {}  # shelf_id -> last thumb_b64 (RAM only)
        self.forecasts: dict[str, Any] = {}  # counter_id -> last QueueForecast
        self.last_heartbeat: Any = None
        self.sync_status: SyncStatus = self._initial_sync_status()
        self.sync_worker: Any = None
        self.tasks: list[asyncio.Task] = []
        self.events_appended = 0
        self.scenario: ScenarioStatus | None = None
        self.model_status: Any = None
        self._floorplan_cache: np.ndarray | None = None
        self.replaying = False

    # -- sync ----------------------------------------------------------------
    def _initial_sync_status(self) -> SyncStatus:
        link = self.wiring.link
        by_cls = self.store.backlog()
        return SyncStatus(
            link=getattr(link, "state", LinkState.UP),
            uplink=self.cfg.device.uplink.mode,
            cloud_reachable=False,
            backlog=sum(by_cls.values()),
            backlog_by_class=by_cls,
            last_ack_ts=None,
            last_ack_seq=None,
            replayed_since_restore=0,
            replay_total_at_restore=0,
            seq_ok=True,
            down_since_ts=getattr(link, "down_since_ts", None),
        )

    def refresh_sync_status(self) -> SyncStatus:
        """Current SyncStatus: from the sync worker when it has one, else computed locally."""
        worker = self.sync_worker
        if worker is not None and hasattr(worker, "status"):
            try:
                self.sync_status = worker.status()
                return self.sync_status
            except Exception:  # pragma: no cover - worker implementation detail
                pass
        by_cls = self.store.backlog()
        self.sync_status = self.sync_status.model_copy(
            update={
                "link": getattr(self.wiring.link, "state", LinkState.UP),
                "backlog": sum(by_cls.values()),
                "backlog_by_class": by_cls,
                "down_since_ts": getattr(self.wiring.link, "down_since_ts", None),
            }
        )
        return self.sync_status

    def on_sync_status(self, status: SyncStatus) -> None:
        """Callback from the sync worker (always on the loop thread)."""
        prev = self.sync_status
        self.sync_status = status
        changed = (
            prev.link != status.link
            or prev.backlog != status.backlog
            or prev.replayed_since_restore != status.replayed_since_restore
            or prev.cloud_reachable != status.cloud_reachable
            or prev.seq_ok != status.seq_ok
        )
        if changed:
            self.ws.emit("sync", self.clock.now(), status, self.cfg.store.store_id)

    # -- helpers used by routers --------------------------------------------
    @property
    def uptime_s(self) -> float:
        return time.time() - self.start_wall

    def now(self) -> float:
        return float(self.clock.now())

    def camera_health(self) -> list[CameraHealth]:
        return [CameraHealth(**w.health()) for w in self.workers]

    def health(self) -> HealthStatus:
        cams = self.camera_health()
        infer = [w.stats.percentile(0.5) for w in self.workers]
        fps = sum(w.stats.fps for w in self.workers)
        if not self.workers or all(c.status == "ok" for c in cams):
            status = "ok"
        elif any(w.stats.frames for w in self.workers):
            status = "degraded"
        else:
            status = "starting"
        if self.uptime_s < 1.0 and not any(w.stats.frames for w in self.workers):
            status = "starting" if status != "ok" else status
        return HealthStatus(
            status=status,
            store_id=self.cfg.store.store_id,
            device_id=self.cfg.device.device_id,
            uptime_s=round(self.uptime_s, 2),
            contracts_version=VERSION,
            detector=self.wiring.detector_name,
            model_version=self.wiring.model_version,
            cameras=cams,
            sync=self.refresh_sync_status(),
            sim_ts=self.now() if isinstance(self.clock, SimClock) else None,
            clock_factor=float(getattr(self.clock, "factor", 1.0)),
            fps=round(fps, 2),
            infer_ms_p50=round(max(infer) if infer else 0.0, 2),
        )

    def synthetic_control(self) -> Any | None:
        """The first frame source that implements SyntheticControl (None on real cameras)."""
        for parts in self.wiring.cameras:
            src = parts.source
            if all(hasattr(src, m) for m in ("apply_scenario", "scenario_status", "restock", "chaos", "truth")):
                return src
        return None

    def apply_scenario(self, name: str, params: dict[str, Any] | None = None) -> Any:
        ctrl = self.synthetic_control()
        if ctrl is not None:
            return ctrl.apply_scenario(name, params or {})
        return {"status": "ok", "scenario": name, "note": "real cameras running"}

    def restock(self, shelf_id: str) -> Any:
        ctrl = self.synthetic_control()
        if ctrl is not None:
            return ctrl.restock(shelf_id)
        return {"status": "ok", "shelf_id": shelf_id}

    def cfg_view(self, camera_id: str) -> dict[str, Any]:
        """Geometry handed to the annotator (plain dict so any annotator can read it)."""
        cfg = self.cfg
        return {
            "camera_id": camera_id,
            "zones": [z.model_dump(mode="json") for z in cfg.zones_for(camera_id)],
            "lines": [ln.model_dump(mode="json") for ln in cfg.lines_for(camera_id)],
            "shelves": [s.model_dump(mode="json") for s in cfg.shelves_for(camera_id)],
            "shelf_states": {v.shelf_id: str(v.state) for v in self.shelf_machine.views()},
        }

    def floorplan_image(self) -> np.ndarray:
        """Floorplan canvas from the registry renderer, falling back to zones on a blank canvas."""
        try:
            img = self.wiring.floorplan_renderer(self.cfg, with_zones=True)
            if isinstance(img, np.ndarray) and img.ndim == 3:
                return img
        except Exception as exc:
            log.debug("floorplan renderer failed (%s); drawing zones on a blank canvas", exc)
        return draw_zones_blank(self.cfg)

    def worker_for(self, camera_id: str) -> CameraWorker | None:
        for w in self.workers:
            if w.parts.camera.camera_id == camera_id:
                return w
        return None

    # -- the single write path --------------------------------------------
    async def ingest(self, observations: list[Observation]) -> list[Event]:
        """Append observations (one transaction), mirror alert state, fan out to websockets."""
        if not observations:
            return []
        events = self.store.append(observations)
        self.events_appended += len(events)
        store_id = self.cfg.store.store_id
        for ev in events:
            if ev.cls != EventClass.TELEMETRY:
                self.ws.emit("event", ev.ts, ev, store_id)
            if ev.type == "alert.raised":
                alert: Alert = ev.payload.alert  # type: ignore[union-attr]
                self.store.upsert_alert(alert)
                self.ws.emit("alert", ev.ts, alert, store_id)
            elif ev.type in ("alert.acked", "alert.resolved"):
                a = self.rules.get(ev.payload.alert_id)  # type: ignore[union-attr]
                if a is not None:
                    self.store.upsert_alert(a)
                    self.ws.emit("alert", ev.ts, a, store_id)
            elif ev.type == "queue.forecast":
                self.forecasts[ev.payload.counter_id] = ev.payload  # type: ignore[union-attr]
                self.ws.emit("forecast", ev.ts, ev.payload, store_id)
        return events

    def broadcast(self, msg: WsMessage) -> None:
        self.ws.broadcast(msg)

    # -- configuration ------------------------------------------------------
    async def apply_config(self, new_cfg: StoreConfig) -> StoreConfig:
        """Bump version, persist YAML, hot-reload workers and emit ``config.applied``."""
        new_cfg = new_cfg.model_copy(update={"config_version": self.cfg.config_version + 1})
        new_cfg = StoreConfig.model_validate(new_cfg.model_dump(mode="json"))  # re-run integrity validators
        self.cfg = new_cfg
        self.wiring.cfg = new_cfg
        self._floorplan_cache = None
        if self.config_path is not None:
            try:
                dump_store_config(new_cfg, self.config_path)
            except Exception as exc:  # disk problems must not break the running store
                log.warning("could not persist config to %s: %s", self.config_path, exc)
        for w in self.workers:
            w.reload(new_cfg)
        if hasattr(self.shelf_machine, "shelves"):
            # keep the state machine's geometry/SKU view in sync for shelves that already exist
            for s in new_cfg.shelves:
                self.shelf_machine.shelves[s.shelf_id] = s
        self.store.set_state("config_version", str(new_cfg.config_version))
        ts = self.now()
        await self.ingest(
            [Observation.of(ConfigApplied(config_version=new_cfg.config_version, config_hash=new_cfg.config_hash()), ts)]
        )
        return new_cfg

    # -- lifecycle ------------------------------------------------------------
    def start_workers(self) -> None:
        for parts in self.wiring.cameras:
            w = CameraWorker(parts, self.cfg, self.results, self.latest, self.stop_threads, clock=self.clock)
            self.workers.append(w)
            w.start()

    def stop_workers(self, timeout: float = 3.0) -> None:
        self.stop_threads.set()
        deadline = time.monotonic() + timeout
        for w in self.workers:
            w.join(max(0.0, deadline - time.monotonic()))

    @property
    def uplink_mode(self) -> UplinkMode:
        return self.cfg.device.uplink.mode


def draw_zones_blank(cfg: StoreConfig) -> np.ndarray:
    """Fallback floorplan: floor-coloured canvas with zone outlines and shelf fills (pure numpy)."""
    fp = cfg.floorplan
    img = np.full((fp.height_px, fp.width_px, 3), 235, dtype=np.uint8)
    for s in cfg.shelves:
        xs = [p[0] for p in s.polygon]
        ys = [p[1] for p in s.polygon]
        x0, y0, x1, y1 = int(min(xs)), int(min(ys)), int(max(xs)), int(max(ys))
        img[max(0, y0) : y1, max(0, x0) : x1] = (110, 110, 110)
    for z in cfg.zones:
        pts = [(int(p[0]), int(p[1])) for p in z.polygon]
        for (xa, ya), (xb, yb) in zip(pts, pts[1:] + pts[:1], strict=False):
            n = max(abs(xb - xa), abs(yb - ya), 1)
            for i in range(n + 1):
                x = xa + (xb - xa) * i // n
                y = ya + (yb - ya) * i // n
                if 0 <= x < fp.width_px and 0 <= y < fp.height_px:
                    img[y, x] = (60, 60, 200)
    return img
