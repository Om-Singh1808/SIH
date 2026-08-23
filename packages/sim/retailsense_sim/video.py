"""Synthetic video: ``VideoGenerator`` (SimState -> BGR frame) and ``SyntheticFrameSource``.

Rendering is designed around one rule: **the only magenta pixels in a frame are
shoppers**.  Everything else uses ``SyntheticPalette`` greys/blues/yellows, so the
colour-blob ``SyntheticDetector`` (edgecv) and the contracts ``FakeDetector`` find
exactly the agents the model placed.  Shelf facings are drawn on top of the
``SHELF_BACKING`` rectangle in the SKU's facing colour, so the classical coverage
estimator sees a coverage that tracks ``ceil(units / units_per_facing)``.

Speed: the static floorplan is rendered once and copied per frame; the dynamic
layer is ~40 ``cv2.rectangle`` calls.  A 640x360 frame renders in well under a
millisecond on a laptop CPU (the >200 fps acceptance test measures model + render).

``SyntheticFrameSource`` is the ``FrameSource`` the edge opens for a
``synthetic:<scenario>`` camera.  It also implements ``SyntheticControl`` so the
demo endpoints (scenario buttons, restock, chaos, clock factor) reach the model.
``read()`` paces to ``clock_factor / SIM_DT_S`` real frames per second (cap 60); a
slower consumer simply slows simulated time, which ``effective_clock_factor``
reports honestly for the heartbeat.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import cv2
import numpy as np

from retailsense_contracts.api import ChaosRequest, ScenarioStatus
from retailsense_contracts.config import CameraConfig, StoreConfig
from retailsense_contracts.events import SimTruth
from retailsense_contracts.geometry import polygon_bbox, polygon_long_axis
from retailsense_contracts.interfaces import Frame
from retailsense_contracts.synthetic import SHOPPER_SIZE_PX, SIM_DT_S, SyntheticPalette

from .chaos import ChaosState
from .floorplan import cashier_rect, render_floorplan
from .scenarios import SCENARIOS
from .store_model import SimState, StoreModel

MAX_REAL_FPS = 60.0
HUD_H = 18


class VideoGenerator:
    """Turns a ``SimState`` into a BGR uint8 frame of ``cfg.floorplan`` size."""

    def __init__(self, cfg: StoreConfig, palette: type[SyntheticPalette] = SyntheticPalette, draw_overlays: bool = False):
        self.cfg = cfg
        self.palette = palette
        self.draw_overlays = draw_overlays
        self._bg = render_floorplan(cfg, with_zones=draw_overlays)
        self._facing_geom = {s.shelf_id: self._facing_layout(s) for s in cfg.shelves}
        self._facing_colour = {
            s.shelf_id: palette.FACING_COLOURS.get(s.sku_id or "", (90, 160, 90)) for s in cfg.shelves
        }
        self._cashiers = [cashier_rect(cfg, c) for c in cfg.counters]
        self.half = SHOPPER_SIZE_PX // 2

    @staticmethod
    def _facing_layout(shelf: Any) -> tuple[str, int, int, int, int, int]:
        """(axis, x0, y0, x1, y1, facing_px): where facing i goes along the long axis."""
        x0, y0, x1, y1 = polygon_bbox(shelf.polygon)
        axis = polygon_long_axis(shelf.polygon)
        span = (x1 - x0) if axis == "x" else (y1 - y0)
        fw = int(shelf.facing_width_px or max(4, (span - 4) // max(1, shelf.capacity_facings)))
        return (axis, int(x0), int(y0), int(x1), int(y1), fw)

    @property
    def size(self) -> tuple[int, int]:
        return (self.cfg.floorplan.width_px, self.cfg.floorplan.height_px)

    def render(self, state: SimState) -> np.ndarray:
        img = self._bg.copy()
        self._draw_facings(img, state.shelf_facings)
        for i, (x0, y0, x1, y1) in enumerate(self._cashiers):
            colour = self.palette.CASHIER if (i > 0 or state.cashier_open) else self.palette.WALL
            cv2.rectangle(img, (x0, y0), (x1, y1), colour, -1)
        self._draw_shoppers(img, state.positions, state.v_jitter)
        if self.draw_overlays:
            self._draw_hud(img, state)
        return img

    def _draw_facings(self, img: np.ndarray, facings: dict[str, int]) -> None:
        for shelf_id, (axis, x0, y0, x1, y1, fw) in self._facing_geom.items():
            n = int(facings.get(shelf_id, 0))
            colour = self._facing_colour[shelf_id]
            for i in range(n):
                if axis == "x":
                    fx0 = x0 + 2 + i * fw
                    cv2.rectangle(img, (fx0, y0 + 3), (fx0 + fw - 3, y1 - 3), colour, -1)
                else:
                    fy0 = y0 + 2 + i * fw
                    cv2.rectangle(img, (x0 + 3, fy0), (x1 - 3, fy0 + fw - 3), colour, -1)

    def _draw_shoppers(self, img: np.ndarray, positions: np.ndarray, v_jitter: np.ndarray) -> None:
        h = self.half
        for (x, y), v in zip(positions, v_jitter, strict=True):
            cx, cy = int(round(float(x))), int(round(float(y)))
            vv = int(v)
            cv2.rectangle(img, (cx - h, cy - h), (cx + h - 1, cy + h - 1), (vv, 0, vv), -1)

    def _draw_hud(self, img: np.ndarray, state: SimState) -> None:
        w = img.shape[1]
        cv2.rectangle(img, (0, 0), (w - 1, HUD_H - 1), self.palette.WALL, -1)
        q = sum(state.queue_counts.values())
        hh, mm = int(state.hour), int((state.hour % 1) * 60)
        text = f"{state.scenario}  {hh:02d}:{mm:02d}  in-store {state.in_store}  queue {q}"
        cv2.putText(img, text, (6, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.42, self.palette.TEXT, 1, cv2.LINE_AA)


class SyntheticFrameSource:
    """``FrameSource`` + ``SyntheticControl`` over a ``StoreModel`` and a ``VideoGenerator``."""

    def __init__(
        self,
        camera: CameraConfig,
        cfg: StoreConfig,
        clock_factor: float | None = None,
        start_ts: float | None = None,
        seed: int = 42,
        *,
        draw_overlays: bool = False,
        pace: bool = True,
    ):
        self.camera_id = camera.camera_id
        self.camera = camera
        self.cfg = cfg
        self.model = StoreModel(cfg, seed=seed, start_ts=start_ts)
        self.model.clock_factor = float(clock_factor if clock_factor is not None else cfg.demo.clock_factor)
        if camera.scenario and camera.scenario in SCENARIOS and SCENARIOS[camera.scenario]["kind"] == "mode":
            self.model.apply_scenario(camera.scenario, {})
        self.gen = VideoGenerator(cfg, draw_overlays=draw_overlays)
        self.chaos_state = ChaosState()
        self.pace = pace
        self._rng = np.random.default_rng(seed + 1)
        self._lock = threading.RLock()
        self._open = False
        self._seq = 0
        self._last_image: np.ndarray | None = None
        self._last_read_real: float | None = None
        self.effective_clock_factor = self.model.clock_factor
        self.dt = SIM_DT_S

    # -- FrameSource -----------------------------------------------------------
    def open(self) -> None:
        self._open = True
        self._last_read_real = None

    def close(self) -> None:
        self._open = False

    @property
    def size(self) -> tuple[int, int]:
        return (self.camera.width, self.camera.height)

    @property
    def nominal_fps(self) -> float:
        return float(min(MAX_REAL_FPS, self.model.clock_factor / self.dt))

    def read(self) -> Frame | None:
        if not self._open:
            self.open()
        now = time.monotonic()
        with self._lock:
            while True:
                image = self._next_image()
                if self.chaos_state.should_drop(self._rng):
                    continue  # dropped frame: the sim advanced, nothing delivered
                break
            ts = self.model.ts
            self._seq += 1
            seq = self._seq
        image = self.chaos_state.apply_to_frame(image, self._rng, now)
        if self.pace:
            self._pace(now)
        frame_img = image if (image.shape[0], image.shape[1]) == (self.camera.height, self.camera.width) else cv2.resize(
            image, (self.camera.width, self.camera.height), interpolation=cv2.INTER_NEAREST
        )
        return Frame(ts=ts, camera_id=self.camera_id, image=frame_img, seq=seq)

    def _next_image(self) -> np.ndarray:
        state = self.model.step(self.dt)
        for req in self.model.pending_chaos:
            self.chaos_state.apply(req)
        self.model.pending_chaos.clear()
        if self.chaos_state.frozen() and self._last_image is not None:
            return self._last_image
        self._last_image = self.gen.render(state)
        return self._last_image

    def _pace(self, now: float) -> None:
        period = max(self.dt / max(self.model.clock_factor, 1e-6), 1.0 / MAX_REAL_FPS)
        if self._last_read_real is not None:
            elapsed = now - self._last_read_real
            if elapsed < period:
                time.sleep(period - elapsed)
            real_gap = max(time.monotonic() - self._last_read_real, 1e-6)
            # EMA of the speed the consumer actually achieves (sim seconds per real second)
            inst = self.dt / real_gap
            self.effective_clock_factor = 0.8 * self.effective_clock_factor + 0.2 * inst
        self._last_read_real = time.monotonic()

    # -- SyntheticControl --------------------------------------------------------
    def apply_scenario(self, name: str, params: dict) -> ScenarioStatus:
        with self._lock:
            status = self.model.apply_scenario(name, params)
            for req in self.model.pending_chaos:
                self.chaos_state.apply(req)
            self.model.pending_chaos.clear()
            return status

    def scenario_status(self) -> ScenarioStatus:
        with self._lock:
            return self.model.scenario_status()

    def restock(self, shelf_id: str, units: int | None = None) -> None:
        with self._lock:
            self.model.restock(shelf_id, units)

    def set_clock_factor(self, factor: float) -> None:
        with self._lock:
            self.model.clock_factor = max(0.01, float(factor))
            self.effective_clock_factor = self.model.clock_factor

    def chaos(self, req: ChaosRequest) -> None:
        self.chaos_state.apply(req)

    def truth(self) -> SimTruth:
        with self._lock:
            return self.model.truth()


__all__ = ["MAX_REAL_FPS", "SyntheticFrameSource", "VideoGenerator"]
