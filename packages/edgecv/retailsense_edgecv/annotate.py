"""Preview overlay for the SenseBoard live tile.

``annotate_frame`` draws zones (coloured by kind), lines with an IN arrow,
shelves coloured by state, track boxes with ids and a queue-count label on an
in-memory copy of the frame.  It never touches disk: the privacy statement
("no raw video persisted") is enforced by construction - the only consumer is
the MJPEG/JPEG preview endpoint.

Privacy: when ``blur_people`` is set every person box is **pixelated**
(downscale 12x, upscale with nearest-neighbour) *before* overlays are drawn,
so an owner can see "someone is at shelf B" but never who.  Pixelation is
confined to the track boxes - the test ``test_annotate_blur_changes_person_pixels_only``
checks exactly that.

``cfg_view`` is a plain dict so the app can pass either pydantic config
objects or JSON-ish dicts (e.g. from the zone editor before save)::

    {"zones": [Zone|dict], "lines": [Line|dict], "shelves": [ShelfPolygon|dict],
     "shelf_states": {shelf_id: ShelfState|str}, "queue_counts": {zone_id: int},
     "show_ids": True, "label": "cam-synth 4.0 fps"}

:func:`view_from_config` builds it from a ``StoreConfig`` for one camera.
"""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np

from retailsense_contracts.config import StoreConfig
from retailsense_contracts.interfaces import Track

PIXELATE_FACTOR = 12

ZONE_COLOURS: dict[str, tuple[int, int, int]] = {  # BGR
    "aisle": (200, 160, 40),
    "queue": (40, 120, 255),
    "entrance": (60, 200, 60),
    "counter": (180, 60, 200),
    "store": (150, 150, 150),
    "custom": (120, 180, 180),
}
SHELF_COLOURS: dict[str, tuple[int, int, int]] = {
    "stocked": (60, 200, 60),
    "partial": (40, 200, 255),
    "empty": (40, 40, 230),
    "unknown": (160, 160, 160),
}
TRACK_COLOUR = (0, 255, 0)
TEXT_COLOUR = (255, 255, 255)
FONT = cv2.FONT_HERSHEY_SIMPLEX


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _poly(points: Any) -> np.ndarray:
    return np.asarray(points, dtype=np.int32).reshape(-1, 1, 2)


def view_from_config(cfg: StoreConfig, camera_id: str, **extra: Any) -> dict[str, Any]:
    """``cfg_view`` for one camera from the store config (states/counts supplied via ``extra``)."""
    view: dict[str, Any] = {
        "zones": [z for z in cfg.zones if z.camera_id == camera_id],
        "lines": [ln for ln in cfg.lines if ln.camera_id == camera_id],
        "shelves": [s for s in cfg.shelves if s.camera_id == camera_id],
        "shelf_states": {},
        "queue_counts": {},
        "show_ids": True,
    }
    view.update(extra)
    return view


