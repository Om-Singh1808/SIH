"""Scenario table and the arrival-rate curve shared by the live model and the history generator.

Two kinds of scenario live in one table because the dashboard exposes them as one
list of buttons:

* **modes** (``baseline``, ``quiet``, ``evening_rush``, ``diwali``, ``footfall_spike``)
  change the arrival multiplier and stay active until another mode is applied;
* **actions** (``stockout``, ``restock``, ``open_counter``, ``close_counter``,
  ``camera_blackout``, ``freeze``, ``seed_history``) do one thing to the model and
  leave the current mode alone.

Every entry carries its default ``params`` so ``ScenarioStatus.params`` is always
fully populated and the board can render the knobs without guessing.
"""

from __future__ import annotations

import math
from typing import Any

# name -> {"kind": "mode"|"action", "params": defaults, "arrival_mult": float (modes only), "doc": str}
SCENARIOS: dict[str, dict[str, Any]] = {
    "baseline": {"kind": "mode", "arrival_mult": 1.0, "params": {}, "doc": "Normal kirana day."},
    "quiet": {"kind": "mode", "arrival_mult": 0.4, "params": {}, "doc": "Slow afternoon: 40 % of normal arrivals."},
    "evening_rush": {"kind": "mode", "arrival_mult": 2.0, "params": {}, "doc": "17-21 h rush: double arrivals."},
    "diwali": {"kind": "mode", "arrival_mult": 2.6, "params": {}, "doc": "Festival week: 2.6x arrivals, bigger baskets."},
    "footfall_spike": {
        "kind": "mode",
        "arrival_mult": 3.5,
        "params": {},
        "doc": "Sudden 3.5x burst (bus just arrived) - trips the footfall_spike rule.",
    },
    "stockout": {
        "kind": "action",
        "params": {"shelf_id": "shelf-A", "over_s": 30.0},
        "doc": "Drain a shelf to 0 units over over_s sim-seconds and disable auto restock.",
    },
    "restock": {"kind": "action", "params": {"shelf_id": "shelf-A"}, "doc": "Refill a shelf to capacity."},
    "open_counter": {"kind": "action", "params": {}, "doc": "Second cashier: service rate doubles."},
    "close_counter": {"kind": "action", "params": {}, "doc": "Cashier steps away: nobody is served."},
    "camera_blackout": {"kind": "action", "params": {"seconds": 20.0}, "doc": "Black frames for N real seconds."},
    "freeze": {"kind": "action", "params": {"seconds": 10.0}, "doc": "Repeat the last frame for N real seconds."},
    "seed_history": {
        "kind": "action",
        "params": {"days": 30},
        "doc": "No-op on the live store; tells the edge/cloud to (re)seed forecast history.",
    },
}

MODES: tuple[str, ...] = tuple(k for k, v in SCENARIOS.items() if v["kind"] == "mode")
ACTIONS: tuple[str, ...] = tuple(k for k, v in SCENARIOS.items() if v["kind"] == "action")


def arrival_mult(name: str) -> float:
    """Arrival multiplier of a mode scenario (1.0 for unknown / action names)."""
    return float(SCENARIOS.get(name, {}).get("arrival_mult", 1.0))


def arrival_rate_pm(hour: float) -> float:
    """Baseline shopper arrivals per minute for a kirana by local hour-of-day (float, 0-24).

    Two Gaussian peaks (08-10 morning, 17-21 evening) on an open-hours plateau, near-zero
    overnight.  The evening peak tops out at 1.2/min (the spec's "evening base"), the
    morning peak at 0.9/min.  Smooth so minute-level history does not have step edges.
    """
    hour = hour % 24.0
    morning = 0.65 * math.exp(-((hour - 9.0) ** 2) / (2 * 1.1**2))
    evening = 0.85 * math.exp(-((hour - 19.0) ** 2) / (2 * 1.7**2))
    plateau = 0.35 if 8.0 <= hour < 22.0 else 0.02
    return plateau + morning + evening


def scenario_defaults(name: str, params: dict[str, Any] | None) -> dict[str, Any]:
    """Merge caller params over the scenario defaults (unknown keys are kept for the board)."""
    merged = dict(SCENARIOS[name]["params"])
    merged.update(params or {})
    return merged


__all__ = ["ACTIONS", "MODES", "SCENARIOS", "arrival_mult", "arrival_rate_pm", "scenario_defaults"]
