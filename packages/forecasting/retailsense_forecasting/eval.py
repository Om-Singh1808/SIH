"""Live accuracy evaluation for the dashboard badge.

SenseCloud stores every emitted forecast in the ``forecasts`` table (contracts ``db.py``: ``made_ts``,
``horizon_min``, ``predicted``, ``actual``, ``model``). Once the target minute has passed the aggregator
fills ``actual``; :func:`rolling_mae` then summarises the last *window* of scored rows so the UI can show
"cloud model MAE 0.8 customers (last 24 h)" next to the edge-trend number. Rows without an ``actual``
(not yet scored) are ignored; the function accepts either SQLAlchemy ``Row`` objects, mappings or
dataclass-like objects so the caller does not have to reshape anything.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any


def _get(row: Any, key: str) -> Any:
    if isinstance(row, Mapping):
        return row.get(key)
    mapping = getattr(row, "_mapping", None)  # SQLAlchemy Row
    if mapping is not None:
        return mapping.get(key)
    return getattr(row, key, None)


def rolling_mae(
    rows: Iterable[Any],
    *,
    window_s: float | None = None,
    now_ts: float | None = None,
    horizon_min: int | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Mean absolute error of scored forecast rows.

    Returns ``{"mae": float | None, "n": int, "by_horizon": {"5": mae, ...}, "by_model": {...}}``.
    Filters: ``window_s`` (only rows with ``made_ts >= now_ts - window_s``), ``horizon_min``, ``model``.
    ``mae`` is ``None`` when nothing is scored yet (the badge then shows "warming up").
    """
    abs_err: list[float] = []
    by_h: dict[str, list[float]] = {}
    by_m: dict[str, list[float]] = {}
    for row in rows:
        actual = _get(row, "actual")
        predicted = _get(row, "predicted")
        if actual is None or predicted is None:
            continue
        if model is not None and _get(row, "model") != model:
            continue
        h = _get(row, "horizon_min")
        if horizon_min is not None and h != horizon_min:
            continue
        if window_s is not None and now_ts is not None:
            made = _get(row, "made_ts")
            if made is None or float(made) < now_ts - window_s:
                continue
        e = abs(float(predicted) - float(actual))
        abs_err.append(e)
        by_h.setdefault(str(h), []).append(e)
        by_m.setdefault(str(_get(row, "model")), []).append(e)

    def _mean(xs: list[float]) -> float:
        return round(sum(xs) / len(xs), 4)

    return {
        "mae": _mean(abs_err) if abs_err else None,
        "n": len(abs_err),
        "by_horizon": {k: _mean(v) for k, v in sorted(by_h.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else 0)},
        "by_model": {k: _mean(v) for k, v in by_m.items()},
    }


__all__ = ["rolling_mae"]
