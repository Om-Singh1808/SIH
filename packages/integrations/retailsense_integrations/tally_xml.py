"""Pure builders and parsers for Tally's native XML-over-HTTP interface.

Tally (ERP 9 / TallyPrime) exposes an HTTP server (default port 9000) that
accepts a single ``<ENVELOPE>`` document per ``POST /``.  Two families matter
for RetailSense:

* **Export** (``<TALLYREQUEST>Export Data</TALLYREQUEST>``) - read a report.
  We use ``Stock Summary`` (closing quantities per stock item) and
  ``Voucher Register`` filtered to *Sales* vouchers (today's takings).
* **Import** (``<TALLYREQUEST>Import Data</TALLYREQUEST>``) - write vouchers.
  We post *Stock Journal* vouchers (shrink adjustments after a reconcile) and
  *Purchase Order* vouchers (reorder suggestions).

The element names, nesting and quirks (quantities like ``" 48 Nos"``, dates
as ``YYYYMMDD``, ``ISDEEMEDPOSITIVE``, ``.LIST`` suffixes) mirror what a real
TallyPrime instance emits, so the same client works against the mock in
``tally_mock.py`` and a real Tally on the shopkeeper's PC.

Everything here is side-effect free: strings in, strings/dicts out.  The
client (``tally.py``) and the mock (``tally_mock.py``) are thin layers on top.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from xml.sax.saxutils import escape

from retailsense_contracts.api import ReorderSuggestion

DEFAULT_UNIT = "Nos"
DEFAULT_GODOWN = "Main Location"
DEFAULT_BATCH = "Primary Batch"
DEFAULT_SUPPLIER = "Distributor"

# TallyRequest verbs.  TallyPrime documents "Export Data"/"Import Data";
# older builds also accept the short forms, so parsers treat both as equal.
EXPORT_REQUESTS = ("Export Data", "Export")
IMPORT_REQUESTS = ("Import Data", "Import")

_INVENTORY_TAGS = (
    "INVENTORYENTRIESIN.LIST",
    "INVENTORYENTRIESOUT.LIST",
    "ALLINVENTORYENTRIES.LIST",
    "INVENTORYENTRIES.LIST",
)


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def tally_date(d: date | str | None = None) -> str:
    """Tally wants ``YYYYMMDD``.  Accepts ISO strings or ``date`` objects."""
    if d is None:
        d = date.today()
    if isinstance(d, str):
        return d.replace("-", "")
    return d.strftime("%Y%m%d")


_QTY_RE = re.compile(r"^\s*(-?[\d,]*\.?\d+)")


def parse_qty(text: str | None) -> float:
    """``" 48 Nos"`` -> 48.0; ``"-2.5 Ltr"`` -> -2.5; ``"1,200 Nos"`` -> 1200.0; blank -> 0."""
    if not text:
        return 0.0
    m = _QTY_RE.match(text.replace(" ", " "))
    if not m:
        return 0.0
    return float(m.group(1).replace(",", ""))


def parse_amount(text: str | None) -> float:
    """Tally amounts: plain decimals, sometimes with thousands separators or a trailing ``Dr``/``Cr``."""
    if not text:
        return 0.0
    m = re.search(r"-?[\d,]*\.?\d+", text)
    return float(m.group(0).replace(",", "")) if m else 0.0


def fmt_qty(qty: float | int, unit: str = DEFAULT_UNIT) -> str:
    """Inverse of :func:`parse_qty`: ``7 -> " 7 Nos"`` (Tally pads with a leading space)."""
    q = int(qty) if float(qty).is_integer() else qty
    return f" {q} {unit}"


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _text(el: ET.Element | None, path: str, default: str = "") -> str:
    if el is None:
        return default
    found = el.find(path)
    if found is None or found.text is None:
        return default
    return found.text


def _tree(xml: str | bytes) -> ET.Element:
    data = xml if isinstance(xml, bytes) else xml.encode("utf-8")
    # Tally exports occasionally contain control characters / bare ampersands; be lenient.
    try:
        return ET.fromstring(data)
    except ET.ParseError:
        cleaned = re.sub(rb"&(?![a-zA-Z#]+;)", b"&amp;", data)
        cleaned = re.sub(rb"[\x00-\x08\x0b\x0c\x0e-\x1f]", b"", cleaned)
        return ET.fromstring(cleaned)


# ---------------------------------------------------------------------------
# request envelopes
# ---------------------------------------------------------------------------


def _static_vars(company: str | None, extra: Iterable[tuple[str, str]] = ()) -> str:
    parts = ["<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>"]
    if company:
        parts.append(f"<SVCURRENTCOMPANY>{escape(company)}</SVCURRENTCOMPANY>")
    for k, v in extra:
        parts.append(f"<{k}>{escape(v)}</{k}>")
    return "<STATICVARIABLES>" + "".join(parts) + "</STATICVARIABLES>"


def build_export_request(
    report: str,
    company: str | None = None,
    *,
    from_date: date | str | None = None,
    to_date: date | str | None = None,
    voucher_type: str | None = None,
) -> str:
    """``Export Data`` envelope for a named report (e.g. ``"Stock Summary"``, ``"Voucher Register"``).

    ``from_date``/``to_date`` populate ``SVFROMDATE``/``SVTODATE`` (required for
    date-ranged reports such as the voucher register); ``voucher_type`` adds
    ``VOUCHERTYPENAME`` so the register is filtered server-side.
    """
    extra: list[tuple[str, str]] = []
    if from_date is not None:
        extra.append(("SVFROMDATE", tally_date(from_date)))
    if to_date is not None:
        extra.append(("SVTODATE", tally_date(to_date)))
    if voucher_type:
        extra.append(("VOUCHERTYPENAME", voucher_type))
    return (
        "<ENVELOPE>"
        "<HEADER><TALLYREQUEST>Export Data</TALLYREQUEST></HEADER>"
        "<BODY><EXPORTDATA><REQUESTDESC>"
        f"<REPORTNAME>{escape(report)}</REPORTNAME>"
        f"{_static_vars(company, extra)}"
        "</REQUESTDESC></EXPORTDATA></BODY>"
        "</ENVELOPE>"
    )


def build_stock_summary_request(company: str | None = None) -> str:
    return build_export_request("Stock Summary", company)


def build_sales_request(company: str | None = None, day: date | str | None = None) -> str:
    """Voucher Register for one day filtered to Sales vouchers."""
    d = day or date.today()
    return build_export_request("Voucher Register", company, from_date=d, to_date=d, voucher_type="Sales")


def _import_envelope(company: str | None, voucher_xml: str) -> str:
    return (
        "<ENVELOPE>"
        "<HEADER><TALLYREQUEST>Import Data</TALLYREQUEST></HEADER>"
        "<BODY><IMPORTDATA><REQUESTDESC>"
        "<REPORTNAME>Vouchers</REPORTNAME>"
        f"{_static_vars(company)}"
        "</REQUESTDESC><REQUESTDATA>"
        f'<TALLYMESSAGE xmlns:UDF="TallyUDF">{voucher_xml}</TALLYMESSAGE>'
        "</REQUESTDATA></IMPORTDATA></BODY>"
        "</ENVELOPE>"
    )


def _inventory_entry(
    list_tag: str, item: str, qty: float, rate: float | None, unit: str, godown: str, batch: str
) -> str:
    q = abs(qty)
    amount = round(q * rate, 2) if rate is not None else 0.0
    rate_xml = f"<RATE>{rate:.2f}/{unit}</RATE>" if rate is not None else ""
    qty_xml = f"<ACTUALQTY>{fmt_qty(q, unit)}</ACTUALQTY><BILLEDQTY>{fmt_qty(q, unit)}</BILLEDQTY>"
    return (
        f"<{list_tag}>"
        f"<STOCKITEMNAME>{escape(item)}</STOCKITEMNAME>"
        "<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>"
        f"{rate_xml}<AMOUNT>{amount:.2f}</AMOUNT>"
        f"{qty_xml}"
        "<BATCHALLOCATIONS.LIST>"
        f"<GODOWNNAME>{escape(godown)}</GODOWNNAME><BATCHNAME>{escape(batch)}</BATCHNAME>"
        f"{qty_xml}<AMOUNT>{amount:.2f}</AMOUNT>"
        "</BATCHALLOCATIONS.LIST>"
        f"</{list_tag}>"
    )


def build_stock_journal(
    adjustments: dict[str, int | float],
    *,
    company: str | None = None,
    day: date | str | None = None,
    narration: str = "RetailSense shrink reconciliation",
    rates: dict[str, float] | None = None,
    unit: str = DEFAULT_UNIT,
    godown: str = DEFAULT_GODOWN,
    batch: str = DEFAULT_BATCH,
) -> str:
    """``Import Data`` envelope carrying one *Stock Journal* voucher.

    ``adjustments`` maps Tally stock item name -> signed delta: negative
    quantities are written out (``INVENTORYENTRIESOUT.LIST`` - stock consumed /
    shrink), positive ones are written in (``INVENTORYENTRIESIN.LIST``).
    """
    rates = rates or {}
    entries: list[str] = []
    for item, delta in adjustments.items():
        if not delta:
            continue
        tag = "INVENTORYENTRIESIN.LIST" if delta > 0 else "INVENTORYENTRIESOUT.LIST"
        entries.append(_inventory_entry(tag, item, delta, rates.get(item), unit, godown, batch))
    voucher = (
        '<VOUCHER VCHTYPE="Stock Journal" ACTION="Create">'
        f"<DATE>{tally_date(day)}</DATE>"
        "<VOUCHERTYPENAME>Stock Journal</VOUCHERTYPENAME>"
        f"<NARRATION>{escape(narration)}</NARRATION>"
        f"{''.join(entries)}"
        "</VOUCHER>"
    )
    return _import_envelope(company, voucher)


def build_purchase_order(
    lines: list[ReorderSuggestion],
    *,
    company: str | None = None,
    day: date | str | None = None,
    supplier: str = DEFAULT_SUPPLIER,
    item_names: dict[str, str] | None = None,
    unit: str = DEFAULT_UNIT,
    reference: str | None = None,
) -> str:
    """``Import Data`` envelope carrying one *Purchase Order* voucher.

    ``item_names`` maps ``sku_id`` -> Tally stock item name (from
    ``SKU.tally_item_name``); SKUs without a mapping use ``name_en``.
    Rate is derived from ``est_cost_inr / suggest_qty``; zero-qty lines are skipped.
    """
    item_names = item_names or {}
    entries: list[str] = []
    total = 0.0
    for ln in lines:
        if ln.suggest_qty <= 0:
            continue
        name = item_names.get(ln.sku_id, ln.name_en)
        rate = ln.est_cost_inr / ln.suggest_qty
        amount = round(ln.est_cost_inr, 2)
        total += amount
        qty_xml = f"<ACTUALQTY>{fmt_qty(ln.suggest_qty, unit)}</ACTUALQTY><BILLEDQTY>{fmt_qty(ln.suggest_qty, unit)}</BILLEDQTY>"
        entries.append(
            "<ALLINVENTORYENTRIES.LIST>"
            f"<STOCKITEMNAME>{escape(name)}</STOCKITEMNAME>"
            "<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>"
            f"<RATE>{rate:.2f}/{unit}</RATE><AMOUNT>-{amount:.2f}</AMOUNT>"
            f"{qty_xml}"
            f"<NARRATION>{escape(ln.reason)}</NARRATION>"
            "</ALLINVENTORYENTRIES.LIST>"
        )
    ref_xml = f"<REFERENCE>{escape(reference)}</REFERENCE>" if reference else ""
    voucher = (
        '<VOUCHER VCHTYPE="Purchase Order" ACTION="Create">'
        f"<DATE>{tally_date(day)}</DATE>"
        "<VOUCHERTYPENAME>Purchase Order</VOUCHERTYPENAME>"
        f"<PARTYLEDGERNAME>{escape(supplier)}</PARTYLEDGERNAME>"
        f"{ref_xml}"
        "<NARRATION>RetailSense reorder suggestion</NARRATION>"
        f"{''.join(entries)}"
        "<LEDGERENTRIES.LIST>"
        f"<LEDGERNAME>{escape(supplier)}</LEDGERNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>"
        f"<AMOUNT>{total:.2f}</AMOUNT>"
        "</LEDGERENTRIES.LIST>"
        "</VOUCHER>"
    )
    return _import_envelope(company, voucher)


# ---------------------------------------------------------------------------
# response parsers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StockLine:
    name: str
    qty: float
    rate: float | None = None
    value: float | None = None


def parse_stock_summary_lines(xml: str | bytes) -> list[StockLine]:
    """Parse a ``Stock Summary`` export into lines (name, closing qty, rate, value).

    Tally emits sibling pairs ``<DSPACCNAME><DSPDISPNAME>..`` and
    ``<DSPSTKINFO><DSPSTKCL><DSPCLQTY>..``; we also accept the collection-style
    ``<STOCKITEM NAME=".."><CLOSINGBALANCE>`` shape some TDL exports use.
    """
    root = _tree(xml)
    lines: list[StockLine] = []
    pending_name: str | None = None
    for el in root.iter():
        tag = _strip_ns(el.tag)
        if tag == "DSPACCNAME":
            pending_name = _text(el, "DSPDISPNAME").strip() or None
        elif tag == "DSPSTKINFO" and pending_name is not None:
            cl = el.find("DSPSTKCL")
            rate_txt = _text(cl, "DSPCLRATE")
            value_txt = _text(cl, "DSPCLAMTA")
            lines.append(
                StockLine(
                    name=pending_name,
                    qty=parse_qty(_text(cl, "DSPCLQTY")),
                    rate=parse_amount(rate_txt) if rate_txt else None,
                    value=parse_amount(value_txt) if value_txt else None,
                )
            )
            pending_name = None
        elif tag == "STOCKITEM":
            name = (el.get("NAME") or _text(el, "NAME")).strip()
            if name:
                lines.append(
                    StockLine(
                        name=name,
                        qty=parse_qty(_text(el, "CLOSINGBALANCE")),
                        rate=parse_amount(_text(el, "CLOSINGRATE")) or None,
                        value=parse_amount(_text(el, "CLOSINGVALUE")) or None,
                    )
                )
    return lines


def parse_stock_summary(xml: str | bytes) -> dict[str, int]:
    """``{stock item name: closing quantity}`` - the :class:`ErpClient` shape."""
    return {ln.name: int(round(ln.qty)) for ln in parse_stock_summary_lines(xml)}


@dataclass(frozen=True)
class SalesVoucher:
    number: str
    date: str  # YYYYMMDD
    amount_inr: float
    items: dict[str, float]  # stock item -> qty


def parse_sales_vouchers(xml: str | bytes) -> list[SalesVoucher]:
    """Parse a Voucher Register / Day Book export into Sales vouchers."""
    root = _tree(xml)
    out: list[SalesVoucher] = []
    for v in root.iter():
        if _strip_ns(v.tag) != "VOUCHER":
            continue
        vtype = (v.get("VCHTYPE") or _text(v, "VOUCHERTYPENAME")).strip().lower()
        if vtype and vtype != "sales":
            continue
        items: dict[str, float] = {}
        amount = 0.0
        for inv in v.findall("ALLINVENTORYENTRIES.LIST") + v.findall("INVENTORYENTRIES.LIST"):
            name = _text(inv, "STOCKITEMNAME").strip()
            if name:
                items[name] = items.get(name, 0.0) + abs(parse_qty(_text(inv, "ACTUALQTY")))
            amount += abs(parse_amount(_text(inv, "AMOUNT")))
        if amount == 0.0:
            # fall back to the party ledger entry (services / non-inventory sales)
            for led in v.findall("LEDGERENTRIES.LIST") + v.findall("ALLLEDGERENTRIES.LIST"):
                if _text(led, "ISPARTYLEDGER").strip().lower() == "yes":
                    amount = abs(parse_amount(_text(led, "AMOUNT")))
                    break
        out.append(
            SalesVoucher(
                number=_text(v, "VOUCHERNUMBER").strip(),
                date=_text(v, "DATE").strip(),
                amount_inr=round(amount, 2),
                items=items,
            )
        )
    return out


def parse_sales_today(xml: str | bytes) -> dict[str, float]:
    """``{"sales_inr": .., "transactions": ..}`` - the :class:`ErpClient.sales_today` shape."""
    vouchers = parse_sales_vouchers(xml)
    return {
        "sales_inr": round(sum(v.amount_inr for v in vouchers), 2),
        "transactions": float(len(vouchers)),
    }


@dataclass(frozen=True)
class ImportResult:
    created: int
    altered: int
    errors: int
    last_vch_id: str | None
    line_error: str | None = None

    @property
    def ok(self) -> bool:
        return self.errors == 0 and (self.created + self.altered) > 0


def parse_import_result(xml: str | bytes) -> ImportResult:
    """Parse Tally's ``IMPORTRESULT`` (``CREATED``/``ALTERED``/``ERRORS``/``LASTVCHID``)."""
    root = _tree(xml)
    res = None
    for el in root.iter():
        if _strip_ns(el.tag) in ("IMPORTRESULT", "RESPONSE"):
            res = el
            break
    if res is None:
        line_error = _text(root, "BODY/DATA/LINEERROR").strip() or "no IMPORTRESULT in response"
        return ImportResult(created=0, altered=0, errors=1, last_vch_id=None, line_error=line_error)
    return ImportResult(
        created=int(parse_amount(_text(res, "CREATED", "0"))),
        altered=int(parse_amount(_text(res, "ALTERED", "0"))),
        errors=int(parse_amount(_text(res, "ERRORS", "0"))),
        last_vch_id=(_text(res, "LASTVCHID").strip() or None),
        line_error=(_text(res, "LINEERROR").strip() or None),
    )


