"""SQLAlchemy Core DDL for the edge (SQLite) and the cloud (SQLite demo / Postgres).

Why Core and not ORM: the edge writes events + outbox in *one* transaction from
a single asyncio thread and the cloud bulk-inserts 500-event batches with
``INSERT ... ON CONFLICT DO NOTHING`` - plain tables and statements are simpler,
faster and dialect-portable.

Key design points mirrored from the spec (C.10):
* ``events.event_id`` (ULID) is the idempotency key for cloud ingest;
  ``(device_id, seq)`` is unique so replay order is provable.
* ``outbox`` is the store-and-forward queue; ``expires_ts``/``evicted_ts`` encode
  the per-class policy in ``topics.py``.
* ``ux_alert_open`` is a *partial* unique index: one non-resolved alert per
  ``(kind, subject_id)`` - supported by both SQLite and PostgreSQL.
* ``sqlite_engine()`` applies ``journal_mode=WAL`` + ``synchronous=FULL`` so a
  power cut mid-batch never loses a committed alert.
"""

from pathlib import Path
from typing import Any, Literal

from sqlalchemy import (
    JSON,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    String,
    Table,
    create_engine,
    event,
    text,
)
from sqlalchemy.engine import Connection, Engine

edge_metadata = MetaData()
cloud_metadata = MetaData()

_OPEN_ALERT_WHERE = text("status <> 'resolved'")


# ---------------------------------------------------------------------------
# table factories (shared shapes, with the cloud variants gaining store_id)
# ---------------------------------------------------------------------------


def _events(md: MetaData) -> Table:
    t = Table(
        "events",
        md,
        Column("event_id", String, primary_key=True),
        Column("store_id", String, nullable=False),
        Column("device_id", String, nullable=False),
        Column("camera_id", String),
        Column("ts", Float, nullable=False),
        Column("hlc", String, nullable=False),
        Column("seq", Integer, nullable=False),
        Column("type", String, nullable=False),
        Column("cls", String, nullable=False),
        Column("version", Integer, nullable=False, server_default="1"),
        Column("payload", JSON, nullable=False),
        Column("created_ts", Float, nullable=False),
    )
    Index("ux_events_device_seq", t.c.device_id, t.c.seq, unique=True)
    Index("ix_events_ts", t.c.store_id, t.c.ts)
    Index("ix_events_type_ts", t.c.type, t.c.ts)
    return t


def _outbox(md: MetaData) -> Table:
    t = Table(
        "outbox",
        md,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("event_id", String, ForeignKey("events.event_id"), nullable=False),
        Column("cls", String, nullable=False),
        Column("enqueued_ts", Float, nullable=False),
        Column("expires_ts", Float),
        Column("sent_ts", Float),
        Column("attempts", Integer, nullable=False, server_default="0"),
        Column("last_error", String),
        Column("evicted_ts", Float),
        sqlite_autoincrement=True,
    )
    Index("ix_outbox_pending", t.c.sent_ts, t.c.evicted_ts, t.c.id)
    return t


def _device_state(md: MetaData) -> Table:
    # seq_next, hlc_last, link_state, replay stats, config_version
    return Table("device_state", md, Column("key", String, primary_key=True), Column("value", String, nullable=False))


def _alerts(md: MetaData, *, cloud: bool) -> Table:
    t = Table(
        "alerts",
        md,
        Column("alert_id", String, primary_key=True),
        Column("store_id", String),
        Column("device_id", String),
        Column("origin", String),
        Column("kind", String),
        Column("severity", String),
        Column("status", String),
        Column("subject_id", String),
        Column("raised_ts", Float),
        Column("acked_ts", Float),
        Column("resolved_ts", Float),
        Column("ack_action", String),
        Column("ack_by", String),
        Column("lost_sales_inr", Float),
        Column("recovered_inr", Float),
        Column("doc", JSON, nullable=False),
    )
    Index("ix_alerts_status", t.c.status, t.c.raised_ts)
    if cloud:
        Index("ix_alerts_store", t.c.store_id, t.c.raised_ts)
        Index(
            "ux_alert_open",
            t.c.store_id,
            t.c.kind,
            t.c.subject_id,
            unique=True,
            sqlite_where=_OPEN_ALERT_WHERE,
            postgresql_where=_OPEN_ALERT_WHERE,
        )
    else:
        Index(
            "ux_alert_open",
            t.c.kind,
            t.c.subject_id,
            unique=True,
            sqlite_where=_OPEN_ALERT_WHERE,
            postgresql_where=_OPEN_ALERT_WHERE,
        )
    return t


