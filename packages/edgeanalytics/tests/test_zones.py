"""Zone membership, dwell, occupancy, heatmap and reload tests for ZoneEngine."""

from __future__ import annotations

from retailsense_contracts.config import Line, Zone
from retailsense_contracts.enums import ZoneKind
from retailsense_contracts.events import Observation
from retailsense_contracts.interfaces import ZoneEngine as ZoneEngineProtocol
from retailsense_contracts.registry import is_real, resolve

from retailsense_edgeanalytics import ZoneEngine

from .conftest import DT, T0, ScaleMapper, track, walk

# Demo aisle-1 polygon: x 130..430, y 70..200.  (200,120) is well inside; (200,250) is outside (and in no zone
# other than 'store').
INSIDE = (200.0, 120.0)
OUTSIDE = (200.0, 250.0)


def test_registry_resolves_real_engine():
    assert resolve("zone_engine") is ZoneEngine
    assert is_real("zone_engine")
    assert issubclass(ZoneEngine, ZoneEngineProtocol)


def test_zone_inertia(make_engine):
    """Membership needs 2 consecutive frames inside and 2 consecutive frames outside to change."""
    eng = make_engine()
    u1 = eng.update([track(1, *INSIDE)], T0)
    assert 1 not in u1.zone_members.get("aisle-1", [])  # 1 frame: not yet
    u2 = eng.update([track(1, *INSIDE)], T0 + DT)
    assert u2.zone_members["aisle-1"] == [1]  # 2 frames: member
    u3 = eng.update([track(1, *OUTSIDE)], T0 + 2 * DT)
    assert u3.zone_members["aisle-1"] == [1]  # 1 frame outside: still member (inertia)
    u4 = eng.update([track(1, *INSIDE)], T0 + 3 * DT)
    assert u4.zone_members["aisle-1"] == [1]  # flicker forgiven
    u5 = eng.update([track(1, *OUTSIDE)], T0 + 4 * DT)
    u6 = eng.update([track(1, *OUTSIDE)], T0 + 5 * DT)
    assert u5.zone_members["aisle-1"] == [1]
    assert "aisle-1" not in u6.zone_members  # 2 frames outside: gone
    # A single-frame blip inside never becomes a membership.
    eng.update([track(2, *INSIDE)], T0 + 6 * DT)
    u8 = eng.update([track(2, *OUTSIDE)], T0 + 7 * DT)
    assert 2 not in u8.zone_members.get("aisle-1", [])


def test_dwell_sample_on_exit_and_loss(make_engine):
    eng = make_engine()
    n_in = 12  # 12 frames x 0.25 s = 3 s inside
    path = [INSIDE] * n_in + [OUTSIDE] * 3
    ups = walk(eng, path)
    samples = [d for u in ups for d in u.dwell_samples]
    assert len(samples) == 1
    s = samples[0]
    assert s.zone_id == "aisle-1"
    assert abs(s.dwell_s - n_in * DT) <= DT + 1e-9  # frames-in-zone x dt, +-1 frame
    assert s.entered_ts == T0 and s.exited_ts == T0 + n_in * DT
    # Persisted form has no track id (privacy) and validates as an Observation.
    obs = ups[-3].observations() + ups[-2].observations() + ups[-1].observations()
    assert any(o.type == "dwell.sample" for o in obs)
    assert "track" not in s.model_dump_json()

    # Track loss: the id simply stops being reported -> sample closed at the current ts.
    ups2 = walk(eng, [INSIDE] * 8, tid=5, t0=T0 + 100)
    assert not [d for u in ups2 for d in u.dwell_samples]
    u_loss = eng.update([], T0 + 100 + 8 * DT)
    assert len(u_loss.dwell_samples) == 1
    assert abs(u_loss.dwell_samples[0].dwell_s - 8 * DT) <= DT + 1e-9

    # Visits shorter than 1 s are not reported.
    ups3 = walk(eng, [INSIDE] * 3 + [OUTSIDE] * 3, tid=9, t0=T0 + 200)
    assert not [d for u in ups3 for d in u.dwell_samples]


def test_dwell_only_for_dwell_kinds(make_engine):
    """The 'store' zone contains everything but never produces dwell samples."""
    eng = make_engine()
    pt = (300.0, 250.0)  # inside 'store' only
    ups = walk(eng, [pt] * 10 + [(700.0, 250.0)] * 3)
    assert ups[5].zone_members == {"store": [1]}
    assert not [d for u in ups for d in u.dwell_samples]


