"""Chaos toggles for the synthetic camera: freeze, drop, blackout, noise.

These simulate the failure modes a real kirana camera shows (a hung RTSP stream, a
power cut, a dropped frame, sensor noise at night) so the ``camera_down`` rule and
the board's freshness badge can be demonstrated on demand.  ``ChaosState`` is pure
bookkeeping in *real* seconds (a blackout lasts 20 wall-clock seconds no matter
what the sim clock factor is) and ``apply_to_frame`` is the only place pixels are
touched.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np

from retailsense_contracts.api import ChaosRequest

NOISE_SIGMA = 6.0  # additive Gaussian noise; keeps palette pixels inside the detector HSV window


@dataclass
class ChaosState:
    freeze_until: float | None = None  # real (monotonic) deadline, None = off
    blackout_until: float | None = None
    noise: bool = False
    drop_p: float = 0.0

    def apply(self, req: ChaosRequest, now: float | None = None) -> None:
        """Apply a ``ChaosRequest``; ``seconds=None`` with ``enabled=True`` means until disabled."""
        now = time.monotonic() if now is None else now
        until = (now + float(req.seconds)) if req.seconds is not None else float("inf")
        if req.kind == "freeze":
            self.freeze_until = until if req.enabled else None
        elif req.kind == "blackout":
            self.blackout_until = until if req.enabled else None
        elif req.kind == "noise":
            self.noise = bool(req.enabled)
        elif req.kind == "drop":
            self.drop_p = float(req.p if req.p is not None else 0.3) if req.enabled else 0.0

    def frozen(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return self.freeze_until is not None and now < self.freeze_until

    def black(self, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        return self.blackout_until is not None and now < self.blackout_until

    def should_drop(self, rng: np.random.Generator) -> bool:
        return self.drop_p > 0.0 and bool(rng.random() < self.drop_p)

    def apply_to_frame(self, image: np.ndarray, rng: np.random.Generator, now: float | None = None) -> np.ndarray:
        """Return the frame the camera would deliver (black / noisy / untouched)."""
        if self.black(now):
            return np.zeros_like(image)
        if self.noise:
            noise = rng.normal(0.0, NOISE_SIGMA, size=image.shape)
            return np.clip(image.astype(np.int16) + noise.astype(np.int16), 0, 255).astype(np.uint8)
        return image

    def describe(self) -> dict[str, float | bool | None]:
        now = time.monotonic()
        return {
            "freeze": self.frozen(now),
            "blackout": self.black(now),
            "noise": self.noise,
            "drop_p": self.drop_p,
        }


__all__ = ["NOISE_SIGMA", "ChaosState"]
