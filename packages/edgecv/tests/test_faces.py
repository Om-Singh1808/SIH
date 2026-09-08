"""Face masking correctness without downloading weights or storing raw footage."""

from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from retailsense_contracts.interfaces import Track

from retailsense_edgecv import faces
from retailsense_edgecv.annotate import annotate_frame
from retailsense_edgecv.faces import FaceRedactor


def track(box=(30, 10, 110, 150)):
    return Track(1, box, 0.9, 2, 2, 0, True)


def texture():
    return np.random.default_rng(31).integers(0, 256, (160, 200, 3), dtype=np.uint8)


class StubYuNet:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.sizes = []

    def setInputSize(self, size):
        self.sizes.append(size)

    def detect(self, image):
        assert (image.shape[1], image.shape[0]) == self.sizes[-1]
        assert image.shape[0] % 32 == image.shape[1] % 32 == 0
        return 1, next(self.outputs)


def row(x, y, w, h, score=0.9):
    return np.array([[x, y, w, h, *([0] * 10), score]], dtype=np.float32)


def test_only_face_pixels_change_and_original_is_preserved(monkeypatch):
    image = texture()
    original = image.copy()
    redactor = FaceRedactor(detector=StubYuNet([]))
    monkeypatch.setattr(redactor, "face_boxes", lambda image, tracks: [(50, 20, 90, 55)])
    result = redactor.redact(image, [track()])
    changed = np.any(result != image, axis=2)
    assert changed[20:55, 50:90].any()
    changed[20:55, 50:90] = False
    assert not changed.any()  # torso, arms, shelves and background unchanged
    np.testing.assert_array_equal(image, original)
    assert len(np.unique(result[20:55, 50:90].reshape(-1, 3), axis=0)) <= 64


def test_full_frame_faces_detected_without_person_tracks():
    detector = StubYuNet([row(240, 96, 192, 144)])  # full-frame scale 4.8
    boxes = FaceRedactor(detector=detector).face_boxes(texture(), [])
    assert boxes == [(46, 17, 94, 53)]


def test_person_crop_recovers_missed_face_and_maps_to_frame():
    detector = StubYuNet([None, row(32, 32, 64, 64)])
    boxes = FaceRedactor(detector=detector).face_boxes(texture(), [track((40, 20, 140, 120))])
    assert boxes == [(48, 28, 72, 52)]


def test_clipped_tracks_invalid_detections_and_empty_results():
    bad = np.concatenate([row(0, 0, 30, 30, 0.1), row(0, 0, -1, 30), row(np.nan, 0, 30, 30)])
    detector = StubYuNet([bad, None])
    redactor = FaceRedactor(detector=detector)
    image = texture()
    result = redactor.redact(image, [track((-10, -20, 40, 80)), track((400, 10, 450, 90))])
    np.testing.assert_array_equal(result, image)
    assert result is not image


def test_faces_at_image_edge_are_clipped():
    redactor = FaceRedactor(detector=StubYuNet([row(-5, -5, 40, 40)]))
    boxes = redactor.face_boxes(texture(), [])
    assert boxes == [(0, 0, 9, 9)]


def test_missing_weights_withhold_preview_and_log(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("RS_FACE_MODEL", str(tmp_path / "missing.onnx"))
    monkeypatch.setattr(faces, "_local", SimpleNamespace())
    image = texture()
    assert not faces.redact_faces(image, []).any()
    assert "faces-only" in caplog.text
    assert image.any()


def test_inference_failure_withholds_preview(monkeypatch):
    class BrokenDetector:
        def redact(self, *args):
            raise cv2.error("inference failed")

    monkeypatch.setattr(faces, "_local", SimpleNamespace(path=faces.model_path(), redactor=BrokenDetector()))
    assert not faces.redact_faces(texture(), []).any()


def test_annotation_redacts_even_without_tracks(monkeypatch):
    calls = []

    def redact(frame, tracks):
        calls.append(tracks)
        return np.zeros_like(frame)

    monkeypatch.setattr("retailsense_edgecv.annotate.redact_faces", redact)
    assert not annotate_frame(texture(), [], {}, blur_people=True).any()
    assert calls == [[]]
    image = texture()
    np.testing.assert_array_equal(annotate_frame(image, [], {}, blur_people=False), image)
    assert len(calls) == 1


def test_pixelation_strength_is_validated():
    with pytest.raises(ValueError, match="at least 2"):
        FaceRedactor(detector=StubYuNet([])).redact(texture(), [], factor=1)
