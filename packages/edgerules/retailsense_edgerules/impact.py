"""``ImpactCalculator`` - a thin, store-aware wrapper over ``retailsense_contracts.impact``.

The contracts module owns THE formula; this class only binds it to a store:

* the store's :class:`ImpactConfig` (factor, citation, baseline gap, ATV),
* SKU lookup by id (so callers pass ``sku_id`` strings, not SKU objects),
* an optional live ATV (average transaction value) derived from Tally sales,
  which overrides the configured ``atv_inr`` for queue abandonment risk,
* the reorder-quantity heuristic used when an owner replies "2 = order".

Every returned :class:`ImpactInr` still carries ``basis``/``factor``/``source``
from the contracts so the number on the alert remains auditable.
"""

import math

from retailsense_contracts.alerts import ImpactInr
from retailsense_contracts.config import SKU, StoreConfig
from retailsense_contracts.impact import (
    ImpactConfig,
    lost_sales,
    queue_abandon_risk,
    rate_per_hour,
    recovered,
    zero_impact,
)

# Trading hours per day when ``store.open_hours`` cannot be parsed (08:00-22:00).
_DEFAULT_OPEN_HOURS = 14.0


def open_hours_per_day(cfg: StoreConfig) -> float:
    """Trading hours per day from ``store.open_hours`` ("08:00","22:00") -> 14.0."""
    try:
        start, end = cfg.store.open_hours
        h0, m0 = (int(x) for x in start.split(":"))
        h1, m1 = (int(x) for x in end.split(":"))
        hours = (h1 + m1 / 60.0) - (h0 + m0 / 60.0)
        if hours <= 0:  # overnight shop, e.g. 20:00-02:00
            hours += 24.0
        return hours
    except (ValueError, TypeError, AttributeError):
        return _DEFAULT_OPEN_HOURS


class ImpactCalculator:
    """Store-bound facade over the contracts impact formulas."""

    def __init__(self, cfg: StoreConfig, *, atv_inr: float | None = None):
        self._cfg = cfg
        self._base = cfg.impact
        self._atv_override: float | None = None
        if atv_inr is not None:
            self.set_atv(atv_inr)

    # -- configuration -----------------------------------------------------
    @property
    def config(self) -> ImpactConfig:
        """Effective config: the store's, with ``atv_inr`` replaced when Tally gave us a live one."""
        if self._atv_override is None:
            return self._base
        return self._base.model_copy(update={"atv_inr": self._atv_override})

    @property
    def atv_inr(self) -> float:
        return self.config.atv_inr

    def set_atv(self, atv_inr: float | None) -> None:
        """Override the average basket value (INR); ``None`` reverts to the configured value."""
        if atv_inr is not None and (not math.isfinite(atv_inr) or atv_inr <= 0):
            raise ValueError(f"atv_inr must be a positive finite number, got {atv_inr!r}")
        self._atv_override = atv_inr

    def set_atv_from_sales(self, sales_inr: float, transactions: int) -> float | None:
        """Derive ATV from a Tally ``sales_today`` summary. Ignored (returns None) when there were no bills."""
        if transactions <= 0 or sales_inr <= 0:
            return None
        atv = float(sales_inr) / float(transactions)
        self.set_atv(atv)
        return atv

    # -- lookups -------------------------------------------------------------
    def sku(self, sku_id: str | None) -> SKU | None:
        return self._cfg.sku(sku_id)

    def rate_per_hour(self, sku_id: str | None) -> float:
        """INR lost per hour of empty shelf for ``sku_id`` (0 when the shelf has no SKU mapped)."""
        sku = self.sku(sku_id)
        return rate_per_hour(sku, self.config) if sku else 0.0

    # -- formulas --------------------------------------------------------------
    def lost_sales(self, sku_id: str | None, gap_minutes: float) -> ImpactInr:
        sku = self.sku(sku_id)
        if sku is None:
            return zero_impact(self.config, reason=f"no SKU mapped ({sku_id or 'unmapped shelf'})")
        return lost_sales(sku, gap_minutes, self.config)

    def recovered(self, sku_id: str | None, actual_gap_minutes: float) -> ImpactInr | None:
        """Sales saved versus the unattended baseline; ``None`` when no SKU is mapped."""
        sku = self.sku(sku_id)
        if sku is None:
            return None
        return recovered(sku, actual_gap_minutes, self.config)

    def queue_abandon_risk(self, count: int, threshold: int) -> ImpactInr:
        return queue_abandon_risk(count, threshold, self.config)

    # -- reorder ---------------------------------------------------------------
    def suggest_order_qty(self, sku_id: str | None) -> int:
        """Units to order so the shelf survives the distributor's lead time.

        ``velocity_units_per_hr x trading hours per day x lead_time_days``, rounded up to
        whole facings so the delivery fills complete facings.
        """
        sku = self.sku(sku_id)
        if sku is None:
            return 0
        units = sku.velocity_units_per_hr * open_hours_per_day(self._cfg) * max(1, sku.lead_time_days)
        per_facing = max(1, int(sku.units_per_facing))
        return int(math.ceil(units / per_facing) * per_facing)

    def order_cost_inr(self, sku_id: str | None, qty: int) -> float | None:
        """Estimated purchase cost: MRP less the retailer's margin."""
        sku = self.sku(sku_id)
        if sku is None:
            return None
        return round(qty * sku.mrp_inr * (1.0 - sku.margin_pct / 100.0), 2)


__all__ = ["ImpactCalculator", "open_hours_per_day"]
