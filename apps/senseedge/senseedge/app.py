"""FastAPI application factory for SenseEdge."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

from retailsense_contracts.config import StoreConfig, load_store_config
from retailsense_contracts.api import ScenarioRequest, ScenarioStatus, SyncStatus, HealthStatus

from .state import EdgeState
from .wiring import Wiring, make_clock


def create_app(
    cfg: StoreConfig | str | Path | None = None,
    *,
    overrides: dict[str, Any] | None = None,
    clock_factor: float | None = None,
    start_background: bool = True,
) -> FastAPI:
    if cfg is None or isinstance(cfg, (str, Path)):
        cfg = load_store_config(cfg)

    wiring = Wiring.from_config(cfg, overrides=overrides or {}, clock_factor=clock_factor)
    state = EdgeState(cfg, wiring)

    app = FastAPI(title="SenseEdge", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.edge_state = state

    @app.on_event("startup")
    def startup_event():
        if start_background:
            state.start_workers()

    @app.on_event("shutdown")
    def shutdown_event():
        if start_background:
            state.stop_workers()

    @app.get("/health")
    def health():
        return state.health()

    @app.get("/kpis/today")
    def kpis_today():
        return state.store.kpi_today(state.now())

    @app.get("/kpis/series")
    def kpis_series(metric: str = "queue_count", range: str = "today"):
        return state.series.get_series(metric)

    @app.get("/sync")
    def sync_status():
        return state.refresh_sync_status()

    @app.post("/demo/scenario")
    def apply_scenario(body: ScenarioRequest):
        res = state.apply_scenario(body.name, body.params)
        return res

    @app.post("/demo/link")
    def set_link(body: dict):
        link_state = body.get("state", "up").lower()
        if link_state == "down":
            state.wiring.link.set_down()
        else:
            state.wiring.link.set_up()
        return state.refresh_sync_status()

    @app.post("/demo/whatsapp/reply")
    def whatsapp_reply(body: dict):
        alert_id = body.get("alert_id")
        reply = body.get("reply", "1")
        return {"status": "ok", "alert_id": alert_id, "reply": reply}

    @app.post("/demo/restock/{shelf_id}")
    def restock_shelf(shelf_id: str):
        state.restock(shelf_id)
        return {"status": "ok", "shelf_id": shelf_id}

    @app.put("/config/zones")
    @app.put("/config")
    async def update_config(cfg: StoreConfig):
        return await state.apply_config(cfg)

    @app.post("/calibrate/shelves/reference-all")
    def calibrate_shelves():
        return {"status": "ok", "calibrated": True}

    @app.get("/preview/{camera_id}")
    def preview_stream(camera_id: str, blur: bool = True):
        return StreamingResponse(
            state.preview.mjpeg_generator(camera_id, blur=blur),
            media_type="multipart/x-mixed-replace; boundary=frame",
        )

    @app.websocket("/ws/live")
    async def ws_live(websocket: WebSocket):
        await state.ws.connect(websocket)
        try:
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            state.ws.disconnect(websocket)

    return app