def test_occupancy_interval(make_engine, cfg):
    eng = make_engine()
    interval = cfg.rules.occupancy_interval_s
    u0 = eng.update([track(1, *INSIDE)], T0)
    assert {o.zone_id for o in u0.occupancy} == {z.zone_id for z in cfg.zones}  # first frame reports
    u1 = eng.update([track(1, *INSIDE)], T0 + DT)
    assert u1.occupancy == []
    u_mid = eng.update([track(1, *INSIDE)], T0 + interval - DT)
    assert u_mid.occupancy == []
    u_due = eng.update([track(1, *INSIDE)], T0 + interval)
    occ = {o.zone_id: o for o in u_due.occupancy}
    assert occ["aisle-1"].count == 1 and occ["aisle-1"].zone_kind == ZoneKind.AISLE
    assert occ["aisle-1"].window_s == interval
    u_next = eng.update([track(1, *INSIDE)], T0 + interval + DT)
    assert u_next.occupancy == []


def test_store_occupancy_is_in_minus_out_bounded(make_engine, cfg):
    eng = make_engine()
    # Two shoppers walk in through the door (upward at x=90), a third only walks out.
    up = [(90, 340), (90, 335), (90, 330), (90, 305), (90, 300), (90, 295)]
    down = list(reversed(up))
    for i, ((x1, y1), (x2, y2), (x3, y3)) in enumerate(zip(up, up, down, strict=True)):
        eng.update([track(1, x1, y1), track(2, x2 + 5, y2), track(3, x3 + 10, y3)], T0 + i * DT)
    assert eng.in_total == 2 and eng.out_total == 1
    assert eng.footfall.occupancy == 1
    # Now two more leave than entered: bounded at zero.
    t = T0 + 50
    for i, (x, y) in enumerate(down):
        eng.update([track(4, x, y), track(5, x + 8, y)], t + i * DT)
    assert eng.out_total == 3 and eng.footfall.occupancy == 0
    u = eng.update([], t + 100)
    store = next(o for o in u.occupancy if o.zone_kind == ZoneKind.STORE)
    assert store.count == 0


