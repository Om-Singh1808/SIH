"""Database access for SenseCloud.

Design notes
------------
* The DDL lives in the frozen contracts (``retailsense_contracts.db.cloud_metadata``);
  this module only owns *engine construction* and a handful of generic helpers
  (``upsert``, ``rows``, ``one``) so every other module can stay in plain SQLAlchemy
  Core without repeating dialect-specific boilerplate.
* SQLite is the demo/dev backend (``sqlite:///var/sensecloud.db``); Postgres is a
  URL swap.  ``upsert()`` compiles to ``INSERT .. ON CONFLICT DO UPDATE`` on both.
* The app is single-process and the handlers are short, so a synchronous engine is
  used from async handlers on purpose: a 500-event batch commits in well under
  300 ms on SQLite and avoids the thread-affinity problems of ``:memory:`` databases.
* Typed accessors (``store_config``, ``alert``) decode the JSON documents back into
  contract models, so callers never touch raw dicts for the important objects.
"""

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Table, create_engine, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.pool import StaticPool

from retailsense_contracts import db as cdb
from retailsense_contracts.alerts import Alert
from retailsense_contracts.config import StoreConfig

SQLITE_PREFIX = "sqlite:///"


def make_engine(url: str) -> Engine:
    """Engine for ``url``; SQLite files get the contracts' WAL/FULL pragmas, ``sqlite://`` is in-memory."""
    if url in ("sqlite://", "sqlite:///:memory:"):
        return create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool, future=True
        )
    if url.startswith(SQLITE_PREFIX):
        path = url[len(SQLITE_PREFIX) :]
        return cdb.sqlite_engine(path, connect_args={"check_same_thread": False}, future=True)
    return create_engine(url, future=True)


def _insert_for(conn: Connection):
    if conn.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert


class Database:
    """Engine holder + generic helpers. One instance per app."""

    def __init__(self, url: str):
        self.url = url
        self.engine = make_engine(url)
        cdb.create_all(self.engine, "cloud")

    # -- connections ------------------------------------------------------
    @contextmanager
    def tx(self) -> Iterator[Connection]:
        """A transaction that commits on success and rolls back on error."""
        with self.engine.begin() as conn:
            yield conn

    @contextmanager
    def read(self) -> Iterator[Connection]:
        with self.engine.connect() as conn:
            yield conn

    # -- generic helpers --------------------------------------------------
    @staticmethod
    def upsert(conn: Connection, table: Table, row: dict[str, Any], *, keys: Sequence[str] | None = None) -> None:
        """INSERT .. ON CONFLICT(pk) DO UPDATE with every non-key column of ``row``."""
        insert = _insert_for(conn)
        pk = list(keys) if keys else [c.name for c in table.primary_key.columns]
        update = {k: v for k, v in row.items() if k not in pk}
        stmt = insert(table).values(**row)
        if update:
            stmt = stmt.on_conflict_do_update(index_elements=pk, set_=update)
        else:
            stmt = stmt.on_conflict_do_nothing(index_elements=pk)
        conn.execute(stmt)

    @staticmethod
    def insert_ignore(conn: Connection, table: Table, rows: list[dict[str, Any]]) -> int:
        return cdb.insert_ignore(conn, table, rows)

    @staticmethod
    def rows(conn: Connection, stmt: Any) -> list[dict[str, Any]]:
        return [dict(r) for r in conn.execute(stmt).mappings().all()]

    @staticmethod
    def one(conn: Connection, stmt: Any) -> dict[str, Any] | None:
        r = conn.execute(stmt).mappings().first()
        return dict(r) if r is not None else None

    # -- typed accessors --------------------------------------------------
    def store_row(self, store_id: str) -> dict[str, Any] | None:
        with self.read() as conn:
            return self.one(conn, select(cdb.stores).where(cdb.stores.c.store_id == store_id))

    def store_config(self, store_id: str) -> StoreConfig | None:
        row = self.store_row(store_id)
        if row is None or not row.get("config"):
            return None
        try:
            return StoreConfig.model_validate(row["config"])
        except Exception:  # pragma: no cover - corrupt row should not take the API down
            return None

    def store_ids(self) -> list[str]:
        with self.read() as conn:
            return [r["store_id"] for r in self.rows(conn, select(cdb.stores.c.store_id).order_by(cdb.stores.c.store_id))]

    def store_lang(self, store_id: str) -> str:
        row = self.store_row(store_id)
        return str(row.get("lang") or "hi") if row else "hi"

    def store_tz(self, store_id: str) -> str:
        row = self.store_row(store_id)
        return str(row.get("tz") or "Asia/Kolkata") if row else "Asia/Kolkata"

    def alert(self, alert_id: str) -> Alert | None:
        with self.read() as conn:
            row = self.one(conn, select(cdb.cloud_alerts.c.doc).where(cdb.cloud_alerts.c.alert_id == alert_id))
        return Alert.model_validate(row["doc"]) if row else None

    @staticmethod
    def alert_row(a: Alert) -> dict[str, Any]:
        """Column projection of an Alert (the ``doc`` column keeps the full JSON)."""
        return {
            "alert_id": a.alert_id,
            "store_id": a.store_id,
            "device_id": a.device_id,
            "origin": str(a.origin),
            "kind": str(a.kind),
            "severity": str(a.severity),
            "status": str(a.status),
            "subject_id": a.subject_id,
            "raised_ts": a.raised_ts,
            "acked_ts": a.acked_ts,
            "resolved_ts": a.resolved_ts,
            "ack_action": str(a.ack_action) if a.ack_action else None,
            "ack_by": str(a.ack_by) if a.ack_by else None,
            "lost_sales_inr": a.impact.lost_sales_inr if a.impact else None,
            "recovered_inr": None,
            "doc": a.model_dump(mode="json"),
        }

    def dispose(self) -> None:
        self.engine.dispose()


__all__ = ["Database", "make_engine"]
