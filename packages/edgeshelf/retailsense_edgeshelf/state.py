"""Persistence-filtered shelf state machine.

Design rationale
----------------
A single scan that says "empty" is not an out-of-stock: a shopper's arm, a
shadow, a flicker of auto-exposure all produce one bad reading. A false
WhatsApp alert costs the owner's trust, so a gap is only *confirmed* after
``persistence_required`` consecutive empty scans (3 by default, 30 s apart in
the demo). The machine therefore keeps, per shelf:

* ``state`` - the confirmed state (UNKNOWN until the first scan);
* ``consecutive_empty_scans`` - the current run of empty readings;
* ``gap_started_ts`` - the timestamp of the *first* empty scan in the run, so
  the reported gap is measured from when the shelf actually emptied, not from
  when we became sure;
* ``fp_count`` - how often the owner replied "false positive" for this shelf.
  Each reply raises the persistence requirement by one (capped by
  ``max_persistence_scans``): the system learns to be more conservative on
  shelves it misjudges, without any model retraining.

Scans flagged ``occluded`` (a person in front of the shelf) are ignored
completely - they neither extend nor reset the run - so a shopper browsing a
gap does not hide it, and a shopper browsing a full shelf does not trigger it.

Raw state comes from coverage thresholds in ``RulesConfig``
(``>= shelf_partial_coverage`` -> STOCKED, ``> shelf_empty_coverage`` ->
PARTIAL, else EMPTY); fewer than ``min_facings`` facings is EMPTY-equivalent
regardless of coverage (a lone facing of milk is not "partially stocked").

Transitions emitted as ``shelf.state`` events:

* ``* -> EMPTY``    when the run reaches ``persistence_required`` (with
  ``gap_minutes`` so far and the INR impact);
* ``EMPTY -> STOCKED/PARTIAL`` on the next non-empty scan (the *resolve*, with
  the total ``gap_minutes`` and final impact);
* ``STOCKED <-> PARTIAL`` immediately, informational (no impact).

OSA and "gap minutes today" are derived from the same bookkeeping (see
:mod:`retailsense_edgeshelf.osa`). :meth:`restore` reloads the machine from
``shelf_state`` rows after a restart so a gap does not reset on reboot.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from retailsense_contracts.alerts import ImpactInr
from retailsense_contracts.api import ShelfStateView
from retailsense_contracts.clock import DEFAULT_TZ, day_start_ts
from retailsense_contracts.config import SKU, RulesConfig, ShelfPolygon, ShelfReference
from retailsense_contracts.enums import ShelfState
from retailsense_contracts.events import ShelfScan, ShelfStateChange
from retailsense_contracts.impact import ImpactConfig, lost_sales, zero_impact

from .osa import gap_minutes_in_day, osa_pct

_RESTORE_KEYS = (
    "coverage",
    "facings",
    "consecutive_empty_scans",
    "fp_count",
    "gap_started_ts",
    "last_scan_ts",
    "gap_minutes_today",
)


@dataclass
class _ShelfSlot:
    """Mutable per-shelf bookkeeping (kept private; :class:`ShelfStateView` is the public form)."""

    state: ShelfState = ShelfState.UNKNOWN
    coverage: float = 0.0
    facings: int = 0
    consecutive_empty_scans: int = 0
    fp_count: int = 0
    gap_started_ts: float | None = None
    last_scan_ts: float | None = None
    gap_minutes_today: float = 0.0  # closed gaps only; the open gap is added on read
    occluded: bool = False
    reference: ShelfReference | None = None
    extra: dict[str, Any] = field(default_factory=dict)


def raw_state(coverage: float, facings: int, min_facings: int, rules: RulesConfig) -> ShelfState:
    """Threshold rule shared by the state machine and the scanner's ``state_raw``."""
    if facings < min_facings:
        return ShelfState.EMPTY
    if coverage >= rules.shelf_partial_coverage:
        return ShelfState.STOCKED
    if coverage > rules.shelf_empty_coverage:
        return ShelfState.PARTIAL
    return ShelfState.EMPTY


