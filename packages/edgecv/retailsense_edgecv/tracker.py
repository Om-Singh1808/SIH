"""ByteTrack-lite: a small, readable multi-object tracker.

Why not pull in a tracking library?  The edge box runs 4 fps per camera and
tracks a handful of shoppers; what matters is *stable ids* (footfall, dwell
and queue counts all derive from them) and zero heavyweight dependencies.

Algorithm per frame (ByteTrack, Zhang et al. 2022, simplified):

1. Every live track predicts its next box with a constant-velocity Kalman
   filter (:class:`~retailsense_edgecv.kalman.KalmanBox`).
2. **Stage 1** - high-confidence detections are matched to all tracks by
   Hungarian assignment (``scipy.optimize.linear_sum_assignment``) on the
   cost ``1 - IoU(predicted, detection)``; pairs with cost above
   ``match_thresh`` are rejected.
3. **Stage 2** - low-confidence detections (``low_thresh <= conf < high_thresh``)
   are matched to the tracks left over from stage 1.  This is the ByteTrack
   trick: an occluded person whose score dips is kept alive instead of
   spawning a new id.
4. **Centroid gate** - still-unmatched high-confidence detections are matched
   to remaining tracks whose predicted centre is within ``centroid_gate_px``.
   This rescues fast movers whose predicted/detected boxes no longer overlap
   (e.g. 8 px/frame sim shoppers at 20 px box size).
5. Leftover high-confidence detections start new tracks; tracks unseen for
   ``max_age`` frames are dropped.  A track is ``confirmed`` after ``min_hits``
   matches.  Ids come from a monotonically increasing counter and are
   **never reused**, so downstream dwell/queue logic can key on them safely.
"""

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linear_sum_assignment

from retailsense_contracts.geometry import iou
from retailsense_contracts.interfaces import Detection, Track

from .kalman import BBox, KalmanBox


@dataclass
class _TrackState:
    track_id: int
    kf: KalmanBox
    bbox: BBox  # last observed (or predicted while coasting) xyxy
    conf: float
    age: int = 1
    hits: int = 1
    time_since_update: int = 0
    predicted: BBox = field(default=(0.0, 0.0, 0.0, 0.0))

    def to_track(self, min_hits: int) -> Track:
        return Track(
            track_id=self.track_id,
            bbox=self.bbox,
            conf=self.conf,
            age=self.age,
            hits=self.hits,
            time_since_update=self.time_since_update,
            confirmed=self.hits >= min_hits,
        )


def _centers(boxes: np.ndarray) -> np.ndarray:
    return np.stack([(boxes[:, 0] + boxes[:, 2]) / 2.0, (boxes[:, 1] + boxes[:, 3]) / 2.0], axis=1)


def _assign(cost: np.ndarray, max_cost: float) -> tuple[list[tuple[int, int]], list[int], list[int]]:
    """Hungarian assignment; returns (matches, unmatched_rows, unmatched_cols)."""
    if cost.size == 0:
        return [], list(range(cost.shape[0])), list(range(cost.shape[1]))
    rows, cols = linear_sum_assignment(cost)
    matches = [(int(r), int(c)) for r, c in zip(rows, cols, strict=True) if cost[r, c] <= max_cost]
    mr = {r for r, _ in matches}
    mc = {c for _, c in matches}
    return matches, [r for r in range(cost.shape[0]) if r not in mr], [c for c in range(cost.shape[1]) if c not in mc]


