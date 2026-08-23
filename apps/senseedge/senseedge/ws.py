"""WebSocket fan-out for ``/ws/live``.

Each connected board gets its own bounded send queue drained by a dedicated
task.  ``broadcast()`` never awaits a slow client: if a socket's queue is full
the message is dropped *for that socket only* (the board re-syncs from REST on
reconnect), so one stalled phone on store Wi-Fi cannot back-pressure the
consumer loop that writes events to the store.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from fastapi import WebSocket
from pydantic import BaseModel

from retailsense_contracts.ws import WsKind, WsMessage

log = logging.getLogger("senseedge.ws")


class _Client:
    def __init__(self, ws: WebSocket, maxsize: int):
        self.ws = ws
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=maxsize)
        self.dropped = 0
        self.task: asyncio.Task | None = None

    async def pump(self) -> None:
        try:
            while True:
                text = await self.queue.get()
                await self.ws.send_text(text)
        except Exception:  # client went away - the receive loop in the route cleans up
            return


class WsManager:
    """connect/disconnect/broadcast with per-socket queues and slow-client dropping."""

    def __init__(self, *, maxsize: int = 200):
        self.maxsize = maxsize
        self._clients: dict[int, _Client] = {}
        self.sent = 0
        self.dropped = 0

    @property
    def count(self) -> int:
        return len(self._clients)

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        c = _Client(ws, self.maxsize)
        c.task = asyncio.create_task(c.pump())
        self._clients[id(ws)] = c

    async def disconnect(self, ws: WebSocket) -> None:
        c = self._clients.pop(id(ws), None)
        if c is None:
            return
        if c.task is not None:
            c.task.cancel()
            with contextlib.suppress(BaseException):
                await c.task

    def broadcast(self, msg: WsMessage) -> int:
        """Enqueue ``msg`` for every client; returns the number of clients that received it."""
        text = msg.model_dump_json()
        delivered = 0
        for c in list(self._clients.values()):
            try:
                c.queue.put_nowait(text)
                delivered += 1
            except asyncio.QueueFull:
                c.dropped += 1
                self.dropped += 1
        self.sent += delivered
        return delivered

    def emit(self, kind: WsKind, ts: float, data: BaseModel | dict[str, Any], store_id: str | None = None) -> int:
        return self.broadcast(WsMessage.of(kind, ts, data, store_id))

    async def send_direct(self, ws: WebSocket, msg: WsMessage) -> None:
        await ws.send_text(msg.model_dump_json())

    async def close_all(self) -> None:
        for c in list(self._clients.values()):
            await self.disconnect(c.ws)
            with contextlib.suppress(Exception):
                await c.ws.close()
