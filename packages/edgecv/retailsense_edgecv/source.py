"""Frame sources and the ``open_source()`` factory.

A ``CameraConfig.source`` string selects the capture backend:

==================  ==========================================================
``synthetic:<sc>``  simulator (``retailsense_sim.video:SyntheticFrameSource`` via
                    the registry; falls back to the contracts ``FakeFrameSource``
                    when the sim package is not installed)
``file:<path>``     recorded video (``FileFrameSource``) - the demo mp4 or a DVR export
``webcam:<n>``      local camera (``WebcamFrameSource``; DirectShow on Windows)
``rtsp://...``      IP camera / DVR stream (``RtspFrameSource``; FFmpeg backend)
==================  ==========================================================

All sources are *lazy*: constructing one touches no hardware, ``open()`` does.
Every ``read()`` returns a contracts ``Frame`` (BGR uint8, ts = epoch seconds)
or ``None`` at end of stream, and raises ``SourceError`` on a fatal failure.

Timestamps: live sources stamp wall-clock time.  ``FileFrameSource`` pretends
the recording ended "now" at open (``start_ts = now - duration``) so that
analytics windows line up with the store day; when looping, time keeps moving
forward (``loop_n * duration`` is added) instead of jumping back.

Live sources decode in a background thread and keep only the *latest* frame;
``read()`` paces itself to ``sample_fps`` and never falls behind a 25 fps
camera when inference is slow - the stale-frame problem every RTSP demo hits.
"""

from __future__ import annotations

import platform
import threading
import time
from collections.abc import Callable
from pathlib import Path

import cv2
import numpy as np

from retailsense_contracts.config import CameraConfig, StoreConfig
from retailsense_contracts.interfaces import Frame, FrameSource, SourceError
from retailsense_contracts.registry import is_real, resolve


# ---------------------------------------------------------------------------
# file
# ---------------------------------------------------------------------------


class FileFrameSource:
    """Read a video file at ``sample_fps``; loops by default."""

    def __init__(
        self,
        camera: CameraConfig,
        path: str | Path,
        loop: bool = True,
        sample_fps: float | None = None,
        *,
        now: Callable[[], float] = time.time,
    ):
        self.camera_id = camera.camera_id
        self.camera = camera
        self.path = Path(path)
        self.loop = bool(loop)
        self.sample_fps = float(sample_fps or camera.fps_sample)
        self._now = now
        self._cap: cv2.VideoCapture | None = None
        self._fps = 0.0
        self._n_frames = 0
        self._size = (camera.width, camera.height)
        self._step = 1
        self._idx = 0  # native frame index within the current loop
        self._loop_n = 0
        self._seq = 0
        self.start_ts = 0.0
        self.duration_s = 0.0

    def open(self) -> None:
        if not self.path.exists():
            raise SourceError(f"video file not found: {self.path}")
        cap = cv2.VideoCapture(str(self.path))
        if not cap.isOpened():
            raise SourceError(f"cannot open video file: {self.path}")
        self._cap = cap
        self._fps = float(cap.get(cv2.CAP_PROP_FPS)) or 25.0
        self._n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if w > 0 and h > 0:
            self._size = (w, h)
        self._step = max(1, int(round(self._fps / self.sample_fps)))
        self.duration_s = self._n_frames / self._fps if self._n_frames > 0 else 0.0
        self.start_ts = float(self._now()) - self.duration_s
        self._idx = 0
        self._loop_n = 0
        self._seq = 0

    def read(self) -> Frame | None:
        if self._cap is None:
            self.open()
        assert self._cap is not None
        while True:
            ok, image = self._cap.read()
            if not ok:
                if not self.loop or self._n_frames == 0:
                    return None
                # wrap: rewind and advance virtual time by one full duration
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                self._loop_n += 1
                self._idx = 0
                ok, image = self._cap.read()
                if not ok:
                    return None
            idx = self._idx
            self._idx += 1
            if idx % self._step != 0:
                continue  # decimate to sample_fps
            ts = self.start_ts + self._loop_n * self.duration_s + idx / self._fps
            seq = self._seq
            self._seq += 1
            return Frame(ts=ts, camera_id=self.camera_id, image=image, seq=seq)

    def close(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    @property
    def nominal_fps(self) -> float:
        return self.sample_fps


# ---------------------------------------------------------------------------
# live sources (rtsp / webcam)
# ---------------------------------------------------------------------------


class _LiveFrameSource:
    """Background-decoding capture that serves only the latest frame at ``sample_fps``."""

    def __init__(self, camera: CameraConfig, sample_fps: float | None, reconnect_s: float):
        self.camera_id = camera.camera_id
        self.camera = camera
        self.sample_fps = float(sample_fps or camera.fps_sample)
        self.reconnect_s = float(reconnect_s)
        self._size = (camera.width, camera.height)
        self._cap: cv2.VideoCapture | None = None
        self._latest: np.ndarray | None = None
        self._latest_ts = 0.0
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seq = 0
        self._next_tick = 0.0
        self.reconnects = 0

    # subclass hook
    def _open_capture(self) -> cv2.VideoCapture:  # pragma: no cover - hardware
        raise NotImplementedError

    def open(self) -> None:
        self._connect()
        self._stop.clear()
        self._thread = threading.Thread(target=self._pump, name=f"capture-{self.camera_id}", daemon=True)
        self._thread.start()
        self._next_tick = time.monotonic()

    def _connect(self) -> None:
        cap = self._open_capture()
        if not cap.isOpened():
            raise SourceError(f"cannot open {self.camera.source}")
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:  # pragma: no cover - backend dependent
            pass
        w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if w > 0 and h > 0:
            self._size = (w, h)
        self._cap = cap

    def _pump(self) -> None:  # pragma: no cover - needs a device
        while not self._stop.is_set():
            cap = self._cap
            ok, image = cap.read() if cap is not None else (False, None)
            if ok:
                with self._lock:
                    self._latest, self._latest_ts = image, time.time()
                continue
            # stream dropped: release and retry
            if cap is not None:
                cap.release()
                self._cap = None
            self._stop.wait(self.reconnect_s)
            if self._stop.is_set():
                break
            try:
                self._connect()
                self.reconnects += 1
            except SourceError:
                continue

    def read(self) -> Frame | None:
        if self._thread is None:
            self.open()
        period = 1.0 / self.sample_fps
        # pace to sample_fps; if we are late just go now (no catch-up burst)
        now = time.monotonic()
        if now < self._next_tick:
            time.sleep(self._next_tick - now)
        self._next_tick = max(self._next_tick + period, time.monotonic())
        deadline = time.monotonic() + max(2.0, 3 * self.reconnect_s)
        while not self._stop.is_set():
            with self._lock:
                image, ts = self._latest, self._latest_ts
                self._latest = None
            if image is not None:
                seq = self._seq
                self._seq += 1
                return Frame(ts=ts, camera_id=self.camera_id, image=image, seq=seq)
            if time.monotonic() > deadline:
                return None  # caller treats None as "no frame"; health marks camera stale
            time.sleep(0.005)
        return None

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    @property
    def nominal_fps(self) -> float:
        return self.sample_fps


class RtspFrameSource(_LiveFrameSource):
    """IP camera / DVR stream over RTSP (FFmpeg backend)."""

    def __init__(self, camera: CameraConfig, url: str, sample_fps: float | None = None, reconnect_s: float = 3.0):
        super().__init__(camera, sample_fps, reconnect_s)
        self.url = url

    def _open_capture(self) -> cv2.VideoCapture:  # pragma: no cover - hardware
        return cv2.VideoCapture(self.url, cv2.CAP_FFMPEG)


class WebcamFrameSource(_LiveFrameSource):
    """Local USB/laptop camera; DirectShow on Windows avoids the MSMF start-up delay."""

    def __init__(self, camera: CameraConfig, index: int, sample_fps: float | None = None, reconnect_s: float = 3.0):
        super().__init__(camera, sample_fps, reconnect_s)
        self.index = int(index)

    def _open_capture(self) -> cv2.VideoCapture:  # pragma: no cover - hardware
        backend = cv2.CAP_DSHOW if platform.system() == "Windows" else cv2.CAP_ANY
        cap = cv2.VideoCapture(self.index, backend)
        if cap.isOpened():
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.camera.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.camera.height)
        return cap


