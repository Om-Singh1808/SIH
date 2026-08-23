"""Per-camera capture -> detect -> track loop.

``CvPipeline.run(stop)`` is the body of one daemon thread per camera in
SenseEdge.  It owns the frame source, feeds every frame through the detector
and tracker, and hands a :class:`FrameResult` to ``on_result`` (the app's
analytics consumer).  The loop is deliberately dumb and robust:

* a ``None`` from the source (end of a non-looping file, or a live source that
  has gone quiet) is reported through ``health()`` as ``stale``; the loop keeps
  waiting for the source to recover unless ``stop`` is set or the file is done;
* exceptions from the detector/tracker are logged and counted, never fatal -
  one bad frame must not kill a day of analytics;
* timing is recorded (fps, inference p50/p95) for ``device.heartbeat``;
* black frames (``std < black_frame_std``) are flagged for the camera_down rule.

``FrameResult`` is a ``NamedTuple`` ``(frame, detections, tracks, infer_ms)`` so
it unpacks as the tuple the spec describes while still reading well.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import NamedTuple

import numpy as np

from retailsense_contracts.config import CameraConfig, StoreConfig
from retailsense_contracts.events import CameraHealth
from retailsense_contracts.interfaces import Detection, Detector, Frame, FrameSource, SourceError, Track, Tracker

from .source import open_source

log = logging.getLogger("retailsense.edgecv.pipeline")


class FrameResult(NamedTuple):
    frame: Frame
    detections: list[Detection]
    tracks: list[Track]
    infer_ms: float


class CvPipeline:
    """Threaded capture/detect/track loop for one camera."""

    def __init__(
        self,
        camera: CameraConfig,
        store_cfg: StoreConfig | None,
        detector: Detector,
        tracker: Tracker,
        on_result: Callable[[FrameResult], None],
        *,
        source: FrameSource | None = None,
        clock_factor: float = 1.0,
        stats_window: int = 200,
    ):
        self.camera = camera
        self.store_cfg = store_cfg
        self.detector = detector
        self.tracker = tracker
        self.on_result = on_result
        self.clock_factor = clock_factor
        self.source: FrameSource | None = source
        self._infer_ms: deque[float] = deque(maxlen=stats_window)
        self._frame_wall: deque[float] = deque(maxlen=stats_window)
        self.frames = 0
        self.errors = 0
        self.last_frame_wall = 0.0
        self.last_frame_ts = 0.0
        self.latest: FrameResult | None = None
        self.black = False
        self.ended = False
        self.last_error: str | None = None
        black_std = store_cfg.rules.black_frame_std if store_cfg is not None else 3.0
        self.black_frame_std = float(black_std)

    # ---------------------------------------------------------------------
    def _ensure_source(self) -> FrameSource:
        if self.source is None:
            self.source = open_source(self.camera, store_cfg=self.store_cfg, clock_factor=self.clock_factor)
        return self.source

    def process(self, frame: Frame) -> FrameResult:
        """Detect + track one frame (synchronous; used by the loop and by tests)."""
        t0 = time.perf_counter()
        dets = self.detector.detect(frame.image)
        infer_ms = (time.perf_counter() - t0) * 1000.0
        tracks = self.tracker.update(dets, frame.ts)
        self.black = self._is_black(frame.image)
        res = FrameResult(frame, dets, tracks, infer_ms)
        self._infer_ms.append(infer_ms)
        now = time.time()
        self._frame_wall.append(now)
        self.last_frame_wall = now
        self.last_frame_ts = frame.ts
        self.frames += 1
        self.latest = res
        return res

    def _is_black(self, image: np.ndarray) -> bool:
        if image.size == 0:
            return True
        sub = image[::8, ::8]
        return float(sub.std()) < self.black_frame_std and float(sub.mean()) < 40.0

    def run(self, stop: threading.Event) -> None:
        """Loop until ``stop`` is set (or a non-looping file ends)."""
        src = self._ensure_source()
        try:
            src.open()
            try:
                self.detector.warmup()
            except Exception as exc:  # warmup is best-effort
                log.warning("detector warmup failed on %s: %s", self.camera.camera_id, exc)
            while not stop.is_set():
                try:
                    frame = src.read()
                except SourceError as exc:
                    self.errors += 1
                    self.last_error = str(exc)
                    log.error("camera %s source error: %s", self.camera.camera_id, exc)
                    stop.wait(1.0)
                    continue
                if frame is None:
                    if self.camera.source.startswith("file:") and not self.camera.loop_file:
                        self.ended = True
                        break
                    stop.wait(0.05)
                    continue
                try:
                    res = self.process(frame)
                    self.on_result(res)
                except Exception as exc:  # noqa: BLE001 - never let one frame kill the loop
                    self.errors += 1
                    self.last_error = str(exc)
                    log.exception("frame %s on %s failed: %s", frame.seq, self.camera.camera_id, exc)
        finally:
            try:
                src.close()
            except Exception:  # pragma: no cover
                pass

    def start(self, stop: threading.Event) -> threading.Thread:
        """Convenience: run in a daemon thread."""
        t = threading.Thread(target=self.run, args=(stop,), name=f"cv-{self.camera.camera_id}", daemon=True)
        t.start()
        return t

    # stats ----------------------------------------------------------------
    def fps(self) -> float:
        if len(self._frame_wall) < 2:
            return 0.0
        span = self._frame_wall[-1] - self._frame_wall[0]
        return (len(self._frame_wall) - 1) / span if span > 0 else 0.0

    def infer_ms_percentiles(self) -> tuple[float, float]:
        if not self._infer_ms:
            return 0.0, 0.0
        arr = np.fromiter(self._infer_ms, dtype=float)
        return float(np.percentile(arr, 50)), float(np.percentile(arr, 95))

    def health(self, now: float | None = None, stale_after_s: float = 15.0) -> CameraHealth:
        now = time.time() if now is None else now
        age = now - self.last_frame_wall if self.last_frame_wall else float("inf")
        if self.last_error and self.frames == 0:
            status = "error"
        elif age > stale_after_s:
            status = "stale"
        elif self.black:
            status = "black"
        else:
            status = "ok"
        return CameraHealth(
            camera_id=self.camera.camera_id,
            status=status,
            fps=round(self.fps(), 2),
            last_frame_age_s=round(age if age != float("inf") else 1e9, 3),
            detector=getattr(self.detector, "name", "unknown"),
        )


__all__ = ["CvPipeline", "FrameResult"]
