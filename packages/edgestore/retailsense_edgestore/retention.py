"""Scheduled purge per ``RetentionPolicy`` (privacy-by-design, DPDP storage limitation).

What goes, and when (defaults from ``RetentionPolicy``):

* telemetry events (heartbeats, heatmap deltas, sim truth) > 24 h
* aggregate events (crossings, snapshots, scans...) > 30 d
* shelf thumbnails (``shelf.scan.thumb_b64`` nulled in place) > 7 d
* sent/evicted outbox rows > 24 h
* heatmap cells > 90 d
* resolved alerts > 365 d

Events that are still *pending* in the outbox are never purged - they have not
reached the cloud yet. The job is deliberately tiny: ``EdgeStore.purge`` does
the SQL; this class adds the schedule, logging and run history so ``/health``
can show "last purge: 03:00, 412 rows".
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from retailsense_contracts.privacy import RetentionPolicy

log = logging.getLogger("retailsense.edgestore.retention")


class RetentionJob:
    """``RetentionJob(store, policy).run(now_ts)`` -> per-bucket counts (same shape as the contracts fake).

    ``RetentionJob.run_once(store, policy, now_ts)`` is the stateless form named in spec D8.
    """

    def __init__(
        self,
        store: Any,
        policy: RetentionPolicy | None = None,
        *,
        clock: Callable[[], float] | None = None,
        interval_s: float = 3600.0,
    ):
        self.store = store
        self.policy = policy or RetentionPolicy()
        self._clock = clock or time.time
        self.interval_s = float(interval_s)
        self.runs: list[dict[str, int]] = []
        self.last_run_ts: float | None = None

    @staticmethod
    def run_once(store: Any, policy: RetentionPolicy, now_ts: float) -> dict[str, int]:
        counts = store.purge(policy, now_ts)
        log.info("retention purge at %.0f: %s", now_ts, counts)
        return counts

    def run(self, now_ts: float | None = None) -> dict[str, int]:
        now = self._clock() if now_ts is None else now_ts
        counts = self.run_once(self.store, self.policy, now)
        self.runs.append(counts)
        self.last_run_ts = now
        return counts

    @property
    def total_purged(self) -> int:
        return sum(sum(r.values()) for r in self.runs)

    async def loop(self, stop: asyncio.Event, *, run_immediately: bool = False) -> None:
        """Run every ``interval_s`` until ``stop`` is set. Errors are logged, never raised."""
        if run_immediately:
            self._safe_run()
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.interval_s)
                break
            except TimeoutError:
                pass
            self._safe_run()

    def _safe_run(self) -> None:
        try:
            self.run()
        except Exception:  # pragma: no cover - defensive; a purge failure must not kill the loop
            log.exception("retention purge failed")


__all__ = ["RetentionJob"]
