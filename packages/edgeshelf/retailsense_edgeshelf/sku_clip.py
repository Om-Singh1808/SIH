"""CLIP-embedding SKU identification (P2, optional weights).

Design: a CLIP ViT-B/32 image encoder exported to ONNX (``models/clip_vitb32.onnx``,
input ``[N,3,224,224]`` float32, output ``[N,512]``) embeds each enrolled photo;
``identify`` embeds the shelf crop and returns the nearest enrolled SKU by cosine
similarity (k-NN over the enrolment bank, majority label, mean similarity as
confidence). No training, no fine-tuning: the owner enrols a handful of phone
photos per SKU and the bank lives in memory / ``sku_enrolment``.

Without the weights file the constructor raises
:class:`retailsense_contracts.registry.Unavailable` so wiring code can fall
back to :class:`retailsense_edgeshelf.sku.TaggedSkuIdentifier`. ``onnxruntime``
is imported lazily for the same reason. The numpy pieces (preprocessing and
k-NN) are real and unit-tested; only the encoder needs the weights.
"""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

import numpy as np

from retailsense_contracts.registry import Unavailable

DEFAULT_MODEL_PATH = Path("models/clip_vitb32.onnx")
CLIP_SIZE = 224
CLIP_MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
CLIP_STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)


def preprocess(crop_bgr: np.ndarray) -> np.ndarray:
    """BGR uint8 crop -> ``[1,3,224,224]`` float32, CLIP-normalised (nearest-neighbour resize)."""
    h, w = crop_bgr.shape[:2]
    ys = (np.arange(CLIP_SIZE) * h / CLIP_SIZE).astype(int)
    xs = (np.arange(CLIP_SIZE) * w / CLIP_SIZE).astype(int)
    rgb = crop_bgr[ys][:, xs][..., ::-1].astype(np.float32) / 255.0
    rgb = (rgb - CLIP_MEAN) / CLIP_STD
    return rgb.transpose(2, 0, 1)[None].astype(np.float32)


def l2_normalise(v: np.ndarray) -> np.ndarray:
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-8)


def cosine_knn(query: np.ndarray, bank: np.ndarray, labels: list[str], k: int = 3) -> tuple[str | None, float]:
    """Majority label among the ``k`` most cosine-similar bank rows; confidence = their mean similarity."""
    if bank.shape[0] == 0:
        return None, 0.0
    sims = l2_normalise(bank) @ l2_normalise(query.reshape(-1))
    top = np.argsort(-sims)[: max(1, min(k, len(labels)))]
    votes: dict[str, list[float]] = {}
    for i in top:
        votes.setdefault(labels[int(i)], []).append(float(sims[int(i)]))
    best = max(votes.items(), key=lambda kv: (len(kv[1]), sum(kv[1])))
    return best[0], round(float(np.mean(best[1])), 4)


class ClipSkuIdentifier:
    """Implements ``SkuIdentifier`` with ``backend="clip_onnx"``; requires the ONNX weights."""

    backend = "clip_onnx"

    def __init__(self, model_path: str | Path = DEFAULT_MODEL_PATH, k: int = 3) -> None:
        self.model_path = Path(model_path)
        self.k = k
        if not self.model_path.is_file():
            raise Unavailable(f"CLIP weights not found at {self.model_path}; use TaggedSkuIdentifier")
        try:
            ort = import_module("onnxruntime")
        except ImportError as e:  # pragma: no cover - depends on environment
            raise Unavailable("onnxruntime is not installed") from e
        self._session = ort.InferenceSession(str(self.model_path), providers=["CPUExecutionProvider"])
        self._input = self._session.get_inputs()[0].name
        self._bank = np.zeros((0, 512), dtype=np.float32)
        self._labels: list[str] = []

    def embed(self, crop_bgr: np.ndarray) -> np.ndarray:
        out = self._session.run(None, {self._input: preprocess(crop_bgr)})[0]
        return l2_normalise(np.asarray(out, dtype=np.float32).reshape(1, -1))[0]

    def enrol(self, sku_id: str, images: list[np.ndarray]) -> int:
        for img in images:
            self._bank = np.vstack([self._bank, self.embed(img)[None]])
            self._labels.append(sku_id)
        return self._labels.count(sku_id)

    def identify(self, crop: np.ndarray, hint_sku_id: str | None) -> tuple[str | None, float]:
        sku, conf = cosine_knn(self.embed(crop), self._bank, self._labels, self.k)
        if sku is None:  # empty bank: fall back to the tag
            return (hint_sku_id, 1.0) if hint_sku_id else (None, 0.0)
        return sku, conf


__all__ = ["CLIP_SIZE", "DEFAULT_MODEL_PATH", "ClipSkuIdentifier", "cosine_knn", "l2_normalise", "preprocess"]
