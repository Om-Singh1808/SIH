"""Pure-numpy geometry, including the normative line-crossing rule on the demo store."""

import numpy as np
import pytest

from retailsense_contracts.enums import Direction
from retailsense_contracts.geometry import (
    bbox_polygon_overlap,
    iou,
    point_in_polygon,
    points_in_polygon,
    polygon_area,
    polygon_bbox,
    polygon_long_axis,
    polygon_mask,
    segments_intersect,
    side_of_line,
)


def test_geometry_side_and_crossing(cfg):
    entrance = cfg.line("entrance")
    start, end = tuple(entrance.start), tuple(entrance.end)
    assert (start, end) == ((120, 315), (60, 315))
    assert side_of_line((90, 330), start, end) == -1
    assert side_of_line((90, 300), start, end) == +1
    assert side_of_line((90, 315), start, end) == 0
    # walking up the image (y decreasing): -1 -> +1 == IN
    path = [(90, 330), (90, 320), (90, 310), (90, 300)]
    sides = [side_of_line(p, start, end) for p in path]
    assert sides == [-1, -1, 1, 1]
    direction = Direction.IN if (sides[0], sides[-1]) == (-1, 1) else Direction.OUT
    assert direction == Direction.IN
    assert segments_intersect(path[0], path[-1], start, end)
    # counter line: moving LEFT out of the queue head is IN (served)
    counter = cfg.line("counter-1-line")
    cs, ce = tuple(counter.start), tuple(counter.end)
    assert side_of_line((560, 120), cs, ce) == -1  # inside the queue zone (right of the line)
    assert side_of_line((500, 120), cs, ce) == +1  # past the counter
    assert not segments_intersect((560, 120), (500, 120), (532, 200), (532, 260))  # far away on the same x


def test_segments_intersect_cases():
    assert segments_intersect((0, 0), (10, 10), (0, 10), (10, 0))
    assert not segments_intersect((0, 0), (1, 1), (2, 2), (3, 3))
    assert segments_intersect((0, 0), (2, 2), (1, 1), (3, 3))  # collinear overlap
    assert segments_intersect((0, 0), (2, 0), (2, 0), (2, 5))  # touching endpoint


def test_point_in_polygon_demo_zones(cfg):
    z = {zone.zone_id: zone.polygon for zone in cfg.zones}
    assert point_in_polygon((200, 100), z["aisle-1"])
    assert not point_in_polygon((200, 250), z["aisle-1"])
    assert point_in_polygon((100, 200), z["aisle-2"])
    assert not point_in_polygon((100, 100), z["aisle-2"])
    assert point_in_polygon((576, 120), z["queue-1"])  # queue head slot from the sim spec
    assert not point_in_polygon((500, 120), z["queue-1"])
    assert point_in_polygon((320, 180), z["store"])
    assert not point_in_polygon((5, 5), z["store"])
    # shelves are inside the store but not in any aisle polygon
    for s in cfg.shelves:
        x0, y0, x1, y1 = polygon_bbox(s.polygon)
        c = ((x0 + x1) / 2, (y0 + y1) / 2)
        assert point_in_polygon(c, z["store"])
        assert not point_in_polygon(c, z["aisle-1"])
    # vectorised form agrees with scalar
    pts = np.array([[200, 100], [200, 250], [100, 200]], dtype=float)
    assert points_in_polygon(pts, z["aisle-1"]).tolist() == [True, False, False]


def test_polygon_helpers(cfg):
    a = cfg.shelf("shelf-A").polygon
    c = cfg.shelf("shelf-C").polygon
    assert polygon_bbox(a) == (130, 30, 270, 62)
    assert polygon_long_axis(a) == "x" and polygon_long_axis(c) == "y"
    assert polygon_area(a) == 140 * 32
    with pytest.raises(ValueError):
        polygon_area([[0, 0], [1, 1]])


def test_iou_and_overlap():
    a = np.array([[0, 0, 10, 10], [20, 20, 30, 30]], dtype=float)
    b = np.array([[0, 0, 10, 10], [5, 5, 15, 15], [100, 100, 110, 110]], dtype=float)
    m = iou(a, b)
    assert m.shape == (2, 3)
    assert m[0, 0] == pytest.approx(1.0)
    assert m[0, 1] == pytest.approx(25 / 175)
    assert m[1, 2] == 0.0
    assert iou(np.zeros((0, 4)), b).shape == (0, 3)
    poly = [[0, 0], [10, 0], [10, 10], [0, 10]]
    assert bbox_polygon_overlap((0, 0, 10, 10), poly) == pytest.approx(1.0)
    assert bbox_polygon_overlap((5, 0, 15, 10), poly) == pytest.approx(0.5)
    assert bbox_polygon_overlap((20, 20, 30, 30), poly) == 0.0
    assert bbox_polygon_overlap((3, 3, 3, 3), poly) == 0.0
    mask = polygon_mask(poly, 20, 20)
    assert mask.sum() == 100 and mask[5, 5] and not mask[15, 15]