def test_heatmap_accumulate_and_flush_deltas(make_engine, cfg):
    eng = make_engine()
    cell = cfg.floorplan.heat_cell_px
    flush_s = cfg.rules.heat_flush_s
    n = int(flush_s / DT)  # frames until the first flush is due
    # One track sits still at (200,120); a second moves one cell to the right every 16 frames.
    ups = []
    for i in range(n + 1):
        x2 = 300.0 + (i // 16) * cell  # 300..600 px: stays inside the 640 px floorplan
        ups.append(eng.update([track(1, *INSIDE), track(2, x2, 300.0)], T0 + i * DT))
    heats = [u.heat for u in ups if u.heat is not None]
    assert len(heats) == 1 and ups[-1].heat is heats[0]
    h = heats[0]
    assert h.cell_px == cell and h.width_cells == 32 and h.height_cells == 18
    total_dwell = sum(t.dwell_s for t in h.tiles)
    # Both tracks confirmed for n intervals of DT each (first frame has dt=0).
    assert abs(total_dwell - 2 * n * DT) < 1e-6
    still = next(t for t in h.tiles if (t.cell_x, t.cell_y) == (200 // cell, 120 // cell))
    assert still.visits == 1 and abs(still.dwell_s - n * DT) < 1e-6
    moving = [t for t in h.tiles if t.cell_y == 300 // cell]
    assert sum(t.visits for t in moving) == (n // 16) + 1  # one visit per cell change
    assert all(t.hour_bucket == int(T0 // 3600) for t in h.tiles)

    # Deltas: the next flush only contains what accumulated after the first one.
    for i in range(n + 1, 2 * n + 1):
        u = eng.update([track(1, *INSIDE)], T0 + i * DT)
    assert u.heat is not None
    assert abs(sum(t.dwell_s for t in u.heat.tiles) - n * DT) < 1e-6
    assert u.observations()[-1].type == "heatmap.tiles"


def test_homography_applied_to_heat(cfg):
    """Heat cells are computed in floor coordinates: a x2 mapper moves the cell of (200,120)."""
    from retailsense_contracts.testing import IdentityMapper

    ident = ZoneEngine(cfg.cameras[0], cfg.zones, cfg.lines, IdentityMapper(), cfg.rules, cfg.floorplan)
    scaled = ZoneEngine(cfg.cameras[0], cfg.zones, cfg.lines, ScaleMapper(1.5), cfg.rules, cfg.floorplan)
    for i in range(3):
        ident.update([track(1, *INSIDE)], T0 + i * DT)
        scaled.update([track(1, *INSIDE)], T0 + i * DT)
    hi, hs = ident.flush(T0 + 10).heat, scaled.flush(T0 + 10).heat
    assert hi is not None and hs is not None
    cell = cfg.floorplan.heat_cell_px
    assert (hi.tiles[0].cell_x, hi.tiles[0].cell_y) == (200 // cell, 120 // cell)
    assert (hs.tiles[0].cell_x, hs.tiles[0].cell_y) == (int(300 // cell), int(180 // cell))
    assert hs.tiles[0].dwell_s == hi.tiles[0].dwell_s
    # Zone membership and line logic stay in image space: unaffected by the mapper.
    assert scaled.members("aisle-1") == [] and ident.members("aisle-1") == []  # flushed
    # Points mapping outside the floorplan are clamped to the border cell, never lost.
    far = ZoneEngine(cfg.cameras[0], cfg.zones, cfg.lines, ScaleMapper(10.0), cfg.rules, cfg.floorplan)
    far.update([track(1, *INSIDE)], T0)
    far.update([track(1, *INSIDE)], T0 + DT)
    t = far.flush(T0 + 1).heat.tiles[0]
    assert (t.cell_x, t.cell_y) == (31, 17)


def test_reload_preserves_tracks(make_engine, cfg):
    eng = make_engine()
    walk(eng, [INSIDE] * 8)  # 2 s in aisle-1, member
    assert eng.members("aisle-1") == [1]
    # Reload with aisle-1 unchanged, aisle-2 removed, a new custom zone and an extra line.
    new_zone = Zone(zone_id="promo", camera_id="cam-synth", kind=ZoneKind.CUSTOM, polygon=[[0, 0], [50, 0], [50, 50]])
    new_line = Line(line_id="extra", camera_id="cam-synth", kind="custom", start=[0, 100], end=[50, 100])
    zones = [z for z in cfg.zones if z.zone_id != "aisle-2"] + [new_zone]
    eng.reload(zones, list(cfg.lines) + [new_line])
    assert eng.members("aisle-1") == [1]
    assert [z.zone_id for z in eng.zones] == ["store", "aisle-1", "queue-1", "promo"]
    assert [ln.line_id for ln in eng.lines] == ["entrance", "counter-1-line", "extra"]
    # The visit continues: leaving now yields a single sample spanning both halves.
    ups = walk(eng, [INSIDE] * 4 + [OUTSIDE] * 3, t0=T0 + 8 * DT)
    samples = [d for u in ups for d in u.dwell_samples]
    assert len(samples) == 1 and abs(samples[0].dwell_s - 12 * DT) <= DT + 1e-9
    assert samples[0].entered_ts == T0


def test_reload_keeps_line_state_mid_crossing(make_engine, cfg):
    eng = make_engine()
    walk(eng, [(90, 340), (90, 335), (90, 330)])
    eng.reload(cfg.zones, cfg.lines)  # same geometry pushed again (editor "save")
    ups = walk(eng, [(90, 305), (90, 300), (90, 295)], t0=T0 + 3 * DT)
    assert [c.direction.value for u in ups for c in u.crossings] == ["in"]


def test_flush_closes_visits_and_returns_heat(make_engine):
    eng = make_engine()
    walk(eng, [INSIDE] * 8)
    u = eng.flush(T0 + 8 * DT)
    assert len(u.dwell_samples) == 1 and u.heat is not None
    assert eng.members("aisle-1") == []
    obs = u.observations()
    assert all(isinstance(o, Observation) for o in obs)
    assert {o.type for o in obs} == {"dwell.sample", "heatmap.tiles"}
    # Nothing left after flush.
    u2 = eng.flush(T0 + 9)
    assert u2.dwell_samples == [] and u2.heat is None


def test_other_camera_geometry_is_ignored(cfg):
    from retailsense_contracts.testing import IdentityMapper

    other = Zone(zone_id="z-other", camera_id="cam-2", kind=ZoneKind.AISLE, polygon=[[0, 0], [9, 0], [9, 9]])
    eng = ZoneEngine(cfg.cameras[0], [*cfg.zones, other], cfg.lines, IdentityMapper(), cfg.rules, cfg.floorplan)
    assert "z-other" not in [z.zone_id for z in eng.zones]
