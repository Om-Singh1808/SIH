"""A stand-in Tally server for demos and tests (``python -m retailsense_integrations.tally_mock``).

It speaks exactly the subset of Tally's XML protocol the client uses:

* ``POST /`` with an **Export** envelope -> ``Stock Summary`` or a Sales
  ``Voucher Register`` / ``Day Book`` rendered in TallyPrime's shape.
* ``POST /`` with an **Import** envelope -> applies *Stock Journal* vouchers
  to the in-memory stock, records *Purchase Orders*, and replies with an
  ``IMPORTRESULT`` carrying a ``LASTVCHID``.
* ``GET /mock/state`` / ``PUT /mock/state`` - JSON view/edit of the state so
  the demo (or a judge with curl) can change "what Tally thinks" live.

Default state is the stage scenario: Amul Taaza 500ml **48**, Parle-G 70g
**120**, Fortune Sunflower 1L **18** - so the reconcile beat reads "Tally says
48, camera sees 41".

The server keeps state in a plain dict guarded by a lock; it is a demo aid,
not a database.  ``create_app(initial)`` is the registry entry
(``"tally_mock_app"``) so SenseCloud tests can mount it in-process.
"""

from __future__ import annotations

import argparse
import threading
import time
from datetime import date
from typing import Any

from fastapi import FastAPI, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from . import tally_xml as tx

DEFAULT_ITEMS: dict[str, dict[str, float]] = {
    "Amul Taaza 500ml": {"qty": 48, "rate": 27.0, "sold_today": 61},
    "Parle-G 70g": {"qty": 120, "rate": 10.0, "sold_today": 34},
    "Fortune Sunflower 1L": {"qty": 18, "rate": 150.0, "sold_today": 5},
}


class MockItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    qty: float = 0
    rate: float = 0.0
    sold_today: float = 0


class MockState(BaseModel):
    """JSON body of ``GET/PUT /mock/state``."""

    model_config = ConfigDict(extra="forbid")
    items: dict[str, MockItem] = Field(default_factory=dict)
    company: str = "Ramesh General Store"
    vouchers: list[dict[str, Any]] = Field(default_factory=list)  # imported vouchers (read-only echo)
    sales_today_transactions: int = 70


