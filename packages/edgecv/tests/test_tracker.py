from conftest import shopper_box

from retailsense_contracts.interfaces import Detection, Tracker
from retailsense_edgecv.kalman import KalmanBox
from retailsense_edgecv.tracker import ByteTrackLite


def det(cx, cy, conf=0.99):
    return Detection(bbox=shopper_box(cx, cy), conf=conf)


def test_kalman_predicts_constant_velocity():
    kf = KalmanBox(shopper_box(0, 0))
    for i in range(1, 10):
        kf.predict()
        kf.update(shopper_box(8 * i, 0))
    pred = kf.predict()
    cx = (pred[0] + pred[2]) / 2
    assert abs(cx - 80) < 3.0
    assert abs(kf.velocity[0] - 8) < 1.5


def test_tracker_keeps_id_linear_motion():
    tr = ByteTrackLite()
    assert isinstance(tr, Tracker)
    ids = set()
    for i in range(30):
        tracks = tr.update([det(50 + 8 * i, 100)], ts=float(i))
        assert len(tracks) == 1
        ids.add(tracks[0].track_id)
        if i >= 1:
            assert tracks[0].confirmed
    assert ids == {1}


def test_tracker_two_crossing_objects_no_swap():
    tr = ByteTrackLite()
    first: dict[int, str] = {}
    for i in range(40):
        a = det(50 + 8 * i, 100 + 4 * i)  # A: left->right, drifting down
        b = det(350 - 8 * i, 100 + 4 * i)  # B: right->left, same row -> cross at i ~ 19
        tracks = tr.update([a, b], ts=float(i))
        assert len(tracks) == 2
        by_id = {t.track_id: t for t in tracks}
        if i == 1:
            for tid, t in by_id.items():
                first[tid] = "A" if abs(t.bbox[0] - a.bbox[0]) < 1 else "B"
            assert set(first.values()) == {"A", "B"}
        if i >= 25:  # well after the crossing
            for tid, t in by_id.items():
                want = a if first[tid] == "A" else b
                assert abs(t.bbox[0] - want.bbox[0]) < 1, f"swap at frame {i}"
    assert len(first) == 2


def test_tracker_ids_never_reused():
    tr = ByteTrackLite(max_age=2, min_hits=1)
    t1 = tr.update([det(100, 100)], 0.0)[0].track_id
    for i in range(1, 6):  # object vanishes -> track dies after max_age
        tr.update([], float(i))
    assert tr.update([], 6.0) == []
    t2 = tr.update([det(100, 100)], 7.0)[0].track_id
    assert t2 > t1
    tr.reset()
    t3 = tr.update([det(100, 100)], 8.0)[0].track_id
    assert t3 > t2
    seen = [t1, t2, t3]
    assert len(set(seen)) == 3 and seen == sorted(seen)


def test_low_conf_second_stage_keeps_track_alive():
    tr = ByteTrackLite(min_hits=1)
    tid = tr.update([det(100, 100)], 0.0)[0].track_id
    for i in range(1, 6):
        tracks = tr.update([det(100 + 2 * i, 100, conf=0.3)], float(i))  # low-conf, would be ignored for birth
        assert [t.track_id for t in tracks] == [tid]
        assert tracks[0].time_since_update == 0
    # a lone low-conf det never spawns a new track
    assert tr.update([det(500, 300, conf=0.3)], 10.0)[0].track_id == tid
    assert tr.next_id == tid + 1


def test_centroid_gate_rescues_fast_mover():
    tr = ByteTrackLite(min_hits=1, centroid_gate_px=60)
    tid = tr.update([det(100, 100)], 0.0)[0].track_id
    # jump 40 px in one frame: zero IoU with a 20 px box, but inside the gate
    tracks = tr.update([det(140, 100)], 1.0)
    assert [t.track_id for t in tracks] == [tid]


def test_coasting_track_reports_prediction_then_dies():
    tr = ByteTrackLite(max_age=3, min_hits=1)
    for i in range(5):
        tr.update([det(100 + 8 * i, 100)], float(i))
    tracks = tr.update([], 5.0)
    assert len(tracks) == 1 and tracks[0].time_since_update == 1
    cx = (tracks[0].bbox[0] + tracks[0].bbox[2]) / 2
    assert 135 < cx < 145  # predicted ahead, not frozen at 132
    for i in range(6, 10):
        tracks = tr.update([], float(i))
    assert tracks == []
