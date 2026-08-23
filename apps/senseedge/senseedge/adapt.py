"""Signature-driven construction of registry implementations.

Why this exists: the contracts freeze the *Protocols* (what an object can do)
but not every constructor (how it is built).  Real packages take a
``CameraConfig`` or a ``StoreConfig``; the contracts fakes take
``n_frames``/``size``/``camera_id`` keyword arguments.  Rather than hard-coding
one shape and breaking the other, ``build()`` inspects the callable's signature
and passes only the keyword arguments it actually declares, from a pool of
everything the wiring layer knows.  Required parameters that cannot be
satisfied from the pool raise a clear ``WiringError`` at boot instead of an
obscure ``TypeError`` deep inside a thread.

Objects that are not callable (tests may inject a ready-made instance through
``create_app(overrides=...)``) are returned as-is.
"""

from __future__ import annotations

import inspect
from typing import Any


class WiringError(RuntimeError):
    """A registry implementation could not be constructed from the known pool."""


def build(target: Any, pool: dict[str, Any], *, positional: tuple[Any, ...] = ()) -> Any:
    """Instantiate ``target`` (class or factory) using only the pool entries its signature names.

    ``positional`` is passed first, verbatim, for Protocols whose constructor is
    *fixed* by the contracts (e.g. ``ZoneEngine(camera, zones, lines, mapper, rules, floorplan)``).
    """
    if not callable(target):
        return target
    if positional:
        return target(*positional)
    try:
        sig = inspect.signature(target)
    except (TypeError, ValueError):
        return target()
    kwargs: dict[str, Any] = {}
    missing: list[str] = []
    for name, param in sig.parameters.items():
        if param.kind in (param.VAR_POSITIONAL, param.VAR_KEYWORD):
            continue
        if name in pool:
            kwargs[name] = pool[name]
        elif param.default is param.empty:
            missing.append(name)
    if missing:
        raise WiringError(f"cannot construct {_name(target)}: missing required parameters {missing}")
    return target(**kwargs)


def _name(obj: Any) -> str:
    return getattr(obj, "__qualname__", None) or getattr(obj, "__name__", None) or repr(obj)
