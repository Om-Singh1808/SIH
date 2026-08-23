"""retailsense_sim - the synthetic store that is both the demo and the test oracle.

Sub-modules:

    scenarios    SCENARIOS table, arrival curve, per-scenario multipliers
    floorplan    render_floorplan(): static background from StoreConfig geometry
    shopper      Shopper agent (state machine + waypoint walker)
    store_model  StoreModel: arrivals, browsing, queue, cashier, shelves, truth
    chaos        ChaosState: freeze / drop / blackout / noise applied to frames
    video        VideoGenerator + SyntheticFrameSource (FrameSource + SyntheticControl)
    history      generate_history(): 30 days of minute/daily rows for the forecaster
    headless     FakeEdge: a store without pixels that POSTs IngestBatch to SenseCloud
    cli          python -m retailsense_sim video|headless|history
"""

from .chaos import ChaosState
from .floorplan import render_floorplan
from .headless import FakeEdge
from .history import generate_history
from .scenarios import SCENARIOS, arrival_rate_pm
from .shopper import Shopper, ShopperState
from .store_model import SimState, StoreModel
from .video import SyntheticFrameSource, VideoGenerator

__all__ = [
    "SCENARIOS",
    "ChaosState",
    "FakeEdge",
    "Shopper",
    "ShopperState",
    "SimState",
    "StoreModel",
    "SyntheticFrameSource",
    "VideoGenerator",
    "arrival_rate_pm",
    "generate_history",
    "render_floorplan",
]
