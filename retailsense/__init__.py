"""RetailSense developer CLI (``python -m retailsense <command>``).

This package contains **no product code** - it is the single front door that
judges, CI and the presenter use to set up, boot, test and lint the monorepo.
All real behaviour lives in the ``packages/*`` and ``apps/*`` projects, reached
through ``retailsense_contracts.registry`` or as subprocesses.
"""

from __future__ import annotations

from pathlib import Path

__version__ = "1.0.0"

#: Repository root (this file lives at ``<root>/retailsense/__init__.py``).
ROOT: Path = Path(__file__).resolve().parent.parent

__all__ = ["ROOT", "__version__"]
