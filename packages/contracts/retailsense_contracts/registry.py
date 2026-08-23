"""Service registry: the *only* way apps reach sibling packages.

``resolve("tracker")`` imports ``retailsense_edgecv.tracker:ByteTrackLite``.  If
that package is not installed (each agent works in its own directory), the
deterministic fake from ``testing.py`` is returned instead and ONE warning is
logged - so every app boots, every test runs, and the real implementation is
picked up automatically the moment its package is ``pip install -e``'d.

``set_override(key, obj)`` lets tests inject their own implementation.
"""

import fnmatch
import importlib
from typing import Any

from .logging import get_logger

log = get_logger("retailsense.registry")

IMPLEMENTATIONS: dict[str, str] = {
    "frame_source.file": "retailsense_edgecv.source:FileFrameSource",
    "frame_source.rtsp": "retailsense_edgecv.source:RtspFrameSource",
    "frame_source.webcam": "retailsense_edgecv.source:WebcamFrameSource",
    "frame_source.synthetic": "retailsense_sim.video:SyntheticFrameSource",
    "detector.synthetic": "retailsense_edgecv.detector_synthetic:SyntheticDetector",
    "detector.onnx": "retailsense_edgecv.detector_onnx:OnnxPersonDetector",
    "detector.ultralytics": "retailsense_edgecv.detector_ultralytics:UltralyticsDetector",
    "tracker": "retailsense_edgecv.tracker:ByteTrackLite",
    "homography": "retailsense_edgecv.homography:Homography",
    "annotator": "retailsense_edgecv.annotate:annotate_frame",
    "zone_engine": "retailsense_edgeanalytics.zones:ZoneEngine",
    "queue_analyzer": "retailsense_edgequeue.queue:QueueAnalyzer",
    "queue_forecaster.edge": "retailsense_edgequeue.forecast:TrendForecaster",
    "coverage_estimator": "retailsense_edgeshelf.coverage:ClassicalCoverageEstimator",
    "shelf_state_machine": "retailsense_edgeshelf.state:ShelfStateMachine",
    "sku_identifier": "retailsense_edgeshelf.sku:TaggedSkuIdentifier",
    "shelf_thumb": "retailsense_edgeshelf.thumbs:shelf_thumbnail",
    "rule_engine": "retailsense_edgerules.engine:RuleEngine",
    "edge_store": "retailsense_edgestore.store:EdgeStore",
    "retention": "retailsense_edgestore.retention:RetentionJob",
    "uplink.http": "retailsense_edgeuplink.http:HttpUplink",
    "uplink.mqtt": "retailsense_edgeuplink.mqtt:MqttUplink",
    "link_controller": "retailsense_edgeuplink.link:LinkController",
    "sync_worker": "retailsense_edgeuplink.sync:SyncWorker",
    "forecaster.queue": "retailsense_forecasting.queue_forecaster:QueueForecaster",
    "forecaster.footfall": "retailsense_forecasting.footfall_forecaster:FootfallForecaster",
    "reorder": "retailsense_forecasting.reorder:suggest_reorder",
    "history_generator": "retailsense_sim.history:generate_history",
    "floorplan_renderer": "retailsense_sim.floorplan:render_floorplan",
    "notifier.simulator": "retailsense_integrations.whatsapp:WhatsAppSimulator",
    "notifier.cloud_api": "retailsense_integrations.whatsapp:WhatsAppCloudNotifier",
    "notifier.telegram": "retailsense_integrations.telegram:TelegramNotifier",
    "erp.tally": "retailsense_integrations.tally:TallyClient",
    "tally_mock_app": "retailsense_integrations.tally_mock:create_app",
    "ondc": "retailsense_integrations.ondc:OndcStubPublisher",
    "reconcile": "retailsense_integrations.reconcile:reconcile",
    "integrations_router": "retailsense_integrations.routers:build_router",
}

