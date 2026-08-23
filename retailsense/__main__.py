"""``python -m retailsense`` - one front door for the whole monorepo.

Design
------
* Every sub-command is a thin dispatcher: it either runs a ``tools/*.py`` script
  in-process (demo, setup, types, video, fetch-models) or spawns a module main
  (``python -m senseedge``, ``python -m sensecloud``, ``python -m retailsense_sim``)
  or an npm script (board).  Nothing here imports product code eagerly, so the
  CLI always works - even on a fresh clone where only the contracts package is
  installed - and missing modules produce a *clear hint* instead of a traceback.
* Unknown arguments after the sub-command are forwarded verbatim to the target
  (``python -m retailsense edge --port 8002`` -> ``python -m senseedge --port 8002``).
* ``PYTHONIOENCODING=utf-8`` is forced for every child so Hindi/₹ strings never
  trip the Windows console code page.

Commands: demo | edge | cloud | sim | board | test | setup | types | video |
fetch-models | lint | ports.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from retailsense import ROOT, __version__

TOOLS = ROOT / "tools"

# sub-command -> (python module to run with -m, pip-installable package dir, human hint)
MODULE_TARGETS: dict[str, tuple[str, str]] = {
    "edge": ("senseedge", "apps/senseedge"),
    "cloud": ("sensecloud", "apps/sensecloud"),
    "sim": ("retailsense_sim", "packages/sim"),
}

# sub-command -> tools script (run in-process so tracebacks stay readable)
TOOL_TARGETS: dict[str, str] = {
    "demo": "demo.py",
    "setup": "setup_dev.py",
    "types": "gen_ts_types.py",
    "video": "make_demo_video.py",
    "fetch-models": "fetch_models.py",
    "ports": "ports.py",
}


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for child processes: UTF-8 I/O + repo root on PYTHONPATH."""
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    pp = env.get("PYTHONPATH", "")
    root = str(ROOT)
    if root not in pp.split(os.pathsep):
        env["PYTHONPATH"] = root + (os.pathsep + pp if pp else "")
    if extra:
        env.update(extra)
    return env


def run(cmd: Sequence[str], *, cwd: Path | None = None, env: dict[str, str] | None = None) -> int:
    """Run ``cmd`` in the foreground and return its exit code (never raises on non-zero)."""
    print(f"$ {' '.join(str(c) for c in cmd)}", flush=True)
    try:
        return subprocess.call(list(cmd), cwd=str(cwd or ROOT), env=env or child_env())
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 127
    except KeyboardInterrupt:
        return 130