def pixelate_boxes(image: np.ndarray, boxes: list[tuple[float, float, float, float]], factor: int = PIXELATE_FACTOR) -> None:
    """In-place mosaic of each xyxy box (down ``factor``x, nearest-neighbour up)."""
    h, w = image.shape[:2]
    for b in boxes:
        x0, y0 = max(0, int(b[0])), max(0, int(b[1]))
        x1, y1 = min(w, int(np.ceil(b[2]))), min(h, int(np.ceil(b[3])))
        if x1 - x0 < 2 or y1 - y0 < 2:
            continue
        roi = image[y0:y1, x0:x1]
        small = cv2.resize(roi, (max(1, (x1 - x0) // factor), max(1, (y1 - y0) // factor)), interpolation=cv2.INTER_AREA)
        image[y0:y1, x0:x1] = cv2.resize(small, (x1 - x0, y1 - y0), interpolation=cv2.INTER_NEAREST)


def _draw_zones(out: np.ndarray, zones: list[Any]) -> None:
    for z in zones:
        kind = str(_get(z, "kind", "custom"))
        colour = ZONE_COLOURS.get(kind, ZONE_COLOURS["custom"])
        pts = _poly(_get(z, "polygon", []))
        if len(pts) < 3:
            continue
        cv2.polylines(out, [pts], isClosed=True, color=colour, thickness=1)
        name = _get(z, "name") or _get(z, "zone_id", "")
        x, y = int(pts[0, 0, 0]), int(pts[0, 0, 1])
        cv2.putText(out, str(name), (x + 3, y + 12), FONT, 0.35, colour, 1, cv2.LINE_AA)


def _draw_lines(out: np.ndarray, lines: list[Any]) -> None:
    for ln in lines:
        s = np.asarray(_get(ln, "start", [0, 0]), dtype=float)
        e = np.asarray(_get(ln, "end", [0, 0]), dtype=float)
        kind = str(_get(ln, "kind", "custom"))
        colour = ZONE_COLOURS.get(kind, ZONE_COLOURS["custom"])
        cv2.line(out, tuple(s.astype(int)), tuple(e.astype(int)), colour, 2)
        # IN is the LEFT side of start->end in image coords (contracts geometry.side_of_line)
        d = e - s
        n = np.hypot(*d)
        if n > 1e-6:
            left = np.array([d[1], -d[0]]) / n  # rotate -90deg: left normal (y down)
            mid = (s + e) / 2.0
            tip = mid + left * 14.0
            cv2.arrowedLine(out, tuple(mid.astype(int)), tuple(tip.astype(int)), colour, 1, tipLength=0.4)
            cv2.putText(out, "IN", (int(tip[0]) + 2, int(tip[1]) + 4), FONT, 0.35, colour, 1, cv2.LINE_AA)


def _draw_shelves(out: np.ndarray, shelves: list[Any], states: dict[str, Any]) -> None:
    for sh in shelves:
        sid = str(_get(sh, "shelf_id", ""))
        state = str(states.get(sid, "unknown"))
        colour = SHELF_COLOURS.get(state, SHELF_COLOURS["unknown"])
        pts = _poly(_get(sh, "polygon", []))
        if len(pts) < 3:
            continue
        cv2.polylines(out, [pts], isClosed=True, color=colour, thickness=2)
        label = f"{_get(sh, 'name', sid)}:{state}"
        x, y = int(pts[0, 0, 0]), int(pts[0, 0, 1])
        cv2.putText(out, label, (x + 2, max(10, y - 3)), FONT, 0.35, colour, 1, cv2.LINE_AA)


def _draw_tracks(out: np.ndarray, tracks: list[Track], show_ids: bool) -> None:
    for tr in tracks:
        x0, y0, x1, y1 = (int(round(v)) for v in tr.bbox)
        colour = TRACK_COLOUR if tr.confirmed else (0, 200, 200)
        cv2.rectangle(out, (x0, y0), (x1, y1), colour, 1)
        if show_ids:
            cv2.putText(out, f"#{tr.track_id}", (x0, max(8, y0 - 2)), FONT, 0.35, colour, 1, cv2.LINE_AA)


def _draw_queue_counts(out: np.ndarray, zones: list[Any], counts: dict[str, Any]) -> None:
    for z in zones:
        zid = str(_get(z, "zone_id", ""))
        if zid not in counts:
            continue
        pts = _poly(_get(z, "polygon", []))
        if len(pts) == 0:
            continue
        x, y = int(pts[:, 0, 0].min()), int(pts[:, 0, 1].max())
        cv2.putText(out, f"queue {counts[zid]}", (x, min(out.shape[0] - 2, y + 14)), FONT, 0.45, ZONE_COLOURS["queue"], 1, cv2.LINE_AA)


def annotate_frame(frame: np.ndarray, tracks: list[Track], cfg_view: dict[str, Any], *, blur_people: bool) -> np.ndarray:
    """Return an annotated *copy* of ``frame`` (the input is never modified, nothing is written to disk)."""
    out = frame.copy()
    if blur_people and tracks:
        pixelate_boxes(out, [tr.bbox for tr in tracks])
    cfg_view = cfg_view or {}
    zones = list(cfg_view.get("zones", []))
    _draw_zones(out, zones)
    _draw_lines(out, list(cfg_view.get("lines", [])))
    _draw_shelves(out, list(cfg_view.get("shelves", [])), dict(cfg_view.get("shelf_states", {}) or {}))
    _draw_tracks(out, tracks, bool(cfg_view.get("show_ids", True)))
    _draw_queue_counts(out, zones, dict(cfg_view.get("queue_counts", {}) or {}))
    label = cfg_view.get("label")
    if label:
        cv2.putText(out, str(label), (6, out.shape[0] - 6), FONT, 0.4, TEXT_COLOUR, 1, cv2.LINE_AA)
    return out


__all__ = ["PIXELATE_FACTOR", "SHELF_COLOURS", "ZONE_COLOURS", "annotate_frame", "pixelate_boxes", "view_from_config"]
