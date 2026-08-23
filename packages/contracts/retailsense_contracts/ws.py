"""WebSocket envelope shared by SenseEdge ``/ws/live`` and SenseCloud ``/v1/ws``.

``data`` is the JSON dump of the model named by ``kind``:

edge ``/ws/live`` emits
  hello{device_id, store_id, contracts_version}, event{Event} (non-telemetry),
  alert{Alert}, kpi{KpiToday} (5 s), health{HealthStatus} (10 s),
  sync{SyncStatus} (on change + 2 s while replaying), scenario{ScenarioStatus},
  forecast{QueueForecast}
cloud ``/v1/ws`` emits
  hello, alert, kpi, device{DeviceStatus}, notification{OutboundMessage},
  forecast, sync{device_id, last_seq, seq_ok, accepted}
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

WsKind = Literal["hello", "event", "alert", "kpi", "health", "sync", "scenario", "notification", "device", "forecast"]

WS_KINDS: tuple[str, ...] = WsKind.__args__  # type: ignore[attr-defined]


class WsMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: WsKind
    ts: float
    store_id: str | None = None
    data: dict[str, Any]

    @classmethod
    def of(cls, kind: WsKind, ts: float, data: BaseModel | dict[str, Any], store_id: str | None = None) -> "WsMessage":
        payload = data.model_dump(mode="json") if isinstance(data, BaseModel) else data
        return cls(kind=kind, ts=ts, store_id=store_id, data=payload)


__all__ = ["WS_KINDS", "WsKind", "WsMessage"]
