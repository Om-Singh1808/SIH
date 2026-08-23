"""Alert model, its per-kind detail payloads and the rupee impact attached to it.

Design notes
* ``Alert`` carries pre-rendered Hindi + English title/message so the phone panel
  and WhatsApp work with the cloud unreachable (rendering happens on the edge via
  ``i18n.render``).
* ``actions`` is the digit menu: WhatsApp reply ``i`` maps to ``actions[i-1]``.
* One OPEN alert per ``(kind, subject_id)`` - enforced by the partial unique index
  in ``db.py`` and by the rule engine.
* ``ImpactInr.basis`` is always filled: the judge must be able to read *why* the
  number is what it is ("Rs27 x 18/hr x 0.33 h x 0.31").
"""

from typing import Union

from pydantic import BaseModel, ConfigDict

from .enums import AckAction, AckBy, AlertKind, AlertStatus, Origin, Severity


class ImpactInr(BaseModel):
    """Rupee impact with its derivation. Produced only by ``impact.py``."""

    model_config = ConfigDict(extra="forbid")

    lost_sales_inr: float
    lost_margin_inr: float
    basis: str  # e.g. "₹27 × 18/hr × 0.33 h × 0.31" - always filled
    factor: float  # multiplier actually used (lost_sale_factor or queue_abandon_factor)
    source: str  # citation string from ImpactConfig


class StockoutAlert(BaseModel):
    """Details for kind=shelf_gap."""

    model_config = ConfigDict(extra="forbid")

    shelf_id: str
    sku_id: str | None
    sku_name: str
    gap_minutes: float
    coverage: float
    facings: int
    min_facings: int
    consecutive_empty_scans: int


class QueueAlertDetails(BaseModel):
    """Details for kind=queue_long / queue_forecast."""

    model_config = ConfigDict(extra="forbid")

    counter_id: str
    counter_name: str
    count: int
    est_wait_s: float
    forecast: float | None = None
    horizon_min: int | None = None
    threshold: int


class CameraAlertDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    camera_id: str
    status: str
    last_frame_age_s: float


class SyncAlertDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    backlog: int
    down_since_ts: float


class DeviceAlertDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: str
    last_seen_ts: float


class ShrinkAlertDetails(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sku_id: str
    sku_name: str
    visual_units: int
    system_units: int
    delta_units: int
    delta_inr: float


class FootfallAlertDetails(BaseModel):
    """Details for kind=footfall_spike (P1)."""

    model_config = ConfigDict(extra="forbid")

    count: int
    baseline: float
    factor: float
    window_min: int = 15


AlertDetails = Union[
    StockoutAlert,
    QueueAlertDetails,
    CameraAlertDetails,
    SyncAlertDetails,
    DeviceAlertDetails,
    ShrinkAlertDetails,
    FootfallAlertDetails,
]


class Alert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_id: str
    store_id: str
    device_id: str
    origin: Origin
    kind: AlertKind
    severity: Severity
    status: AlertStatus = AlertStatus.OPEN
    subject_id: str  # shelf_id | counter_id | camera_id | device_id | sku_id
    title_en: str
    title_hi: str
    message_en: str
    message_hi: str
    details: AlertDetails
    impact: ImpactInr | None = None
    actions: list[AckAction]  # digit i on WhatsApp == actions[i-1]
    raised_ts: float
    acked_ts: float | None = None
    resolved_ts: float | None = None
    ack_action: AckAction | None = None
    ack_by: AckBy | None = None

    # -- convenience ------------------------------------------------------
    def title(self, lang: str) -> str:
        return self.title_hi if str(lang) == "hi" else self.title_en

    def message(self, lang: str) -> str:
        return self.message_hi if str(lang) == "hi" else self.message_en

    def action_for_digit(self, digit: int) -> AckAction | None:
        """WhatsApp digit -> action (1-based); None if out of range."""
        if 1 <= digit <= len(self.actions):
            return self.actions[digit - 1]
        return None


ACTIONS_BY_KIND: dict[AlertKind, list[AckAction]] = {
    AlertKind.SHELF_GAP: [AckAction.RESTOCKED, AckAction.ORDER, AckAction.FALSE_POSITIVE],
    AlertKind.QUEUE_LONG: [AckAction.OPENED_COUNTER, AckAction.IGNORE],
    AlertKind.QUEUE_FORECAST: [AckAction.OPENED_COUNTER, AckAction.IGNORE],
    AlertKind.CAMERA_DOWN: [AckAction.CHECKED],
    AlertKind.SYNC_BACKLOG: [],
    AlertKind.DEVICE_OFFLINE: [],
    AlertKind.SHRINK_SUSPECT: [AckAction.INVESTIGATE, AckAction.FALSE_POSITIVE],
    AlertKind.FOOTFALL_SPIKE: [],
}


class AlertAckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: AckAction
    by: AckBy = AckBy.BOARD
    note: str | None = None


__all__ = [
    "ACTIONS_BY_KIND",
    "Alert",
    "AlertAckRequest",
    "AlertDetails",
    "CameraAlertDetails",
    "DeviceAlertDetails",
    "FootfallAlertDetails",
    "ImpactInr",
    "QueueAlertDetails",
    "ShrinkAlertDetails",
    "StockoutAlert",
    "SyncAlertDetails",
]
