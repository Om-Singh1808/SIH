"""Coverage estimator tests on palette-rendered shelves and photo-like fixtures."""

from __future__ import annotations

import numpy as np
import pytest

from retailsense_contracts.config import ShelfPolygon
from retailsense_contracts.interfaces import CoverageEstimator
from retailsense_contracts.synthetic import SyntheticPalette
from retailsense_contracts.testing import draw_rect
from retailsense_edgeshelf.coverage import ClassicalCoverageEstimator, bgr_to_lab, count_facings_from_runs, local_std

from _render import blank_frame, render_shelf


def test_protocol_and_lab_sanity():
    est = ClassicalCoverageEstimator()
    assert isinstance(est, CoverageEstimator)
    lab = bgr_to_lab(np.array([[[255, 255, 255]], [[0, 0, 0]]], dtype=np.uint8))
    assert lab[0, 0, 0] == pytest.approx(100.0, abs=0.5) and lab[1, 0, 0] == pytest.approx(0.0, abs=0.5)
    flat = np.full((10, 10), 7.0)
    assert local_std(flat).max() == pytest.approx(0.0)


def test_coverage_full_partial_empty_synthetic(shelves):
    shelf = shelves["shelf-A"]  # horizontal, capacity 9, facing 15 px
    est = ClassicalCoverageEstimator()
    full = blank_frame()
    render_shelf(full, shelf, shelf.capacity_facings)
    ref = est.calibrate(full, shelf)
    assert ref.backing_bgr == list(SyntheticPalette.SHELF_BACKING)
    for n in (9, 6, 3, 0):
        img = blank_frame()
        render_shelf(img, shelf, n)
        res = est.estimate(img, shelf, ref)
        assert abs(res.coverage - n / shelf.capacity_facings) <= 0.1, (n, res)
        assert res.facings == n
    # without a reference the raw value is returned: still full-ish for a full shelf
    assert est.estimate(full, shelf, None).coverage >= 0.8


def test_coverage_vertical_shelf_long_axis(shelves):
    shelf = shelves["shelf-C"]  # vertical, capacity 7, facing 22 px along y
    est = ClassicalCoverageEstimator()
    full = blank_frame()
    render_shelf(full, shelf, shelf.capacity_facings)
    ref = est.calibrate(full, shelf)
    assert len(ref.profile) == 170  # one value per row of the 170-px-tall shelf
    for n in (7, 4, 1, 0):
        img = blank_frame()
        render_shelf(img, shelf, n)
        res = est.estimate(img, shelf, ref)
        assert abs(res.coverage - n / shelf.capacity_facings) <= 0.1, (n, res)
        assert res.facings == n


def test_calibration_normalises(shelves):
    shelf = shelves["shelf-B"]
    est = ClassicalCoverageEstimator()
    full = blank_frame()
    render_shelf(full, shelf, shelf.capacity_facings, gap_px=4)  # wide gaps -> raw well below 1
    raw = est.estimate(full, shelf, None)
    assert 0.6 < raw.raw_coverage < 0.9
    ref = est.calibrate(full, shelf)
    assert ref.raw_coverage_full == pytest.approx(raw.raw_coverage, abs=1e-3)
    norm = est.estimate(full, shelf, ref)
    assert norm.coverage == pytest.approx(1.0)
    # a half-full shelf normalises to ~0.5 rather than ~0.4
    half = blank_frame()
    render_shelf(half, shelf, 4, gap_px=4)
    assert abs(est.estimate(half, shelf, ref).coverage - 4 / 9) <= 0.1
    # reference with a bogus tiny raw_coverage_full is floored at 0.05 (no divide-by-zero blow-ups)
    bad = ref.model_copy(update={"raw_coverage_full": 0.0})
    assert est.estimate(half, shelf, bad).coverage == 1.0


def test_facings_runs(shelves):
    shelf = shelves["shelf-A"]
    est = ClassicalCoverageEstimator()
    # scattered facings with 2-px gaps are counted exactly
    img = blank_frame()
    render_shelf(img, shelf, [0, 2, 3, 6, 8], gap_px=2)
    assert est.estimate(img, shelf, None).facings == 5
    # touching facings (no gap) still count by width
    img = blank_frame()
    render_shelf(img, shelf, [1, 2, 3], gap_px=0)
    assert est.estimate(img, shelf, None).facings == 3
    # without facing_width_px facings fall back to round(coverage x capacity)
    nofw = shelf.model_copy(update={"facing_width_px": None})
    full = blank_frame()
    render_shelf(full, shelf, 9)
    ref = est.calibrate(full, nofw)
    img = blank_frame()
    render_shelf(img, shelf, 4)
    assert est.estimate(img, nofw, ref).facings == 4
    # the pure run counter: 2 runs of 14 + one sliver of 3 -> 2 facings at width 15
    cov = np.array([1] * 14 + [0] * 2 + [1] * 14 + [0] * 5 + [1] * 3, dtype=bool)
    assert count_facings_from_runs(cov, 15.0) == 2
    assert count_facings_from_runs(np.ones(30, dtype=bool), 15.0) == 2


def test_real_image_sanity_flat_vs_texture():
    """Photo-like shelf: random texture (product) vs a flat, non-palette backing (empty)."""
    rng = np.random.default_rng(7)
    shelf = ShelfPolygon(shelf_id="s", camera_id="c", name="s", polygon=[[10, 10], [210, 10], [210, 60], [10, 60]])
    est = ClassicalCoverageEstimator()
    empty = np.empty((100, 300, 3), dtype=np.uint8)
    empty[:] = (200, 210, 220)  # beige board, nothing like SHELF_BACKING
    res_empty = est.estimate(empty, shelf, None)
    assert res_empty.coverage < 0.3, res_empty
    assert res_empty.debug["backing_mode"] == 3.0  # auto-detected median of flat pixels
    textured = empty.copy()
    textured[10:60, 10:210] = rng.integers(0, 255, size=(50, 200, 3), dtype=np.uint8)
    res_full = est.estimate(textured, shelf, None)
    assert res_full.coverage > 0.9, res_full
    # the same texture at roughly the backing's mean colour is still seen via the std cue
    faint = empty.copy()
    faint[10:60, 10:210] = np.clip(
        np.array([200, 210, 220]) + rng.integers(-40, 40, size=(50, 200, 3)), 0, 255
    ).astype(np.uint8)
    assert est.estimate(faint, shelf, None).coverage > 0.8


def test_explicit_backing_and_offscreen_polygon():
    est = ClassicalCoverageEstimator(backing_bgr=[200, 210, 220])
    shelf = ShelfPolygon(shelf_id="s", camera_id="c", name="s", polygon=[[10, 10], [110, 10], [110, 40], [10, 40]])
    img = np.empty((60, 200, 3), dtype=np.uint8)
    img[:] = (200, 210, 220)
    draw_rect(img, (10, 12, 60, 38), (30, 30, 200))
    res = est.estimate(img, shelf, None)
    assert abs(res.coverage - 0.5) <= 0.05 and res.debug["backing_mode"] == 0.0
    off = ShelfPolygon(shelf_id="o", camera_id="c", name="o", polygon=[[500, 500], [600, 500], [600, 540], [500, 540]])
    assert est.estimate(img, off, None).coverage == 0.0
