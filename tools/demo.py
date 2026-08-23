"""One-command demo supervisor: ``python -m retailsense demo``.

Why a hand-written supervisor instead of docker-compose / a Procfile runner?
The stage box is a Windows laptop with no Docker, no ``make`` and a console code
page that chokes on Hindi.  This script therefore:

* boots the services **in dependency order** and waits for each ``/health``
  before starting the next (cloud -> tally mock -> edge -> headless chain
  stores -> board), so the edge finds a reachable cloud and the board finds both;
* spawns every child in its **own process group** (``CREATE_NEW_PROCESS_GROUP``
  on Windows, ``start_new_session`` on posix) and tears the whole tree down with
  ``taskkill /T /F`` / ``killpg`` - uvicorn reloaders and ``npm run dev`` both
  fork grandchildren that would otherwise outlive us and squat on the ports;
* forces ``PYTHONIOENCODING=utf-8`` and writes each child's output to
  ``var/logs/<service>.log`` so a crash is diagnosable after the fact;
* **reuses** an already-healthy service on a port (presenter left the cloud
  running from a rehearsal) instead of failing, and refuses clearly when the port
  is held by something else;
* fails fast with an actionable message when a module is not installed yet
  (``pip install -e apps/senseedge`` ...) - it never hangs waiting for a server
  that was never going to start;
* ``--smoke`` boots, verifies a handful of REST observables, shuts down and
  exits 0 - the CI/merge gate.

The boot sequence and flags follow IMPLEMENTATION_SPEC.md section D14/E.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import signal
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VAR = ROOT / "var"
LOGS = VAR / "logs"
DEMO_CONFIG = ROOT / "packages/contracts/retailsense_contracts/examples/store_demo.yaml"

IS_WIN = sys.platform == "win32"

# --------------------------------------------------------------------------------------
# Service table.  Each entry is a factory so CLI flags can shape the command line.
# If another module renames a flag, this is the only place to touch.
# --------------------------------------------------------------------------------------


@dataclass
class Service:
    name: str
    module: str  # python module whose presence gates the service ("npm" for the board)
    pkg_dir: str  # where `pip install -e` would come from (for the hint)
    cmd: list[str]
    health_url: str
    port: int
    env: dict[str, str] = field(default_factory=dict)
    cwd: Path = ROOT
    required: bool = True  # a missing/crashing required service aborts the demo
    timeout_s: float = 60.0  # how long to wait for /health
    proc: subprocess.Popen | None = None
    reused: bool = False
    log_path: Path | None = None

    @property
    def running(self) -> bool:
        return self.reused or (self.proc is not None and self.proc.poll() is None)


class BootError(RuntimeError):
    """Raised when a required service cannot be started; message is user-facing."""


def module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.setdefault("PYTHONUNBUFFERED", "1")
    root = str(ROOT)
    pp = env.get("PYTHONPATH", "")
    if root not in pp.split(os.pathsep):
        env["PYTHONPATH"] = root + (os.pathsep + pp if pp else "")
    if extra:
        env.update(extra)
    return env


def http_get(url: str, timeout: float = 2.0) -> tuple[int, bytes]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except (urllib.error.URLError, OSError, ValueError):
        return 0, b""


def http_post(url: str, body: dict | None = None, timeout: float = 5.0) -> tuple[int, bytes]:
    data = json.dumps(body or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, b""
    except (urllib.error.URLError, OSError, ValueError):
        return 0, b""


def healthy(url: str) -> bool:
    status, _ = http_get(url, timeout=1.5)
    return 200 <= status < 300


# --------------------------------------------------------------------------------------
# Supervisor
# --------------------------------------------------------------------------------------


class Supervisor:
    def __init__(self, services: list[Service], *, quiet: bool = False):
        self.services = services
        self.quiet = quiet
        LOGS.mkdir(parents=True, exist_ok=True)

    # -- output helpers --------------------------------------------------------------
    def say(self, msg: str) -> None:
        if not self.quiet:
            print(msg, flush=True)

    # -- lifecycle -------------------------------------------------------------------
    def start(self, svc: Service) -> None:
        """Start one service and block until its health URL answers (or raise BootError)."""
        if healthy(svc.health_url):
            svc.reused = True
            self.say(f"  ~ {svc.name:<10} already healthy on :{svc.port} - reusing it")
            return
        if not port_free(svc.port):
            raise BootError(
                f"port {svc.port} ({svc.name}) is held by another process that is not a healthy RetailSense service.\n"
                f"  Run `python -m retailsense ports` to see the PID, then `python -m retailsense ports --kill`."
            )
        if svc.module != "npm" and not module_available(svc.module):
            msg = (
                f"{svc.name}: python module '{svc.module}' is not installed (expected from {svc.pkg_dir}/).\n"
                f"  Fix: pip install -e {svc.pkg_dir}   or   python -m retailsense setup"
            )
            if svc.required:
                raise BootError(msg)
            self.say(f"  - {svc.name:<10} skipped: {msg.splitlines()[0]}")
            return
        svc.log_path = LOGS / f"{svc.name}.log"
        log = open(svc.log_path, "ab", buffering=0)  # noqa: SIM115 - handed to the child, closed in stop()
        log.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start: {' '.join(svc.cmd)}\n".encode())
        popen_kwargs: dict = {}
        if IS_WIN:
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        else:
            popen_kwargs["start_new_session"] = True
        try:
            svc.proc = subprocess.Popen(
                svc.cmd,
                cwd=str(svc.cwd),
                env=child_env(svc.env),
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                **popen_kwargs,
            )
        except FileNotFoundError as exc:
            log.close()
            raise BootError(f"{svc.name}: cannot launch {svc.cmd[0]!r}: {exc}") from exc
        svc._log_handle = log  # type: ignore[attr-defined]
        self.say(f"  > {svc.name:<10} pid {svc.proc.pid}  ->  {svc.health_url}   (log: {svc.log_path.relative_to(ROOT)})")
        self.wait_healthy(svc)

    def wait_healthy(self, svc: Service) -> None:
        deadline = time.monotonic() + svc.timeout_s
        while time.monotonic() < deadline:
            if healthy(svc.health_url):
                self.say(f"  ok {svc.name:<10} healthy")
                return
            if svc.proc is not None and svc.proc.poll() is not None:
                raise BootError(
                    f"{svc.name} exited with code {svc.proc.returncode} before becoming healthy.\n"
                    f"{self.log_tail(svc)}"
                )
            time.sleep(0.4)
        self.stop(svc)
        raise BootError(f"{svc.name} did not answer {svc.health_url} within {svc.timeout_s:.0f}s.\n{self.log_tail(svc)}")

    def log_tail(self, svc: Service, n: int = 25) -> str:
        if not svc.log_path or not svc.log_path.exists():
            return ""
        lines = svc.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
        return f"--- last {len(lines)} lines of {svc.log_path.relative_to(ROOT)} ---\n" + "\n".join(lines)

    def stop(self, svc: Service) -> None:
        proc = svc.proc
        if proc is None:
            return
        if proc.poll() is None:
            kill_tree(proc)
        handle = getattr(svc, "_log_handle", None)
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass
        svc.proc = None

    def stop_all(self) -> None:
        for svc in reversed(self.services):
            if svc.proc is not None:
                self.say(f"  x stopping {svc.name}")
                self.stop(svc)

    def check_alive(self) -> Service | None:
        """Return the first *required* service that has died, else None."""
        for svc in self.services:
            if svc.required and svc.proc is not None and svc.proc.poll() is not None:
                return svc
        return None


def port_free(port: int) -> bool:
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        try:
            sock.connect(("127.0.0.1", port))
        except OSError:
            return True
        return False


def kill_tree(proc: subprocess.Popen) -> None:
    """Kill ``proc`` and every descendant.

    Windows: ``taskkill /T /F`` walks the tree for us (a plain ``terminate()``
    would leave uvicorn workers / vite children holding the port).  Posix: the
    child was started as a session leader, so ``killpg`` reaches the whole group.
    """
    try:
        if IS_WIN:
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=15,
                check=False,
            )
        else:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except ProcessLookupError:
                return
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    except (OSError, subprocess.SubprocessError):
        try:
            proc.kill()
        except OSError:
            pass


# --------------------------------------------------------------------------------------
# Service table
# --------------------------------------------------------------------------------------


def build_services(args: argparse.Namespace) -> list[Service]:
    cloud_port, edge_port, board_port = args.ports
    cloud_url = f"http://localhost:{cloud_port}"
    py = sys.executable
    services: list[Service] = []

    services.append(
        Service(
            name="cloud",
            module="sensecloud",
            pkg_dir="apps/sensecloud",
            cmd=[py, "-m", "sensecloud", "--port", str(cloud_port)],
            health_url=f"http://127.0.0.1:{cloud_port}/health",
            port=cloud_port,
            env={
                "SENSECLOUD_PORT": str(cloud_port),
                "SENSECLOUD_DB_URL": f"sqlite:///{(VAR / 'sensecloud.db').as_posix()}",
                "SENSECLOUD_DEV": "1",
                "SENSECLOUD_SEED_HISTORY": "1",
                "SENSECLOUD_NOTIFIER": "simulator",
            },
            timeout_s=args.timeout,
        )
    )
    if not args.no_tally:
        services.append(
            Service(
                name="tally",
                module="retailsense_integrations.tally_mock",
                pkg_dir="packages/integrations",
                cmd=[py, "-m", "retailsense_integrations.tally_mock", "--port", str(args.tally_port)],
                health_url=f"http://127.0.0.1:{args.tally_port}/mock/state",
                port=args.tally_port,
                required=False,
                timeout_s=30,
            )
        )
    edge_cmd = [
        py,
        "-m",
        "senseedge",
        "--config",
        str(args.config),
        "--port",
        str(edge_port),
        "--cloud",
        cloud_url,
        "--detector",
        args.detector,
        "--clock",
        str(args.clock),
        "--uplink",
        args.uplink,
    ]
    if args.camera:
        edge_cmd += ["--camera", args.camera]
    services.append(
        Service(
            name="edge",
            module="senseedge",
            pkg_dir="apps/senseedge",
            cmd=edge_cmd,
            health_url=f"http://127.0.0.1:{edge_port}/health",
            port=edge_port,
            env={
                "RS_CONFIG": str(args.config),
                "RS_EDGE_PORT": str(edge_port),
                "RS_CLOUD_URL": cloud_url,
                "RS_CLOCK_FACTOR": str(args.clock),
                "RS_DETECTOR": args.detector,
                "RS_UPLINK": args.uplink,
            },
            timeout_s=args.timeout,
        )
    )
    if not args.no_chain:
        # Headless FakeEdges have no HTTP server; we treat "process still alive after 2 s" as healthy
        # by pointing health at the cloud (already up) and marking them optional.
        for store_id, name in (("STR-MH-002", "Sharma Kirana, Pune"), ("STR-KA-003", "Reddy Stores, Bengaluru")):
            services.append(
                Service(
                    name=f"sim-{store_id[-3:]}",
                    module="retailsense_sim",
                    pkg_dir="packages/sim",
                    cmd=[
                        py,
                        "-m",
                        "retailsense_sim",
                        "headless",
                        "--store-id",
                        store_id,
                        "--name",
                        name,
                        "--cloud",
                        cloud_url,
                        "--clock",
                        str(args.clock),
                    ],
                    health_url=f"http://127.0.0.1:{cloud_port}/health",
                    port=0,
                    required=False,
                    timeout_s=10,
                )
            )
    if not args.no_board:
        npm = shutil.which("npm.cmd") or shutil.which("npm") or "npm"
        services.append(
            Service(
                name="board",
                module="npm",
                pkg_dir="apps/senseboard",
                cmd=[npm, "run", "dev", "--", "--port", str(board_port), "--strictPort"],
                health_url=f"http://127.0.0.1:{board_port}/",
                port=board_port,
                cwd=ROOT / "apps" / "senseboard",
                env={
                    "VITE_EDGE_URL": f"http://localhost:{edge_port}",
                    "VITE_CLOUD_URL": cloud_url,
                    "VITE_STORE_ID": "STR-DL-001",
                    "BROWSER": "none",
                },
                required=False,
                timeout_s=90,
            )
        )
    return services


# --------------------------------------------------------------------------------------
# Banner + smoke checks
# --------------------------------------------------------------------------------------

BEATS = [
    ("0:00", "Zones + calibrate", "PUT /config/zones ; POST /calibrate/shelves/reference-all"),
    ("0:30", "Evening rush -> queue_long alert, tap 1", "POST /demo/scenario {name: evening_rush}"),
    ("0:55", "15-min forecast + MAE badge", "(automatic)"),
    ("1:10", "Amul stockout -> shelf_gap alert in Hindi with Rs", "POST /demo/scenario {name: stockout, params:{shelf_id: shelf-A}}"),
    ("1:35", "Cable kaat do -> offline, backlog climbs", "POST /demo/link {state: down}"),
    ("2:00", "Reconnect -> N/N replayed, seq ordered", "POST /demo/link {state: up}"),
    ("2:15", "Tap 1 = bhar diya -> resolved, Rs saved", "POST /demo/whatsapp/reply ; POST /demo/restock/shelf-A"),
    ("2:30", "Tally 48 vs camera 41 -> shrink Rs189", "POST /v1/stores/STR-DL-001/integrations/tally/reconcile"),
    ("2:45", "Numbers + BMC", "/owner"),
]


def banner(args: argparse.Namespace, services: list[Service]) -> str:
    cloud_port, edge_port, board_port = args.ports
    lines = [
        "",
        "=" * 78,
        "  RetailSense demo is up            (Ctrl+C stops everything)",
        "=" * 78,
        f"  SenseBoard  http://localhost:{board_port}/owner      (dashboard, Hindi default)",
        f"  SenseEdge   http://localhost:{edge_port}/docs        (LAN API, /health, /ws/live)",
        f"  SenseCloud  http://localhost:{cloud_port}/docs        (ingest, KPIs, fleet, WhatsApp outbox)",
        f"  Tally mock  http://localhost:{args.tally_port}/mock/state",
        f"  Logs        {LOGS.relative_to(ROOT)}/*.log",
        "",
        "  Services: " + "  ".join(f"{s.name}={'reused' if s.reused else ('up' if s.running else 'skipped')}" for s in services),
        "",
        "  3-minute beats (full curl fallbacks in docs/DEMO_SCRIPT.md):",
    ]
    for t, what, call in BEATS:
        lines.append(f"   {t}  {what:<48} {call}")
    lines.append("=" * 78)
    return "\n".join(lines)


def smoke_checks(args: argparse.Namespace) -> list[str]:
    """Hit a few endpoints and return a list of failures (empty = green)."""
    cloud_port, edge_port, _ = args.ports
    failures: list[str] = []
    checks = [
        (f"http://127.0.0.1:{cloud_port}/health", "cloud /health"),
        (f"http://127.0.0.1:{edge_port}/health", "edge /health"),
        (f"http://127.0.0.1:{edge_port}/kpis/today", "edge /kpis/today"),
        (f"http://127.0.0.1:{edge_port}/sync", "edge /sync"),
        (f"http://127.0.0.1:{cloud_port}/v1/stores", "cloud /v1/stores"),
    ]
    for url, label in checks:
        status, body = http_get(url, timeout=5)
        if not (200 <= status < 300):
            failures.append(f"{label} -> HTTP {status}")
            continue
        try:
            json.loads(body or b"null")
        except ValueError:
            failures.append(f"{label} -> not JSON")
    if args.scenario and args.scenario != "baseline":
        status, _ = http_post(f"http://127.0.0.1:{edge_port}/demo/scenario", {"name": args.scenario, "params": {}})
        if not (200 <= status < 300):
            failures.append(f"POST /demo/scenario {args.scenario} -> HTTP {status}")
    return failures


# --------------------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------------------


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m retailsense demo",
        description="Boot the full RetailSense stage demo on this machine (no Docker, no internet).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent(
            """
            examples:
              python -m retailsense demo                       # everything, opens the board
              python -m retailsense demo --no-board --no-chain # API only (curl beats)
              python -m retailsense demo --smoke --no-board    # CI gate: boot, verify, exit 0
              python -m retailsense demo --camera webcam:0 --detector onnx
            """
        ),
    )
    p.add_argument("--no-board", action="store_true", help="do not start the Vite dev server")
    p.add_argument("--no-chain", action="store_true", help="do not start the two headless chain stores")
    p.add_argument("--no-tally", action="store_true", help="do not start the Tally XML mock")
    p.add_argument("--camera", default=None, help="extra/override camera spec: synthetic:<scenario> | file:<mp4> | webcam:0 | rtsp://")
    p.add_argument("--detector", default="auto", choices=["auto", "synthetic", "onnx", "ultralytics", "fake"])
    p.add_argument("--clock", type=float, default=10.0, help="simulation clock factor (default 10x)")
    p.add_argument("--scenario", default="baseline", help="scenario to apply after boot (baseline|evening_rush|stockout|...)")
    p.add_argument("--uplink", default="http", choices=["http", "mqtt", "none"])
    p.add_argument("--config", type=Path, default=DEMO_CONFIG, help="store.yaml (default: contracts examples/store_demo.yaml)")
    p.add_argument("--ports", default="8000,8001,5173", help="cloud,edge,board ports")
    p.add_argument("--tally-port", type=int, default=9000)
    p.add_argument("--timeout", type=float, default=60.0, help="seconds to wait for each /health")
    p.add_argument("--smoke", action="store_true", help="boot, verify REST observables, shut down, exit 0/1")
    p.add_argument("--open", action="store_true", help="open the board in the default browser once healthy")
    p.add_argument("--quiet", action="store_true")
    args = p.parse_args(argv)
    try:
        parts = [int(x) for x in args.ports.split(",")]
        if len(parts) != 3:
            raise ValueError
    except ValueError:
        p.error("--ports must be three comma-separated integers: cloud,edge,board")
    args.ports = tuple(parts)
    args.config = args.config if args.config.is_absolute() else (ROOT / args.config)
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    os.environ["PYTHONIOENCODING"] = "utf-8"
    VAR.mkdir(exist_ok=True)
    if not args.config.exists():
        print(f"error: config not found: {args.config}", file=sys.stderr)
        return 2

    services = build_services(args)
    sup = Supervisor(services, quiet=args.quiet)
    sup.say("RetailSense demo supervisor")
    sup.say(f"  root {ROOT}")
    sup.say("  ports " + ", ".join(f"{s.name}:{s.port}" for s in services if s.port))
    t0 = time.monotonic()
    rc = 0
    try:
        for svc in services:
            try:
                sup.start(svc)
            except BootError as exc:
                if svc.required:
                    raise
                sup.say(f"  - {svc.name:<10} optional, continuing: {str(exc).splitlines()[0]}")
        # Headless sims: give them a moment and confirm they did not die immediately.
        for svc in services:
            if svc.name.startswith("sim-") and svc.proc is not None:
                time.sleep(0.5)
                if svc.proc.poll() is not None:
                    sup.say(f"  - {svc.name} exited early (code {svc.proc.returncode}); see {svc.log_path}")
        sup.say(f"  boot took {time.monotonic() - t0:.1f}s")
        if args.scenario and args.scenario != "baseline" and not args.smoke:
            status, _ = http_post(f"http://127.0.0.1:{args.ports[1]}/demo/scenario", {"name": args.scenario, "params": {}})
            sup.say(f"  scenario {args.scenario!r} -> HTTP {status}")
        print(banner(args, services), flush=True)
        if args.smoke:
            failures = smoke_checks(args)
            if failures:
                print("SMOKE FAILED:\n  " + "\n  ".join(failures), file=sys.stderr)
                rc = 1
            else:
                print(f"SMOKE OK in {time.monotonic() - t0:.1f}s", flush=True)
            return rc
        if args.open and not args.no_board:
            webbrowser.open(f"http://localhost:{args.ports[2]}/owner")
        # Supervise until Ctrl+C or a required child dies.
        while True:
            time.sleep(1.0)
            dead = sup.check_alive()
            if dead is not None:
                print(f"\n{dead.name} exited with code {dead.proc.returncode if dead.proc else '?'}; shutting down.", file=sys.stderr)
                print(sup.log_tail(dead), file=sys.stderr)
                rc = 1
                return rc
    except BootError as exc:
        print(f"\nBOOT FAILED: {exc}", file=sys.stderr)
        rc = 1
        return rc
    except KeyboardInterrupt:
        sup.say("\nCtrl+C - shutting down")
        return 0
    finally:
        sup.stop_all()


if __name__ == "__main__":
    sys.exit(main())
