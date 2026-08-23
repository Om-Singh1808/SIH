"""Model manifest handling (OTA) and detector selection.

``models/manifest.json`` is a ``ModelManifest`` (contracts C.11).  The edge
loads it at boot, verifies the sha256 of the weights it is about to run, and
compares it with the manifest the cloud publishes to decide whether an update
is assigned to this device (``assigned_version`` - pinned > canary > stable).

:func:`select_detector` implements the ``detector: auto`` rule from
``CameraConfig``: synthetic sources (and files rendered by the simulator)
use the weight-free HSV detector; real cameras use the ONNX model when its
weights are present, else ``ultralytics`` if installed, else an ``Unavailable``
error with the exact command to fix it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from retailsense_contracts.api import ModelStatus
from retailsense_contracts.config import CameraConfig
from retailsense_contracts.enums import DetectorKind
from retailsense_contracts.interfaces import Detector
from retailsense_contracts.manifest import ModelEntry, ModelManifest, assigned_version, version_key
from retailsense_contracts.registry import Unavailable

PLACEHOLDER_SHA = "0" * 64
DEFAULT_MANIFEST = "models/manifest.json"
PERSON_MODEL_ID = "person_detect"
SYNTHETIC_MODEL_ID = "person_detect_synthetic"
SYNTHETIC_FILE_HINTS = ("synthetic", "demo_store")


def sha256_of(path: str | Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


class ModelManager:
    """Load / verify / compare model manifests for one edge device."""

    def __init__(self, manifest_path: str | Path = DEFAULT_MANIFEST, *, device_id: str = "EDGE-000"):
        self.manifest_path = Path(manifest_path)
        self.device_id = device_id
        self.local: ModelManifest | None = None

    # loading ----------------------------------------------------------------
    def load_local(self, manifest_path: str | Path | None = None) -> ModelManifest:
        path = Path(manifest_path) if manifest_path is not None else self.manifest_path
        with open(path, encoding="utf-8") as f:
            self.local = ModelManifest.model_validate(json.load(f))
        self.manifest_path = path
        return self.local

    def save_local(self, manifest: ModelManifest, path: str | Path | None = None) -> Path:
        p = Path(path) if path is not None else self.manifest_path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(manifest.model_dump(mode="json"), indent=2) + "\n", encoding="utf-8")
        self.local = manifest
        return p

    # files --------------------------------------------------------------------
    def weights_path(self, entry: ModelEntry) -> Path | None:
        """Resolve ``entry.file`` (repo-relative, e.g. ``models/yolo11n.onnx``) against likely roots."""
        if not entry.file:
            return None
        rel = Path(entry.file)
        if rel.is_absolute():
            return rel
        mdir = self.manifest_path.resolve().parent
        candidates = [mdir.parent / rel, mdir / rel, mdir / rel.name, Path.cwd() / rel]
        for c in candidates:
            if c.exists():
                return c
        return candidates[0]

    def verify_sha(self, entry: ModelEntry) -> bool:
        """True iff the weights file exists and its sha256 matches the manifest (placeholders never verify)."""
        if entry.output_format == "synthetic":
            return True  # no weights by design
        if entry.sha256 == PLACEHOLDER_SHA:
            return False
        path = self.weights_path(entry)
        if path is None or not path.exists():
            return False
        return sha256_of(path) == entry.sha256

    def weights_available(self, model_id: str = PERSON_MODEL_ID) -> Path | None:
        if self.local is None:
            return None
        entry = self.local.entry(model_id)
        if entry is None:
            return None
        p = self.weights_path(entry)
        return p if p is not None and p.exists() else None

    # OTA ----------------------------------------------------------------------
    def compare(self, remote: ModelManifest | None, model_id: str = PERSON_MODEL_ID) -> ModelStatus:
        """Describe local vs remote state for ``model_id`` as the REST ``ModelStatus``."""
        local_entry = self.local.entry(model_id) if self.local is not None else None
        active_version = local_entry.version if local_entry is not None else "none"
        available = [local_entry.version] if local_entry is not None else []
        assigned: str | None = None
        update = False
        if remote is not None:
            remote_entry = remote.entry(model_id)
            if remote_entry is not None:
                available.append(remote_entry.version)
            try:
                assigned = assigned_version(remote, self.device_id, model_id, available)
            except ValueError:
                assigned = None
            if assigned is not None and active_version != "none":
                update = version_key(assigned) > version_key(active_version)
            elif assigned is not None:
                update = True
        return ModelStatus(
            local=self.local,
            remote=remote,
            active_model_id=model_id,
            active_version=active_version,
            update_available=update,
            assigned_version=assigned,
        )


# ---------------------------------------------------------------------------
# detector selection
# ---------------------------------------------------------------------------


def _looks_synthetic(camera: CameraConfig) -> bool:
    if camera.is_synthetic:
        return True
    if camera.source.startswith("file:"):
        name = Path(camera.source[len("file:") :]).name.lower()
        return any(h in name for h in SYNTHETIC_FILE_HINTS)
    return False


def select_detector(camera: CameraConfig, manifest_path: str | Path = DEFAULT_MANIFEST) -> Detector:
    """Build the detector a camera should run, honouring ``camera.detector`` (auto rule in module doc)."""
    kind = DetectorKind(camera.detector)
    if kind == DetectorKind.FAKE:
        from retailsense_contracts.testing import FakeDetector

        return FakeDetector()
    if kind == DetectorKind.SYNTHETIC or (kind == DetectorKind.AUTO and _looks_synthetic(camera)):
        from .detector_synthetic import SyntheticDetector

        return SyntheticDetector()

    mm = ModelManager(manifest_path)
    weights: Path | None = None
    entry: ModelEntry | None = None
    if mm.manifest_path.exists():
        mm.load_local()
        entry = mm.local.entry(PERSON_MODEL_ID) if mm.local else None
        weights = mm.weights_available(PERSON_MODEL_ID)

    if kind in (DetectorKind.ONNX, DetectorKind.AUTO) and weights is not None:
        from .detector_onnx import OnnxPersonDetector

        version = entry.version if entry is not None else None
        return OnnxPersonDetector(weights, model_version=version)
    if kind == DetectorKind.ONNX:
        raise Unavailable(
            f"detector=onnx but no weights found (manifest {mm.manifest_path}); run `python tools/fetch_models.py`"
        )
    # ultralytics explicitly, or auto with no onnx weights
    from .detector_ultralytics import INSTALL_HINT, UltralyticsDetector

    try:
        return UltralyticsDetector()
    except Unavailable as exc:
        if kind == DetectorKind.ULTRALYTICS:
            raise
        raise Unavailable(
            f"camera {camera.camera_id!r}: no person detector available for source {camera.source!r}. "
            f"Export ONNX weights with `python tools/fetch_models.py`, or {INSTALL_HINT}, "
            "or set detector: synthetic for simulated sources."
        ) from exc


__all__ = [
    "DEFAULT_MANIFEST",
    "PERSON_MODEL_ID",
    "PLACEHOLDER_SHA",
    "SYNTHETIC_MODEL_ID",
    "ModelManager",
    "select_detector",
    "sha256_of",
]
