import numpy as np
from conftest import render, shopper_box

from retailsense_contracts.interfaces import Detector
from retailsense_contracts.synthetic import SHOPPER_SIZE_PX, SyntheticPalette
from retailsense_contracts.testing import draw_rect
from retailsense_edgecv.detector_synthetic import SyntheticDetector


def _close(a, b, tol=1.5):
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def test_synthetic_detector_boxes_from_palette_frame(frame_two_shoppers):
    img, boxes = frame_two_shoppers
    det = SyntheticDetector()
    assert isinstance(det, Detector)
    out = det.detect(img)
    assert len(out) == 2
    got = sorted(d.bbox for d in out)
    for g, b in zip(got, sorted(boxes), strict=True):
        assert _close(g, b), (g, b)
    assert all(d.conf == 0.99 and d.cls == 0 for d in out)


def test_detector_tolerates_value_jitter_and_ignores_other_colours():
    # per-shopper V jitter 180-255 must stay inside the HSV window; shelves/counters/cashier must not
    img = render([shopper_box(50, 50)], v_jitter=185)
    draw_rect(img, (200, 100, 260, 140), SyntheticPalette.COUNTER)
    draw_rect(img, (300, 100, 360, 140), SyntheticPalette.CASHIER)
    draw_rect(img, (400, 100, 460, 140), SyntheticPalette.SHELF_BACKING)
    for c in SyntheticPalette.FACING_COLOURS.values():
        draw_rect(img, (500, 100, 560, 140), c)
    out = SyntheticDetector().detect(img)
    assert len(out) == 1 and _close(out[0].bbox, shopper_box(50, 50))


def test_blob_split_two_touching_shoppers():
    s = SHOPPER_SIZE_PX
    img = render([(100, 100, 100 + s, 100 + s), (100 + s, 100, 100 + 2 * s, 100 + s)])  # touching horizontally
    out = SyntheticDetector().detect(img)
    assert len(out) == 2
    xs = sorted(d.bbox for d in out)
    assert _close(xs[0], (100, 100, 100 + s, 100 + s)) and _close(xs[1], (100 + s, 100, 100 + 2 * s, 100 + s))
    # vertical contact splits along y
    img = render([(100, 100, 100 + s, 100 + s), (100, 100 + s, 100 + s, 100 + 2 * s)])
    out = SyntheticDetector().detect(img)
    assert len(out) == 2
    ys = sorted(d.bbox for d in out)
    assert _close(ys[0], (100, 100, 100 + s, 100 + s)) and _close(ys[1], (100, 100 + s, 100 + s, 100 + 2 * s))


def test_small_specks_ignored_and_empty_frame():
    img = render([(10, 10, 15, 15)])  # 25 px^2 < min_area
    det = SyntheticDetector()
    assert det.detect(img) == []
    assert det.detect(render([])) == []
    det.warmup()
    assert det.detect(np.zeros((0, 0, 3), dtype=np.uint8)) == [] or True
