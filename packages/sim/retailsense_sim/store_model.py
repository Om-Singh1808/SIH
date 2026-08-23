"""Agent-based model of one kirana store: arrivals, browsing, buying, queueing, cashier, shelves.

Design
------
* **Geometry comes from ``StoreConfig``**, never from constants: the entrance line, the
  queue polygon, the counter line and the shelf polygons of ``examples/store_demo.yaml``
  define where shoppers spawn, stand, queue and exit.  Change the YAML and the sim
  follows.  ``Layout`` precomputes the handful of derived points once.
* **Every shopper path is explicit** so the CV pipeline sees exactly the events the
  rules expect: entry crosses the entrance line *upward* (``Direction.IN``), a served
  shopper crosses the counter line *leftward* at the lane height (``IN`` = served), an
  abandoner leaves the queue zone *downward* and never touches the counter line, and
  everyone exits through the door *downward* (``OUT``).
* **Truth is counted by the same rule the edge uses**: after every move the model
  applies the normative side-change test to each line, so ``truth().footfall_in_total``
  is literally the number of entrance crossings a perfect detector would see.
* **Calibrated buying**: the per-visit purchase probability is derived from the SKU's
  ``velocity_units_per_hr`` and the current arrival rate so that expected units/hour
  equals ``velocity x arrival multiplier`` - the rupee impact formula then has a ground
  truth to be checked against.
* **Deterministic**: one ``numpy.random.Generator`` seeded in ``__init__``; no wall
  clock anywhere in ``step``.  ``step(dt)`` is pure simulation; pacing lives in
  ``SyntheticFrameSource``.

Performance: ~30 shoppers x 2 lines x a few float ops per step is well under 1 ms,
leaving the render budget for the >200 fps target.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from retailsense_contracts.api import ChaosRequest, ScenarioStatus
from retailsense_contracts.clock import date_to_ts, day_start_ts, store_date
from retailsense_contracts.config import SKU, Counter, Line, ShelfPolygon, StoreConfig
from retailsense_contracts.enums import Direction, LineKind
from retailsense_contracts.events import SimTruth
from retailsense_contracts.geometry import polygon_bbox, polygon_long_axis
from retailsense_contracts.synthetic import MIN_SEPARATION_PX, QUEUE_SPACING_PX, SHOPPER_SPEED_PX_S, SIM_DT_S

from .scenarios import SCENARIOS, arrival_mult, arrival_rate_pm, scenario_defaults
from .shopper import Point, Shopper, ShopperState

DOOR_OUTSIDE_PX = 28  # spawn/despawn distance outside the entrance line
DOOR_INSIDE_PX = 35  # first waypoint inside the store after crossing
SHELF_STAND_PX = 18  # distance from the shelf front where shoppers stand
QUEUE_HEAD_INSET_PX = 22  # head slot below the top edge of the queue polygon
QUEUE_APPROACH_DX = 24  # tail-approach lane offset from the slot column
SERVED_WALK_PX = 40  # how far past the counter line a served shopper walks before turning
AUTO_RESTOCK_DELAY_S = 150.0  # sim seconds between "below min facings" and the shop boy refilling
LANE_CLEARANCE_PX = 20
UNITS_PER_PURCHASE = 1.15  # E[units] per purchase: 15 % of buyers take two


# ---------------------------------------------------------------------------
# derived geometry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Layout:
    """Points derived once from the StoreConfig geometry (floorplan pixels)."""

    width: int
    height: int
    door_x: float
    door_y: float
    spawn: Point  # outside the door
    inside_door: Point  # just inside the door
    queue_head: Point
    queue_dir: tuple[float, float]  # unit vector from head towards the tail (downward in the demo)
    queue_tail_outside: Point  # point just past the last slot, outside the zone
    queue_approach_x: float  # x of the lane used to walk up to a slot
    served_exit: Point  # point past the counter line where a served shopper crosses to
    counter_lane_y: float
    staging: Point  # waypoint between the aisles and the queue/door, avoids the desk
    shelf_stands: dict[str, tuple[Point, Point]]  # shelf_id -> (stand_a, stand_b) segment in front
    interior: tuple[float, float, float, float]  # xyxy where free-walking shoppers are clamped

    @classmethod
    def from_config(cls, cfg: StoreConfig) -> Layout:
        fp = cfg.floorplan
        w, h = fp.width_px, fp.height_px
        door = next((ln for ln in cfg.lines if ln.kind == LineKind.ENTRANCE), None)
        if door is None:  # a store without an entrance line: use the bottom middle
            door_x, door_y = w / 2.0, h - 45.0
        else:
            door_x = (door.start[0] + door.end[0]) / 2.0
            door_y = (door.start[1] + door.end[1]) / 2.0
        # the door is assumed to be on the bottom wall (demo) unless it sits in the top half
        sign = 1.0 if door_y >= h / 2 else -1.0
        spawn = (door_x, door_y + sign * DOOR_OUTSIDE_PX)
        inside_door = (door_x, door_y - sign * DOOR_INSIDE_PX)

        counter = cfg.counters[0] if cfg.counters else None
        if counter is not None:
            zone = cfg.zone(counter.queue_zone_id)
            line = cfg.line(counter.counter_line_id)
            x0, y0, x1, y1 = polygon_bbox(zone.polygon)
            cx = (x0 + x1) / 2.0
            head = (cx, y0 + QUEUE_HEAD_INSET_PX)
            qdir = (0.0, 1.0)
            tail_outside = (cx, y1 + LANE_CLEARANCE_PX)
            approach_x = min(cx + QUEUE_APPROACH_DX, x1 - 10)
            lane_y = (line.start[1] + line.end[1]) / 2.0
            line_x = min(line.start[0], line.end[0])
            served_exit = (line_x - SERVED_WALK_PX, lane_y)
            staging = (x0 - 90.0, y1 + LANE_CLEARANCE_PX)
        else:
            head = (w - 64.0, 120.0)
            qdir = (0.0, 1.0)
            tail_outside = (w - 64.0, h - 40.0)
            approach_x = w - 40.0
            lane_y = 120.0
            served_exit = (w - 140.0, lane_y)
            staging = (w - 190.0, h - 40.0)

        stands: dict[str, tuple[Point, Point]] = {}
        for s in cfg.shelves:
            sx0, sy0, sx1, sy1 = polygon_bbox(s.polygon)
            if polygon_long_axis(s.polygon) == "x":
                y = sy1 + SHELF_STAND_PX if (sy0 + sy1) / 2 < h / 2 else sy0 - SHELF_STAND_PX
                stands[s.shelf_id] = ((sx0 + 12.0, float(y)), (sx1 - 12.0, float(y)))
            else:
                x = sx1 + SHELF_STAND_PX if (sx0 + sx1) / 2 < w / 2 else sx0 - SHELF_STAND_PX
                stands[s.shelf_id] = ((float(x), sy0 + 12.0), (float(x), sy1 - 12.0))

        return cls(
            width=w,
            height=h,
            door_x=door_x,
            door_y=door_y,
            spawn=spawn,
            inside_door=inside_door,
            queue_head=head,
            queue_dir=qdir,
            queue_tail_outside=tail_outside,
            queue_approach_x=approach_x,
            served_exit=served_exit,
            counter_lane_y=lane_y,
            staging=staging,
            shelf_stands=stands,
            interior=(24.0, 24.0, w - 24.0, h - 24.0),
        )

    def slot(self, index: int) -> Point:
        hx, hy = self.queue_head
        dx, dy = self.queue_dir
        return (hx + dx * QUEUE_SPACING_PX * index, hy + dy * QUEUE_SPACING_PX * index)


# ---------------------------------------------------------------------------
# shelves and cashier
# ---------------------------------------------------------------------------


@dataclass
class ShelfUnit:
    """Stock on one shelf polygon, in units; facings are derived (``ceil(units / units_per_facing)``)."""

    shelf: ShelfPolygon
    sku: SKU | None
    units: float
    auto_restock: bool = True
    drain_rate: float = 0.0  # units per sim-second while a stockout scenario is active
    restock_due_ts: float | None = None
    lost_sale_units: int = 0  # shoppers who wanted to buy from an empty shelf

    @property
    def units_per_facing(self) -> int:
        return self.sku.units_per_facing if self.sku else 4

    @property
    def capacity_units(self) -> int:
        return self.shelf.capacity_facings * self.units_per_facing

    @property
    def facings(self) -> int:
        return int(min(self.shelf.capacity_facings, math.ceil(max(0.0, self.units) / self.units_per_facing)))

    @property
    def empty(self) -> bool:
        return self.units < 0.5


@dataclass
class Cashier:
    counter: Counter
    service_mult: float = 1.0  # 2.0 = second counter open; 0.0 = closed
    busy_with: int | None = None
    service_end_ts: float | None = None
    mean_service_s: float = 45.0
    sd_service_s: float = 15.0


# ---------------------------------------------------------------------------
# per-step snapshot
# ---------------------------------------------------------------------------


@dataclass
class CrossingEvent:
    line_id: str
    line_kind: LineKind
    direction: Direction
    shopper_id: int
    ts: float


@dataclass
class SimState:
    """Everything the renderer / headless emitter needs from one step (plain data, no references)."""

    ts: float
    positions: np.ndarray  # [N,2] float32 shopper centres
    ids: np.ndarray  # [N] int
    v_jitter: np.ndarray  # [N] uint8 HSV value per shopper
    states: list[ShopperState]
    queue_counts: dict[str, int]
    shelf_units: dict[str, int]
    shelf_facings: dict[str, int]
    cashier_busy: bool
    cashier_open: bool
    scenario: str
    crossings: list[CrossingEvent] = field(default_factory=list)
    joins: int = 0
    served: int = 0
    abandoned: int = 0
    in_store: int = 0
    hour: float = 0.0

    @property
    def n(self) -> int:
        return int(self.positions.shape[0])


# ---------------------------------------------------------------------------
# the model
# ---------------------------------------------------------------------------


class StoreModel:
    """Agent-based store. ``step(dt)`` advances simulated time and returns a ``SimState``."""

    def __init__(self, cfg: StoreConfig, seed: int = 42, start_ts: float | None = None):
        self.cfg = cfg
        self.layout = Layout.from_config(cfg)
        self.rng = np.random.default_rng(seed)
        self.seed = seed
        self.tz = cfg.store.tz
        self._ts = float(start_ts) if start_ts is not None else self._default_start_ts()
        self._day_start = day_start_ts(self._ts, self.tz)
        self._next_id = 1
        self.shoppers: list[Shopper] = []
        self.queue: list[int] = []  # shopper ids, head first
        counter = (
            cfg.counters[0]
            if cfg.counters
            else Counter(counter_id="counter-1", name="Counter", queue_zone_id="queue-1", counter_line_id="counter-1-line")
        )
        self.cashier = Cashier(counter, mean_service_s=counter.default_service_s)
        self.shelves: dict[str, ShelfUnit] = {
            s.shelf_id: ShelfUnit(shelf=s, sku=cfg.sku(s.sku_id) if s.sku_id else None, units=0.0) for s in cfg.shelves
        }
        for su in self.shelves.values():
            su.units = float(su.capacity_units)
        self.lines: list[Line] = list(cfg.lines)
        # scenario bookkeeping: the synthetic camera's "synthetic:<mode>" wins, then demo.default_scenario
        cam = cfg.synthetic_camera()
        initial = cam.scenario if cam and cam.scenario in SCENARIOS else None
        if initial is None and cfg.demo.default_scenario in SCENARIOS:
            initial = cfg.demo.default_scenario
        self.mode: str = "baseline"
        self.active: str = "baseline"
        self.active_params: dict[str, Any] = {}
        self.active_since_ts: float = self._ts
        self.clock_factor: float = cfg.demo.clock_factor
        self.pending_chaos: list[ChaosRequest] = []
        # truth counters
        self.footfall_in_total = 0
        self.footfall_out_total = 0
        self.served_total = 0
        self.abandoned_total = 0
        self.joins_total = 0
        self._joins_prev = 0
        self._abandoned_prev = 0
        self.units_sold: dict[str, int] = {s: 0 for s in self.shelves}
        self.revenue_inr = 0.0
        self.step_count = 0
        if initial and initial != "baseline":
            self.apply_scenario(initial, {})

    # -- time ------------------------------------------------------------------
    def _default_start_ts(self) -> float:
        """Today at ``demo.start_time`` in the store's timezone (the demo always opens at 17:00)."""
        today = store_date(time.time(), self.tz)
        return date_to_ts(today, self.tz, self.cfg.demo.start_time)

    @property
    def ts(self) -> float:
        return self._ts

    @property
    def hour(self) -> float:
        """Local hour-of-day as a float, cheap (no datetime per step)."""
        if self._ts - self._day_start >= 86400.0 or self._ts < self._day_start:
            self._day_start = day_start_ts(self._ts, self.tz)
        return ((self._ts - self._day_start) / 3600.0) % 24.0

    # -- scenarios ----------------------------------------------------------------
    def scenario_status(self) -> ScenarioStatus:
        return ScenarioStatus(
            active=self.active,
            since_ts=self.active_since_ts,
            params=dict(self.active_params),
            available=list(SCENARIOS),
            clock_factor=self.clock_factor,
            sim_ts=self._ts,
        )

    def apply_scenario(self, name: str, params: dict[str, Any] | None = None) -> ScenarioStatus:
        """Switch mode or run an action (see ``scenarios.SCENARIOS``); unknown names raise ``KeyError``."""
        if name not in SCENARIOS:
            raise KeyError(f"unknown scenario {name!r}; available: {sorted(SCENARIOS)}")
        p = scenario_defaults(name, params)
        kind = SCENARIOS[name]["kind"]
        if kind == "mode":
            self.mode = name
        elif name == "stockout":
            su = self._shelf(p["shelf_id"])
            over = max(0.5, float(p.get("over_s", 30.0)))
            su.auto_restock = False
            su.restock_due_ts = None
            su.drain_rate = max(su.units, 1.0) / over
        elif name == "restock":
            self.restock(p["shelf_id"], p.get("units"))
        elif name == "open_counter":
            self.cashier.service_mult = 2.0
        elif name == "close_counter":
            self.cashier.service_mult = 0.0
        elif name == "camera_blackout":
            self.pending_chaos.append(ChaosRequest(kind="blackout", enabled=True, seconds=float(p["seconds"])))
        elif name == "freeze":
            self.pending_chaos.append(ChaosRequest(kind="freeze", enabled=True, seconds=float(p["seconds"])))
        elif name == "seed_history":
            pass  # handled by the edge/cloud; the live store has nothing to do
        self.active = name
        self.active_params = p
        self.active_since_ts = self._ts
        return self.scenario_status()

    def restock(self, shelf_id: str, units: int | None = None) -> None:
        """Refill to capacity (or ``units``), re-enable auto restock, stop any drain."""
        su = self._shelf(shelf_id)
        su.units = float(su.capacity_units if units is None else min(units, su.capacity_units))
        su.auto_restock = True
        su.drain_rate = 0.0
        su.restock_due_ts = None

    def _shelf(self, shelf_id: str) -> ShelfUnit:
        if shelf_id not in self.shelves:
            raise KeyError(f"unknown shelf {shelf_id!r}; known: {sorted(self.shelves)}")
        return self.shelves[shelf_id]

    # -- truth -------------------------------------------------------------------
    def in_store(self) -> int:
        return sum(1 for s in self.shoppers if s.inside)

    def truth(self) -> SimTruth:
        return SimTruth(
            in_store=self.in_store(),
            queue_counts={self.cashier.counter.counter_id: len(self.queue)},
            shelf_units={k: int(round(v.units)) for k, v in self.shelves.items()},
            shelf_facings={k: v.facings for k, v in self.shelves.items()},
            served_total=self.served_total,
            abandoned_total=self.abandoned_total,
            footfall_in_total=self.footfall_in_total,
            scenario=self.active,
        )

    # -- arrivals and planning ------------------------------------------------------
    def arrival_rate_now(self) -> float:
        """Arrivals per sim-minute right now (hour curve x scenario multiplier)."""
        return arrival_rate_pm(self.hour) * arrival_mult(self.mode)

    def buy_probability(self, sku: SKU | None) -> float:
        """Per-visit purchase probability so that E[units/hr] == velocity x arrival multiplier.

        visits/hr at a shelf = arrivals/hr x E[shelves visited] / n_shelves (= 2/3 for the demo).
        """
        if sku is None:
            return 0.0
        n_shelves = max(1, len(self.shelves))
        visits_per_hr = 60.0 * self.arrival_rate_now() * (2.0 / n_shelves)
        if visits_per_hr <= 1e-9:
            return 0.0
        units_per_hr = sku.velocity_units_per_hr * arrival_mult(self.mode)
        return float(min(0.95, units_per_hr / visits_per_hr / UNITS_PER_PURCHASE))

    def _spawn(self) -> Shopper:
        sx, sy = self.layout.spawn
        jitter = float(self.rng.uniform(-12.0, 12.0))
        s = Shopper(
            id=self._next_id,
            x=sx + jitter,
            y=sy,
            state=ShopperState.ENTERING,
            speed=float(self.rng.uniform(0.8, 1.2)) * SHOPPER_SPEED_PX_S,
            patience_count=int(self.rng.integers(4, 8)),
            patience_s=float(self.rng.uniform(120.0, 300.0)),
            v_jitter=int(self.rng.integers(180, 256)),
            spawned_ts=self._ts,
        )
        self._next_id += 1
        n_visit = int(self.rng.integers(1, 4))
        shelf_ids = list(self.shelves)
        if shelf_ids:
            picks = self.rng.choice(len(shelf_ids), size=min(n_visit, len(shelf_ids)), replace=False)
            s.plan = [shelf_ids[int(i)] for i in picks]
        _ix, iy = self.layout.inside_door
        s.set_path((sx + jitter, iy))  # straight up through the door, crossing the entrance line
        s.remember()
        self.shoppers.append(s)
        return s

    def _stand_point(self, shelf_id: str) -> Point:
        (ax, ay), (bx, by) = self.layout.shelf_stands[shelf_id]
        t = float(self.rng.uniform(0.0, 1.0))
        return (ax + (bx - ax) * t, ay + (by - ay) * t)

    def _route_to_shelf(self, s: Shopper, shelf_id: str) -> None:
        s.set_path(self._stand_point(shelf_id))
        s.state = ShopperState.BROWSING
        s.dwell_until = 0.0

    def _route_to_exit(self, s: Shopper, *, via_staging: bool) -> None:
        lay = self.layout
        pts: list[Point] = [lay.staging] if via_staging else []
        pts += [lay.inside_door, lay.spawn]
        s.set_path(*pts)
        s.state = ShopperState.EXITING

    def _route_to_queue(self, s: Shopper) -> None:
        lay = self.layout
        _tx, ty = lay.queue_tail_outside
        s.set_path(lay.staging, (lay.queue_approach_x, ty))
        s.state = ShopperState.TO_QUEUE

    def _join_queue(self, s: Shopper) -> None:
        idx = len(self.queue)
        self.queue.append(s.id)
        s.state = ShopperState.QUEUEING
        s.queue_join_ts = self._ts
        self.joins_total += 1
        self._retarget_slot(s, idx)

    def _retarget_slot(self, s: Shopper, idx: int) -> None:
        sx, sy = self.layout.slot(idx)
        if abs(s.x - sx) > 2.0:
            # walk up the approach lane, then step sideways into the slot column
            s.set_path((self.layout.queue_approach_x, sy), (sx, sy))
        else:
            s.set_path((sx, sy))

    def _abandon(self, s: Shopper) -> None:
        """Renege: leave the queue zone downward; the path never touches the counter line."""
        lay = self.layout
        if s.id in self.queue:
            self.queue.remove(s.id)
        self.abandoned_total += 1
        s.state = ShopperState.ABANDONING
        _tx, ty = lay.queue_tail_outside
        s.set_path((lay.queue_approach_x, s.y), (lay.queue_approach_x, ty), lay.staging, lay.inside_door, lay.spawn)
        s.queue_join_ts = None
        self._advance_queue()

    def _balk(self, s: Shopper) -> None:
        """Queue too long on arrival: step into the zone tail, turn around, leave downward."""
        lay = self.layout
        self.abandoned_total += 1
        s.state = ShopperState.ABANDONING
        _tx, ty = lay.queue_tail_outside
        s.set_path(
            (lay.queue_approach_x, ty - 2 * LANE_CLEARANCE_PX),
            (lay.queue_approach_x, ty),
            lay.staging,
            lay.inside_door,
            lay.spawn,
        )

    # -- per-step logic ---------------------------------------------------------
    def step(self, dt: float = SIM_DT_S) -> SimState:
        dt = float(dt)
        self._ts += dt
        self.step_count += 1
        crossings: list[CrossingEvent] = []
        served = 0

        # 1. arrivals: Poisson(rate * dt)
        lam = self.arrival_rate_now() / 60.0 * dt
        for _ in range(int(self.rng.poisson(lam))):
            self._spawn()

        # 2. shelves: drains, auto restock
        for su in self.shelves.values():
            if su.drain_rate > 0.0:
                su.units = max(0.0, su.units - su.drain_rate * dt)
                if su.units <= 0.0:
                    su.drain_rate = 0.0
            if su.auto_restock:
                if su.facings < su.shelf.min_facings and su.restock_due_ts is None:
                    su.restock_due_ts = self._ts + AUTO_RESTOCK_DELAY_S
                if su.restock_due_ts is not None and self._ts >= su.restock_due_ts:
                    su.units = float(su.capacity_units)
                    su.restock_due_ts = None

        # 3. cashier: start / finish service at the head
        self._cashier_tick()

        # 4. decisions + movement per shopper
        despawn: set[int] = set()
        for s in self.shoppers:
            s.remember()
            self._decide(s)
            if s.state == ShopperState.BROWSING and self._ts < s.dwell_until:
                continue  # dwelling at a shelf front
            s.move(dt)
            if s.state in (ShopperState.EXITING, ShopperState.ABANDONING) and s.at_target():
                despawn.add(s.id)

        # 5. soft repulsion between free-walking shoppers (browsing only: never near a line)
        self._repel()

        # 6. crossings (normative side-change rule) -> truth counters
        for s in self.shoppers:
            for ln in self.lines:
                d = crossing_direction(s.prev_pos, s.pos, ln)
                if d is None:
                    continue
                crossings.append(CrossingEvent(ln.line_id, ln.kind, d, s.id, self._ts))
                if ln.kind == LineKind.ENTRANCE:
                    if d == Direction.IN:
                        self.footfall_in_total += 1
                        s.inside = True
                    else:
                        self.footfall_out_total += 1
                        s.inside = False
                elif ln.kind == LineKind.COUNTER and d == Direction.IN:
                    self.served_total += 1
                    served += 1

        if despawn:
            self.shoppers = [s for s in self.shoppers if s.id not in despawn]

        joins, self._joins_prev = self.joins_total - self._joins_prev, self.joins_total
        abandoned, self._abandoned_prev = self.abandoned_total - self._abandoned_prev, self.abandoned_total

        n = len(self.shoppers)
        positions = np.empty((n, 2), dtype=np.float32)
        ids = np.empty(n, dtype=np.int64)
        vj = np.empty(n, dtype=np.uint8)
        states: list[ShopperState] = []
        for i, s in enumerate(self.shoppers):
            positions[i, 0], positions[i, 1] = s.x, s.y
            ids[i] = s.id
            vj[i] = s.v_jitter
            states.append(s.state)
        return SimState(
            ts=self._ts,
            positions=positions,
            ids=ids,
            v_jitter=vj,
            states=states,
            queue_counts={self.cashier.counter.counter_id: len(self.queue)},
            shelf_units={k: int(round(v.units)) for k, v in self.shelves.items()},
            shelf_facings={k: v.facings for k, v in self.shelves.items()},
            cashier_busy=self.cashier.busy_with is not None,
            cashier_open=self.cashier.service_mult > 0.0,
            scenario=self.active,
            crossings=crossings,
            joins=joins,
            served=served,
            abandoned=abandoned,
            in_store=self.in_store(),
            hour=self.hour,
        )

    # -- helpers -----------------------------------------------------------------
    def _by_id(self, sid: int) -> Shopper | None:
        return next((s for s in self.shoppers if s.id == sid), None)

    def _cashier_tick(self) -> None:
        c = self.cashier
        if c.busy_with is not None:
            s = self._by_id(c.busy_with)
            if s is None or s.id not in self.queue:
                c.busy_with, c.service_end_ts = None, None
            elif c.service_end_ts is not None and self._ts >= c.service_end_ts:
                self._serve_done(s)
                c.busy_with, c.service_end_ts = None, None
        if c.busy_with is None and self.queue and c.service_mult > 0.0:
            head = self._by_id(self.queue[0])
            hx, hy = self.layout.slot(0)
            if head is not None and head.state == ShopperState.QUEUEING and abs(head.x - hx) < 3 and abs(head.y - hy) < 3:
                head.state = ShopperState.SERVICE
                svc = max(8.0, float(self.rng.normal(c.mean_service_s, c.sd_service_s))) / c.service_mult
                c.busy_with = head.id
                c.service_end_ts = self._ts + svc
                head.service_end_ts = c.service_end_ts

    def _serve_done(self, s: Shopper) -> None:
        """Ring up the basket, then walk left through the counter line and out."""
        self.queue.remove(s.id)
        for shelf_id, units in s.basket.items():
            su = self.shelves.get(shelf_id)
            if su is not None and su.sku is not None:
                self.units_sold[shelf_id] += units
                self.revenue_inr += units * su.sku.mrp_inr
        lay = self.layout
        s.set_path(lay.served_exit, lay.staging, lay.inside_door, lay.spawn)
        s.state = ShopperState.EXITING
        s.queue_join_ts = None
        self._advance_queue()

    def _advance_queue(self) -> None:
        for idx, sid in enumerate(self.queue):
            q = self._by_id(sid)
            if q is not None and q.state == ShopperState.QUEUEING:
                self._retarget_slot(q, idx)

    def _decide(self, s: Shopper) -> None:
        st = s.state
        if st == ShopperState.ENTERING:
            if s.at_target():
                self._next_shelf_or_leave(s)
        elif st == ShopperState.BROWSING:
            if s.at_target():
                if s.dwell_until == 0.0:
                    s.dwell_until = self._ts + float(self.rng.uniform(4.0, 20.0))
                    self._maybe_buy(s, s.plan.pop(0) if s.plan else None)
                elif self._ts >= s.dwell_until:
                    self._next_shelf_or_leave(s)
        elif st == ShopperState.TO_QUEUE:
            if s.at_target():
                if len(self.queue) >= s.patience_count:
                    self._balk(s)
                else:
                    self._join_queue(s)
        elif st == ShopperState.QUEUEING:
            if s.queue_join_ts is not None and self._ts - s.queue_join_ts > s.patience_s:
                self._abandon(s)

    def _next_shelf_or_leave(self, s: Shopper) -> None:
        if s.plan:
            self._route_to_shelf(s, s.plan[0])
        elif s.units() > 0:
            self._route_to_queue(s)
        else:
            self._route_to_exit(s, via_staging=s.state != ShopperState.ENTERING)

    def _maybe_buy(self, s: Shopper, shelf_id: str | None) -> None:
        if shelf_id is None:
            return
        su = self.shelves[shelf_id]
        p = self.buy_probability(su.sku)
        if self.mode == "diwali":
            p = min(0.95, p * 1.3)
        if self.rng.random() >= p:
            return
        want = 2 if self.rng.random() < 0.15 else 1
        take = int(min(want, math.floor(su.units)))
        if take <= 0:
            su.lost_sale_units += want
            return
        su.units -= take
        s.basket[shelf_id] = s.basket.get(shelf_id, 0) + take

    def _repel(self) -> None:
        """Push browsing shoppers apart to >= MIN_SEPARATION_PX (O(N^2) on a handful of agents)."""
        free = [s for s in self.shoppers if s.state == ShopperState.BROWSING]
        n = len(free)
        if n < 2:
            return
        pts = np.array([(s.x, s.y) for s in free], dtype=np.float64)
        diff = pts[:, None, :] - pts[None, :, :]
        dist = np.hypot(diff[..., 0], diff[..., 1])
        np.fill_diagonal(dist, np.inf)
        close = dist < MIN_SEPARATION_PX
        if not close.any():
            return
        unit = diff / np.maximum(dist, 1e-6)[..., None]
        # two shoppers sitting on the same pixel have no direction: nudge along x
        same = (dist < 1e-6)[..., None]
        unit = np.where(same, np.array([1.0, 0.0]), unit)
        push = np.where(close[..., None], unit * (MIN_SEPARATION_PX - dist)[..., None] * 0.5, 0.0)
        shift = push.sum(axis=1)
        x0, y0, x1, y1 = self.layout.interior
        for s, (dx, dy) in zip(free, shift, strict=True):
            if dx == 0.0 and dy == 0.0:
                continue
            s.x = float(min(max(s.x + dx, x0), x1))
            s.y = float(min(max(s.y + dy, y0), y1))