_T = "retailsense_contracts.testing"
FAKES: dict[str, str] = {
    "frame_source.*": f"{_T}:FakeFrameSource",
    "detector.*": f"{_T}:FakeDetector",
    "tracker": f"{_T}:FakeTracker",
    "homography": f"{_T}:IdentityMapper",
    "zone_engine": f"{_T}:FakeZoneEngine",
    "queue_analyzer": f"{_T}:FakeQueueAnalyzer",
    "queue_forecaster.edge": f"{_T}:FakeEdgeForecaster",
    "coverage_estimator": f"{_T}:FakeCoverageEstimator",
    "shelf_state_machine": f"{_T}:FakeShelfStateMachine",
    "sku_identifier": f"{_T}:FakeSkuIdentifier",
    "rule_engine": f"{_T}:FakeRuleEngine",
    "edge_store": f"{_T}:InMemoryEdgeStore",
    "uplink.*": f"{_T}:FakeUplink",
    "link_controller": f"{_T}:SimpleLinkController",
    "notifier.*": f"{_T}:FakeNotifier",
    "erp.*": f"{_T}:FakeErp",
    "ondc": f"{_T}:FakeOndc",
    "forecaster.*": f"{_T}:FakeForecaster",
    "history_generator": f"{_T}:fake_history",
    "reconcile": f"{_T}:fake_reconcile",
    # extra fakes for keys that have no protocol (keeps apps bootable with nothing installed)
    "annotator": f"{_T}:fake_annotate_frame",
    "shelf_thumb": f"{_T}:fake_shelf_thumbnail",
    "floorplan_renderer": f"{_T}:fake_render_floorplan",
    "retention": f"{_T}:FakeRetentionJob",
    "reorder": f"{_T}:fake_suggest_reorder",
    "sync_worker": f"{_T}:FakeSyncWorker",
}

# Keys that need FastAPI (not a contracts dependency): resolve() raises Unavailable without the real package.
NO_FAKE: frozenset[str] = frozenset({"tally_mock_app", "integrations_router"})

_OVERRIDES: dict[str, Any] = {}


class Unavailable(RuntimeError):
    """Neither the real implementation nor a fake could be resolved."""


def _import(spec: str) -> Any:
    module_name, _, attr = spec.partition(":")
    if not module_name or not attr:
        raise ImportError(f"bad import spec {spec!r}; expected 'module:attr'")
    module = importlib.import_module(module_name)
    return getattr(module, attr)


def fake_spec(key: str) -> str | None:
    """Import spec of the fake for ``key`` (exact match first, then wildcards)."""
    if key in FAKES:
        return FAKES[key]
    for pattern, spec in FAKES.items():
        if "*" in pattern and fnmatch.fnmatchcase(key, pattern):
            return spec
    return None


def resolve(key: str, *, allow_fake: bool = True) -> Any:
    """Return the real implementation for ``key``; fall back to its fake (one WARNING); else raise Unavailable."""
    if key in _OVERRIDES:
        return _OVERRIDES[key]
    real = IMPLEMENTATIONS.get(key)
    if real is None and fake_spec(key) is None:
        raise Unavailable(f"unknown registry key {key!r}")
    if real is not None:
        try:
            return _import(real)
        except (ImportError, AttributeError) as exc:
            reason = f"{type(exc).__name__}: {exc}"
    else:
        reason = "no real implementation registered"
    if not allow_fake:
        raise Unavailable(f"{key} -> {real} unavailable ({reason})")
    spec = fake_spec(key)
    if spec is None:
        raise Unavailable(f"{key} -> {real} unavailable ({reason}) and no fake registered")
    try:
        obj = _import(spec)
    except (ImportError, AttributeError) as exc:  # pragma: no cover - would be a contracts bug
        raise Unavailable(f"fake for {key} ({spec}) failed: {exc}") from exc
    log.warning("using fake for %s (%s)", key, reason, extra={"key": key, "fake": spec})
    return obj


def is_real(key: str) -> bool:
    """True if the real implementation for ``key`` imports cleanly (overrides count as real)."""
    if key in _OVERRIDES:
        return True
    real = IMPLEMENTATIONS.get(key)
    if real is None:
        return False
    try:
        _import(real)
        return True
    except (ImportError, AttributeError):
        return False


def set_override(key: str, obj: Any) -> None:
    """Inject ``obj`` for ``key`` (tests / app wiring overrides)."""
    _OVERRIDES[key] = obj


def clear_overrides(key: str | None = None) -> None:
    if key is None:
        _OVERRIDES.clear()
    else:
        _OVERRIDES.pop(key, None)


def status() -> dict[str, bool]:
    """``{key: is_real}`` for every registered key - handy for a boot banner."""
    return {k: is_real(k) for k in IMPLEMENTATIONS}


__all__ = [
    "FAKES",
    "IMPLEMENTATIONS",
    "NO_FAKE",
    "Unavailable",
    "clear_overrides",
    "fake_spec",
    "is_real",
    "resolve",
    "set_override",
    "status",
]