# ---------------------------------------------------------------------------
# request parsers (used by the mock server; handy for tests too)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParsedRequest:
    kind: str  # "export" | "import" | "unknown"
    report: str
    company: str | None
    voucher_type: str | None
    from_date: str | None
    to_date: str | None
    vouchers: list[ET.Element]


def parse_request(xml: str | bytes) -> ParsedRequest:
    """Classify an incoming envelope and pull out the bits the mock needs."""
    root = _tree(xml)
    req = _text(root, "HEADER/TALLYREQUEST").strip()
    if req in EXPORT_REQUESTS:
        kind, desc = "export", root.find("BODY/EXPORTDATA/REQUESTDESC")
    elif req in IMPORT_REQUESTS:
        kind, desc = "import", root.find("BODY/IMPORTDATA/REQUESTDESC")
    else:
        kind, desc = "unknown", None
    sv = desc.find("STATICVARIABLES") if desc is not None else None
    vouchers = [el for el in root.iter() if _strip_ns(el.tag) == "VOUCHER"]
    return ParsedRequest(
        kind=kind,
        report=_text(desc, "REPORTNAME").strip(),
        company=_text(sv, "SVCURRENTCOMPANY").strip() or None,
        voucher_type=_text(sv, "VOUCHERTYPENAME").strip() or None,
        from_date=_text(sv, "SVFROMDATE").strip() or None,
        to_date=_text(sv, "SVTODATE").strip() or None,
        vouchers=vouchers,
    )


