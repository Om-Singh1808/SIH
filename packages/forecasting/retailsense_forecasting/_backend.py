"""Gradient-boosting backend selection.

scikit-learn's ``HistGradientBoostingRegressor`` is the default: it is already a dependency, handles NaN
features natively (our lag features are NaN for the first minutes of each counter) and fits 43k rows in
well under a second per horizon. LightGBM is used when it happens to be installed (typically faster and
slightly more accurate) but it is *never* imported eagerly - it is a heavy optional wheel and the cloud
service must start without it. Both are wrapped behind the same ``fit``/``predict`` surface.
"""

from __future__ import annotations

import contextlib
import importlib
import os
from collections.abc import Iterator
from typing import Any, Literal

import numpy as np

Backend = Literal["lightgbm", "sklearn"]


def lightgbm_available() -> bool:
    """True if ``import lightgbm`` succeeds (checked lazily, never at module import)."""
    try:
        importlib.import_module("lightgbm")
    except Exception:  # ImportError, but also OSError on broken native wheels
        return False
    return True


def choose_backend(preferred: Backend | Literal["auto"] = "auto") -> Backend:
    """Resolve ``"auto"`` to lightgbm-if-importable-else-sklearn; validate explicit choices."""
    if preferred == "auto":
        return "lightgbm" if lightgbm_available() else "sklearn"
    if preferred == "lightgbm" and not lightgbm_available():
        raise ImportError("lightgbm requested but not importable; pip install lightgbm")
    if preferred not in ("lightgbm", "sklearn"):
        raise ValueError(f"unknown backend {preferred!r}")
    return preferred


def make_regressor(backend: Backend, *, max_iter: int = 200, seed: int = 0, **overrides: Any) -> Any:
    """Instantiate a regressor for ``backend`` with sensible defaults for small tabular data."""
    if backend == "lightgbm":
        lgb = importlib.import_module("lightgbm")
        params: dict[str, Any] = {
            "n_estimators": max_iter,
            "learning_rate": 0.05,
            "num_leaves": 31,
            "min_child_samples": 20,
            "subsample": 0.9,
            "subsample_freq": 1,
            "colsample_bytree": 0.9,
            "random_state": seed,
            "verbose": -1,
        }
        params.update(overrides)
        return lgb.LGBMRegressor(**params)
    from sklearn.ensemble import HistGradientBoostingRegressor

    params = {
        "max_iter": max_iter,
        "learning_rate": 0.08,
        "max_leaf_nodes": 31,
        "min_samples_leaf": 20,
        "l2_regularization": 0.1,
        "early_stopping": False,
        "random_state": seed,
    }
    params.update(overrides)
    return HistGradientBoostingRegressor(**params)


FIT_THREADS = int(os.environ.get("RETAILSENSE_FIT_THREADS", "4"))
"""OpenMP threads used while fitting. Hybrid laptop CPUs (P+E cores, 20+ logical threads) oversubscribe
badly with histogram boosting - 4 threads measured ~10x faster than "all 22" on the dev machine."""


@contextlib.contextmanager
def fit_threads(n: int | None = None) -> Iterator[None]:
    """Limit OpenMP/BLAS threads for the duration of a fit (no-op if threadpoolctl is unavailable)."""
    try:
        from threadpoolctl import threadpool_limits
    except Exception:  # pragma: no cover - threadpoolctl ships with scikit-learn
        yield
        return
    with threadpool_limits(limits=n or FIT_THREADS):
        yield


def predict_nonneg(model: Any, X: Any) -> np.ndarray:
    """Predict and clip to >= 0 (queue counts / footfall can never be negative)."""
    return np.clip(np.asarray(model.predict(X), dtype="float64"), 0.0, None)


__all__ = [
    "FIT_THREADS",
    "Backend",
    "choose_backend",
    "fit_threads",
    "lightgbm_available",
    "make_regressor",
    "predict_nonneg",
]
