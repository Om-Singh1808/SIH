"""Fleet registry: devices, cloud -> device commands, model manifests and rollouts.

* ``devices`` rows are upserted by ingest (token, last_seen, last_seq, heartbeat
  fields).  ``DeviceStatus`` is derived: ``never`` (no batch yet), ``online``
  (seen within ``offline_after_s``) or ``offline``.
* Commands are a tiny transactional outbox in the other direction: the cloud
  enqueues (``ack_alert``, ``model_update``, ...) and the device receives them
  piggybacked on its next ``IngestAck`` (or on the MQTT cmd topic).  ``delivered_ts``
  marks them consumed so a command is delivered exactly once.
* Manifests: the active ``ModelManifest`` decides what every device *should* run.
  ``assigned_version`` (contracts.manifest) applies pins > canary bucket > stable, so
  the same rule that the edge uses to decide whether to download a model is used
  here to flag ``version_drift``.  A rollout request bumps a model's version in the
  active manifest and switches the rollout channel to ``canary`` with the given
  percentage.
"""

import json
from typing import Any

from sqlalchemy import and_, select

from retailsense_contracts import db as cdb
from retailsense_contracts.api import Command, DeviceStatus, FleetView, RolloutRequest
from retailsense_contracts.clock import Clock
from retailsense_contracts.enums import LinkState
from retailsense_contracts.ids import new_ulid
from retailsense_contracts.manifest import ModelManifest, assigned_version
from retailsense_contracts.testing import example_path

from .db import Database

PERSON_MODEL_ID = "person_detect"


