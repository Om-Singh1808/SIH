"""WebSocket fan-out (``/v1/ws?store_id=``).

A ``WsManager`` keeps one set of sockets per store plus a wildcard set for
chain-level dashboards.  ``broadcast()`` never raises: a dead socket is dropped
silently so one closed browser tab can never stall an ingest request.
Messages are the contracts' ``WsMessage`` envelope (kinds: hello, alert, kpi,
device, notification, forecast, sync).
"""

import asyncio
from collections import defaultdict
from typing import Any

from fastapi import WebSocket
from pydantic import BaseModel

from retailsense_contracts.ws import WsKind, WsMessage


class WsManager:
    def __init__(self) -> None:
        self._by_store: dict[str | None, set[WebSocket]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def connect(self, ws: WebSocket, store_id: str | None) -> None:
        await ws.accept()
        async with self._lock:
            self._by_store[store_id].add(ws)

    async def disconnect(self, ws: WebSocket, store_id: str | None) -> None:
        async with self._lock:
            self._by_store[store_id].discard(ws)

    def connections(self, store_id: str | None = None) -> int:
        if store_id is None:
            return sum(len(s) for s in self._by_store.values())
        return len(self._by_store.get(store_id, ())) + len(self._by_store.get(None, ()))

    async def broadcast(self, msg: WsMessage) -> int:
        """Send to every socket subscribed to ``msg.store_id`` (and the wildcard subscribers). Returns sends."""
        targets = set(self._by_store.get(msg.store_id, set())) | set(self._by_store.get(None, set()))
        if msg.store_id is None:
            targets = {ws for group in self._by_store.values() for ws in group}
        text = msg.model_dump_json()
        sent = 0
        for ws in list(targets):
            try:
                await ws.send_text(text)
                sent += 1
            except Exception:
                for group in self._by_store.values():
                    group.discard(ws)
        return sent

    async def emit(self, kind: WsKind, ts: float, data: BaseModel | dict[str, Any], store_id: str | None) -> int:
        return await self.broadcast(WsMessage.of(kind, ts, data, store_id))


__all__ = ["WsManager"]