def voucher_inventory_deltas(voucher: ET.Element) -> dict[str, float]:
    """Signed stock deltas from a voucher: IN/ALL entries add, OUT entries subtract.

    Used by the mock to apply Stock Journals (and to turn Purchase Orders into
    pending orders without touching stock).
    """
    deltas: dict[str, float] = {}
    for child in voucher:
        tag = _strip_ns(child.tag)
        if tag not in _INVENTORY_TAGS:
            continue
        name = _text(child, "STOCKITEMNAME").strip()
        if not name:
            continue
        qty = abs(parse_qty(_text(child, "ACTUALQTY")))
        sign = -1.0 if tag == "INVENTORYENTRIESOUT.LIST" else 1.0
        deltas[name] = deltas.get(name, 0.0) + sign * qty
    return deltas


# ---------------------------------------------------------------------------
# response builders (the mock's side; real Tally produces the same shapes)
# ---------------------------------------------------------------------------


def render_stock_summary(items: dict[str, dict[str, float]], unit: str = DEFAULT_UNIT) -> str:
    """Render ``{name: {qty, rate}}`` as a Tally ``Stock Summary`` export."""
    parts = ["<ENVELOPE>"]
    for name, info in items.items():
        qty = float(info.get("qty", 0))
        rate = float(info.get("rate", 0.0))
        parts.append(
            "<DSPACCNAME>"
            f"<DSPDISPNAME>{escape(name)}</DSPDISPNAME>"
            "</DSPACCNAME>"
            "<DSPSTKINFO><DSPSTKCL>"
            f"<DSPCLQTY>{fmt_qty(qty, unit)}</DSPCLQTY>"
            f"<DSPCLRATE>{rate:.2f}/{unit}</DSPCLRATE>"
            f"<DSPCLAMTA>{qty * rate:.2f}</DSPCLAMTA>"
            "</DSPSTKCL></DSPSTKINFO>"
        )
    parts.append("</ENVELOPE>")
    return "".join(parts)


