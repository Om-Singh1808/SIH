"""Pre/post-processing math on synthetic tensors, plus an end-to-end run on a tiny ONNX graph built in-test."""

import numpy as np
import pytest
from conftest import render, shopper_box

from retailsense_contracts.interfaces import Detector
from retailsense_contracts.registry import Unavailable
from retailsense_edgecv.detector_onnx import (
    OnnxPersonDetector,
    available_providers,
    decode_yolov8,
    letterbox,
    nms,
    to_input_tensor,
)


def _head_output(boxes_xyxy_padded, scores, n=8400, n_classes=80) -> np.ndarray:
    """Build a YOLOv8-style [1, 4+C, N] tensor with the given person boxes at the first slots."""
    out = np.zeros((1, 4 + n_classes, n), dtype=np.float32)
    for i, (b, s) in enumerate(zip(boxes_xyxy_padded, scores, strict=True)):
        x0, y0, x1, y1 = b
        out[0, 0, i] = (x0 + x1) / 2
        out[0, 1, i] = (y0 + y1) / 2
        out[0, 2, i] = x1 - x0
        out[0, 3, i] = y1 - y0
        out[0, 4, i] = s  # class 0 = person
        out[0, 5, i] = 0.9  # a strong non-person class score must be ignored
    return out


def test_letterbox_and_decode_roundtrip():
    img = np.zeros((360, 640, 3), dtype=np.uint8)
    padded, scale, pad = letterbox(img, 640)
    assert padded.shape == (640, 640, 3)
    assert scale == 1.0 and pad == (0.0, 140.0)
    assert padded[0, 0].tolist() == [114, 114, 114]  # grey border
    assert padded[200, 0].tolist() == [0, 0, 0]
    tensor = to_input_tensor(padded)
    assert tensor.shape == (1, 3, 640, 640) and tensor.dtype == np.float32 and tensor.max() <= 1.0

    # boxes expressed in padded coords decode back to original-image coords
    orig = [(100.0, 50.0, 140.0, 130.0), (300.0, 200.0, 340.0, 300.0)]
    padded_boxes = [
        (x0 * scale + pad[0], y0 * scale + pad[1], x1 * scale + pad[0], y1 * scale + pad[1]) for x0, y0, x1, y1 in orig
    ]
    raw = _head_output(padded_boxes, [0.9, 0.2])
    boxes, scores = decode_yolov8(raw, conf=0.35, scale=scale, pad=pad)
    assert boxes.shape == (1, 4) and np.allclose(boxes[0], orig[0], atol=1e-3)
    assert scores.tolist() == pytest.approx([0.9])
    # transposed layout [1, N, 4+C] is also accepted
    boxes_t, _ = decode_yolov8(raw.transpose(0, 2, 1), conf=0.35, scale=scale, pad=pad)
    assert np.allclose(boxes_t, boxes)
    # nothing above threshold -> empty arrays, not an error
    b, s = decode_yolov8(raw, conf=0.95, scale=scale, pad=pad)
    assert b.shape == (0, 4) and s.shape == (0,)


def test_letterbox_non_square_scale():
    img = np.zeros((480, 1280, 3), dtype=np.uint8)
    padded, scale, pad = letterbox(img, 640)
    assert padded.shape == (640, 640, 3)
    assert scale == 0.5 and pad == (0.0, 200.0)
    boxes, _ = decode_yolov8(_head_output([(100, 250, 200, 350)], [0.8]), 0.3, scale, pad)
    assert np.allclose(boxes[0], (200, 100, 400, 300))


def test_nms():
    boxes = np.array([[10, 10, 50, 50], [12, 12, 52, 52], [200, 200, 240, 240], [11, 11, 51, 51]], dtype=np.float32)
    scores = np.array([0.9, 0.8, 0.7, 0.85], dtype=np.float32)
    keep = nms(boxes, scores, conf=0.3, iou_thresh=0.5)
    assert sorted(keep) == [0, 2]
    assert keep[0] == 0  # highest score first
    assert nms(np.zeros((0, 4)), np.zeros((0,)), 0.3, 0.5) == []
    # score below conf is dropped by NMS too
    assert nms(boxes, np.array([0.9, 0.1, 0.1, 0.1], dtype=np.float32), 0.3, 0.5) == [0]