def module_available(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def missing_module_hint(module: str, pkg_dir: str) -> int:
    print(
        f"error: Python module '{module}' is not installed.\n"
        f"  It lives in {pkg_dir}/ - if that directory exists run:\n"
        f"    pip install -e {pkg_dir}\n"
        f"  or install everything at once:\n"
        f"    python -m retailsense setup",
        file=sys.stderr,
    )
    return 2


def run_tool(script: str, argv: Sequence[str]) -> int:
    """Execute ``tools/<script>`` in-process as ``__main__`` (keeps tracebacks short)."""
    path = TOOLS / script
    if not path.exists():
        print(
            f"error: {path.relative_to(ROOT)} is not present yet (owned by another module).\n"
            f"  See docs/design/IMPLEMENTATION_SPEC.md section B for who delivers it.",
            file=sys.stderr,
        )
        return 2
    # Isolate sys.argv so the tool's argparse sees only its own arguments.
    saved_argv = sys.argv
    sys.argv = [str(path), *argv]
    try:
        spec = importlib.util.spec_from_file_location(f"retailsense_tool_{path.stem}", path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        try:
            spec.loader.exec_module(module)
        except SystemExit as exc:  # tools call sys.exit(main()) under __main__ guard -> not triggered here
            return int(exc.code or 0)
        main = getattr(module, "main", None)
        if main is None:
            print(f"error: {script} has no main(argv) function", file=sys.stderr)
            return 2
        try:
            rc = main(list(argv))
        except KeyboardInterrupt:
            return 130
        return int(rc or 0)
    finally:
        sys.argv = saved_argv


def cmd_module(name: str, argv: Sequence[str]) -> int:
    module, pkg_dir = MODULE_TARGETS[name]
    if not module_available(module):
        return missing_module_hint(module, pkg_dir)
    return run([sys.executable, "-m", module, *argv])


def npm_exe() -> str | None:
    return shutil.which("npm.cmd") or shutil.which("npm")


def cmd_board(argv: Sequence[str]) -> int:
    board = ROOT / "apps" / "senseboard"
    if not (board / "package.json").exists():
        print("error: apps/senseboard is not present yet (SenseBoard is delivered by the dashboard module).", file=sys.stderr)
        return 2
    npm = npm_exe()
    if npm is None:
        print("error: npm not found on PATH - install Node 22 (https://nodejs.org) and re-run.", file=sys.stderr)
        return 127
    if not (board / "node_modules").exists():
        print("note: node_modules missing - running `npm install` first (python -m retailsense setup does this too)")
        rc = run([npm, "install"], cwd=board)
        if rc:
            return rc
    script = list(argv) or ["run", "dev"]
    return run([npm, *script], cwd=board)


def cmd_test(argv: Sequence[str]) -> int:
    """Root pytest (integration + every package's tests) then vitest when the board exists."""
    extra = list(argv)
    rc = run([sys.executable, "-m", "pytest", *extra] if extra else [sys.executable, "-m", "pytest"])
    board = ROOT / "apps" / "senseboard"
    if rc == 0 and (board / "package.json").exists() and (board / "node_modules").exists():
        npm = npm_exe()
        if npm:
            rc = run([npm, "run", "test", "--", "--run"], cwd=board)
    return rc


def cmd_lint(argv: Sequence[str]) -> int:
    rc = run([sys.executable, "-m", "ruff", "check", ".", *argv])
    board = ROOT / "apps" / "senseboard"
    if (board / "tsconfig.json").exists() and (board / "node_modules").exists():
        npx = shutil.which("npx.cmd") or shutil.which("npx")
        if npx:
            rc = run([npx, "tsc", "--noEmit", "-p", "."], cwd=board) or rc
    return rc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m retailsense",
        description="RetailSense monorepo front door. Quickstart: python -m retailsense setup && python -m retailsense demo",
        epilog=(
            "Arguments after the sub-command are forwarded to the target "
            "(e.g. `python -m retailsense demo --no-board --smoke`, `python -m retailsense edge --port 8002`)."
        ),
    )
    parser.add_argument("--version", action="version", version=f"retailsense {__version__}")
    sub = parser.add_subparsers(dest="command", metavar="command")
    sub.add_parser("demo", help="boot cloud + tally mock + edge + headless sims + board (tools/demo.py)")
    sub.add_parser("edge", help="run SenseEdge (python -m senseedge ...)")
    sub.add_parser("cloud", help="run SenseCloud (python -m sensecloud ...)")
    sub.add_parser("sim", help="synthetic store CLI: video|headless|history (python -m retailsense_sim ...)")
    sub.add_parser("board", help="SenseBoard dev server (npm run dev in apps/senseboard)")
    sub.add_parser("test", help="pytest (root config) then vitest when the board is installed")
    sub.add_parser("setup", help="pip install -e every package + npm install (tools/setup_dev.py)")
    sub.add_parser("types", help="regenerate TS types from pydantic models (tools/gen_ts_types.py)")
    sub.add_parser("video", help="render var/demo_store.mp4 (tools/make_demo_video.py)")
    sub.add_parser("fetch-models", help="export YOLO11n ONNX weights (tools/fetch_models.py)")
    sub.add_parser("lint", help="ruff check . (+ tsc --noEmit when the board is installed)")
    sub.add_parser("ports", help="show which demo ports are free / in use (tools/ports.py)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args, rest = parser.parse_known_args(list(sys.argv[1:] if argv is None else argv))
    cmd = args.command
    if cmd is None:
        parser.print_help()
        return 0
    if cmd in TOOL_TARGETS:
        return run_tool(TOOL_TARGETS[cmd], rest)
    if cmd in MODULE_TARGETS:
        return cmd_module(cmd, rest)
    if cmd == "board":
        return cmd_board(rest)
    if cmd == "test":
        return cmd_test(rest)
    if cmd == "lint":
        return cmd_lint(rest)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