def _shelf_state(md: MetaData, *, cloud: bool) -> Table:
    cols: list[Any] = []
    if cloud:
        cols.append(Column("store_id", String, primary_key=True))
    cols += [
        Column("shelf_id", String, primary_key=True),
        Column("sku_id", String),
        Column("state", String),
        Column("coverage", Float),
        Column("facings", Integer),
        Column("consecutive_empty_scans", Integer),
        Column("persistence_required", Integer),
        Column("gap_started_ts", Float),
        Column("last_scan_ts", Float),
        Column("gap_minutes_today", Float, server_default="0"),
        Column("fp_count", Integer, server_default="0"),
        Column("reference", JSON),
    ]
    return Table("shelf_state", md, *cols)


def _queue_state(md: MetaData, *, cloud: bool) -> Table:
    cols: list[Any] = []
    if cloud:
        cols.append(Column("store_id", String, primary_key=True))
    cols += [
        Column("counter_id", String, primary_key=True),
        Column("snapshot", JSON),
        Column("forecast", JSON),
        Column("updated_ts", Float),
    ]
    return Table("queue_state", md, *cols)


def _heatmap_cells(md: MetaData, *, cloud: bool) -> Table:
    cols: list[Any] = []
    if cloud:
        cols.append(Column("store_id", String, primary_key=True))
    cols += [
        Column("camera_id", String, primary_key=True),
        Column("cell_x", Integer, primary_key=True),
        Column("cell_y", Integer, primary_key=True),
        Column("hour_bucket", Integer, primary_key=True),
        Column("dwell_s", Float),
        Column("visits", Integer),
    ]
    return Table("heatmap_cells", md, *cols)


def _kpi_daily(md: MetaData) -> Table:
    return Table(
        "kpi_daily",
        md,
        Column("store_id", String, primary_key=True),
        Column("date", String, primary_key=True),
        Column("footfall_in", Integer),
        Column("footfall_out", Integer),
        Column("visual_transactions", Integer),
        Column("conversion_pct", Float),
        Column("atv_inr", Float),
        Column("osa_pct", Float),
        Column("gap_minutes_total", Float),
        Column("avg_wait_s", Float),
        Column("max_wait_s", Float),
        Column("abandoned", Integer),
        Column("lost_sales_inr", Float),
        Column("lost_margin_inr", Float),
        Column("recovered_inr", Float),
        Column("shrink_inr", Float),
        Column("alerts_total", Integer),
        Column("updated_ts", Float),
    )


def _sku_enrolment(md: MetaData) -> Table:
    return Table(
        "sku_enrolment",
        md,
        Column("sku_id", String, primary_key=True),
        Column("idx", Integer, primary_key=True),
        Column("embedding", LargeBinary),
    )


# --- EDGE ------------------------------------------------------------------
events = _events(edge_metadata)
outbox = _outbox(edge_metadata)
device_state = _device_state(edge_metadata)
alerts = _alerts(edge_metadata, cloud=False)
shelf_state = _shelf_state(edge_metadata, cloud=False)
queue_state = _queue_state(edge_metadata, cloud=False)
heatmap_cells = _heatmap_cells(edge_metadata, cloud=False)
kpi_daily = _kpi_daily(edge_metadata)
sku_enrolment = _sku_enrolment(edge_metadata)