class _StubIO:
    def __init__(self, name):
        self.name = name


class _StubSession:
    """Session double: returns a canned head output regardless of input."""

    def __init__(self, raw):
        self.raw = raw
        self.calls = 0

    def get_inputs(self):
        return [_StubIO("images")]

    def get_outputs(self):
        return [_StubIO("output0")]

    def get_providers(self):
        return ["CPUExecutionProvider"]

    def run(self, outputs, feeds):
        self.calls += 1
        assert feeds["images"].shape == (1, 3, 640, 640)
        return [self.raw]


def test_detector_end_to_end_with_stub_session():
    img = np.zeros((360, 640, 3), dtype=np.uint8)
    _, scale, pad = letterbox(img)
    target = (100.0, 50.0, 140.0, 130.0)
    padded = [(target[0] + pad[0], target[1] + pad[1], target[2] + pad[0], target[3] + pad[1])] * 3
    raw = _head_output(padded, [0.9, 0.85, 0.8])  # three near-identical boxes -> NMS keeps one
    det = OnnxPersonDetector("nonexistent.onnx", session=_StubSession(raw), model_version="stub")
    assert isinstance(det, Detector)
    out = det.detect(img)
    assert len(out) == 1 and np.allclose(out[0].bbox, target, atol=1e-2) and out[0].conf == pytest.approx(0.9)
    det.warmup()
    assert det.last_infer_ms >= 0


def test_missing_weights_raise_unavailable(tmp_path):
    with pytest.raises(Unavailable, match="fetch_models"):
        OnnxPersonDetector(tmp_path / "nope.onnx")


def test_providers_end_with_cpu():
    p = available_providers()
    assert p[-1] == "CPUExecutionProvider"


def test_tiny_onnx_graph_roundtrip(tmp_path):
    """P1: real onnxruntime session over a graph built with onnx.helper that emits a fixed head output."""
    onnx = pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    from onnx import TensorProto, helper, numpy_helper

    img = render([shopper_box(320, 180)])
    _, scale, pad = letterbox(img)
    target = shopper_box(320, 180)
    padded = [
        (target[0] * scale + pad[0], target[1] * scale + pad[1], target[2] * scale + pad[0], target[3] * scale + pad[1])
    ]
    raw = _head_output(padded, [0.77])

    # output = Constant(raw) + 0 * ReduceMax(input)  -> input is consumed, output is the canned tensor
    inp = helper.make_tensor_value_info("images", TensorProto.FLOAT, [1, 3, 640, 640])
    outp = helper.make_tensor_value_info("output0", TensorProto.FLOAT, [1, 84, 8400])
    const = helper.make_node("Constant", [], ["head"], value=numpy_helper.from_array(raw, "head"))
    zero = helper.make_node(
        "Constant", [], ["zero"], value=numpy_helper.from_array(np.array(0.0, dtype=np.float32), "zero")
    )
    red = helper.make_node("ReduceMax", ["images"], ["m"], keepdims=0)
    mul = helper.make_node("Mul", ["m", "zero"], ["mz"])
    add = helper.make_node("Add", ["head", "mz"], ["output0"])
    graph = helper.make_graph([const, zero, red, mul, add], "stub_yolo", [inp], [outp])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 13)])
    model.ir_version = 8
    onnx.checker.check_model(model)
    path = tmp_path / "stub.onnx"
    onnx.save(model, str(path))

    det = OnnxPersonDetector(path, conf=0.35, providers=["CPUExecutionProvider"])
    assert det.active_provider == "CPUExecutionProvider"
    out = det.detect(img)
    assert len(out) == 1 and np.allclose(out[0].bbox, target, atol=1e-2)
    assert out[0].conf == pytest.approx(0.77, abs=1e-4)
