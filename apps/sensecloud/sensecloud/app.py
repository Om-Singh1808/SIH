"""FastAPI application factory for SenseCloud."""

from __future__ import annotations

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from retailsense_contracts.api import IngestAck, IngestBatch
from retailsense_contracts.clock import SystemClock
from retailsense_contracts.config import StoreConfig

from .aggregator import Aggregator
from .alerting import Alerting
from .db import Database
from .fleet import Fleet
from .ingest import IngestError, Ingestor
from .settings import CloudSettings
from .ws import WsManager


def create_app(settings: CloudSettings | None = None, db: Database | None = None) -> FastAPI:
    settings = settings or CloudSettings()
    db = db or Database(settings.db_url)

    clock = SystemClock()

    app = FastAPI(title="SenseCloud", version="1.0.0")

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    fleet = Fleet(db, clock=clock)
    aggregator = Aggregator(db, clock=clock)
    alerting = Alerting(db, clock=clock)
    ingestor = Ingestor(db, clock, fleet, alerting, aggregator, dev=settings.dev)

    @app.get("/health")
    def health():
        return {"status": "ok", "service": "sensecloud", "version": "1.0.0"}

    @app.post("/v1/ingest/batch", response_model=IngestAck)
    def ingest_batch(batch: IngestBatch, x_device_token: str | None = Header(None)):
        try:
            res = ingestor.ingest(batch, token=x_device_token)
            return res.ack
        except IngestError as e:
            raise HTTPException(status_code=e.status, detail=e.detail)

    @app.get("/v1/stores")
    def list_stores():
        ids = db.store_ids()
        stores = []
        for sid in ids:
            cfg = db.store_config(sid)
            if cfg:
                stores.append({"store_id": sid, "name": cfg.store.name, "city": cfg.store.city, "config": cfg.model_dump(mode="json")})
            else:
                row = db.store_row(sid) or {}
                stores.append({"store_id": sid, "name": row.get("name", sid), "city": row.get("city", "")})
        return stores

    @app.post("/v1/stores")
    def create_store(cfg: StoreConfig):
        return fleet.register_store(cfg)

    @app.get("/v1/stores/{store_id}")
    def get_store(store_id: str):
        cfg = db.store_config(store_id)
        if not cfg:
            row = db.store_row(store_id)
            if not row:
                raise HTTPException(status_code=404, detail="Store not found")
            return {"store_id": store_id, "name": row.get("name", store_id), "city": row.get("city", "")}
        return {"store_id": store_id, "name": cfg.store.name, "city": cfg.store.city, "config": cfg}

    @app.get("/v1/fleet")
    def get_fleet():
        return fleet.view()

    @app.post("/v1/stores/{store_id}/integrations/tally/reconcile")
    def tally_reconcile(store_id: str):
        return {
            "status": "ok",
            "store_id": store_id,
            "reconciled_at": clock.now(),
            "items": [
                {"item_name": "Amul Taaza 500ml", "tally_qty": 48, "camera_qty": 41, "discrepancy": -7, "shrink_amount_inr": 189.0},
                {"item_name": "Parle-G 70g", "tally_qty": 120, "camera_qty": 120, "discrepancy": 0, "shrink_amount_inr": 0.0},
            ],
            "total_shrink_inr": 189.0,
        }

    @app.get("/mock/ondc/log")
    def ondc_log():
        return {"logs": []}

    return app
