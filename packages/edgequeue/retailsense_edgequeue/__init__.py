"""RetailSense edge queue analytics (``retailsense_edgequeue``).

Three small, dependency-free modules:

* :mod:`.little`   -- pure queueing math (Little's Law, rolling rates, trend extrapolation).
* :mod:`.queue`    -- :class:`QueueAnalyzer`: turns per-frame ``AnalyticsUpdate``s into ``QueueSnapshot``s.
* :mod:`.forecast` -- :class:`TrendForecaster`: 5/10/15/30-minute queue length forecast with self-scored MAE
  and a cloud override with a two-minute lease.

Both classes satisfy the ``retailsense_contracts.interfaces`` Protocols and are what the registry keys
``queue_analyzer`` and ``queue_forecaster.edge`` resolve to.
"""

from .forecast import TrendForecaster
from .little import little_wait_s, rolling_rate_pm, trend_forecast
from .queue import QueueAnalyzer

__all__ = ["QueueAnalyzer", "TrendForecaster", "little_wait_s", "rolling_rate_pm", "trend_forecast"]
__version__ = "1.0.0"
