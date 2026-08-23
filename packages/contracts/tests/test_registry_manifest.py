"""Registry fallback semantics and OTA manifest assignment."""

import logging

import pytest

from retailsense_contracts import registry
from retailsense_contracts.manifest import ModelManifest, RolloutPolicy, assigned_version, device_bucket, version_key
from retailsense_contracts.registry import FAKES, IMPLEMENTATIONS, Unavailable, fake_spec, is_real, resolve
from retailsense_contracts.testing import sample_manifest


def test_registry_fake_fallback(caplog):
    # no sibling package is installed in the contracts test environment
    for key in (
        "tracker",
        "edge_store",
        "detector.onnx",
        "frame_source.rtsp",
        "uplink.mqtt",
        "notifier.telegram",
        "erp.tally",
        "forecaster.footfall",
    ):
        caplog.clear()
        with caplog.at_level(logging.WARNING, logger="retailsense.registry"):
            obj = resolve(key)
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 1 and "using fake for" in warnings[0].getMessage() and key in warnings[0].getMessage()
        assert obj.__module__ == "retailsense_contracts.testing"
    from retailsense_contracts.testing import FakeDetector, FakeTracker, InMemoryEdgeStore

    assert resolve("tracker") is FakeTracker
    assert resolve("edge_store") is InMemoryEdgeStore
    assert resolve("detector.synthetic") is FakeDetector
    with pytest.raises(Unavailable):
        resolve("tracker", allow_fake=False)
    with pytest.raises(Unavailable):
        resolve("no.such.key")


def test_registry_every_key_has_a_fake():
    from retailsense_contracts.registry import NO_FAKE

    for key in IMPLEMENTATIONS:
        if key in NO_FAKE:  # FastAPI apps/routers: no fake, raises Unavailable until the real package is installed
            with pytest.raises(Unavailable):
                resolve(key)
            continue
        assert fake_spec(key) is not None, key
        assert resolve(key) is not None
    assert "detector.*" in FAKES and fake_spec("detector.whatever") == FAKES["detector.*"]


def test_registry_is_real_and_overrides():
    assert not is_real("tracker")
    assert is_real("nope") is False
    sentinel = object()
    registry.set_override("tracker", sentinel)
    try:
        assert resolve("tracker") is sentinel and is_real("tracker")
    finally:
        registry.clear_overrides("tracker")
    assert resolve("tracker") is not sentinel
    st = registry.status()
    assert set(st) == set(IMPLEMENTATIONS) and not any(st.values())


def test_registry_real_import_path_is_used(monkeypatch):
    # point a key at something importable to prove the real path wins over the fake
    monkeypatch.setitem(IMPLEMENTATIONS, "tracker", "json:dumps")
    import json

    assert resolve("tracker") is json.dumps and is_real("tracker")


def test_manifest_demo_loads():
    m = sample_manifest()
    assert isinstance(m, ModelManifest) and m.entry("person_detect").version == "yolo11n-1.0"
    assert m.rollout.channel == "canary" and m.rollout.pinned_devices["EDGE-001"] == "yolo11n-1.0"


def test_manifest_assignment_deterministic():
    m = sample_manifest()
    avail = ["yolo11n-1.0", "yolo11n-1.1"]
    # pinned wins even over canary and newer versions
    assert assigned_version(m, "EDGE-001", "person_detect", avail) == "yolo11n-1.0"
    # canary bucket is a pure function of device_id: same answer every call, ~canary_pct of devices
    devices = [f"DEV-{i:03d}" for i in range(500)]
    first = [assigned_version(m, d, "person_detect", avail) for d in devices]
    second = [assigned_version(m, d, "person_detect", avail) for d in devices]
    assert first == second
    canary = [d for d, v in zip(devices, first, strict=True) if v == "yolo11n-1.1"]
    assert all(device_bucket(d) < 10 for d in canary)
    assert 20 <= len(canary) <= 90  # ~10 % of 500
    assert set(first) == {"yolo11n-1.0", "yolo11n-1.1"}
    # stable channel: everyone gets the manifest version regardless of newer availability
    stable = m.model_copy(update={"rollout": RolloutPolicy(channel="stable", canary_pct=50)})
    assert {assigned_version(stable, d, "person_detect", avail) for d in devices} == {"yolo11n-1.0"}
    # canary_pct 100 -> everyone newest (except pins)
    allc = m.model_copy(update={"rollout": RolloutPolicy(channel="canary", canary_pct=100)})
    assert {assigned_version(allc, d, "person_detect", avail) for d in devices} == {"yolo11n-1.1"}
    # unknown model: newest available
    assert assigned_version(m, "DEV-001", "nope", ["1.0", "1.10", "1.9"]) in ("1.10",)
    with pytest.raises(ValueError):
        assigned_version(m, "DEV-001", "nope", [])


def test_version_key_numeric_order():
    assert sorted(["1.10", "1.9", "1.2.3", "yolo11n-1.0", "yolo11n-1.10", "2026.08.1"], key=version_key) == [
        "1.2.3",
        "1.9",
        "1.10",
        "2026.08.1",
        "yolo11n-1.0",
        "yolo11n-1.10",
    ]
