"""Developer / judge setup: ``python -m retailsense setup``.

Installs every Python project in the monorepo in editable mode (so the registry
picks up real implementations the moment they exist), installs the dashboard's
npm dependencies, and optionally fetches the YOLO11n ONNX weights.

Order matters: ``packages/contracts`` goes first because every other project
depends on it; the rest are discovered dynamically by scanning ``packages/*``
and ``apps/*`` for a ``pyproject.toml`` - a new module needs no edit here.

Flags::

    --no-npm      skip `npm install` in apps/senseboard
    --models      also run tools/fetch_models.py (needs internet once)
    --dev         also install the root [dev] extras (pytest, ruff, httpx ...)
    --dry-run     print what would run
    --only NAME   install only the project directory named NAME (e.g. sim)
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "packages" / "contracts"


def discover_projects() -> list[Path]:
    """Python project dirs with a pyproject.toml: contracts first, then packages/*, apps/*, root."""
    found: list[Path] = []
    if (CONTRACTS / "pyproject.toml").exists():
        found.append(CONTRACTS)
    for parent in ("packages", "apps"):
        base = ROOT / parent
        if not base.exists():
            continue
        for child in sorted(base.iterdir()):
            if child == CONTRACTS or not child.is_dir():
                continue
            if (child / "pyproject.toml").exists():
                found.append(child)
    return found


def run(cmd: list[str], *, cwd: Path, dry_run: bool) -> int:
    rel = cwd.relative_to(ROOT) if cwd != ROOT else Path(".")
    print(f"[{rel}] $ {' '.join(cmd)}", flush=True)
    if dry_run:
        return 0
    env = dict(os.environ, PYTHONIOENCODING="utf-8", PIP_DISABLE_PIP_VERSION_CHECK="1")
    return subprocess.call(cmd, cwd=str(cwd), env=env)


def pip_install_editable(project: Path, *, dry_run: bool, extras: str | None = None) -> int:
    target = f"{project}[{extras}]" if extras else str(project)
    return run([sys.executable, "-m", "pip", "install", "-e", target], cwd=ROOT, dry_run=dry_run)


def npm_install(board: Path, *, dry_run: bool) -> int:
    npm = shutil.which("npm.cmd") or shutil.which("npm")
    if npm is None:
        print("warning: npm not found - skipping SenseBoard dependencies (install Node 22 to run the dashboard)")
        return 0
    use_ci = (board / "package-lock.json").exists() and os.environ.get("CI")
    return run([npm, "ci" if use_ci else "install"], cwd=board, dry_run=dry_run)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m retailsense setup", description=__doc__.split("\n\n")[0])
    p.add_argument("--no-npm", action="store_true")
    p.add_argument("--models", action="store_true", help="also export YOLO11n weights via tools/fetch_models.py")
    p.add_argument("--dev", action="store_true", help="also install root [dev] extras")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--only", default=None, help="install only this project dir name (e.g. sim, senseedge)")
    args = p.parse_args(argv)

    projects = discover_projects()
    if args.only:
        projects = [pr for pr in projects if pr.name == args.only]
        if not projects:
            print(f"error: no project named {args.only!r}", file=sys.stderr)
            return 2
    print(f"Installing {len(projects)} Python project(s) in editable mode:")
    for pr in projects:
        print(f"  - {pr.relative_to(ROOT)}")
    failures: list[str] = []
    for pr in projects:
        if pip_install_editable(pr, dry_run=args.dry_run):
            failures.append(str(pr.relative_to(ROOT)))
    if args.dev and (ROOT / "pyproject.toml").exists():
        if run([sys.executable, "-m", "pip", "install", "-e", ".[dev]"], cwd=ROOT, dry_run=args.dry_run):
            failures.append("root [dev] extras")

    board = ROOT / "apps" / "senseboard"
    if not args.no_npm and (board / "package.json").exists():
        if npm_install(board, dry_run=args.dry_run):
            failures.append("npm install (apps/senseboard)")
    elif not args.no_npm:
        print("note: apps/senseboard not present yet - skipping npm install")

    if args.models:
        fetch = ROOT / "tools" / "fetch_models.py"
        if fetch.exists():
            if run([sys.executable, str(fetch)], cwd=ROOT, dry_run=args.dry_run):
                failures.append("fetch_models")
        else:
            print("note: tools/fetch_models.py not present - synthetic detector is the default anyway")

    (ROOT / "var" / "logs").mkdir(parents=True, exist_ok=True)
    if failures:
        print("\nSetup finished with failures:\n  - " + "\n  - ".join(failures), file=sys.stderr)
        return 1
    print("\nSetup complete. Next: python -m retailsense demo")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
