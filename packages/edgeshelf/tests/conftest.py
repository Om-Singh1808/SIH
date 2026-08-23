"""pytest fixtures; the shelf renderer lives in ``_render.py`` (importable by tests via sys.path)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from _render import blank_frame, render_shelf  # noqa: E402

from retailsense_contracts.testing import sample_store_config  # noqa: E402

__all__ = ["blank_frame", "render_shelf"]


@pytest.fixture(scope="session")
def cfg():
    return sample_store_config()


@pytest.fixture(scope="session")
def shelves(cfg):
    return {s.shelf_id: s for s in cfg.shelves}
