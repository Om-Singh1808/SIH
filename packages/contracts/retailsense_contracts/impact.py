"""THE rupee formula - single source of truth for edgerules, the cloud aggregator and the sim.

    lost_sales_inr   = mrp × velocity_units_per_hr × gap_hours × lost_sale_factor
    lost_margin_inr  = lost_sales_inr × margin_pct / 100
    recovered_inr    = rate_per_hour × max(0, baseline_unattended_gap_min − actual_gap_min) / 60
    queue_risk_inr   = max(0, count − threshold + 1) × queue_abandon_factor × atv_inr

``lost_sale_factor = 0.31`` comes from Gruen, Corsten & Bharadwaj (2002), the
GMA/FMI study of 71,000 shoppers in 29 countries: on an out-of-stock 31 % buy the
item elsewhere (and 9 % abandon the trip).  It is editable per store in
``ImpactConfig`` and every ``ImpactInr`` carries the factor and a ``basis``
string so the number on the alert is auditable.
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from .alerts import ImpactInr

if TYPE_CHECKING:  # avoid circular import: config.py imports ImpactConfig
    from .config import SKU

# Margin used when a formula has no SKU (queue abandonment): matches SKU.margin_pct default.
DEFAULT_MARGIN_PCT = 10.0


class ImpactConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    lost_sale_factor: float = 0.31  # share of OOS shoppers who buy elsewhere
    lost_sale_source: str = (
        "Gruen, Corsten & Bharadwaj 2002 (GMA/FMI, 71k shoppers, 29 countries): 31% buy elsewhere, 9% abandon"
    )
    queue_abandon_factor: float = 0.32
    queue_abandon_source: str = "Retail queue studies: 32% abandon after long lines; tolerance 5-8 min"
    atv_inr: float = 180.0  # avg basket; overridden by Tally sales when connected
    baseline_unattended_gap_min: float = 120.0  # assumption: unmonitored gap lasts ~2 h until next manual walk


def _r2(x: float) -> float:
    return round(float(x) + 0.0, 2)


def _g(x: float) -> str:
    """Compact number for basis strings: 27.0 -> '27', 0.33 -> '0.33'."""
    return f"{x:g}"


def rate_per_hour(sku: "SKU", cfg: ImpactConfig) -> float:
    """Rupees of sales lost per hour of empty shelf: mrp × velocity × factor."""
    return float(sku.mrp_inr) * float(sku.velocity_units_per_hr) * float(cfg.lost_sale_factor)


def lost_sales(sku: "SKU", gap_minutes: float, cfg: ImpactConfig) -> ImpactInr:
    """Lost sales for a shelf gap of ``gap_minutes`` on ``sku``."""
    gap_h = max(0.0, float(gap_minutes)) / 60.0
    lost = float(sku.mrp_inr) * float(sku.velocity_units_per_hr) * gap_h * float(cfg.lost_sale_factor)
    margin = lost * float(sku.margin_pct) / 100.0
    basis = f"₹{_g(sku.mrp_inr)} × {_g(sku.velocity_units_per_hr)}/hr × {gap_h:.2f} h × {_g(cfg.lost_sale_factor)}"
    return ImpactInr(
        lost_sales_inr=_r2(lost),
        lost_margin_inr=_r2(margin),
        basis=basis,
        factor=float(cfg.lost_sale_factor),
        source=cfg.lost_sale_source,
    )


def recovered(sku: "SKU", actual_gap_minutes: float, cfg: ImpactConfig) -> ImpactInr:
    """Sales *saved* because the alert shortened the gap versus the unattended baseline."""
    saved_min = max(0.0, float(cfg.baseline_unattended_gap_min) - max(0.0, float(actual_gap_minutes)))
    rate = rate_per_hour(sku, cfg)
    saved = rate * saved_min / 60.0
    margin = saved * float(sku.margin_pct) / 100.0
    basis = (
        f"₹{_g(rate)}/hr × max(0, {_g(cfg.baseline_unattended_gap_min)} − {_g(actual_gap_minutes)}) min ÷ 60"
        f" (factor {_g(cfg.lost_sale_factor)})"
    )
    return ImpactInr(
        lost_sales_inr=_r2(saved),
        lost_margin_inr=_r2(margin),
        basis=basis,
        factor=float(cfg.lost_sale_factor),
        source=cfg.lost_sale_source,
    )


def queue_abandon_risk(count: int, threshold: int, cfg: ImpactConfig) -> ImpactInr:
    """Revenue at risk from shoppers likely to abandon a queue of ``count`` (threshold ``threshold``)."""
    excess = max(0, int(count) - int(threshold) + 1)
    risk = excess * float(cfg.queue_abandon_factor) * float(cfg.atv_inr)
    margin = risk * DEFAULT_MARGIN_PCT / 100.0
    basis = f"max(0, {count} − {threshold} + 1) × {_g(cfg.queue_abandon_factor)} × ₹{_g(cfg.atv_inr)}"
    return ImpactInr(
        lost_sales_inr=_r2(risk),
        lost_margin_inr=_r2(margin),
        basis=basis,
        factor=float(cfg.queue_abandon_factor),
        source=cfg.queue_abandon_source,
    )


def zero_impact(cfg: ImpactConfig | None = None, reason: str = "no SKU mapped") -> ImpactInr:
    """An explicit ₹0 impact (used when a shelf has no SKU), still with a basis."""
    cfg = cfg or ImpactConfig()
    return ImpactInr(
        lost_sales_inr=0.0, lost_margin_inr=0.0, basis=reason, factor=cfg.lost_sale_factor, source=cfg.lost_sale_source
    )


__all__ = [
    "DEFAULT_MARGIN_PCT",
    "ImpactConfig",
    "ImpactInr",
    "lost_sales",
    "queue_abandon_risk",
    "rate_per_hour",
    "recovered",
    "zero_impact",
]