def render_sales_vouchers(vouchers: list[SalesVoucher], unit: str = DEFAULT_UNIT) -> str:
    """Render Sales vouchers the way a Voucher Register export looks."""
    parts = ["<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER><BODY><DATA>"]
    for v in vouchers:
        parts.append(
            '<TALLYMESSAGE xmlns:UDF="TallyUDF"><VOUCHER VCHTYPE="Sales" ACTION="Create">'
            f"<DATE>{v.date}</DATE><VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>"
            f"<VOUCHERNUMBER>{escape(v.number)}</VOUCHERNUMBER><PARTYLEDGERNAME>Cash</PARTYLEDGERNAME>"
        )
        total_qty = sum(v.items.values()) or 1.0
        for name, qty in v.items.items():
            share = v.amount_inr * (qty / total_qty)
            parts.append(
                "<ALLINVENTORYENTRIES.LIST>"
                f"<STOCKITEMNAME>{escape(name)}</STOCKITEMNAME><ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>"
                f"<ACTUALQTY>{fmt_qty(qty, unit)}</ACTUALQTY><BILLEDQTY>{fmt_qty(qty, unit)}</BILLEDQTY>"
                f"<AMOUNT>{share:.2f}</AMOUNT>"
                "</ALLINVENTORYENTRIES.LIST>"
            )
        parts.append(
            "<LEDGERENTRIES.LIST><LEDGERNAME>Cash</LEDGERNAME><ISPARTYLEDGER>Yes</ISPARTYLEDGER>"
            f"<ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE><AMOUNT>-{v.amount_inr:.2f}</AMOUNT></LEDGERENTRIES.LIST>"
            "</VOUCHER></TALLYMESSAGE>"
        )
    parts.append("</DATA></BODY></ENVELOPE>")
    return "".join(parts)


