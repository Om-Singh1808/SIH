"""Port hygiene for the demo supervisor.

RetailSense boots four local servers (SenseCloud :8000, SenseEdge :8001,
SenseBoard :5173, Tally mock :9000).  Before the supervisor starts anything it
wants to know two things per port:

* is it **free** (nothing listening) so a fresh process can bind it, or
* is it **already a healthy RetailSense service** (e.g. the presenter left the
  cloud running from a previous rehearsal) so we can reuse it instead of failing.

Everything here is pure stdlib so it works in CI, on Windows and inside the
Docker images alike.  ``python -m tools.ports`` (or ``python tools/ports.py``)
prints a one-line status per port, which is also what ``retailsense demo``
shows in its banner.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass

#: Canonical port table (spec section E).  Keep in sync with docs/RUNBOOK.md.
DEFAULT_PORTS: dict[str, int] = {
    "cloud": 8000,
    "edge": 8001,
    "board": 5173,
    "tally": 9000,
    "board_nginx": 8080,
    "mqtt": 1883,
    "mqtt_ws": 9001,
    "postgres": 5432,
}


@dataclass(frozen=True)
class PortStatus:
    """Result of probing one TCP port on localhost."""

    name: str
    port: int
    free: bool
    #: best-effort PID of the listener (Windows ``netstat``/posix ``lsof``); None when unknown
    pid: int | None = None
    #: True when the listener answered ``GET /health`` with 2xx (it is one of ours)
    healthy: bool = False

    def describe(self) -> str:
        if self.free:
            return f"{self.name:<12} :{self.port}  free"
        who = f" pid {self.pid}" if self.pid else ""
        state = "healthy RetailSense service" if self.healthy else "IN USE by another process"
        return f"{self.name:<12} :{self.port}  {state}{who}"


def is_free(port: int, host: str = "127.0.0.1") -> bool:
    """True when nothing is listening on ``host:port``.

    We *connect* rather than *bind*: binding can succeed on Windows even while a
    server listens on the wildcard address, whereas a successful connect is an
    unambiguous "someone is there".
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        try:
            sock.connect((host, port))
        except OSError:
            return True
        return False


def health_ok(port: int, path: str = "/health", timeout: float = 1.0) -> bool:
    """True when ``http://127.0.0.1:{port}{path}`` answers with a 2xx status."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        return False


def listener_pid(port: int) -> int | None:
    """Best-effort PID of the process listening on ``port`` (None if unknown)."""
    try:
        if sys.platform == "win32":
            out = subprocess.run(
                ["netstat", "-ano", "-p", "tcp"], capture_output=True, text=True, timeout=5, check=False
            ).stdout
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[0].upper() == "TCP" and parts[1].endswith(f":{port}"):
                    if parts[3].upper() == "LISTENING":
                        return int(parts[4])
        else:
            out = subprocess.run(
                ["lsof", "-t", "-iTCP:%d" % port, "-sTCP:LISTEN"], capture_output=True, text=True, timeout=5, check=False
            ).stdout
            first = out.strip().splitlines()
            if first:
                return int(first[0])
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return None


def probe(name: str, port: int, *, check_health: bool = True) -> PortStatus:
    free = is_free(port)
    if free:
        return PortStatus(name=name, port=port, free=True)
    healthy = health_ok(port) if check_health else False
    return PortStatus(name=name, port=port, free=False, pid=listener_pid(port), healthy=healthy)


def probe_all(ports: dict[str, int] | None = None) -> list[PortStatus]:
    return [probe(name, port) for name, port in (ports or DEFAULT_PORTS).items()]


def free_port(start: int = 20000, end: int = 40000) -> int:
    """Return an ephemeral port the OS reports as free (used by tests that spawn servers)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    if start <= port <= end:
        return port
    return port  # any free port is fine; the range is advisory


def kill_pid(pid: int) -> bool:
    """Terminate ``pid`` and its children (Windows ``taskkill /T /F``; posix SIGKILL)."""
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=10, check=False)
        else:
            import os
            import signal

            os.kill(pid, signal.SIGKILL)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="RetailSense port check")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument("--kill", action="store_true", help="kill non-healthy listeners on our ports (use with care)")
    parser.add_argument("--ports", default=None, help="comma list name=port (default: the spec table)")
    args = parser.parse_args(argv)
    ports = dict(DEFAULT_PORTS)
    if args.ports:
        ports = {}
        for item in args.ports.split(","):
            name, _, value = item.partition("=")
            ports[name.strip() or f"port{value}"] = int(value)
    statuses = probe_all(ports)
    if args.kill:
        for st in statuses:
            if not st.free and not st.healthy and st.pid:
                kill_pid(st.pid)
        statuses = probe_all(ports)
    if args.json:
        print(json.dumps([asdict(s) for s in statuses], indent=2))
    else:
        for st in statuses:
            print(st.describe())
    return 0 if all(s.free or s.healthy for s in statuses) else 1


if __name__ == "__main__":
    raise SystemExit(main())
