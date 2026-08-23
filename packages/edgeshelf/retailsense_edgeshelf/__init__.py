"""RetailSense edge shelf monitoring (agent A04).

Registry keys served by this package: ``coverage_estimator``, ``shelf_state_machine``,
``sku_identifier``, ``shelf_thumb``.
"""

from .coverage import ClassicalCoverageEstimator
from .scanner import ShelfScanner, occluded_by
from .sku import TaggedSkuIdentifier
from .sku_clip import ClipSkuIdentifier
from .state import ShelfStateMachine, raw_state
from .thumbs import shelf_thumbnail

__all__ = [
    "ClassicalCoverageEstimator",
    "ClipSkuIdentifier",
    "ShelfScanner",
    "ShelfStateMachine",
    "TaggedSkuIdentifier",
    "occluded_by",
    "raw_state",
    "shelf_thumbnail",
]