class TallyMockStore:
    """Thread-safe in-memory Tally.  Used directly by tests; wrapped by the FastAPI app."""

    def __init__(self, initial: dict[str, Any] | None = None):
        self._lock = threading.Lock()
        self._vch_counter = 1000
        self.state = self._coerce(initial)
        self.requests: list[dict[str, Any]] = []  # last N request summaries (debug page)

    @staticmethod
    def _coerce(initial: dict[str, Any] | None) -> MockState:
        if initial is None:
            return MockState(items={k: MockItem(**v) for k, v in DEFAULT_ITEMS.items()})
        if "items" in initial:
            return MockState.model_validate(initial)
        # shorthand: {"Amul Taaza 500ml": 48, ...} or {name: {qty, rate}}
        items = {
            k: (MockItem(qty=float(v)) if isinstance(v, (int, float)) else MockItem(**v)) for k, v in initial.items()
        }
        return MockState(items=items)

    # -- JSON side ------------------------------------------------------------
    def get_state(self) -> MockState:
        with self._lock:
            return self.state.model_copy(deep=True)

    def put_state(self, new: MockState) -> MockState:
        with self._lock:
            self.state = new.model_copy(deep=True)
            return self.state.model_copy(deep=True)

    def reset(self) -> None:
        with self._lock:
            self.state = self._coerce(None)

    # -- XML side -------------------------------------------------------------
    def handle_xml(self, body: bytes) -> tuple[str, int]:
        """Answer one envelope; returns (xml, http_status)."""
        try:
            req = tx.parse_request(body)
        except Exception as exc:  # malformed XML - Tally answers 200 with an error body
            return tx.render_error(f"Invalid XML: {exc}"), 200
        self.requests.append({"ts": time.time(), "kind": req.kind, "report": req.report})
        del self.requests[:-50]
        if req.kind == "export":
            return self._export(req), 200
        if req.kind == "import":
            return self._import(req), 200
        return tx.render_error("Unknown TALLYREQUEST"), 200

    def _export(self, req: tx.ParsedRequest) -> str:
        report = req.report.lower().replace(" ", "")
        with self._lock:
            st = self.state
            if report == "stocksummary":
                return tx.render_stock_summary({k: v.model_dump() for k, v in st.items.items()})
            if report in ("voucherregister", "daybook"):
                if req.voucher_type and req.voucher_type.lower() != "sales":
                    return tx.render_sales_vouchers([])
                return tx.render_sales_vouchers(self._synth_sales(st, req.from_date))
        return tx.render_error(f"Report '{req.report}' not supported by the mock")

    @staticmethod
    def _synth_sales(st: MockState, day: str | None) -> list[tx.SalesVoucher]:
        """Spread ``sold_today`` per item evenly over ``sales_today_transactions`` vouchers.

        Voucher ``i`` receives ``sold // n`` units of every item plus one extra
        unit while ``i < sold % n`` - deterministic and the totals match exactly.
        """
        n = max(1, st.sales_today_transactions)
        d = day or tx.tally_date(date.today())
        vouchers: list[tx.SalesVoucher] = []
        for i in range(n):
            items: dict[str, float] = {}
            amount = 0.0
            for name, item in st.items.items():
                sold = int(item.sold_today)
                q = sold // n + (1 if i < sold % n else 0)
                if q > 0:
                    items[name] = float(q)
                    amount += q * item.rate
            vouchers.append(tx.SalesVoucher(number=f"S{i + 1:04d}", date=d, amount_inr=round(amount, 2), items=items))
        return vouchers

    def _import(self, req: tx.ParsedRequest) -> str:
        created = 0
        last_id: str | None = None
        with self._lock:
            for v in req.vouchers:
                vtype = (v.get("VCHTYPE") or "").strip()
                deltas = tx.voucher_inventory_deltas(v)
                self._vch_counter += 1
                last_id = str(self._vch_counter)
                if vtype.lower() == "stock journal":
                    for name, d in deltas.items():
                        item = self.state.items.setdefault(name, MockItem())
                        item.qty = item.qty + d
                elif vtype.lower() == "purchase order":
                    pass  # orders do not move stock until the goods arrive
                else:
                    return tx.render_import_result(0, None, errors=1, line_error=f"Unsupported voucher type '{vtype}'")
                self.state.vouchers.append({"id": last_id, "type": vtype, "items": deltas, "ts": time.time()})
                created += 1
        return tx.render_import_result(created, last_id)


def create_app(initial: dict[str, Any] | None = None) -> FastAPI:
    """FastAPI app: ``POST /`` (Tally XML), ``GET/PUT /mock/state``, ``POST /mock/reset``, ``GET /health``."""
    store = TallyMockStore(initial)
    app = FastAPI(title="RetailSense Tally mock", version="1.0.0", docs_url="/mock/docs")
    app.state.store = store  # tests reach the store via app.state

    @app.post("/")
    async def tally_endpoint(request: Request) -> Response:
        body = await request.body()
        xml, status = store.handle_xml(body)
        return Response(content=xml, media_type="text/xml", status_code=status)

    @app.get("/mock/state", response_model=MockState)
    def get_state() -> MockState:
        return store.get_state()

    @app.put("/mock/state", response_model=MockState)
    def put_state(state: MockState) -> MockState:
        return store.put_state(state)

    @app.post("/mock/reset", response_model=MockState)
    def reset() -> MockState:
        store.reset()
        return store.get_state()

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok", "service": "tally-mock", "items": len(store.get_state().items)}

    return app


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the Tally mock server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000)
    args = parser.parse_args(argv)
    import uvicorn  # lazy: only needed when actually serving

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    main()


__all__ = ["DEFAULT_ITEMS", "MockItem", "MockState", "TallyMockStore", "create_app", "main"]
