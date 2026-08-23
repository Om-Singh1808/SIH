"""Shelf thumbnails: the *only* image data that ever leaves the edge box.

Privacy by construction: the thumbnail is the shelf polygon alone (pixels
outside the polygon are blacked out), downscaled to at most 96×96 and JPEG
encoded at quality 70. A shopper cannot be in it because occluded scans are
skipped before a thumbnail is made (see :func:`scanner.occluded_by`), and the
payload is capped at 16 KB of base64 so a 30-day history of scans stays small.

Encoders are tried in order - OpenCV, then Pillow - and both are imported
lazily; with neither installed the function returns ``None``, which the
``ShelfScan.thumb_b64`` contract explicitly allows.
"""

from __future__ import annotations

import base64
from importlib import import_module

import numpy as np

from retailsense_contracts.config import ShelfPolygon
from retailsense_contracts.geometry import polygon_bbox, polygon_mask

THUMB_SIDE = 96
JPEG_QUALITY = 70
MAX_B64_CHARS = 16384


def _resize_area(img: np.ndarray, out_h: int, out_w: int) -> np.ndarray:
    """Box-filter downscale in pure numpy (nearest for upscales; we never upscale)."""
    h, w = img.shape[:2]
    if out_h >= h and out_w >= w:
        return img
    ys = (np.arange(out_h + 1) * h / out_h).astype(int)
    xs = (np.arange(out_w + 1) * w / out_w).astype(int)
    out = np.empty((out_h, out_w, img.shape[2]), dtype=np.float32)
    for i in range(out_h):
        rows = img[ys[i] : max(ys[i] + 1, ys[i + 1])]
        for j in range(out_w):
            out[i, j] = rows[:, xs[j] : max(xs[j] + 1, xs[j + 1])].reshape(-1, img.shape[2]).mean(axis=0)
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def fit_thumbnail(crop: np.ndarray, side: int = THUMB_SIDE) -> np.ndarray:
    """Downscale so the longer edge is ``side`` px, keeping aspect ratio."""
    h, w = crop.shape[:2]
    scale = min(1.0, side / max(h, w))
    out_h, out_w = max(1, int(round(h * scale))), max(1, int(round(w * scale)))
    return _resize_area(crop, out_h, out_w)


def encode_jpeg(img_bgr: np.ndarray, quality: int = JPEG_QUALITY) -> bytes | None:
    """JPEG bytes via cv2 or Pillow; ``None`` when no encoder is installed."""
    try:
        cv2 = import_module("cv2")
        ok, buf = cv2.imencode(".jpg", img_bgr, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
        return bytes(buf) if ok else None
    except ImportError:
        pass
    try:
        pil_image = import_module("PIL.Image")
        import io

        bio = io.BytesIO()
        pil_image.fromarray(img_bgr[..., ::-1]).save(bio, format="JPEG", quality=int(quality))
        return bio.getvalue()
    except ImportError:
        return None


def shelf_crop(image: np.ndarray, shelf: ShelfPolygon) -> np.ndarray | None:
    """The polygon bbox with everything outside the polygon blacked out."""
    h, w = image.shape[:2]
    x0, y0, x1, y1 = polygon_bbox(shelf.polygon)
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
    if x1 <= x0 or y1 <= y0:
        return None
    crop = image[y0:y1, x0:x1].copy()
    mask = polygon_mask(shelf.polygon, h, w)[y0:y1, x0:x1]
    crop[~mask] = 0
    return crop


def shelf_thumbnail(image: np.ndarray, shelf: ShelfPolygon) -> str | None:
    """96×96-max JPEG (q=70) of the shelf polygon as base64; ``None`` if unavailable or > 16 KB."""
    crop = shelf_crop(image, shelf)
    if crop is None:
        return None
    data = encode_jpeg(fit_thumbnail(crop))
    if data is None:
        return None
    b64 = base64.b64encode(data).decode("ascii")
    return b64 if len(b64) <= MAX_B64_CHARS else None


__all__ = ["JPEG_QUALITY", "MAX_B64_CHARS", "THUMB_SIDE", "encode_jpeg", "fit_thumbnail", "shelf_crop", "shelf_thumbnail"]