class Fleet:
    def __init__(self, db: Database, clock: Clock, *, offline_after_s: float = 60.0):
        self.db = db
        self.clock = clock
        self.offline_after_s = offline_after_s

    # --------------------------------------------------------------- devices
    def register_device(self, device_id: str, store_id: str, token: str) -> None:
        with self.db.tx() as conn:
            existing = self.db.one(conn, select(cdb.devices).where(cdb.devices.c.device_id == device_id))
            row = dict(existing) if existing else {"device_id": device_id, "status": "never"}
            row.update(store_id=store_id, token=token)
            self.db.upsert(conn, cdb.devices, row)

    def device_row(self, device_id: str) -> dict[str, Any] | None:
        with self.db.read() as conn:
            return self.db.one(conn, select(cdb.devices).where(cdb.devices.c.device_id == device_id))

    def device_ids(self, store_id: str) -> list[str]:
        with self.db.read() as conn:
            rows = self.db.rows(
                conn,
                select(cdb.devices.c.device_id).where(cdb.devices.c.store_id == store_id).order_by(cdb.devices.c.device_id),
            )
        return [r["device_id"] for r in rows]

    def status(self, row: dict[str, Any], manifest: ModelManifest | None, now: float) -> DeviceStatus:
        last = row.get("last_seen_ts")
        if last is None:
            state = "never"
        elif now - float(last) > self.offline_after_s:
            state = "offline"
        else:
            state = "online"
        assigned = self.assigned_for(manifest, row["device_id"]) if manifest else None
        model_version = row.get("model_version")
        return DeviceStatus(
            device_id=row["device_id"],
            store_id=row.get("store_id") or "",
            status=state,
            last_seen_ts=last,
            model_version=model_version,
            assigned_version=assigned,
            version_drift=bool(assigned and model_version and assigned != model_version),
            fps=row.get("fps"),
            backlog=row.get("backlog"),
            link=LinkState(row["link"]) if row.get("link") else None,
            uptime_s=row.get("uptime_s"),
        )

    def view(self, now: float | None = None) -> FleetView:
        now = self.clock.now() if now is None else now
        manifest = self.active_manifest()
        with self.db.read() as conn:
            rows = self.db.rows(conn, select(cdb.devices).order_by(cdb.devices.c.device_id))
        devices = [self.status(r, manifest, now) for r in rows]
        return FleetView(
            devices=devices,
            online=sum(1 for d in devices if d.status == "online"),
            offline=sum(1 for d in devices if d.status == "offline"),
            manifest_version=manifest.version if manifest else None,
        )

    # -------------------------------------------------------------- commands
    def enqueue(self, device_id: str, kind: str, payload: dict[str, Any]) -> Command:
        cmd = Command(
            command_id=new_ulid(self.clock.now()),
            device_id=device_id,
            kind=kind,  # type: ignore[arg-type]
            payload=payload,
            created_ts=self.clock.now(),
        )
        with self.db.tx() as conn:
            conn.execute(
                cdb.commands.insert().values(
                    command_id=cmd.command_id,
                    device_id=device_id,
                    kind=kind,
                    payload=payload,
                    created_ts=cmd.created_ts,
                    delivered_ts=None,
                )
            )
        return cmd

    def pending(self, device_id: str, *, mark_delivered: bool = False, conn=None) -> list[Command]:
        def _run(c) -> list[Command]:
            rows = self.db.rows(
                c,
                select(cdb.commands)
                .where(and_(cdb.commands.c.device_id == device_id, cdb.commands.c.delivered_ts.is_(None)))
                .order_by(cdb.commands.c.created_ts),
            )
            cmds = [
                Command(
                    command_id=r["command_id"],
                    device_id=r["device_id"],
                    kind=r["kind"],
                    payload=r["payload"] or {},
                    created_ts=float(r["created_ts"]),
                )
                for r in rows
            ]
            if mark_delivered and cmds:
                c.execute(
                    cdb.commands.update()
                    .where(cdb.commands.c.command_id.in_([x.command_id for x in cmds]))
                    .values(delivered_ts=self.clock.now())
                )
            return cmds

        if conn is not None:
            return _run(conn)
        with self.db.tx() as c:
            return _run(c)

    # ------------------------------------------------------------- manifests
    def active_manifest(self) -> ModelManifest | None:
        with self.db.read() as conn:
            row = self.db.one(
                conn,
                select(cdb.model_manifests)
                .where(cdb.model_manifests.c.active == 1)
                .order_by(cdb.model_manifests.c.published_ts.desc()),
            )
        return ModelManifest.model_validate(row["doc"]) if row else None

    def publish_manifest(self, manifest: ModelManifest, *, activate: bool = True) -> ModelManifest:
        with self.db.tx() as conn:
            if activate:
                conn.execute(cdb.model_manifests.update().values(active=0))
            self.db.upsert(
                conn,
                cdb.model_manifests,
                {
                    "version": manifest.version,
                    "doc": manifest.model_dump(mode="json"),
                    "published_ts": self.clock.now(),
                    "active": 1 if activate else 0,
                },
            )
        return manifest

    def ensure_demo_manifest(self) -> ModelManifest:
        """Load ``examples/manifest_demo.json`` once so ``/v1/fleet/manifest`` is never empty."""
        current = self.active_manifest()
        if current is not None:
            return current
        doc = json.loads(example_path("manifest_demo.json").read_text(encoding="utf-8"))
        return self.publish_manifest(ModelManifest.model_validate(doc))

    def known_versions(self, manifest: ModelManifest, model_id: str) -> list[str]:
        with self.db.read() as conn:
            rows = self.db.rows(conn, select(cdb.model_manifests.c.doc))
        versions: set[str] = set()
        for r in rows:
            for m in (r["doc"] or {}).get("models", []):
                if m.get("model_id") == model_id:
                    versions.add(m["version"])
        entry = manifest.entry(model_id)
        if entry:
            versions.add(entry.version)
        return sorted(versions)

    def assigned_for(self, manifest: ModelManifest, device_id: str, model_id: str = PERSON_MODEL_ID) -> str | None:
        versions = self.known_versions(manifest, model_id)
        try:
            return assigned_version(manifest, device_id, model_id, versions)
        except ValueError:
            return None

    def rollout(self, req: RolloutRequest) -> ModelManifest:
        """Bump ``model_id`` to ``version`` on a canary channel with ``canary_pct`` of devices."""
        manifest = self.ensure_demo_manifest()
        models = []
        found = False
        for m in manifest.models:
            if m.model_id == req.model_id:
                models.append(m.model_copy(update={"version": req.version}))
                found = True
            else:
                models.append(m)
        if not found:
            raise KeyError(req.model_id)
        rollout = manifest.rollout.model_copy(
            update={"channel": "canary" if 0 < req.canary_pct < 100 else "stable", "canary_pct": req.canary_pct}
        )
        bumped = manifest.model_copy(
            update={
                "version": f"{manifest.version}+{req.model_id}-{req.version}",
                "generated_ts": self.clock.now(),
                "models": models,
                "rollout": rollout,
            }
        )
        # previous manifest stays as history (inactive) so known_versions still lists the stable version
        self.publish_manifest(bumped)
        with self.db.tx() as conn:
            rows = self.db.rows(conn, select(cdb.devices))
            for r in rows:
                assigned = self.assigned_for(bumped, r["device_id"], req.model_id)
                if assigned and assigned != r.get("model_version"):
                    conn.execute(
                        cdb.commands.insert().values(
                            command_id=new_ulid(self.clock.now()),
                            device_id=r["device_id"],
                            kind="model_update",
                            payload={"model_id": req.model_id, "version": assigned},
                            created_ts=self.clock.now(),
                        )
                    )
        return bumped


__all__ = ["PERSON_MODEL_ID", "Fleet"]
