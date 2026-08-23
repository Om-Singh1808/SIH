"""Line-crossing tests against the demo entrance line [120,315] -> [60,315]."""

from __future__ import annotations

from retailsense_contracts.enums import Direction, LineKind
from retailsense_contracts.geometry import side_of_line
from retailsense_contracts.testing import FakeZoneEngine, IdentityMapper

from retailsense_edgeanalytics.lines import LineCrosser, extend_line

from .conftest import DT, T0, track, walk

UP = [(90, 340), (90, 335), (90, 330), (90, 325), (90, 305), (90, 300), (90, 295), (90, 290)]


def test_normative_side_rule_demo_entrance(cfg):
    ln = cfg.line("entrance")
    assert side_of_line((90, 330), ln.start, ln.end) == -1  # below the line (y larger) = side -1
    assert side_of_line((90, 300), ln.start, ln.end) == +1  # above = side +1 -> moving up is IN


def test_line_in_out_demo_entrance(make_engine):
    eng = make_engine()
    ups = walk(eng, UP)
    crossings = [c for u in ups for c in u.crossings]
    assert len(crossings) == 1
    assert crossings[0].direction == Direction.IN
    assert crossings[0].line_id == "entrance" and crossings[0].line_kind == LineKind.ENTRANCE
    footfall = [f for u in ups for f in u.footfall]
    assert len(footfall) == 1 and footfall[0].direction == Direction.IN and footfall[0].count == 1
    assert eng.in_total == 1 and eng.out_total == 0

    # The same path downward, well after the cooldown, is an OUT.
    down = [(x, y) for x, y in reversed(UP)]
    ups2 = walk(eng, down, tid=2, t0=T0 + 60)
    crossings2 = [c for u in ups2 for c in u.crossings]
    assert [c.direction for c in crossings2] == [Direction.OUT]
    assert eng.in_total == 1 and eng.out_total == 1


def test_real_engine_agrees_with_contracts_fake_on_clean_walk(cfg):
    """On a jitter-free walk the real engine and the contracts FakeZoneEngine count the same."""
    fake = FakeZoneEngine(cfg.cameras[0], cfg.zones, cfg.lines, IdentityMapper(), cfg.rules, cfg.floorplan)
    ups = [fake.update([track(1, x, y)], T0 + i * DT) for i, (x, y) in enumerate(UP)]
    assert [c.direction for u in ups for c in u.crossings] == [Direction.IN]


def test_line_jitter_no_double_count(make_engine):
    """An anchor flickering across the line while a shopper hesitates counts at most once."""
    eng = make_engine()
    path = [(90, 330), (90, 325), (90, 318), (90, 312), (90, 318), (90, 312), (90, 317), (90, 313)]
    path += [(90, 310), (90, 300), (90, 290), (90, 280)]
    ups = walk(eng, path)
    dirs = [c.direction for u in ups for c in u.crossings]
    assert dirs == [Direction.IN]
    assert eng.in_total == 1 and eng.out_total == 0


def test_line_requires_segment_intersection(make_engine):
    """A teleport from one side to the other that never crosses the line segment is not a crossing."""
    eng = make_engine()
    # x=300 is far from the line's x-range [60,120] (even extended by 10 %): sides flip but no intersection.
    path = [(300, 330), (300, 330), (300, 300), (300, 300), (300, 300)]
    ups = walk(eng, path)
    assert all(not u.crossings for u in ups)


def test_line_cooldown(make_engine):
    """Two genuine crossings by the same track within 2 s collapse into one."""
    eng = make_engine(line_cooldown_s=2.0)
    # up (IN) then immediately back down (OUT) within ~1.5 s total at 4 fps.
    path = [(90, 330), (90, 325), (90, 305), (90, 300), (90, 325), (90, 330), (90, 335)]
    ups = walk(eng, path)
    dirs = [c.direction for u in ups for c in u.crossings]
    assert dirs == [Direction.IN]
    # After the cooldown has elapsed, the reverse crossing is counted.
    path2 = [(90, 330), (90, 330), (90, 305), (90, 300), (90, 300)]
    ups2 = walk(eng, path2, t0=T0 + 10)
    assert [c.direction for u in ups2 for c in u.crossings] == [Direction.IN]
    path3 = [(90, 330), (90, 335), (90, 340)]
    ups3 = walk(eng, path3, t0=T0 + 20)
    assert [c.direction for u in ups3 for c in u.crossings] == [Direction.OUT]


def test_line_needs_two_frames_each_side(cfg):
    ln = cfg.line("entrance")
    lc = LineCrosser([ln], min_frames=2, cooldown_s=2.0)
    # One frame below then above: old side held only 1 frame -> no crossing.
    assert lc.update(1, (90, 330), T0) == []
    assert lc.update(1, (90, 300), T0 + DT) == []
    assert lc.update(1, (90, 295), T0 + 2 * DT) == []
    # Fresh track held 2 frames below, then 2 above -> crossing on the confirming frame.
    assert lc.update(2, (90, 330), T0) == []
    assert lc.update(2, (90, 330), T0 + DT) == []
    assert lc.update(2, (90, 300), T0 + 2 * DT) == []
    out = lc.update(2, (90, 300), T0 + 3 * DT)
    assert len(out) == 1 and out[0].direction == Direction.IN and out[0].track_id == 2


def test_unconfirmed_tracks_ignored(make_engine):
    eng = make_engine()
    for i, (x, y) in enumerate(UP):
        u = eng.update([track(7, x, y, confirmed=False)], T0 + i * DT)
        assert not u.crossings and not u.zone_members
    assert eng.in_total == 0


def test_extend_line():
    s, e = extend_line((120, 315), (60, 315))
    assert s == (126, 315) and e == (54, 315)
