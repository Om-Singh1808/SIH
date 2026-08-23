"""RetailSense edge analytics (``retailsense_edgeanalytics``).

Turns tracker output into privacy-safe aggregates: zone membership, line
crossings, dwell samples, occupancy, a floor heatmap and footfall counters.
Registry key ``zone_engine`` -> :class:`ZoneEngine`.
"""

from .dwell import DWELL_KINDS, ZoneTracker
from .footfall import FootfallCounter
from .heatmap import HeatmapAccumulator
from .lines import LineCrosser
from .zones import ZoneEngine

__version__ = "1.0.0"
__all__ = ["DWELL_KINDS", "FootfallCounter", "HeatmapAccumulator", "LineCrosser", "ZoneEngine", "ZoneTracker"]