class ByteTrackLite:
    """Tracker satisfying the contracts ``Tracker`` Protocol."""

    def __init__(
        self,
        high_thresh: float = 0.5,
        low_thresh: float = 0.1,
        match_thresh: float = 0.8,
        max_age: int = 30,
        min_hits: int = 2,
        centroid_gate_px: float = 60.0,
    ):
        self.high_thresh = float(high_thresh)
        self.low_thresh = float(low_thresh)
        self.match_thresh = float(match_thresh)  # max allowed (1 - IoU)
        self.max_age = int(max_age)
        self.min_hits = int(min_hits)
        self.centroid_gate_px = float(centroid_gate_px)
        self._tracks: list[_TrackState] = []
        self._next_id = 1
        self.frame_count = 0

    # Tracker Protocol ------------------------------------------------------
    def reset(self) -> None:
        """Drop all tracks. The id counter is intentionally *not* reset (ids are never reused)."""
        self._tracks.clear()
        self.frame_count = 0

    def update(self, detections: list[Detection], ts: float) -> list[Track]:
        self.frame_count += 1
        for t in self._tracks:
            t.predicted = t.kf.predict()
            t.age += 1
            t.time_since_update += 1

        high = [d for d in detections if d.conf >= self.high_thresh]
        low = [d for d in detections if self.low_thresh <= d.conf < self.high_thresh]

        # stage 1: high-confidence dets vs all tracks on IoU
        matches, un_tracks, un_high = self._match_iou(list(range(len(self._tracks))), high)
        for ti, di in matches:
            self._hit(self._tracks[ti], high[di])

        # stage 2: low-confidence dets vs leftover tracks on IoU
        if low and un_tracks:
            m2, un_tracks, _ = self._match_iou(un_tracks, low)
            for ti, di in m2:
                self._hit(self._tracks[ti], low[di])

        # stage 3: centroid gate for leftover high dets vs leftover tracks
        if un_high and un_tracks:
            m3, un_tracks, un_high = self._match_centroid(un_tracks, [high[i] for i in un_high], un_high)
            for ti, di in m3:
                self._hit(self._tracks[ti], high[di])

        # coasting tracks show their prediction
        for ti in un_tracks:
            t = self._tracks[ti]
            t.bbox = t.predicted

        # births
        for di in un_high:
            self._tracks.append(self._new_track(high[di]))

        # deaths
        self._tracks = [t for t in self._tracks if t.time_since_update <= self.max_age]
        return [t.to_track(self.min_hits) for t in self._tracks]

    # internals -------------------------------------------------------------
    def _match_iou(
        self, track_idx: list[int], dets: list[Detection]
    ) -> tuple[list[tuple[int, int]], list[int], list[int]]:
        if not track_idx or not dets:
            return [], list(track_idx), list(range(len(dets)))
        pred = np.array([self._tracks[i].predicted for i in track_idx], dtype=float)
        dbox = np.array([d.bbox for d in dets], dtype=float)
        cost = 1.0 - iou(pred, dbox)
        m, ur, uc = _assign(cost, self.match_thresh)
        return [(track_idx[r], c) for r, c in m], [track_idx[r] for r in ur], uc

    def _match_centroid(
        self, track_idx: list[int], dets: list[Detection], det_idx: list[int]
    ) -> tuple[list[tuple[int, int]], list[int], list[int]]:
        pred = np.array([self._tracks[i].predicted for i in track_idx], dtype=float)
        dbox = np.array([d.bbox for d in dets], dtype=float)
        dist = np.linalg.norm(_centers(pred)[:, None, :] - _centers(dbox)[None, :, :], axis=2)
        m, ur, uc = _assign(dist, self.centroid_gate_px)
        return [(track_idx[r], det_idx[c]) for r, c in m], [track_idx[r] for r in ur], [det_idx[c] for c in uc]

    @staticmethod
    def _hit(t: _TrackState, d: Detection) -> None:
        t.kf.update(d.bbox)
        t.bbox = (float(d.bbox[0]), float(d.bbox[1]), float(d.bbox[2]), float(d.bbox[3]))
        t.conf = float(d.conf)
        t.hits += 1
        t.time_since_update = 0

    def _new_track(self, d: Detection) -> _TrackState:
        tid = self._next_id
        self._next_id += 1
        bbox = (float(d.bbox[0]), float(d.bbox[1]), float(d.bbox[2]), float(d.bbox[3]))
        return _TrackState(track_id=tid, kf=KalmanBox(bbox), bbox=bbox, conf=float(d.conf))

    @property
    def next_id(self) -> int:
        return self._next_id


__all__ = ["ByteTrackLite"]
