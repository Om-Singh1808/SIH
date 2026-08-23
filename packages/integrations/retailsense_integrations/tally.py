"""``TallyClient`` - the :class:`retailsense_contracts.interfaces.ErpClient` for Tally.

Tally's integration surface is a plain HTTP POST of an XML envelope to the
machine running Tally (``http://<pc>:9000``).  This client is deliberately
synchronous (Tally is a desktop app on the shop PC - one request at a time -
and the calls are made from background tasks, not the request path) and keeps
zero state beyond its ``httpx.Client``.

Why ``httpx`` and an injectable ``transport``: tests drive the exact same
code against :mod:`retailsense_integrations.tally_mock` through
``httpx.ASGITransport`` / ``MockTransport`` - no sockets, no ports.

Failure model: a Tally that is switched off is the *normal* case in a kirana
(the shopkeeper runs it in the evening).  Network errors therefore surface as
:class:`TallyUnavailable` (a ``ConnectionError``) which callers such as the
reconcile endpoint turn into a 503 with a human message instead of a trace.
"""

from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any

import httpx

from retailsense_contracts.api import ReorderSuggestion

from . import tally_xml as tx

log = logging.getLogger("retailsense.integrations.tally")


class TallyError(RuntimeError):
    """Tally answered but refused the request (``ERRORS > 0`` / ``STATUS 0``)."""


class TallyUnavailable(ConnectionError):
    """Could not reach the Tally HTTP server (closed, wrong URL, firewall)."""


class TallyClient:
    """Read stock + sales from Tally and write Stock Journal / Purchase Order vouchers."""

    source = "tally"

    def __init__(
        self,
        url: str = "http://localhost:9000",
        company: str | None = None,
        timeout: float = 3.0,
        *,
        transport: httpx.BaseTransport | None = None,
        item_names: dict[str, str] | None = None,
        supplier: str = tx.DEFAULT_SUPPLIER,
    ) -> None:
        self.url = url.rstrip("/") or url
        self.company = company
        self.timeout = timeout
        self.item_names = dict(item_names or {})  # sku_id -> Tally stock item name
        self.supplier = supplier
        self._client = httpx.Client(
            timeout=timeout, transport=transport, headers={"Content-Type": "text/xml; charset=utf-8"}
        )
        self.last_ok_ts: float | None = None
        self.last_error: str | None = None

    # -- transport ----------------------------------------------------------
    def _post(self, envelope: str) -> str:
        """POST one envelope; return the raw XML reply (``TallyUnavailable`` on network failure)."""
        try:
            r = self._client.post(self.url + "/", content=envelope.encode("utf-8"))
            r.raise_for_status()
        except httpx.HTTPError as exc:  # connect / timeout / 5xx
            self.last_error = f"{type(exc).__name__}: {exc}"
            log.warning("tally unreachable at %s: %s", self.url, self.last_error)
            raise TallyUnavailable(f"Tally at {self.url} unreachable: {self.last_error}") from exc
        self.last_ok_ts = time.time()
        self.last_error = None
        return r.text

    # -- ErpClient protocol -------------------------------------------------
    def stock_summary(self) -> dict[str, int]:
        """``{tally_item_name: closing qty}`` from the Stock Summary report."""
        xml = self._post(tx.build_stock_summary_request(self.company))
        return tx.parse_stock_summary(xml)

    def sales_today(self, day: date | str | None = None) -> dict[str, float]:
        """``{"sales_inr": total, "transactions": n}`` from today's Sales vouchers."""
        xml = self._post(tx.build_sales_request(self.company, day))
        return tx.parse_sales_today(xml)

    def post_stock_journal(self, adjustments: dict[str, int]) -> bool:
        """Write a Stock Journal voucher; ``True`` when Tally reports ``CREATED >= 1`` and no errors."""
        if not any(adjustments.values()):
            return True  # nothing to adjust is a successful no-op
        xml = self._post(tx.build_stock_journal(adjustments, company=self.company))
        res = tx.parse_import_result(xml)
        if not res.ok:
            log.warning("tally stock journal rejected: %s", res)
        return res.ok

    def post_purchase_order(self, lines: list[ReorderSuggestion]) -> str:
        """Write a Purchase Order voucher; returns Tally's voucher id (``LASTVCHID``)."""
        xml = self._post(
            tx.build_purchase_order(lines, company=self.company, supplier=self.supplier, item_names=self.item_names)
        )
        res = tx.parse_import_result(xml)
        if not res.ok:
            raise TallyError(f"purchase order rejected by Tally: {res.line_error or res}")
        return res.last_vch_id or f"PO-{int(time.time())}"

    # -- extras used by the router / status page ----------------------------
    def ping(self) -> bool:
        """Cheap reachability probe (a Stock Summary export)."""
        try:
            self.stock_summary()
            return True
        except (TallyUnavailable, TallyError, ValueError):
            return False

    def status(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "url": self.url,
            "company": self.company,
            "reachable": self.ping(),
            "last_ok_ts": self.last_ok_ts,
            "last_error": self.last_error,
        }

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> TallyClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


__all__ = ["TallyClient", "TallyError", "TallyUnavailable"]
