"""Camera worker threads: source -> detector -> tracker -> analytics -> queue.

Process model (spec D9, normative): one :class:`CameraWorker` thread per
camera.  The thread owns everything that touches pixels (frame source,
detector, tracker, zone engine, queue analyzers, shelf scanner) and publishes
two things to the asyncio side:

* a :class:`FrameResult` (observations + tracks + timings) on a bounded
  ``queue.Queue``; when the queue is full the *oldest telemetry-only* result is
  dropped so alerts-in-waiting are never lost to a slow consumer;
* the :class:`LatestFrame` holder used by the MJPEG preview and the shelf
  calibration endpoints (never persisted - privacy by design).

The EdgeStore is **never** touched from this thread; the consumer task on the
event loop is the only writer.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from retailsense_contracts.clock import day_start_ts
from retailsense_contracts.config import CameraConfig, ShelfPolygon, ShelfReference, StoreConfig
from retailsense_contracts.enums import EventClass, ShelfState
from retailsense_contracts.events import Observation, ShelfScan
from retailsense_contracts.geometry import bbox_polygon_overlap
from retailsense_contracts.interfaces import AnalyticsUpdate, Frame, SourceError, Track

from senseedge.adapt import build
from senseedge.wiring import CameraParts

log = logging.getLogger("senseedge.workers")


@dataclass
class FrameResult:
    """What one processed frame hands to the asyncio consumer."""

    ts: float
    camera_id: str
    tracks: list[Track]
    infer_ms: float
    observations: list[Observation]
    update: AnalyticsUpdate | None = None

    @property
    def telemetry_only(self) -> bool:
        return all(o.cls == EventClass.TELEMETRY for o in self.observations)


@dataclass
class LatestFrame:
    """Thread-safe holder of the most recent frame + tracks per camera (RAM only)."""

    _lock: threading.Lock = field(default_factory=threading.Lock)
    _frames: dict[str, tuple[Frame, list[Track]]] = field(default_factory=dict)

    def put(self, frame: Frame, tracks: list[Track]) -> None:
        with self._lock:
            self._frames[frame.camera_id] = (frame, list(tracks))

    def get(self, camera_id: str) -> tuple[Frame, list[Track]] | None:
        with self._lock:
            return self._frames.get(camera_id)

    def cameras(self) -> list[str]:
        with self._lock:
            return list(self._frames)


@dataclass
class WorkerStats:
    """Rolling counters read by the heartbeat task (no locks: single-writer, torn reads are harmless)."""

    frames: int = 0
    fps: float = 0.0
    last_frame_wall: float | None = None
    last_frame_ts: float | None = None
    status: str = "starting"
    error: str | None = None
    infer_ms: deque = field(default_factory=lambda: deque(maxlen=200))
    dropped: int = 0

    def percentile(self, p: float) -> float:
        if not self.infer_ms:
            return 0.0
        xs = sorted(self.infer_ms)
        return float(xs[min(len(xs) - 1, int(p * len(xs)))])


class BoundedResultQueue:
    """``queue.Queue(maxsize)`` with drop-oldest-telemetry semantics on overflow."""

    def __init__(self, maxsize: int = 1000):
        self.q: queue.Queue[FrameResult] = queue.Queue(maxsize=maxsize)
        self.dropped = 0

    def put(self, item: FrameResult) -> None:
        try:
            self.q.put_nowait(item)
            return
        except queue.Full:
            pass
        # Drop one old telemetry-only result to make room; never drop results carrying alerts.
        try:
            old = self.q.get_nowait()
            self.dropped += 1
            if not old.telemetry_only:
                # put it back at the end rather than lose aggregate/alert observations
                item = FrameResult(old.ts, old.camera_id, old.tracks, old.infer_ms, old.observations + item.observations)
        except queue.Empty:
            pass
        try:
            self.q.put_nowait(item)
        except queue.Full:  # pragma: no cover - only under pathological contention
            self.dropped += 1

    def get(self, timeout: float) -> FrameResult | None:
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def qsize(self) -> int:
        return self.q.qsize()


class ShelfScanner:
    """Every ``shelf_scan_interval_s`` estimate coverage of each shelf on this camera.

    Occlusion-aware: if a tracked person overlaps the shelf polygon by more than
    ``rules.occlusion_skip_overlap`` the scan is skipped (the persistence filter
    downstream would otherwise count a shopper as an empty shelf).
    """

    def __init__(self, parts: CameraParts, cfg: StoreConfig):
        self.parts = parts
        self.rules = cfg.rules
        self.interval_s = parts.camera.shelf_scan_interval_s
        self.shelves: list[ShelfPolygon] = list(cfg.shelves_for(parts.camera.camera_id))
        self.references: dict[str, ShelfReference] = {s.shelf_id: s.reference for s in self.shelves if s.reference}
        self.thumbs_enabled = cfg.privacy.shelf_thumbnails
        self._last_scan_ts: float | None = None

    def reload(self, cfg: StoreConfig) -> None:
        self.rules = cfg.rules
        self.shelves = list(cfg.shelves_for(self.parts.camera.camera_id))
        for s in self.shelves:
            if s.reference is not None:
                self.references[s.shelf_id] = s.reference

    def set_reference(self, ref: ShelfReference) -> None:
        self.references[ref.shelf_id] = ref

    def due(self, ts: float) -> bool:
        return self._last_scan_ts is None or ts - self._last_scan_ts >= self.interval_s

    def _occluded(self, shelf: ShelfPolygon, tracks: list[Track]) -> bool:
        for tr in tracks:
            if bbox_polygon_overlap(tr.bbox, shelf.polygon) > self.rules.occlusion_skip_overlap:
                return True
        return False

    def scan(self, frame: Frame, tracks: list[Track]) -> list[Observation]:
        self._last_scan_ts = frame.ts
        out: list[Observation] = []
        for shelf in self.shelves:
            if self._occluded(shelf, tracks):
                continue
            ref = self.references.get(shelf.shelf_id)
            res = self.parts.coverage_estimator.estimate(frame.image, shelf, ref)
            thumb = None
            if self.thumbs_enabled:
                try:
                    thumb = self.parts.shelf_thumb(frame.image, shelf)
                except Exception:  # pragma: no cover - thumbnail is best-effort
                    thumb = None
            scan = ShelfScan(
                shelf_id=shelf.shelf_id,
                sku_id=shelf.sku_id,
                coverage=float(res.coverage),
                facings=int(res.facings),
                capacity_facings=shelf.capacity_facings,
                state_raw=raw_state(res.coverage, self.rules),
                occluded=False,
                method=str(res.method),
                thumb_b64=thumb,
            )
            out.append(Observation.of(scan, frame.ts, frame.camera_id))
        return out


def raw_state(coverage: float, rules: Any) -> ShelfState:
    """Instantaneous shelf state from coverage thresholds (persistence is applied downstream)."""
    if coverage <= rules.shelf_empty_coverage:
        return ShelfState.EMPTY
    if coverage < rules.shelf_partial_coverage:
        return ShelfState.PARTIAL
    return ShelfState.STOCKED


class CameraWorker(threading.Thread):
    """One thread per camera running the perception + analytics pipeline."""

    def __init__(
        self,
        parts: CameraParts,
        cfg: StoreConfig,
        out: BoundedResultQueue,
        latest: LatestFrame,
        stop: threading.Event,
        *,
        clock: Any,
        pace: bool = True,
    ):
        super().__init__(name=f"camera-{parts.camera.camera_id}", daemon=True)
        self.parts = parts
        self.cfg = cfg
        self.out = out
        self.latest = latest
        self.stop_event = stop
        self.clock = clock
        self.pace = pace
        self.stats = WorkerStats()
        self.scanner = ShelfScanner(parts, cfg)
        self._lock = threading.Lock()  # guards hot-reload swaps of the analytics objects
        self._build_analytics(cfg)

    # -- analytics (rebuilt on hot reload) ----------------------------------
    def _build_analytics(self, cfg: StoreConfig) -> None:
        cam: CameraConfig = self.parts.camera
        zones, lines = cfg.zones_for(cam.camera_id), cfg.lines_for(cam.camera_id)
        zone_engine = build(
            self.parts.zone_engine_cls, {}, positional=(cam, zones, lines, self.parts.mapper, cfg.rules, cfg.floorplan)
        )
        day0 = day_start_ts(self.clock.now(), cfg.store.tz)
        zone_ids = {z.zone_id for z in zones}
        analyzers = {
            c.counter_id: build(self.parts.queue_analyzer_cls, {}, positional=(c, cfg.rules, day0))
            for c in cfg.counters
            if c.queue_zone_id in zone_ids
        }
        with self._lock:
            self.zone_engine = zone_engine
            self.queue_analyzers = analyzers

    def reload(self, cfg: StoreConfig) -> None:
        """Hot reload zones/lines/counters/shelves/rules without restarting the source."""
        self.cfg = cfg
        self._build_analytics(cfg)
        self.scanner.reload(cfg)

    def set_reference(self, ref: ShelfReference) -> None:
        self.scanner.set_reference(ref)

    # -- loop ----------------------------------------------------------------
    def run(self) -> None:  # noqa: C901 - the loop is deliberately linear and commented
        src = self.parts.source
        try:
            src.open()
            if hasattr(self.parts.detector, "warmup"):
                self.parts.detector.warmup()
        except Exception as exc:
            self.stats.status, self.stats.error = "error", f"open: {exc}"
            log.error("camera %s failed to open: %s", self.parts.camera.camera_id, exc)
            return
        self.stats.status = "ok"
        interval = 1.0 / max(0.5, float(self.parts.camera.fps_sample))
        next_due = time.monotonic()
        while not self.stop_event.is_set():
            try:
                frame = src.read()
            except SourceError as exc:
                self.stats.status, self.stats.error = "error", str(exc)
                log.error("camera %s source error: %s", self.parts.camera.camera_id, exc)
                break
            except Exception as exc:  # pragma: no cover - defensive: keep the thread alive
                self.stats.status, self.stats.error = "error", str(exc)
                self.stop_event.wait(0.5)
                continue
            if frame is None:
                if not self.parts.camera.loop_file:
                    self.stats.status = "stale"
                    break
                src.close()
                src.open()
                if hasattr(self.parts.tracker, "reset"):
                    self.parts.tracker.reset()
                continue
            self.process(frame)
            if self.pace:
                next_due += interval
                delay = next_due - time.monotonic()
                if delay > 0:
                    self.stop_event.wait(delay)
                else:
                    next_due = time.monotonic()
        try:
            src.close()
        except Exception:  # pragma: no cover
            pass
        if self.stats.status == "ok":
            self.stats.status = "stale"

    def process(self, frame: Frame) -> FrameResult:
        """Run the per-frame pipeline and publish the result (also used directly by tests)."""
        t0 = time.perf_counter()
        detections = self.parts.detector.detect(frame.image)
        tracks = self.parts.tracker.update(detections, frame.ts)
        infer_ms = (time.perf_counter() - t0) * 1000.0
        confirmed = [t for t in tracks if t.confirmed]
        with self._lock:
            upd = self.zone_engine.update(confirmed, frame.ts)
            analyzers = list(self.queue_analyzers.values())
        observations: list[Observation] = []
        cam_id = frame.camera_id
        observations += [Observation.of(o, frame.ts, cam_id) for o in upd.footfall]
        observations += [Observation.of(o, frame.ts, cam_id) for o in upd.occupancy]
        observations += [Observation.of(d, frame.ts, cam_id) for d in upd.dwell_samples]
        if upd.heat is not None:
            observations.append(Observation.of(upd.heat, frame.ts, cam_id))
        for qa in analyzers:
            snap = qa.update(upd)
            if snap is not None:
                observations.append(Observation.of(snap, frame.ts, cam_id))
        if self.scanner.shelves and self.scanner.due(frame.ts):
            observations += self.scanner.scan(frame, confirmed)
        result = FrameResult(frame.ts, cam_id, confirmed, infer_ms, observations, upd)
        self.latest.put(frame, confirmed)
        self._account(frame, infer_ms)
        self.out.put(result)
        return result

    def _account(self, frame: Frame, infer_ms: float) -> None:
        st = self.stats
        now = time.monotonic()
        if st.last_frame_wall is not None:
            dt = max(1e-3, now - st.last_frame_wall)
            inst = 1.0 / dt
            st.fps = inst if st.fps == 0 else 0.8 * st.fps + 0.2 * inst
        st.last_frame_wall = now
        st.last_frame_ts = frame.ts
        st.frames += 1
        st.infer_ms.append(infer_ms)
        if frame.image is not None and frame.image.size and float(np.std(frame.image[::8, ::8])) < self.cfg.rules.black_frame_std:
            st.status = "black"
        elif st.status in ("black", "starting"):
            st.status = "ok"

    def health(self) -> dict[str, Any]:
        """CameraHealth fields for the heartbeat (age measured on the wall clock)."""
        age = 0.0 if self.stats.last_frame_wall is None else time.monotonic() - self.stats.last_frame_wall
        status = self.stats.status
        if status == "ok" and age > self.cfg.rules.camera_down_s:
            status = "stale"
        return {
            "camera_id": self.parts.camera.camera_id,
            "status": status if status in ("ok", "stale", "black", "error") else "stale",
            "fps": round(self.stats.fps, 2),
            "last_frame_age_s": round(age, 2),
            "detector": str(getattr(self.parts.detector, "name", self.parts.detector_key)),
        }
