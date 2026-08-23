"""Privacy by design, stated as data.

``RetentionPolicy`` is the single schedule that the edge ``RetentionJob`` and the
cloud purge job enforce (DPDP Act 2023 storage-limitation principle).
``PrivacyManifest`` is what the edge exposes on ``/health``/``/config`` and the
deck quotes: no faces, no raw video persisted, track IDs never leave the edge.
"""

from pydantic import BaseModel, ConfigDict, Field


class RetentionPolicy(BaseModel):
    """How long each data class lives before the purge job deletes it."""

    model_config = ConfigDict(extra="forbid")

    telemetry_hours: int = 24
    aggregate_days: int = 30
    thumbnails_days: int = 7
    heatmap_days: int = 90
    alerts_days: int = 365
    sent_outbox_hours: int = 24


class PrivacyManifest(BaseModel):
    """Machine-readable statement of what RetailSense does and does not collect."""

    model_config = ConfigDict(extra="forbid")

    face_recognition: bool = False
    raw_video_persisted: bool = False
    track_ids_leave_edge: bool = False
    biometric_templates: bool = False
    shelf_thumbnails: bool = True
    thumbnail_max_px: int = 96
    thumbnail_scope: str = "shelf polygon only; never includes people"
    preview_blur_people: bool = True
    data_leaving_edge: list[str] = Field(
        default_factory=lambda: [
            "aggregate counts (footfall, occupancy, queue length)",
            "dwell seconds per zone without identifiers",
            "shelf coverage/facings + optional 96x96 shelf thumbnail",
            "alerts with rupee impact",
            "device heartbeat (fps, backlog, uptime)",
        ]
    )
    retention: RetentionPolicy = RetentionPolicy()
    lawful_basis: str = (
        "Legitimate business purpose of the store owner (DPDP Act 2023, s.7); no personal data of shoppers is retained."
    )
    statement: str = "No face recognition; no raw video persisted; track IDs never leave the edge."


def default_privacy_manifest(
    *, preview_blur_people: bool = True, shelf_thumbnails: bool = True, retention: RetentionPolicy | None = None
) -> PrivacyManifest:
    return PrivacyManifest(
        preview_blur_people=preview_blur_people,
        shelf_thumbnails=shelf_thumbnails,
        retention=retention or RetentionPolicy(),
    )


__all__ = ["PrivacyManifest", "RetentionPolicy", "default_privacy_manifest"]
