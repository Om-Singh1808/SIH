"""False-positive feedback plumbing.

When the owner replies "3 = false alert" to a shelf_gap message, the shelf state
machine should learn (it bumps ``persistence_scans`` for that shelf, capped by
``max_persistence_scans``).  The rule engine does not import edgeshelf - it only
knows a callback - so this module provides a tiny hub that:

* accepts the simple ``Callable[[shelf_id], Any]`` the contracts' fake engine uses
  (``engine.feedback = shelf_machine.feedback_false_positive``), and
* accepts richer listeners that receive a :class:`FalsePositive` record for every
  kind (shrink, queue...), e.g. to log training data.

Listener exceptions are swallowed and logged: a broken learner must never stop
an ack from being recorded.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from retailsense_contracts.enums import AckBy, AlertKind

log = logging.getLogger("retailsense.edgerules.feedback")


@dataclass(frozen=True)
class FalsePositive:
    """One false-positive acknowledgement."""

    alert_id: str
    kind: AlertKind
    subject_id: str
    by: AckBy
    ts: float


ShelfFeedback = Callable[[str], Any]
Listener = Callable[[FalsePositive], Any]


class FeedbackHub:
    """Fan-out for false-positive acks."""

    def __init__(self) -> None:
        self.shelf_callback: ShelfFeedback | None = None
        self._listeners: list[Listener] = []
        self.history: list[FalsePositive] = []

    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def remove_listener(self, listener: Listener) -> None:
        self._listeners = [cb for cb in self._listeners if cb is not listener]

    def dispatch(self, fp: FalsePositive) -> None:
        self.history.append(fp)
        if fp.kind == AlertKind.SHELF_GAP and self.shelf_callback is not None:
            self._safe(self.shelf_callback, fp.subject_id)
        for cb in list(self._listeners):
            self._safe(cb, fp)

    @staticmethod
    def _safe(cb: Callable[..., Any], arg: Any) -> None:
        try:
            cb(arg)
        except Exception:  # a learner bug must not break the ack path
            log.exception("feedback listener %r failed", cb)


__all__ = ["FalsePositive", "FeedbackHub", "Listener", "ShelfFeedback"]
