"""The shopper agent: a tiny state machine that walks a list of waypoints.

A shopper is deliberately dumb.  All *decisions* (which shelves to visit, whether to
buy, join, abandon) are made by ``StoreModel`` which knows the geometry and the
queue; the agent only knows where it is, where it is going next and how fast.
Keeping the walker separate from the policy makes the path tests
(``test_shopper_path_crosses_entrance_then_counter``) read like a story.

Positions are floorplan pixels (the demo camera is the floorplan, identity
homography).  ``prev_pos`` is kept so the model can check line crossings with the
normative side-change rule after every move.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from enum import StrEnum

from retailsense_contracts.synthetic import SHOPPER_SPEED_PX_S


class ShopperState(StrEnum):
    ENTERING = "entering"  # spawned outside the door, walking in (crosses the entrance line upward)
    BROWSING = "browsing"  # walking between / dwelling at shelf fronts
    TO_QUEUE = "to_queue"  # basket non-empty, walking to the queue tail
    QUEUEING = "queueing"  # holds a slot in the queue zone, advancing towards the head
    SERVICE = "service"  # at the head, being served by the cashier
    EXITING = "exiting"  # walking to the door (crosses the entrance line downward, then despawns)
    ABANDONING = "abandoning"  # leaving the queue zone downward, never crossing the counter line


Point = tuple[float, float]


@dataclass
class Shopper:
    id: int
    x: float
    y: float
    state: ShopperState = ShopperState.ENTERING
    waypoints: list[Point] = field(default_factory=list)
    speed: float = SHOPPER_SPEED_PX_S  # px per sim-second (per-shopper jitter applied by the model)
    patience_count: int = 5  # balk if the queue already holds this many
    patience_s: float = 180.0  # renege after waiting this long
    basket: dict[str, int] = field(default_factory=dict)  # sku_id -> units
    plan: list[str] = field(default_factory=list)  # shelf ids still to visit
    v_jitter: int = 220  # HSV value used when rendering (180-255 keeps the blob in the detector window)
    dwell_until: float = 0.0  # sim ts until which the shopper stands still at a shelf front
    queue_join_ts: float | None = None
    service_end_ts: float | None = None
    spawned_ts: float = 0.0
    inside: bool = False  # crossed the entrance line inward (for truth.in_store)
    prev_x: float = 0.0
    prev_y: float = 0.0

    # -- geometry helpers ----------------------------------------------------
    @property
    def pos(self) -> Point:
        return (self.x, self.y)

    @property
    def prev_pos(self) -> Point:
        return (self.prev_x, self.prev_y)

    def set_path(self, *points: Point) -> None:
        self.waypoints = list(points)

    def at_target(self) -> bool:
        return not self.waypoints

    def remember(self) -> None:
        """Snapshot the position before a move so the model can detect line crossings."""
        self.prev_x, self.prev_y = self.x, self.y

    def move(self, dt: float) -> None:
        """Advance along the waypoint list by ``speed * dt`` pixels (straight segments).

        Leftover distance carries into the next waypoint so corners do not slow the walker.
        """
        budget = self.speed * dt
        while self.waypoints and budget > 1e-9:
            tx, ty = self.waypoints[0]
            dx, dy = tx - self.x, ty - self.y
            dist = math.hypot(dx, dy)
            if dist <= budget:
                self.x, self.y = tx, ty
                self.waypoints.pop(0)
                budget -= dist
            else:
                self.x += dx / dist * budget
                self.y += dy / dist * budget
                budget = 0.0

    def units(self) -> int:
        return sum(self.basket.values())


__all__ = ["Point", "Shopper", "ShopperState"]
