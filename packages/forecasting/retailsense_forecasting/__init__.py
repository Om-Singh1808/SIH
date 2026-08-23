"""RetailSense cloud forecasting (agent A12).

Public surface (all re-exported here):

* :mod:`festivals`          - Indian festival calendar loaded from the frozen contracts CSV.
* :mod:`features`           - leakage-free feature engineering for the minute and daily history frames.
* :mod:`queue_forecaster`   - ``QueueForecaster`` (one gradient-boosting model per horizon 5/10/15/30 min).
* :mod:`footfall_forecaster`- ``FootfallForecaster`` (daily footfall with a +/-1.28 x MAE band).
* :mod:`reorder`            - ``suggest_reorder`` (forecast-scaled demand over the supplier lead time).
* :mod:`eval`               - ``rolling_mae`` for the live "model accuracy" badge.

Everything depends on ``retailsense_contracts`` only; scikit-learn is the default backend and LightGBM
is used automatically when it is importable (never a hard dependency).
"""

from .eval import rolling_mae
from .features import (
    HORIZONS,
    QUEUE_FEATURE_COLUMNS,
    make_daily_features,
    make_queue_features,
    target_column,
)
from .festivals import Festival, festival_features, is_salary_week, load_festivals
from .footfall_forecaster import FootfallForecaster
from .queue_forecaster import QueueForecaster
from .reorder import suggest_reorder

__all__ = [
    "HORIZONS",
    "QUEUE_FEATURE_COLUMNS",
    "Festival",
    "FootfallForecaster",
    "QueueForecaster",
    "festival_features",
    "is_salary_week",
    "load_festivals",
    "make_daily_features",
    "make_queue_features",
    "rolling_mae",
    "suggest_reorder",
    "target_column",
]
