"""DDL for edge and cloud, insert_ignore on sqlite + postgres dialect, sqlite PRAGMAs."""

from unittest import mock

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from retailsense_contracts import db
from retailsense_contracts.testing import sample_events_all


def _event_rows():
    return [e.model_dump(mode="json") for e in sample_events_all()]


def test_db_create_all_both(tmp_path):
    for which, md, expected in (
        (
            "edge",
            db.edge_metadata,
            {
                "events",
                "outbox",
                "device_state",
                "alerts",
                "shelf_state",
                "queue_state",
                "heatmap_cells",
                "kpi_daily",
                "sku_enrolment",
            },
        ),
        (
            "cloud",
            db.cloud_metadata,
            {
                "events",
                "alerts",
                "shelf_state",
                "queue_state",
                "heatmap_cells",
                "kpi_daily",
                "stores",
                "devices",
                "ingest_log",
                "commands",
                "notifications",
                "series_5m",
                "agg_cursor",
                "stock_recon",
                "forecasts",
                "model_manifests",
                "festivals",
                "ondc_log",
            },
        ),
    ):
        engine = db.sqlite_engine(tmp_path / f"{which}.db")
        db.create_all(engine, which)
        insp = sa.inspect(engine)
        assert set(insp.get_table_names()) == expected, which
        idx = {i["name"]: i for i in insp.get_indexes("alerts")}
        assert idx["ux_alert_open"]["unique"]
        assert "ux_events_device_seq" in {i["name"] for i in insp.get_indexes("events")}
        assert set(md.tables) == expected
        # idempotent
        db.create_all(engine, which)
        engine.dispose()


def test_sqlite_pragmas_and_parent_dirs(tmp_path):
    path = tmp_path / "deep" / "er" / "edge.db"
    engine = db.sqlite_engine(path)
    with engine.connect() as conn:
        assert conn.execute(sa.text("PRAGMA journal_mode")).scalar().lower() == "wal"
        assert conn.execute(sa.text("PRAGMA synchronous")).scalar() == 2  # FULL
        assert conn.execute(sa.text("PRAGMA foreign_keys")).scalar() == 1
        assert conn.execute(sa.text("PRAGMA busy_timeout")).scalar() == 5000
    assert path.exists()
    engine.dispose()
    mem = db.sqlite_engine(":memory:", wal=False, synchronous_full=False)
    with mem.connect() as conn:
        assert conn.execute(sa.text("select 1")).scalar() == 1


def test_partial_unique_index_one_open_alert_per_kind_subject(tmp_path):
    engine = db.sqlite_engine(tmp_path / "e.db")
    db.create_all(engine, "edge")
    row = dict(alert_id="A1", kind="shelf_gap", subject_id="shelf-A", status="open", doc={})
    with engine.begin() as conn:
        conn.execute(db.alerts.insert().values(**row))
        # a resolved alert for the same subject is fine
        conn.execute(db.alerts.insert().values(**{**row, "alert_id": "A0", "status": "resolved"}))
        conn.execute(db.alerts.insert().values(**{**row, "alert_id": "A00", "status": "resolved"}))
    with engine.connect() as conn:
        assert db.insert_ignore(conn, db.alerts, [{**row, "alert_id": "A2"}]) == 0  # second OPEN one is ignored
        assert db.insert_ignore(conn, db.alerts, [{**row, "alert_id": "A3", "subject_id": "shelf-B"}]) == 1
        conn.commit()
    engine.dispose()


def test_insert_ignore_dedup(tmp_path):
    engine = db.sqlite_engine(tmp_path / "c.db")
    db.create_all(engine, "cloud")
    rows = _event_rows()
    with engine.begin() as conn:
        assert db.insert_ignore(conn, db.cloud_events, rows) == len(rows)
        assert db.insert_ignore(conn, db.cloud_events, rows) == 0  # exact resend: all duplicates
        extra = dict(rows[0], event_id="01ZZZZZZZZZZZZZZZZZZZZZZZZ", seq=999)
        assert db.insert_ignore(conn, db.cloud_events, rows + [extra]) == 1  # mixed batch: only the new one
        assert db.insert_ignore(conn, db.cloud_events, []) == 0
        n = conn.execute(sa.select(sa.func.count()).select_from(db.cloud_events)).scalar()
        assert n == len(rows) + 1
        # unique (device_id, seq) also dedups
        dup_seq = dict(rows[1], event_id="01YYYYYYYYYYYYYYYYYYYYYYYY")
        assert db.insert_ignore(conn, db.cloud_events, [dup_seq]) == 0
    # big batch goes through chunking
    with engine.begin() as conn:
        big = [dict(rows[0], event_id=f"01B{i:023d}", seq=10_000 + i) for i in range(1000)]
        assert db.insert_ignore(conn, db.cloud_events, big) == 1000
    engine.dispose()


def test_insert_ignore_postgres_dialect_compiles_on_conflict():
    """No Postgres on the dev box: assert the statement we would execute is ON CONFLICT DO NOTHING."""
    executed = []

    class FakeResult:
        rowcount = 2

    conn = mock.Mock()
    conn.dialect = postgresql.dialect()
    conn.execute.side_effect = lambda stmt: executed.append(stmt) or FakeResult()
    rows = _event_rows()[:2]
    assert db.insert_ignore(conn, db.cloud_events, rows) == 2
    assert len(executed) == 1
    sql = str(executed[0].compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT DO NOTHING" in sql and "INSERT INTO events" in sql
    # DDL for postgres renders the partial index with WHERE
    ddl = str(
        sa.schema.CreateIndex(next(i for i in db.cloud_alerts.indexes if i.name == "ux_alert_open")).compile(
            dialect=postgresql.dialect()
        )
    )
    assert "WHERE status <> 'resolved'" in ddl and "UNIQUE" in ddl


def test_sqlite_url_windows_path():
    assert db.sqlite_url(r"C:\x\y.db") == "sqlite:///C:/x/y.db"
    assert db.sqlite_url(":memory:") == "sqlite://"
