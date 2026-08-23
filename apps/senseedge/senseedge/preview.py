"""Live preview: annotated stills and an MJPEG stream - never persisted.

Privacy: when ``privacy.preview_blur_people`` (or the camera's own flag) is on,
every tracked person's box is *pixelated* before encoding, so even the LAN
preview carries no identifiable faces.  Pixelation is done here in numpy
regardless of what the registry annotator does, so the guarantee does not
depend on which annotator is installed.

Encoding: OpenCV when available (JPEG); otherwise a tiny pure-Python PNG
encoder (zlib + struct) keeps the endpoints working on a bare install.
"""

from __future__ import annotations

import asyncio
import base64
import struct
import time
import zlib
from collections.abc import AsyncIterator
from typing import Any

import numpy as np

from retailsense_contracts.interfaces import Track

try:  # lazy, optional
    import cv2  # type: ignore
except Exception:  # pragma: no cover - environment dependent
    cv2 = None

BOUNDARY = "senseedgeframe"


def pixelate_tracks(image: np.ndarray, tracks: list[Track], block: int = 12) -> np.ndarray:
    """Mosaic every track's bbox with ``block``-px cells (returns a copy)."""
    out = image.copy()
    h, w = out.shape[:2]
    for tr in tracks:
        x0, y0, x1, y1 = (int(round(v)) for v in tr.bbox)
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        region = out[y0:y1, x0:x1]
        for by in range(0, region.shape[0], block):
            for bx in range(0, region.shape[1], block):
                cell = region[by : by + block, bx : bx + block]
                cell[:] = cell.reshape(-1, 3).mean(axis=0).astype(np.uint8)
    return out


def encode_png(image: np.ndarray) -> bytes:
    """Minimal RGB PNG encoder (BGR input) - no third-party dependency."""
    if image.ndim == 2:
        image = np.stack([image] * 3, axis=-1)
    rgb = np.ascontiguousarray(image[:, :, ::-1]).astype(np.uint8)
    h, w = rgb.shape[:2]
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )


def encode_image(image: np.ndarray, fmt: str = "jpg", quality: int = 80) -> tuple[bytes, str]:
    """Encode to (bytes, mime). JPEG via cv2 when present, PNG otherwise (or when asked)."""
    if fmt == "jpg" and cv2 is not None:
        ok, buf = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        if ok:
            return bytes(buf), "image/jpeg"
    if cv2 is not None and fmt == "png":
        ok, buf = cv2.imencode(".png", image)
        if ok:
            return bytes(buf), "image/png"
    return encode_png(image), "image/png"


def decode_image(data: bytes) -> np.ndarray | None:
    """Decode an uploaded image (cv2 if present; None when undecodable)."""
    if cv2 is None:
        return None
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, 1)
    return img


def resize(image: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """Nearest-neighbour resize without cv2 (good enough for 96x96 thumbnails)."""
    w, h = size
    if cv2 is not None:
        return cv2.resize(image, (w, h), interpolation=cv2.INTER_AREA)
    ys = (np.arange(h) * image.shape[0] / h).astype(int)
    xs = (np.arange(w) * image.shape[1] / w).astype(int)
    return image[ys][:, xs]


def thumb_b64(image: np.ndarray, polygon: list[list[float]], size: int = 96) -> str | None:
    """Crop the shelf polygon bbox, shrink to <= size px and base64-encode (JPEG or PNG)."""
    xs = [p[0] for p in polygon]
    ys = [p[1] for p in polygon]
    x0, y0 = max(0, int(min(xs))), max(0, int(min(ys)))
    x1, y1 = min(image.shape[1], int(max(xs))), min(image.shape[0], int(max(ys)))
    if x1 - x0 < 2 or y1 - y0 < 2:
        return None
    crop = image[y0:y1, x0:x1]
    scale = size / max(crop.shape[0], crop.shape[1])
    tw, th = max(1, int(crop.shape[1] * scale)), max(1, int(crop.shape[0] * scale))
    data, _ = encode_image(resize(crop, (tw, th)), "jpg", quality=60)
    b64 = base64.b64encode(data).decode("ascii")
    return b64 if len(b64) <= 16384 else None


class PreviewStreamer:
    """Annotated frames from :class:`LatestFrame` at <= ``max_fps``; privacy blur per config."""

    def __init__(self, state: Any, *, max_fps: float = 10.0):
        self.state = state
        self.max_fps = max_fps

    def blur_for(self, camera_id: str) -> bool:
        cfg = self.state.cfg
        cam = cfg.camera(camera_id)
        return bool(cfg.privacy.preview_blur_people and cam.preview_blur_people)

    def still(self, camera_id: str, *, annotate: bool = True) -> np.ndarray:
        """Latest frame (or the floorplan canvas before the first frame), annotated + pixelated."""
        got = self.state.latest.get(camera_id)
        if got is None:
            return self.state.floorplan_image()
        frame, tracks = got
        image = frame.image
        blur = self.blur_for(camera_id)
        if blur and tracks:
            image = pixelate_tracks(image, tracks)
        if annotate:
            try:
                image = self.state.wiring.annotator(image, tracks, self.state.cfg_view(camera_id), blur_people=False)
            except Exception:  # annotator is cosmetic; never break the preview
                pass
        return image

    async def mjpeg(self, camera_id: str, *, max_frames: int = 0) -> AsyncIterator[bytes]:
        """Multipart stream; ``max_frames`` > 0 bounds the stream (tests / curl)."""
        interval = 1.0 / self.max_fps
        sent = 0
        while not self.state.stopping.is_set():
            t0 = time.monotonic()
            data, mime = encode_image(self.still(camera_id), "jpg")
            yield (
                f"--{BOUNDARY}\r\nContent-Type: {mime}\r\nContent-Length: {len(data)}\r\n\r\n".encode() + data + b"\r\n"
            )
            sent += 1
            if max_frames and sent >= max_frames:
                break
            await asyncio.sleep(max(0.0, interval - (time.monotonic() - t0)))