# --- CLOUD -----------------------------------------------------------------
cloud_events = _events(cloud_metadata)
cloud_alerts = _alerts(cloud_metadata, cloud=True)
cloud_shelf_state = _shelf_state(cloud_metadata, cloud=True)
cloud_queue_state = _queue_state(cloud_metadata, cloud=True)
cloud_heatmap_cells = _heatmap_cells(cloud_metadata, cloud=True)
cloud_kpi_daily = _kpi_daily(cloud_metadata)

stores = Table(
    "stores",
    cloud_metadata,
    Column("store_id", String, primary_key=True),
    Column("name", String),
    Column("tier", String),
    Column("lang", String),
    Column("tz", String),
    Column("config", JSON),
    Column("registered_ts", Float),
)
devices = Table(
    "devices",
    cloud_metadata,
    Column("device_id", String, primary_key=True),
    Column("store_id", String),
    Column("token", String),
    Column("last_seen_ts", Float),
    Column("last_seq", Integer),
    Column("model_version", String),
    Column("fps", Float),
    Column("backlog", Integer),
    Column("link", String),
    Column("uptime_s", Float),
    Column("status", String),
)
ingest_log = Table(
    "ingest_log",
    cloud_metadata,
    Column("batch_id", String, primary_key=True),
    Column("device_id", String),
    Column("received_ts", Float),
    Column("accepted", Integer),
    Column("duplicates", Integer),
    Column("first_seq", Integer),
    Column("last_seq", Integer),
    Column("seq_ok", Integer),
)
commands = Table(
    "commands",
    cloud_metadata,
    Column("command_id", String, primary_key=True),
    Column("device_id", String),
    Column("kind", String),
    Column("payload", JSON),
    Column("created_ts", Float),
    Column("delivered_ts", Float),
)
notifications = Table(
    "notifications",
    cloud_metadata,
    Column("message_id", String, primary_key=True),
    Column("store_id", String),
    Column("channel", String),
    Column("to_addr", String),
    Column("text", String),
    Column("buttons", JSON),
    Column("alert_id", String),
    Column("status", String),
    Column("created_ts", Float),
    Column("delivered_ts", Float),
)
series_5m = Table(
    "series_5m",
    cloud_metadata,
    Column("store_id", String, primary_key=True),
    Column("metric", String, primary_key=True),
    Column("bucket_ts", Float, primary_key=True),
    Column("value", Float),
    Column("n", Integer),
)
agg_cursor = Table(
    "agg_cursor",
    cloud_metadata,
    Column("store_id", String, primary_key=True),
    Column("last_event_seq", JSON),
    Column("updated_ts", Float),
)
stock_recon = Table(
    "stock_recon",
    cloud_metadata,
    Column("id", String, primary_key=True),
    Column("store_id", String),
    Column("sku_id", String),
    Column("shelf_id", String),
    Column("ts", Float),
    Column("visual_units", Integer),
    Column("system_units", Integer),
    Column("delta_units", Integer),
    Column("delta_inr", Float),
    Column("source", String),
)
forecasts = Table(
    "forecasts",
    cloud_metadata,
    Column("id", String, primary_key=True),
    Column("store_id", String),
    Column("counter_id", String),
    Column("made_ts", Float),
    Column("horizon_min", Integer),
    Column("predicted", Float),
    Column("actual", Float),
    Column("model", String),
)
model_manifests = Table(
    "model_manifests",
    cloud_metadata,
    Column("version", String, primary_key=True),
    Column("doc", JSON),
    Column("published_ts", Float),
    Column("active", Integer),
)
festivals = Table(
    "festivals",
    cloud_metadata,
    Column("date", String, primary_key=True),
    Column("name", String, primary_key=True),
    Column("region", String),
    Column("weight", Float),
)
ondc_log = Table(
    "ondc_log",
    cloud_metadata,
    Column("message_id", String, primary_key=True),
    Column("store_id", String),
    Column("item_id", String),
    Column("available", Integer),
    Column("qty", Integer),
    Column("ts", Float),
    Column("payload", JSON),
)

