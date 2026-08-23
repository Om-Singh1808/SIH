"""OTA model manifest (``models/manifest.json``) and the rollout assignment rule.

The cloud publishes a ``ModelManifest``; each edge compares it with its local
copy (``ModelStatus``) and downloads/activates the version it is *assigned*:

    pinned_devices[device_id]           (explicit pin wins)
    > canary: sha1(device_id) % 100 < canary_pct  -> newest available version
    > stable: the version listed in the manifest entry for that model

Assignment is a pure function of the manifest and device id, so the cloud,
the edge and the tests agree without talking to each other.
"""

import hashlib
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")


class ModelIO(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shape: list[int]
    layout: str = "NCHW"
    normalize: str = "0-1"
    letterbox: bool = True


class ModelEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_id: str
    version: str
    task: Literal["person_detect", "shelf_gap", "sku_embed"]
    format: Literal["onnx", "pt", "tflite"]
    file: str
    sha256: str
    size_bytes: int
    input: ModelIO
    output_format: Literal["yolov8", "yolox", "synthetic", "none"]
    classes: list[str]
    source_url: str | None
    license: str
    min_runtime: str
    notes: str = ""

    @field_validator("sha256")
    @classmethod
    def _sha(cls, v: str) -> str:
        if not _SHA256.match(v):
            raise ValueError("sha256 must be 64 hex chars (use 64 zeros for a placeholder)")
        return v.lower()


class RolloutPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: Literal["canary", "stable"] = "stable"
    canary_pct: int = 10
    abort_failure_pct: int = 5
    pinned_devices: dict[str, str] = Field(default_factory=dict)  # device_id -> version


class ModelManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    manifest_version: int = 1
    version: str
    generated_ts: float
    models: list[ModelEntry]
    rollout: RolloutPolicy = RolloutPolicy()

    def entry(self, model_id: str) -> ModelEntry | None:
        return next((m for m in self.models if m.model_id == model_id), None)


def version_key(v: str) -> tuple:
    """Sort key for versions like ``1.2.3``, ``yolo11n-1.0``, ``2026.08.1``: numeric chunks compare numerically."""
    parts = re.split(r"(\d+)", v)
    return tuple((0, int(p)) if p.isdigit() else (1, p) for p in parts if p != "")


def device_bucket(device_id: str) -> int:
    """Deterministic 0-99 bucket of a device id (sha1, not Python's salted hash())."""
    return int(hashlib.sha1(device_id.encode("utf-8")).hexdigest(), 16) % 100


def assigned_version(manifest: ModelManifest, device_id: str, model_id: str, versions_available: list[str]) -> str:
    """Which version ``device_id`` should run for ``model_id``. See module docstring."""
    pinned = manifest.rollout.pinned_devices.get(device_id)
    if pinned:
        return pinned
    entry = manifest.entry(model_id)
    stable = (
        entry.version if entry is not None else (max(versions_available, key=version_key) if versions_available else "")
    )
    candidates = set(versions_available) | ({stable} if stable else set())
    if not candidates:
        raise ValueError(f"no versions known for model {model_id!r}")
    newest = max(candidates, key=version_key)
    if manifest.rollout.channel == "canary" and manifest.rollout.canary_pct > 0:
        if device_bucket(device_id) < manifest.rollout.canary_pct:
            return newest
    return stable or newest


__all__ = [
    "ModelEntry",
    "ModelIO",
    "ModelManifest",
    "RolloutPolicy",
    "assigned_version",
    "device_bucket",
    "version_key",
]
