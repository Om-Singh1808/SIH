"""Reorder suggestions (D12 formula).

For each SKU in the store config::

    demand  = velocity_units_per_hr x open_hours x lead_time_days x uplift
    uplift  = mean(forecast footfall over the lead time) / average daily footfall   (1.0 if unknown)
    safety  = 0.5 day of demand  = 0.5 x velocity x open_hours
    suggest = max(0, ceil(demand + safety - stock))

Stock resolution: the ERP (Tally) figure keyed by ``tally_item_name`` (falling back to ``sku_id``) wins
because it covers the back room; the vision count (``visual``, keyed by ``sku_id``) is used when the ERP
is not connected. Both are echoed in the suggestion so the dashboard can show "system 48 / shelf 12".

Why this formula: a kirana owner orders "enough for the time until the next delivery plus a little
buffer"; expressing that as velocity x open hours x lead time keeps the number explainable, and the
footfall uplift is what makes the pre-Diwali order larger without any manual input.
"""

from __future__ import annotations

import math

from retailsense_contracts.api import FootfallForecast, ReorderSuggestion
from retailsense_contracts.config import SKU, StoreConfig

SAFETY_DAYS = 0.5


def open_hours_per_day(cfg: StoreConfig) -> float:
    """Hours between ``store.open_hours`` (HH:MM) - at least 1 to keep demand non-degenerate."""
    h0, m0 = (int(x) for x in cfg.store.open_hours[0].split(":")[:2])
    h1, m1 = (int(x) for x in cfg.store.open_hours[1].split(":")[:2])
    hours = (h1 * 60 + m1 - h0 * 60 - m0) / 60.0
    if hours <= 0:  # overnight shop, e.g. 18:00-02:00
        hours += 24.0
    return max(1.0, hours)


def footfall_uplift(footfall_fc: FootfallForecast | None, lead_time_days: int, avg_daily_footfall: float | None) -> float:
    """``mean(forecast over lead time) / avg`` - 1.0 when either side is unknown."""
    if footfall_fc is None or not footfall_fc.days or not avg_daily_footfall or avg_daily_footfall <= 0:
        return 1.0
    window = footfall_fc.days[: max(1, lead_time_days)]
    mean_fc = sum(d.predicted for d in window) / len(window)
    return max(0.0, mean_fc / avg_daily_footfall)


def _stock_for(sku: SKU, system_stock: dict[str, int] | None, visual: dict[str, int] | None) -> tuple[int | None, int | None]:
    sys_units = None
    if system_stock:
        key = sku.tally_item_name or sku.sku_id
        sys_units = system_stock.get(key, system_stock.get(sku.sku_id))
    vis_units = visual.get(sku.sku_id) if visual else None
    return (int(sys_units) if sys_units is not None else None, int(vis_units) if vis_units is not None else None)


def suggest_reorder(
    cfg: StoreConfig,
    footfall_fc: FootfallForecast | None,
    system_stock: dict[str, int] | None,
    visual: dict[str, int] | None,
    *,
    avg_daily_footfall: float | None = None,
) -> list[ReorderSuggestion]:
    """One :class:`ReorderSuggestion` per SKU in ``cfg.skus`` (zero-quantity rows included).

    ``avg_daily_footfall`` is the denominator of the uplift ratio (e.g.
    ``FootfallForecaster.average_daily_footfall()``); without it the uplift is 1.0.
    """
    hours = open_hours_per_day(cfg)
    out: list[ReorderSuggestion] = []
    for sku in cfg.skus:
        uplift = footfall_uplift(footfall_fc, sku.lead_time_days, avg_daily_footfall)
        demand = sku.velocity_units_per_hr * hours * sku.lead_time_days * uplift
        safety = SAFETY_DAYS * sku.velocity_units_per_hr * hours
        sys_units, vis_units = _stock_for(sku, system_stock, visual)
        stock = sys_units if sys_units is not None else (vis_units if vis_units is not None else 0)
        qty = max(0, math.ceil(demand + safety - stock))
        unit_cost = sku.mrp_inr * (1 - sku.margin_pct / 100.0)
        reason = (
            f"velocity {sku.velocity_units_per_hr:g}/hr × {hours:g} h × {sku.lead_time_days} d lead"
            f" × uplift {uplift:.2f} + {SAFETY_DAYS} day safety − stock {stock}"
        )
        out.append(
            ReorderSuggestion(
                sku_id=sku.sku_id,
                name_en=sku.name_en,
                name_hi=sku.name_hi,
                system_units=sys_units,
                visual_units=vis_units,
                forecast_units_lead=round(demand, 1),
                safety_stock=round(safety, 1),
                suggest_qty=qty,
                est_cost_inr=round(qty * unit_cost, 2),
                reason=reason,
            )
        )
    return out


__all__ = ["SAFETY_DAYS", "footfall_uplift", "open_hours_per_day", "suggest_reorder"]
