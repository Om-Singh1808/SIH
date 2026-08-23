"""Outbox diagnostics: one SQL pass that summarises the store-and-forward queue.

The sync worker and ``/sync/status`` only need ``EdgeStore.backlog()``; this
module serves the runbook/debug endpoint with a richer picture (oldest pending
age, attempts, evicted/expired totals) without adding surface to the Protocol.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import func, select

from retailsense_contracts.db import outbox as t_outbox


@dataclass
class OutboxStats:
    pending: int = 0
    sent: int = 0
    evicted: int = 0
    pending_by_class: dict[str, int] = field(default_factory=dict)
    oldest_pending_ts: float | None = None
    max_attempts: int = 0
    last_error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def outbox_stats(store: Any) -> OutboxStats:
    """Summarise ``store.engine``'s outbox table."""
    engine = store.engine
    pending_where = (t_outbox.c.sent_ts.is_(None), t_outbox.c.evicted_ts.is_(None))
    with engine.connect() as conn:
        sent = conn.execute(select(func.count()).where(t_outbox.c.sent_ts.is_not(None))).scalar() or 0
        evicted = conn.execute(select(func.count()).where(t_outbox.c.evicted_ts.is_not(None))).scalar() or 0
        by_cls = {
            str(c): int(n)
            for c, n in conn.execute(select(t_outbox.c.cls, func.count()).where(*pending_where).group_by(t_outbox.c.cls))
        }
        oldest = conn.execute(select(func.min(t_outbox.c.enqueued_ts)).where(*pending_where)).scalar()
        max_att = conn.execute(select(func.max(t_outbox.c.attempts)).where(*pending_where)).scalar() or 0
        last_err = conn.execute(
            select(t_outbox.c.last_error)
            .where(t_outbox.c.last_error.is_not(None))
            .order_by(t_outbox.c.id.desc())
            .limit(1)
        ).scalar()
    return OutboxStats(
        pending=sum(by_cls.values()),
        sent=int(sent),
        evicted=int(evicted),
        pending_by_class=by_cls,
        oldest_pending_ts=None if oldest is None else float(oldest),
        max_attempts=int(max_att),
        last_error=last_err,
    )


__all__ = ["OutboxStats", "outbox_stats"]
