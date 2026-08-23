"""fake_history (History DataFrame contract), FakeForecaster and the FakeSyncWorker replay loop."""

import asyncio

import pytest

from retailsense_contracts.enums import LinkState
from retailsense_contracts.interfaces import HISTORY_DAILY_COLUMNS, HISTORY_MINUTE_COLUMNS
from retailsense_contracts.testing import (
    FakeForecaster,
    FakeSyncWorker,
    FakeUplink,
    InMemoryEdgeStore,
    SimpleLinkController,
    fake_history,
    load_festivals_csv,
    sample_observation,
)

pd = pytest.importorskip("pandas")


def test_festivals_csv_has_required_dates():
    rows = load_festivals_csv()
    by_name = {(r["date"], r["name"].split(" (")[0].split(" /")[0]) for r in rows}
    for must in [
        ("2026-03-04", "Holi"),
        ("2026-08-15", "Independence Day"),
        ("2026-08-26", "Onam"),
        ("2026-11-08", "Diwali"),
        ("2026-11-15", "Chhath Puja"),
        ("2027-03-22", "Holi"),
        ("2027-01-14", "Makar Sankranti"),
    ]:
        assert must in by_name, must
    assert all(0 < r["weight"] <= 1 for r in rows) and all(isinstance(r["verified"], bool) for r in rows)
    assert [r["date"] for r in rows] == sorted(r["date"] for r in rows)


def test_fake_history_contract(cfg):
    minute, daily = fake_history(30, cfg, seed=42, end_date="2026-08-30")
    assert list(minute.columns) == list(HISTORY_MINUTE_COLUMNS)
    assert list(daily.columns) == list(HISTORY_DAILY_COLUMNS)
    assert len(minute) == 43_200 and len(daily) == 30
    assert minute["ts"].is_monotonic_increasing and minute["ts"].diff().dropna().eq(60).all()
    assert (minute["queue_count"] >= 0).all() and (daily["footfall_in"] > 0).all()
    assert set(minute["store_id"]) == {"STR-DL-001"} and set(minute["counter_id"]) == {"counter-1"}
    assert daily["date"].iloc[0] == "2026-08-01" and daily["date"].iloc[-1] == "2026-08-30"
    fest = daily[daily["is_festival"]]
    assert set(fest["date"]) == {"2026-08-15", "2026-08-26", "2026-08-28"}
    assert daily.loc[daily["date"] == "2026-08-14", "days_to_festival"].item() == 1
    assert (
        daily[daily["date"] <= "2026-08-07"]["is_salary_week"].all()
        and not daily[daily["date"] > "2026-08-07"]["is_salary_week"].any()
    )
    assert minute["minute_of_day"].max() == 1439 and minute["hour"].max() == 23
    # evening busier than 3 am
    evening = minute[minute["hour"] == 19]["queue_count"].mean()
    night = minute[minute["hour"] == 3]["queue_count"].mean()
    assert evening > night
    again, _ = fake_history(30, cfg, seed=42, end_date="2026-08-30")
    assert again.equals(minute)
    other, _ = fake_history(30, cfg, seed=7, end_date="2026-08-30")
    assert not other["queue_count"].equals(minute["queue_count"])


def test_fake_forecaster_roundtrip(cfg):
    minute, daily = fake_history(7, cfg, end_date="2026-08-30")
    fc = FakeForecaster()
    assert fc.report() is None
    rep = fc.fit(minute)
    assert (
        rep.target == "queue_count"
        and rep.n_rows == len(minute)
        and rep.mae_holdout < rep.mae_baseline
        and rep.horizons == [5, 10, 15, 30]
    )
    pred = fc.predict(minute.tail(30), minute["ts"].iloc[-1])
    assert set(pred) == {"5", "10", "15", "30"} and all(v >= 0 for v in pred.values())
    assert fc.report() is rep
    ff = FakeForecaster()
    ff.fit(daily)
    days = ff.predict_days("2026-08-24", 7)
    assert [d.date for d in days][0] == "2026-08-24" and len(days) == 7
    onam = next(d for d in days if d.date == "2026-08-26")
    assert onam.is_festival and onam.festival_name.startswith("Onam") and onam.days_to_festival == 0
    assert all(d.lower <= d.predicted <= d.upper for d in days)
    assert days[0].days_to_festival == 2


def test_fake_sync_worker_replays_after_cut(cfg):
    store = InMemoryEdgeStore(cfg)
    uplink = FakeUplink()
    link = SimpleLinkController()
    statuses = []
    commands = []
    worker = FakeSyncWorker(store, uplink, link, cfg, on_status=statuses.append, on_command=commands.append)

    async def run():
        await worker.tick()  # empty heartbeat batch
        assert uplink.sends == 1 and worker.cloud_reachable
        link.cut()
        store.append([sample_observation("zone.occupancy") for _ in range(500)])
        assert (await worker.tick()) is None and uplink.sends == 1  # nothing leaves while the cable is cut
        st = worker.status()
        assert st.link == LinkState.DOWN and st.backlog == 500 and st.down_since_ts is not None
        link.restore()
        assert worker.replay_total_at_restore == 500 and worker.replayed_since_restore == 0
        ack = await worker.tick()
        assert ack.accepted == 500 and ack.seq_ok and ack.last_seq == 500
        st = worker.status()
        assert st.backlog == 0 and st.replayed_since_restore == 500 and st.replay_total_at_restore == 500 and st.seq_ok
        assert [e.seq for e in uplink.events] == list(range(1, 501))
        # lost ack -> resend is reported as duplicates and nothing is double-counted
        flaky = FakeUplink(drop_every=1)
        w2 = FakeSyncWorker(store, flaky, link, cfg)
        store.append([sample_observation("footfall.crossing")])
        assert (await w2.tick()) is None and w2.errors == 1 and store.pending(10)[0][1].seq == 501
        flaky.drop_every = 0
        ack = await w2.tick()
        assert ack.duplicates == 1 and ack.accepted == 0 and store.pending(10) == []
        # run() loop stops on the event
        stop = asyncio.Event()
        task = asyncio.create_task(worker.run(stop))
        await asyncio.sleep(0.1)
        stop.set()
        await asyncio.wait_for(task, 2)

    asyncio.run(run())
    assert statuses and statuses[-1].link == LinkState.UP and commands == []
