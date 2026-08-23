"""Visual stock vs ERP stock -> shrink report (``registry`` key ``"reconcile"``).

The idea in one line: the camera counts what is *on the shelf*
(``facings x units_per_facing``), Tally says what the *books* hold; when the
books show materially more than the shelf, the difference is either stock in
the back room or shrink - and the owner should look.

Rules (``RulesConfig``):

* ``visual_units = facings x SKU.units_per_facing`` (an explicit
  ``visual_units`` map can override per SKU when a finer counter exists, e.g.
  the SKU identifier counting individual packs).
* ``delta_units = system_units - visual_units``; ``delta_inr = delta x mrp``.
* flagged when ``delta_units >= shrink_min_units`` **and**
  ``delta_inr >= shrink_min_inr`` - both guards, so three missing Parle-G
  (Rs 30) do not page the owner, nor does a single Rs 150 oil bottle.

Outputs:

* :class:`ReconcileReport` (the REST/board shape) - returned by :func:`reconcile`.
* ``stock.reconciled`` :class:`Observation` per row and a fully rendered
  ``shrink_suspect`` :class:`Alert` per flagged row - returned by
  :func:`reconcile_full` so SenseCloud can persist them and notify.

Matching an SKU to a Tally line uses ``SKU.tally_item_name`` first, then
``sku_id``, then a case/space-insensitive name match - Tally item names are
typed by humans.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from retailsense_contracts.alerts import ACTIONS_BY_KIND, Alert, ShrinkAlertDetails
from retailsense_contracts.api import ReconcileReport, ReconcileRow, ShelfStateView
from retailsense_contracts.config import SKU, ImpactConfig, RulesConfig, StoreConfig
from retailsense_contracts.enums import AlertKind, AlertStatus, Origin, Severity
from retailsense_contracts.events import Observation, StockReconciled
from retailsense_contracts.i18n import render
from retailsense_contracts.ids import new_ulid

log = logging.getLogger("retailsense.integrations.reconcile")

_SOURCES = ("tally", "zoho", "manual", "mock")


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def match_system_units(sku: SKU, stock: dict[str, int]) -> int | None:
    """Find ``sku`` in an ERP stock map; ``None`` when the ERP has no such item."""
    for key in (sku.tally_item_name, sku.sku_id, sku.name_en):
        if key and key in stock:
            return int(stock[key])
    wanted = {_norm(k) for k in (sku.tally_item_name, sku.sku_id, sku.name_en) if k}
    for name, qty in stock.items():
        if _norm(name) in wanted:
            return int(qty)
    return None


def visual_units_for(view: ShelfStateView, sku: SKU, override: dict[str, int] | None = None) -> int:
    """``facings x units_per_facing`` unless an explicit per-SKU count is supplied."""
    if override and sku.sku_id in override:
        return int(override[sku.sku_id])
    return int(view.facings) * int(sku.units_per_facing)


def is_flagged(delta_units: int, delta_inr: float, rules: RulesConfig) -> bool:
    return delta_units >= rules.shrink_min_units and delta_inr >= rules.shrink_min_inr


def _source_literal(erp: Any) -> str:
    src = str(getattr(erp, "source", "manual"))
    return src if src in _SOURCES else "manual"


def build_shrink_alert(row: ReconcileRow, cfg: StoreConfig, sku: SKU, ts: float) -> Alert:
    """A cloud-origin ``shrink_suspect`` alert with Hindi + English text pre-rendered."""
    details = ShrinkAlertDetails(
        sku_id=sku.sku_id,
        sku_name=sku.name_en,
        visual_units=row.visual_units,
        system_units=row.system_units,
        delta_units=row.delta_units,
        delta_inr=row.delta_inr,
    )
    params_en = {
        "sku_name": sku.name_en,
        "system_units": row.system_units,
        "visual_units": row.visual_units,
        "delta_inr": row.delta_inr,
    }
    params_hi = dict(params_en, sku_name=sku.name_hi)
    severity = Severity.HIGH if row.delta_inr >= 2 * cfg.rules.shrink_min_inr else Severity.WARN
    return Alert(
        alert_id=new_ulid(ts),
        store_id=cfg.store.store_id,
        device_id=cfg.device.device_id,
        origin=Origin.CLOUD,
        kind=AlertKind.SHRINK_SUSPECT,
        severity=severity,
        status=AlertStatus.OPEN,
        subject_id=sku.sku_id,
        title_en=render("shrink_suspect.title", "en", **params_en),
        title_hi=render("shrink_suspect.title", "hi", **params_hi),
        message_en=render("shrink_suspect.msg", "en", **params_en),
        message_hi=render("shrink_suspect.msg", "hi", **params_hi),
        details=details,
        impact=None,
        actions=list(ACTIONS_BY_KIND[AlertKind.SHRINK_SUSPECT]),
        raised_ts=ts,
    )


@dataclass
class ReconcileResult:
    """Everything a reconcile run produces; ``report`` is the REST shape."""

    report: ReconcileReport
    observations: list[Observation] = field(default_factory=list)  # one stock.reconciled per row
    alerts: list[Alert] = field(default_factory=list)  # one shrink_suspect per flagged row
    unmatched: list[str] = field(default_factory=list)  # sku_ids the ERP does not know


def reconcile_full(
    store_cfg: StoreConfig,
    erp: Any,
    shelf_views: list[ShelfStateView],
    rules: RulesConfig | None = None,
    impact: ImpactConfig | None = None,
    *,
    visual_units: dict[str, int] | None = None,
    ts: float | None = None,
) -> ReconcileResult:
    """Run a reconcile and return report + observations + alerts.

    ``rules``/``impact`` default to the store's own; ``impact`` is accepted for
    signature parity with the registry contract (shrink is valued at MRP, so
    the impact model is not consulted).
    """
    rules = rules or store_cfg.rules
    ts = time.time() if ts is None else ts
    stock = erp.stock_summary()
    source = _source_literal(erp)
    rows: list[ReconcileRow] = []
    observations: list[Observation] = []
    alerts: list[Alert] = []
    unmatched: list[str] = []
    seen: set[str] = set()
    total = 0.0
    for view in shelf_views:
        sku = store_cfg.sku(view.sku_id) if view.sku_id else None
        if sku is None or sku.sku_id in seen:
            continue  # shelf without an SKU, or a second shelf of the same SKU (first wins)
        seen.add(sku.sku_id)
        system = match_system_units(sku, stock)
        if system is None:
            unmatched.append(sku.sku_id)
            log.info("reconcile: %s not found in %s stock", sku.sku_id, source)
            continue
        visual = visual_units_for(view, sku, visual_units)
        delta = system - visual
        delta_inr = round(delta * sku.mrp_inr, 2)
        flagged = is_flagged(delta, delta_inr, rules)
        row = ReconcileRow(
            sku_id=sku.sku_id,
            name=sku.name_en,
            shelf_id=view.shelf_id,
            visual_units=visual,
            system_units=system,
            delta_units=delta,
            delta_inr=delta_inr,
            flagged=flagged,
        )
        rows.append(row)
        observations.append(
            Observation.of(
                StockReconciled(
                    sku_id=sku.sku_id,
                    shelf_id=view.shelf_id,
                    visual_units=visual,
                    system_units=system,
                    delta_units=delta,
                    delta_inr=delta_inr,
                    source=source,  # type: ignore[arg-type]
                ),
                ts,
            )
        )
        if flagged:
            total += delta_inr
            alerts.append(build_shrink_alert(row, store_cfg, sku, ts))
    report = ReconcileReport(
        store_id=store_cfg.store.store_id,
        ts=ts,
        source=source,
        rows=rows,
        shrink_inr_total=round(total, 2),
        alerts_raised=len(alerts),
    )
    return ReconcileResult(report=report, observations=observations, alerts=alerts, unmatched=unmatched)


def reconcile(
    store_cfg: StoreConfig,
    erp: Any,
    shelf_views: list[ShelfStateView],
    rules: RulesConfig | None = None,
    impact: ImpactConfig | None = None,
    *,
    visual_units: dict[str, int] | None = None,
    ts: float | None = None,
) -> ReconcileReport:
    """Registry entry point (``"reconcile"``): the :class:`ReconcileReport` only."""
    return reconcile_full(store_cfg, erp, shelf_views, rules, impact, visual_units=visual_units, ts=ts).report


__all__ = [
    "ReconcileResult",
    "build_shrink_alert",
    "is_flagged",
    "match_system_units",
    "reconcile",
    "reconcile_full",
    "visual_units_for",
]
