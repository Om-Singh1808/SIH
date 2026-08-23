"""asyncio side of the pipeline: consumer + periodic background tasks.

* ``consumer_task``   drains the worker queue -> shelf state machine / forecasters /
                      RuleEngine -> ``EdgeState.ingest`` -> views.
* ``heartbeat_task``  every 10 s: DeviceHeartbeat from worker stats, RuleEngine.on_health /
                      on_sync (camera_down, sync_backlog alerts), WS ``health``.
* ``kpi_task``        every 5 s: KpiToday broadcast + series samples.
* ``retention_task``  hourly DPDP purge through the registry RetentionJob.
* ``forecast_task``   every 30 s: edge TrendForecaster.predict -> queue.forecast; when the
                      cloud is reachable, fetch its GBM forecast and hand it to the forecaster.
* ``model_check_task`` every 5 min: compare models/manifest.json with the cloud manifest.
* ``sync_task``       the registry SyncWorker (store-and-forward over HTTP/MQTT).

All intervals come from ``EdgeState.intervals`` so tests can run them fast.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from retailsense_contracts import VERSION
from retailsense_contracts.api import Command
from retailsense_contracts.enums import AckAction, AckBy, LinkState
from retailsense_contracts.events import DeviceHeartbeat, Observation, QueueForecast, QueueSnapshot, ShelfScan

from senseedge.adapt import build
from senseedge.state import EdgeState
from senseedge.workers import FrameResult

log = logging.getLogger("senseedge.consumer")


# ---------------------------------------------------------------------------
# consumer
# ---------------------------------------------------------------------------
async def process_result(state: EdgeState, result: FrameResult) -> list[Any]:
    """Turn one FrameResult into stamped events (exposed for tests)."""
    out: list[Observation] = []
    for obs in result.observations:
        p = obs.payload
        if isinstance(p, ShelfScan):
            out.extend(_on_shelf_scan(state, obs, p))
        elif isinstance(p, QueueSnapshot):
            out.extend(_on_queue_snapshot(state, obs, p))
        elif obs.type == "heatmap.tiles":
            state.store.heat_add(obs.camera_id or result.camera_id, p)
            out.append(obs)
        else:
            out.append(obs)
    return await state.ingest(out)


def _on_shelf_scan(state: EdgeState, obs: Observation, scan: ShelfScan) -> list[Observation]:
    out = [obs]
    if scan.thumb_b64:
        state.thumbs[scan.shelf_id] = scan.thumb_b64
    change = state.shelf_machine.apply(scan, obs.ts)
    if change is not None:
        out.append(Observation.of(change, obs.ts, obs.camera_id))
        view = state.shelf_machine.view(scan.shelf_id)
        out.extend(state.rules.on_shelf_change(change, view, obs.ts))
    _persist_shelf(state, scan.shelf_id)
    return out


def _persist_shelf(state: EdgeState, shelf_id: str) -> None:
    try:
        view = state.shelf_machine.view(shelf_id)
    except KeyError:
        return
    ref = None
    for w in state.workers:
        ref = w.scanner.references.get(shelf_id) or ref
    state.store.upsert_shelf(view, ref)


def _on_queue_snapshot(state: EdgeState, obs: Observation, snap: QueueSnapshot) -> list[Observation]:
    out = [obs]
    fc = state.forecasters.get(snap.counter_id)
    if fc is not None:
        fc.observe(snap)
    forecast = state.forecasts.get(snap.counter_id)
    out.extend(state.rules.on_queue(snap, forecast, obs.ts))
    state.store.upsert_queue(snap.counter_id, snap, None)
    state.series.record("queue_count", obs.ts, snap.count)
    state.series.record("est_wait_s", obs.ts, snap.est_wait_s)
    return out


async def consumer_task(state: EdgeState) -> None:
    idle = state.intervals["consumer_idle"]
    while not state.stopping.is_set():
        result = state.results.get(timeout=0) if state.results.qsize() else None
        if result is None:
            await asyncio.sleep(idle)
            continue
        try:
            await process_result(state, result)
        except Exception:
            log.exception("consumer failed on frame %s@%s", result.camera_id, result.ts)


# ---------------------------------------------------------------------------
# periodic helpers
# ---------------------------------------------------------------------------
async def _every(state: EdgeState, key: str, fn: Callable[[], Awaitable[None]], *, first_delay: float = 0.0) -> None:
    """Run ``fn`` every ``intervals[key]`` seconds until shutdown; exceptions are logged, not fatal."""
    if first_delay:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(state.stopping.wait(), timeout=first_delay)
    while not state.stopping.is_set():
        try:
            await fn()
        except Exception:
            log.exception("%s task failed", key)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(state.stopping.wait(), timeout=state.intervals[key])


def build_heartbeat(state: EdgeState) -> DeviceHeartbeat:
    p50 = [w.stats.percentile(0.5) for w in state.workers]
    p95 = [w.stats.percentile(0.95) for w in state.workers]
    sync = state.refresh_sync_status()
    clock = state.clock
    return DeviceHeartbeat(
        uptime_s=round(state.uptime_s, 2),
        fps=round(sum(w.stats.fps for w in state.workers), 2),
        infer_ms_p50=round(max(p50) if p50 else 0.0, 2),
        infer_ms_p95=round(max(p95) if p95 else 0.0, 2),
        detector=state.wiring.detector_name,
        model_version=state.wiring.model_version,
        backlog=sync.backlog,
        link=sync.link,
        cameras=state.camera_health(),
        contracts_version=VERSION,
        clock_factor=float(getattr(clock, "factor", 1.0)),
        sim_ts=state.now() if hasattr(clock, "factor") else None,
    )


async def heartbeat_once(state: EdgeState) -> None:
    ts = state.now()
    hb = build_heartbeat(state)
    state.last_heartbeat = hb
    obs = [Observation.of(hb, ts)]
    obs += state.rules.on_health(hb, ts)
    obs += state.rules.on_sync(state.sync_status, ts)
    await state.ingest(obs)
    state.ws.emit("health", ts, state.health(), state.cfg.store.store_id)


async def kpi_once(state: EdgeState) -> None:
    ts = state.now()
    kpi = state.store.kpi_today(ts)
    state.series.record("osa_pct", ts, kpi.osa_pct)
    state.series.record("occupancy", ts, kpi.occupancy_now)
    state.series.record("footfall_in", ts, kpi.footfall_in)
    state.ws.emit("kpi", ts, kpi, state.cfg.store.store_id)


async def retention_once(state: EdgeState) -> None:
    job = build(state.wiring.retention_cls, {"store": state.store, "policy": state.cfg.privacy.retention, "cfg": state.cfg})
    ts = state.now()
    result = job.run(ts) if "now_ts" in inspect.signature(job.run).parameters else job.run()
    log.info("retention purge: %s", result)


async def forecast_once(state: EdgeState) -> None:
    ts = state.now()
    obs: list[Observation] = []
    for counter_id, fc in state.forecasters.items():
        cloud = await fetch_cloud_forecast(state, counter_id)
        if cloud is not None:
            fc.set_cloud_forecast(cloud)
        pred = fc.predict(ts)
        if pred is None:
            continue
        obs.append(Observation.of(pred, ts))
        state.store.upsert_queue(counter_id, None, pred)
    await state.ingest(obs)


async def fetch_cloud_forecast(state: EdgeState, counter_id: str) -> QueueForecast | None:
    """GET {cloud_url}/v1/stores/{id}/forecast/queue - only when the link is up and the cloud answered recently."""
    if state.cfg.device.uplink.mode == "none" or state.sync_status.link != LinkState.UP or not state.sync_status.cloud_reachable:
        return None
    try:
        import httpx

        url = f"{state.cfg.device.cloud_url.rstrip('/')}/v1/stores/{state.cfg.store.store_id}/forecast/queue"
        async with httpx.AsyncClient(timeout=2.0) as client:
            r = await client.get(url, params={"counter_id": counter_id}, headers={"X-Device-Token": state.cfg.device.token})
        if r.status_code != 200:
            return None
        return QueueForecast.model_validate(r.json())
    except Exception:
        return None


async def model_check_once(state: EdgeState) -> None:
    from senseedge.routers.models import compute_model_status

    state.model_status = await compute_model_status(state, remote=True)


# ---------------------------------------------------------------------------
# sync worker + command dispatch
# ---------------------------------------------------------------------------
async def dispatch_command(state: EdgeState, cmd: Command) -> None:
    """Commands ride back in IngestAck (cloud -> edge)."""
    ts = state.now()
    try:
        if cmd.kind == "ack_alert":
            action = AckAction(cmd.payload.get("action", "checked"))
            by = AckBy(cmd.payload.get("by", AckBy.WHATSAPP))
            await state.ingest(state.rules.on_ack(cmd.payload["alert_id"], action, by, ts))
        elif cmd.kind == "set_link":
            (state.wiring.link.cut if cmd.payload.get("state") == "down" else state.wiring.link.restore)()
        elif cmd.kind == "set_scenario":
            ctl = state.synthetic_control()
            if ctl is not None:
                state.scenario = ctl.apply_scenario(cmd.payload.get("name", "baseline"), cmd.payload.get("params", {}))
                state.ws.emit("scenario", ts, state.scenario, state.cfg.store.store_id)
        elif cmd.kind == "model_update":
            await model_check_once(state)
        elif cmd.kind == "apply_config":
            from retailsense_contracts.config import StoreConfig

            await state.apply_config(StoreConfig.model_validate(cmd.payload["config"]))
        # "ping" needs no action - the next heartbeat batch is the reply
    except Exception:
        log.exception("command %s failed", cmd.kind)


def _on_command(state: EdgeState, cmd: Command) -> None:
    asyncio.get_running_loop().create_task(dispatch_command(state, cmd))


async def sync_task(state: EdgeState) -> None:
    """Run the registry SyncWorker (class-with-run(stop) or free-standing coroutine)."""
    w = state.wiring
    if w.sync_worker_cls is None or w.uplink is None:
        return
    pool = {
        "store": state.store,
        "uplink": w.uplink,
        "link": w.link,
        "cfg": state.cfg,
        "config": state.cfg,
        "on_status": state.on_sync_status,
        "on_command": lambda cmd: _on_command(state, cmd),
        "stop": state.stopping,
        "clock": state.clock,
    }
    with contextlib.suppress(Exception):
        await w.uplink.connect()
    target = w.sync_worker_cls
    if inspect.iscoroutinefunction(target):  # D8 style: async run(store, uplink, link, cfg, on_status, on_command, stop)
        await build(target, pool)
        return
    worker = build(target, pool)
    state.sync_worker = worker
    run = getattr(worker, "run", None)
    if run is None:
        return
    params = inspect.signature(run).parameters
    kwargs = {k: v for k, v in pool.items() if k in params}
    if "stop" in params:
        kwargs["stop"] = state.stopping
    res = run(**kwargs) if kwargs else run(state.stopping)
    if inspect.isawaitable(res):
        await res


TASKS: dict[str, Callable[[EdgeState], Awaitable[None]]] = {
    "consumer": consumer_task,
    "sync": sync_task,
    "heartbeat": lambda s: _every(s, "heartbeat", lambda: heartbeat_once(s)),
    "kpi": lambda s: _every(s, "kpi", lambda: kpi_once(s), first_delay=0.05),
    "retention": lambda s: _every(s, "retention", lambda: retention_once(s), first_delay=5.0),
    "forecast": lambda s: _every(s, "forecast", lambda: forecast_once(s), first_delay=1.0),
    "model_check": lambda s: _every(s, "model_check", lambda: model_check_once(s), first_delay=2.0),
}


def start_tasks(state: EdgeState) -> list[asyncio.Task]:
    state.tasks = [asyncio.create_task(fn(state), name=f"senseedge-{name}") for name, fn in TASKS.items()]
    return state.tasks


async def stop_tasks(state: EdgeState, timeout: float = 3.0) -> None:
    state.stopping.set()
    for t in state.tasks:
        t.cancel()
    with contextlib.suppress(BaseException):
        await asyncio.wait_for(asyncio.gather(*state.tasks, return_exceptions=True), timeout=timeout)
    state.tasks = []
