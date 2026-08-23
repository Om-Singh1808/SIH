"""Line crossing detection (``LineCrosser``).

Design rationale
----------------
Counting people across a virtual line looks trivial ("side changed -> count")
but a naive rule over-counts badly in practice: detector jitter makes an
anchor hop back and forth across the line while a shopper stands in the
doorway, and a tracker that re-acquires a lost person can teleport the anchor
to the other side without a real walk-through.  ``LineCrosser`` therefore
requires *four* things before it emits a :class:`Crossing`:

1. **Side flip** using the normative rule from
   :func:`retailsense_contracts.geometry.side_of_line` -- moving from side
   ``-1`` to ``+1`` is ``Direction.IN``, the reverse is ``OUT``.
2. **Path intersection** -- the segment joining the last settled anchor and
   the newly settled anchor must actually intersect the line (extended by 10 % at
   each end so that a person brushing past an endpoint still counts).
3. **Persistence on both sides** (hysteresis) -- a side only becomes the
   track's *settled* side after ``min_frames`` consecutive frames there, and
   a crossing is the moment the settled side changes.  A flip that is undone
   before ``min_frames`` frames is jitter and leaves no trace, so a shopper
   hesitating on the threshold is counted exactly once, when they finally
   commit to a side.
4. **Cooldown** -- at most one crossing per ``(track, line)`` every
   ``cooldown_s`` seconds.

Points exactly *on* the line (side ``0``) are ignored: they neither reset nor
extend the frame counters, so a shopper pausing on the threshold cannot
break a crossing in progress.

State is kept per ``(track_id, line_id)`` and dropped when the track
disappears, so memory is bounded by the number of live tracks.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from retailsense_contracts.config import Line
from retailsense_contracts.enums import Direction
from retailsense_contracts.geometry import Point, segments_intersect, side_of_line
from retailsense_contracts.interfaces import Crossing

#: Frames a side must be held before/after a flip (spec D5: ">= 2 frames").
DEFAULT_MIN_FRAMES = 2
#: Seconds between two crossings of the same track over the same line.
DEFAULT_COOLDOWN_S = 2.0
#: Fraction by which the line is extended at each end for the segment test.
LINE_EXTENSION = 0.10


def extend_line(start: Point, end: Point, frac: float = LINE_EXTENSION) -> tuple[Point, Point]:
    """Return ``start``/``end`` pushed outwards by ``frac`` of the line length on each side."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    return (start[0] - dx * frac, start[1] - dy * frac), (end[0] + dx * frac, end[1] + dy * frac)


@dataclass
class _TrackLineState:
    """Per ``(track, line)`` hysteresis state.

    ``settled_side`` is the side the track has *demonstrably* been on (held for
    ``min_frames``); ``settled_pt`` is its last anchor while on that side.  A
    different side becomes a candidate and has to persist ``min_frames``
    frames before it replaces the settled side -- that replacement is the
    crossing.
    """

    settled_side: int | None = None
    settled_pt: Point | None = None
    cand_side: int | None = None
    cand_frames: int = 0
    last_cross_ts: float | None = None


@dataclass
class LineCrosser:
    """Stateful crossing detector for a set of lines belonging to one camera."""

    lines: list[Line]
    min_frames: int = DEFAULT_MIN_FRAMES
    cooldown_s: float = DEFAULT_COOLDOWN_S
    _state: dict[tuple[int, str], _TrackLineState] = field(default_factory=dict, repr=False)
    _ext: dict[str, tuple[Point, Point]] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        self.reload(self.lines)

    # ------------------------------------------------------------------ config

    def reload(self, lines: list[Line]) -> None:
        """Swap the line set; state for lines that still exist (by id) is kept."""
        self.lines = list(lines)
        self._ext = {
            ln.line_id: extend_line((ln.start[0], ln.start[1]), (ln.end[0], ln.end[1])) for ln in self.lines
        }
        keep = set(self._ext)
        for key in [k for k in self._state if k[1] not in keep]:
            del self._state[key]

    # ------------------------------------------------------------------ update

    def update(self, track_id: int, pt: Point, ts: float) -> list[Crossing]:
        """Feed one anchor position for ``track_id``; returns confirmed crossings (usually 0 or 1)."""
        out: list[Crossing] = []
        for ln in self.lines:
            cr = self._update_one(ln, track_id, pt, ts)
            if cr is not None:
                out.append(cr)
        return out

    def _update_one(self, ln: Line, track_id: int, pt: Point, ts: float) -> Crossing | None:
        side = side_of_line(pt, (ln.start[0], ln.start[1]), (ln.end[0], ln.end[1]))
        if side == 0:
            return None  # on the line: neither side gains evidence
        st = self._state.setdefault((track_id, ln.line_id), _TrackLineState())

        if side == st.settled_side:
            # Back on (or still on) the settled side: any candidate flip was jitter.
            st.cand_side, st.cand_frames, st.settled_pt = None, 0, pt
            return None

        # Opposite (or first) side: accumulate evidence for it.
        if st.cand_side == side:
            st.cand_frames += 1
        else:
            st.cand_side, st.cand_frames = side, 1
        if st.cand_frames < self.min_frames:
            return None

        # The new side has persisted: it becomes the settled side.
        prev_side, prev_pt = st.settled_side, st.settled_pt
        st.settled_side, st.settled_pt = side, pt
        st.cand_side, st.cand_frames = None, 0
        if prev_side is None or prev_pt is None:
            return None  # first settle: nothing to cross from
        if not segments_intersect(prev_pt, pt, *self._ext[ln.line_id]):
            return None  # side flipped but the path never met the line (teleport / re-acquired track)
        if st.last_cross_ts is not None and (ts - st.last_cross_ts) < self.cooldown_s:
            return None  # too soon after the previous count for this track
        st.last_cross_ts = ts
        direction = Direction.IN if (prev_side == -1 and side == 1) else Direction.OUT
        return Crossing(line_id=ln.line_id, line_kind=ln.kind, track_id=track_id, direction=direction, ts=ts)

    # ------------------------------------------------------------------ housekeeping

    def forget(self, track_id: int) -> None:
        """Drop all state for a track that the tracker has lost."""
        for key in [k for k in self._state if k[0] == track_id]:
            del self._state[key]

    def retain(self, live_track_ids: set[int]) -> None:
        """Drop state for every track not in ``live_track_ids``."""
        for key in [k for k in self._state if k[0] not in live_track_ids]:
            del self._state[key]

    def reset(self) -> None:
        self._state.clear()

    @property
    def tracked(self) -> int:
        """Number of ``(track, line)`` pairs currently remembered (for tests / diagnostics)."""
        return len(self._state)


__all__ = ["DEFAULT_COOLDOWN_S", "DEFAULT_MIN_FRAMES", "LINE_EXTENSION", "LineCrosser", "extend_line"]