class ShelfStateMachine:
    """Implements :class:`retailsense_contracts.interfaces.ShelfStateMachine`."""

    def __init__(
        self,
        shelves: list[ShelfPolygon],
        skus: list[SKU],
        rules: RulesConfig,
        impact: ImpactConfig,
        tz_name: str = DEFAULT_TZ,
    ) -> None:
        self.shelves: dict[str, ShelfPolygon] = {s.shelf_id: s for s in shelves}
        self.skus: dict[str, SKU] = {s.sku_id: s for s in skus}
        self.rules = rules
        self.impact = impact
        self.tz_name = tz_name
        self._slots: dict[str, _ShelfSlot] = {s.shelf_id: _ShelfSlot(reference=s.reference) for s in shelves}
        self._day_start: float | None = None

    # -- helpers ---------------------------------------------------------------

    def _required(self, slot: _ShelfSlot) -> int:
        return min(self.rules.persistence_scans + slot.fp_count, self.rules.max_persistence_scans)

    def _impact(self, shelf: ShelfPolygon, gap_minutes: float) -> ImpactInr:
        sku = self.skus.get(shelf.sku_id or "")
        return lost_sales(sku, gap_minutes, self.impact) if sku else zero_impact(self.impact)

    def _roll_day(self, ts: float) -> float:
        """Return today's day-start, resetting per-shelf daily totals when the store-day changes."""
        if self._day_start is None or ts >= self._day_start + 86400.0 or ts < self._day_start:
            new_start = day_start_ts(ts, self.tz_name)
            if self._day_start is not None and new_start != self._day_start:
                for slot in self._slots.values():
                    slot.gap_minutes_today = 0.0
            self._day_start = new_start
        return self._day_start

    def _change(self, shelf: ShelfPolygon, slot: _ShelfSlot, frm: ShelfState, to: ShelfState, **kw: Any) -> ShelfStateChange:
        slot.state = to
        return ShelfStateChange(
            shelf_id=shelf.shelf_id,
            sku_id=shelf.sku_id,
            from_state=frm,
            to_state=to,
            consecutive_empty_scans=slot.consecutive_empty_scans,
            **kw,
        )

    # -- protocol --------------------------------------------------------------

    def apply(self, scan: ShelfScan, ts: float) -> ShelfStateChange | None:
        """Feed one scan; return the ``shelf.state`` change it causes, if any."""
        shelf = self.shelves.get(scan.shelf_id)
        if shelf is None:
            return None
        slot = self._slots[scan.shelf_id]
        day_start = self._roll_day(ts)

        if scan.occluded:  # no information: neither extend nor reset the run
            slot.occluded = True
            return None
        slot.occluded = False
        slot.coverage, slot.facings, slot.last_scan_ts = scan.coverage, scan.facings, ts

        raw = raw_state(scan.coverage, scan.facings, shelf.min_facings, self.rules)
        prev = slot.state

        if raw == ShelfState.EMPTY:
            slot.consecutive_empty_scans += 1
            if slot.gap_started_ts is None:
                slot.gap_started_ts = ts
            if prev != ShelfState.EMPTY and slot.consecutive_empty_scans >= self._required(slot):
                gap_min = (ts - slot.gap_started_ts) / 60.0
                return self._change(
                    shelf,
                    slot,
                    prev,
                    ShelfState.EMPTY,
                    gap_started_ts=slot.gap_started_ts,
                    gap_minutes=round(gap_min, 2),
                    impact=self._impact(shelf, gap_min),
                )
            return None

        # non-empty reading: the run is over
        slot.consecutive_empty_scans = 0
        gap_started, slot.gap_started_ts = slot.gap_started_ts, None
        if prev == ShelfState.EMPTY and gap_started is not None:
            gap_min = (ts - gap_started) / 60.0
            slot.gap_minutes_today += gap_minutes_in_day(gap_started, ts, day_start)
            return self._change(
                shelf,
                slot,
                prev,
                raw,
                gap_started_ts=gap_started,
                gap_minutes=round(gap_min, 2),
                impact=self._impact(shelf, gap_min),
            )
        if prev != raw:  # UNKNOWN/STOCKED/PARTIAL shuffles: informational
            return self._change(shelf, slot, prev, raw)
        return None

    def view(self, shelf_id: str) -> ShelfStateView:
        shelf = self.shelves[shelf_id]
        slot = self._slots[shelf_id]
        sku = self.skus.get(shelf.sku_id or "")
        gap_min = None
        impact_open = None
        if slot.state == ShelfState.EMPTY and slot.gap_started_ts is not None and slot.last_scan_ts is not None:
            gap_min = round((slot.last_scan_ts - slot.gap_started_ts) / 60.0, 2)
            impact_open = self._impact(shelf, gap_min)
        return ShelfStateView(
            shelf_id=shelf_id,
            name=shelf.name,
            sku_id=shelf.sku_id,
            sku_name=sku.name_en if sku else shelf.name,
            state=slot.state,
            coverage=slot.coverage,
            facings=slot.facings,
            capacity_facings=shelf.capacity_facings,
            min_facings=shelf.min_facings,
            consecutive_empty_scans=slot.consecutive_empty_scans,
            persistence_required=self._required(slot),
            gap_started_ts=slot.gap_started_ts,
            gap_minutes=gap_min,
            last_scan_ts=slot.last_scan_ts,
            occluded=slot.occluded,
            impact_open=impact_open,
            has_reference=slot.reference is not None,
        )

    def views(self) -> list[ShelfStateView]:
        return [self.view(sid) for sid in self.shelves]

    def feedback_false_positive(self, shelf_id: str) -> int:
        """Owner said "it was not empty": raise persistence for this shelf and drop the run.

        The confirmed EMPTY state (if any) becomes UNKNOWN so the next good scan
        is an informational transition rather than a "resolved gap" with impact;
        nothing is added to today's gap minutes because there was no gap.
        """
        slot = self._slots[shelf_id]
        slot.fp_count += 1
        slot.consecutive_empty_scans = 0
        slot.gap_started_ts = None
        if slot.state == ShelfState.EMPTY:
            slot.state = ShelfState.UNKNOWN
        return self._required(slot)

    def restore(self, rows: list[dict]) -> None:
        """Reload from ``shelf_state`` rows (EdgeStore.shelves()) after a restart."""
        for row in rows:
            slot = self._slots.get(row.get("shelf_id", ""))
            if slot is None:
                continue
            for key in _RESTORE_KEYS:
                if row.get(key) is not None:
                    setattr(slot, key, row[key])
            if row.get("fp_count") is None and row.get("persistence_required") is not None:
                slot.fp_count = max(0, int(row["persistence_required"]) - self.rules.persistence_scans)
            if row.get("state"):
                slot.state = ShelfState(row["state"])
            ref = row.get("reference")
            if ref:
                slot.reference = ref if isinstance(ref, ShelfReference) else ShelfReference.model_validate(ref)

    # -- references (used by the scanner / calibrate endpoint) -------------------

    def set_reference(self, ref: ShelfReference) -> None:
        if ref.shelf_id in self._slots:
            self._slots[ref.shelf_id].reference = ref

    def reference(self, shelf_id: str) -> ShelfReference | None:
        return self._slots[shelf_id].reference

    # -- KPIs ----------------------------------------------------------------------

    def gap_minutes_today(self, ts: float) -> float:
        """Closed gaps today plus every still-open confirmed gap up to ``ts``."""
        day_start = self._day_start if self._day_start is not None else day_start_ts(ts, self.tz_name)
        total = 0.0
        for slot in self._slots.values():
            total += slot.gap_minutes_today
            if slot.state == ShelfState.EMPTY and slot.gap_started_ts is not None:
                total += gap_minutes_in_day(slot.gap_started_ts, ts, day_start)
        return round(total, 2)

    def osa_pct(self, ts: float) -> float:
        day_start = self._day_start if self._day_start is not None else day_start_ts(ts, self.tz_name)
        return osa_pct(self.gap_minutes_today(ts), len(self._slots), (ts - day_start) / 60.0)


__all__ = ["ShelfStateMachine", "raw_state"]