def render_import_result(
    created: int, last_vch_id: str | None, errors: int = 0, line_error: str | None = None
) -> str:
    err_xml = f"<LINEERROR>{escape(line_error)}</LINEERROR>" if line_error else ""
    vch_xml = f"<LASTVCHID>{escape(last_vch_id)}</LASTVCHID>" if last_vch_id else ""
    return (
        "<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>1</STATUS></HEADER><BODY><DATA>"
        f"<IMPORTRESULT><CREATED>{created}</CREATED><ALTERED>0</ALTERED><DELETED>0</DELETED>"
        f"{vch_xml}<COMBINED>0</COMBINED><IGNORED>0</IGNORED><ERRORS>{errors}</ERRORS>{err_xml}"
        "<CANCELLED>0</CANCELLED></IMPORTRESULT></DATA></BODY></ENVELOPE>"
    )


def render_error(message: str) -> str:
    """Tally's shape for a request it could not serve."""
    return (
        "<ENVELOPE><HEADER><VERSION>1</VERSION><STATUS>0</STATUS></HEADER><BODY><DATA>"
        f"<LINEERROR>{escape(message)}</LINEERROR></DATA></BODY></ENVELOPE>"
    )


__all__ = [
    "ImportResult",
    "ParsedRequest",
    "SalesVoucher",
    "StockLine",
    "build_export_request",
    "build_purchase_order",
    "build_sales_request",
    "build_stock_journal",
    "build_stock_summary_request",
    "fmt_qty",
    "parse_amount",
    "parse_import_result",
    "parse_qty",
    "parse_request",
    "parse_sales_today",
    "parse_sales_vouchers",
    "parse_stock_summary",
    "parse_stock_summary_lines",
    "render_error",
    "render_import_result",
    "render_sales_vouchers",
    "render_stock_summary",
    "tally_date",
    "voucher_inventory_deltas",
]
