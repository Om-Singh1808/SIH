"""Loader for ``rules_default.yaml`` (and any edited copy of it).

The YAML mirrors :class:`retailsense_contracts.config.RulesConfig` one-to-one so
the on-stage workflow is "open the file, change a number, restart the edge".
Because ``RulesConfig`` forbids unknown keys, a typo fails at load time instead
of silently being ignored.
"""

from importlib import resources
from pathlib import Path
from typing import Any

import yaml
from retailsense_contracts.config import RulesConfig


def rules_default_path() -> Path:
    """Absolute path of the packaged ``rules_default.yaml``."""
    return Path(str(resources.files(__package__) / "rules_default.yaml"))


def load_rules_yaml(path: str | Path | None = None) -> RulesConfig:
    """Parse a rules YAML into a validated :class:`RulesConfig`.

    Accepts either a flat mapping of rule fields, or a document with a top-level
    ``rules:`` mapping (so a full ``store.yaml`` can be pointed at as well).
    ``path=None`` loads the packaged defaults.
    """
    p = Path(path) if path is not None else rules_default_path()
    with p.open("r", encoding="utf-8") as fh:
        doc: Any = yaml.safe_load(fh) or {}
    if not isinstance(doc, dict):
        raise ValueError(f"{p}: expected a mapping at top level, got {type(doc).__name__}")
    if "rules" in doc and isinstance(doc["rules"], dict):
        doc = doc["rules"]
    return RulesConfig.model_validate(doc)


__all__ = ["load_rules_yaml", "rules_default_path"]