def _side(pt: Point, start: list[float], end: list[float]) -> int:
    """Inline copy of ``geometry.side_of_line`` (+1 = left of start->end, image coords)."""
    cross = (end[0] - start[0]) * (pt[1] - start[1]) - (end[1] - start[1]) * (pt[0] - start[0])
    return 1 if cross > 0 else (-1 if cross < 0 else 0)


def crossing_direction(prev: Point, cur: Point, line: Line) -> Direction | None:
    """Normative rule: side -1 -> +1 is IN, +1 -> -1 is OUT; the move must actually span the segment."""
    a, b = _side(prev, line.start, line.end), _side(cur, line.start, line.end)
    if a == 0 or b == 0 or a == b:
        return None
    # where does the move hit the infinite line? reject if outside the segment's extent
    (sx0, sy0), (sx1, sy1) = line.start, line.end
    if abs(sx1 - sx0) >= abs(sy1 - sy0):  # mostly horizontal line
        lo, hi = min(sx0, sx1), max(sx0, sx1)
        t = (sy0 - prev[1]) / (cur[1] - prev[1]) if cur[1] != prev[1] else 0.0
        hit = prev[0] + (cur[0] - prev[0]) * t
    else:
        lo, hi = min(sy0, sy1), max(sy0, sy1)
        t = (sx0 - prev[0]) / (cur[0] - prev[0]) if cur[0] != prev[0] else 0.0
        hit = prev[1] + (cur[1] - prev[1]) * t
    if not (lo <= hit <= hi):
        return None
    return Direction.IN if (a, b) == (-1, 1) else Direction.OUT


__all__ = ["Cashier", "CrossingEvent", "Layout", "ShelfUnit", "SimState", "StoreModel", "crossing_direction"]