# ---------------------------------------------------------------------------
# factory
# ---------------------------------------------------------------------------


def parse_source(spec: str) -> tuple[str, str]:
    """Split a source spec into ``(kind, arg)``: ('synthetic', scenario) | ('file', path) | ('webcam', 'N') | ('rtsp', url)."""
    s = spec.strip()
    low = s.lower()
    if low.startswith("synthetic:"):
        return "synthetic", s.split(":", 1)[1] or "baseline"
    if low.startswith("file:"):
        return "file", s[len("file:") :]
    if low.startswith("webcam:"):
        arg = s.split(":", 1)[1]
        if not arg.isdigit():
            raise SourceError(f"webcam index must be an integer: {spec!r}")
        return "webcam", arg
    if low.startswith(("rtsp://", "rtsps://", "http://", "https://")):
        return "rtsp", s
    raise SourceError(f"unrecognised camera source {spec!r} (expected synthetic:<scenario> | file:<path> | webcam:<n> | rtsp://...)")


def open_source(camera: CameraConfig, *, store_cfg: StoreConfig | None = None, clock_factor: float = 1.0) -> FrameSource:
    """Build (but do not open) the ``FrameSource`` for ``camera``."""
    kind, arg = parse_source(camera.source)
    if kind == "synthetic":
        cls = resolve("frame_source.synthetic")
        if is_real("frame_source.synthetic"):
            if store_cfg is None:
                raise SourceError("synthetic source needs store_cfg (the simulator renders the configured store)")
            return cls(camera, store_cfg, clock_factor, None)
        # contracts FakeFrameSource fallback: a few empty floor frames so the pipeline still runs
        return cls(n_frames=10_000, size=(camera.width, camera.height), camera_id=camera.camera_id, fps=camera.fps_sample)
    if kind == "file":
        return FileFrameSource(camera, arg, loop=camera.loop_file, sample_fps=camera.fps_sample)
    if kind == "webcam":
        return WebcamFrameSource(camera, int(arg), sample_fps=camera.fps_sample)
    return RtspFrameSource(camera, arg, sample_fps=camera.fps_sample)


__all__ = ["FileFrameSource", "RtspFrameSource", "WebcamFrameSource", "open_source", "parse_source"]
