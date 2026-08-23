"""RetailSense edge rule engine (``retailsense_edgerules``).

Public API
* :class:`RuleEngine` - implements the contracts ``RuleEngine`` Protocol.
* :class:`ImpactCalculator` - store-bound wrapper over ``retailsense_contracts.impact``.
* :func:`load_rules_yaml` / :func:`rules_default_path` - ``rules_default.yaml`` loader.
* :class:`FeedbackHub` / :class:`FalsePositive` - false-positive feedback fan-out.
* :func:`render_alert` - bilingual title/message rendering.
"""

from .engine import CRITICAL_GAP_MIN, HIGH_RATE_INR_PER_HOUR, RuleEngine
from .feedback import FalsePositive, FeedbackHub
from .impact import ImpactCalculator, open_hours_per_day
from .render import AlertTexts, render_alert
from .rules import load_rules_yaml, rules_default_path

__version__ = "1.0.0"

__all__ = [
    "CRITICAL_GAP_MIN",
    "HIGH_RATE_INR_PER_HOUR",
    "AlertTexts",
    "FalsePositive",
    "FeedbackHub",
    "ImpactCalculator",
    "RuleEngine",
    "load_rules_yaml",
    "open_hours_per_day",
    "render_alert",
    "rules_default_path",
]