EDGE_TABLES: dict[str, Table] = {t.name: t for t in edge_metadata.sorted_tables}
CLOUD_TABLES: dict[str, Table] = {t.name: t for t in cloud_metadata.sorted_tables}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def metadata_for(which: Literal["edge", "cloud"]) -> MetaData:
    if which == "edge":
        return edge_metadata
    if which == "cloud":
        return cloud_metadata
    raise ValueError(f"unknown metadata set {which!r}")


def create_all(engine: Engine, which: Literal["edge", "cloud"]) -> None:
    """Create every table/index of the chosen set (idempotent)."""
    metadata_for(which).create_all(engine)


_CHUNK = 400  # rows per multi-VALUES statement; keeps SQLite well under its variable limit


def insert_ignore(conn: Connection, table: Table, rows: list[dict]) -> int:
    """Insert ``rows`` skipping primary-key/unique conflicts. Returns the number actually inserted.

    sqlite -> ``INSERT ... ON CONFLICT DO NOTHING`` (same semantics as INSERT OR IGNORE);
    postgresql -> ``INSERT ... ON CONFLICT DO NOTHING``; other dialects fall back to
    one plain INSERT per row inside a savepoint.
    """
    if not rows:
        return 0
    dialect = conn.dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as _insert
    elif dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as _insert
    else:  # pragma: no cover - portable fallback
        from sqlalchemy.exc import IntegrityError

        inserted = 0
        for row in rows:
            sp = conn.begin_nested()
            try:
                conn.execute(table.insert().values(**row))
                sp.commit()
                inserted += 1
            except IntegrityError:
                sp.rollback()
        return inserted

    inserted = 0
    for i in range(0, len(rows), _CHUNK):
        chunk = rows[i : i + _CHUNK]
        stmt = _insert(table).values(chunk).on_conflict_do_nothing()
        result = conn.execute(stmt)
        rc = result.rowcount
        inserted += rc if rc is not None and rc >= 0 else 0
    return inserted


def sqlite_url(path: str | Path) -> str:
    if str(path) == ":memory:":
        return "sqlite://"
    return "sqlite:///" + Path(path).as_posix()


def sqlite_engine(path: str | Path, *, wal: bool = True, synchronous_full: bool = True, **kwargs: Any) -> Engine:
    """SQLite engine with durability PRAGMAs applied on every new connection.

    journal_mode=WAL lets the API read while the writer commits; synchronous=FULL
    makes each commit survive a power cut; busy_timeout avoids spurious
    'database is locked'; foreign_keys=ON enforces outbox -> events.
    Parent directories of ``path`` are created.
    """
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(sqlite_url(path), **kwargs)

    @event.listens_for(engine, "connect")
    def _pragmas(dbapi_conn, _record) -> None:  # pragma: no cover - exercised via tests indirectly
        cur = dbapi_conn.cursor()
        try:
            if wal and str(path) != ":memory:":
                cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=" + ("FULL" if synchronous_full else "NORMAL"))
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA foreign_keys=ON")
        finally:
            cur.close()

    return engine


__all__ = [
    "CLOUD_TABLES",
    "EDGE_TABLES",
    "agg_cursor",
    "alerts",
    "cloud_alerts",
    "cloud_events",
    "cloud_heatmap_cells",
    "cloud_kpi_daily",
    "cloud_metadata",
    "cloud_queue_state",
    "cloud_shelf_state",
    "commands",
    "create_all",
    "device_state",
    "devices",
    "edge_metadata",
    "events",
    "festivals",
    "forecasts",
    "heatmap_cells",
    "ingest_log",
    "insert_ignore",
    "kpi_daily",
    "metadata_for",
    "model_manifests",
    "notifications",
    "ondc_log",
    "outbox",
    "queue_state",
    "series_5m",
    "shelf_state",
    "sku_enrolment",
    "sqlite_engine",
    "sqlite_url",
    "stock_recon",
    "stores",
]
