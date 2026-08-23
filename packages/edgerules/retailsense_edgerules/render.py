"""Bilingual alert text rendering.

Every alert is rendered into Hindi **and** English at raise time on the edge,
using ``retailsense_contracts.i18n.render`` so the templates stay the single
source of truth.  Pre-rendering means the phone panel, the WhatsApp simulator
and the cloud all show identical text even when the edge is offline.

The only thing that differs between the two languages is the parameter set:
SKU names have a Hindi form (``SKU.name_hi``); counters/cameras do not.  The
``AlertTexts`` record carries the four rendered strings that ``Alert`` needs.
"""

from dataclasses import dataclass
from typing import Any

from retailsense_contracts.config import SKU
from retailsense_contracts.enums import AlertKind, Lang
from retailsense_contracts.i18n import render


@dataclass(frozen=True)
class AlertTexts:
    title_en: str
    title_hi: str
    message_en: str
    message_hi: str


def render_alert(kind: AlertKind, params_en: dict[str, Any], params_hi: dict[str, Any] | None = None) -> AlertTexts:
    """Render ``{kind}.title`` / ``{kind}.msg`` in both languages.

    ``params_hi`` defaults to ``params_en``; pass a different mapping when some
    parameter (the SKU name) has a Hindi form.
    """
    ph = params_en if params_hi is None else params_hi
    k = str(kind)
    return AlertTexts(
        title_en=render(f"{k}.title", Lang.EN, **params_en),
        title_hi=render(f"{k}.title", Lang.HI, **ph),
        message_en=render(f"{k}.msg", Lang.EN, **params_en),
        message_hi=render(f"{k}.msg", Lang.HI, **ph),
    )


def sku_names(sku: SKU | None, fallback: str) -> tuple[str, str]:
    """``(name_en, name_hi)`` for a SKU, both falling back to ``fallback`` (shelf name) when unmapped."""
    if sku is None:
        return fallback, fallback
    return sku.name_en, sku.name_hi or sku.name_en


def bilingual(params: dict[str, Any], **hi_overrides: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split one parameter dict into (en, hi) with Hindi-specific overrides applied."""
    return params, {**params, **hi_overrides}


__all__ = ["AlertTexts", "bilingual", "render_alert", "sku_names"]
