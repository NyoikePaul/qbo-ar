"""Invoice operations: list open items, create idempotently, email, void."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from .models import Invoice
from .query import Query, paginate
from .util import quantize_money, request_id_for, to_decimal


@dataclass(frozen=True)
class LineItem:
    item_id: str
    quantity: Decimal
    unit_price: Decimal
    description: str | None = None
    tax_code: str | None = None  # QBO TaxCodeRef value, e.g. "NON" or a region-specific id

    @property
    def amount(self) -> Decimal:
        return quantize_money(self.quantity * self.unit_price)


def open_invoices(
    client: Any, *, customer_id: str | None = None, due_before: date | None = None
) -> list[Invoice]:
    """All invoices with an outstanding balance, oldest due date first."""
    q = Query("Invoice").where("Balance", ">", 0)
    if customer_id:
        q = q.where("CustomerRef", "=", customer_id)
    if due_before:
        q = q.where("DueDate", "<", due_before)
    return [Invoice.from_api(r) for r in paginate(client, q.order_by("DueDate"))]


def get_invoice(client: Any, invoice_id: str) -> Invoice:
    return Invoice.from_api(client.get(f"invoice/{invoice_id}")["Invoice"])


def find_by_doc_number(client: Any, doc_number: str) -> Invoice | None:
    rows = list(paginate(client, Query("Invoice").where("DocNumber", "=", doc_number)))
    return Invoice.from_api(rows[0]) if rows else None


def build_invoice_payload(
    customer_id: str,
    lines: list[LineItem],
    *,
    txn_date: date,
    terms_days: int = 30,
    doc_number: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    if not lines:
        raise ValueError("An invoice needs at least one line")
    payload: dict[str, Any] = {
        "CustomerRef": {"value": customer_id},
        "TxnDate": txn_date.isoformat(),
        "DueDate": (txn_date + timedelta(days=terms_days)).isoformat(),
        "Line": [],
    }
    for ln in lines:
        detail: dict[str, Any] = {
            "ItemRef": {"value": ln.item_id},
            "Qty": float(ln.quantity),
            "UnitPrice": float(ln.unit_price),
        }
        if ln.tax_code:
            detail["TaxCodeRef"] = {"value": ln.tax_code}
        line: dict[str, Any] = {
            "DetailType": "SalesItemLineDetail",
            "Amount": float(ln.amount),
            "SalesItemLineDetail": detail,
        }
        if ln.description:
            line["Description"] = ln.description
        payload["Line"].append(line)
    if doc_number:
        payload["DocNumber"] = doc_number
    if note:
        payload["PrivateNote"] = note
    return payload


def create_invoice(
    client: Any,
    customer_id: str,
    lines: list[LineItem],
    *,
    txn_date: date,
    terms_days: int = 30,
    doc_number: str | None = None,
    note: str | None = None,
    request_id: str | None = None,
) -> Invoice:
    """Create an invoice. Re-running with the same inputs is safe: the request id is derived
    from the content, so QuickBooks de-duplicates a retried POST instead of billing twice."""
    payload = build_invoice_payload(
        customer_id, lines, txn_date=txn_date, terms_days=terms_days,
        doc_number=doc_number, note=note,
    )
    rid = request_id or request_id_for(
        "invoice", customer_id, doc_number, txn_date, payload["Line"]
    )
    return Invoice.from_api(client.post("invoice", payload, request_id=rid)["Invoice"])


def send_invoice(client: Any, invoice_id: str, email: str | None = None) -> Invoice:
    """Email the invoice using QuickBooks' own template (the body cannot be customised)."""
    params = {"sendTo": email} if email else None
    out = client.request(
        "POST", f"invoice/{invoice_id}/send", params=params, data=b"",
        headers={"Content-Type": "application/octet-stream"},
    )
    return Invoice.from_api(out["Invoice"])


def void_invoice(client: Any, invoice: Invoice) -> Invoice:
    """Void (not delete) so the audit trail and document number are preserved."""
    body = {"Id": invoice.id, "SyncToken": invoice.sync_token}
    return Invoice.from_api(client.post("invoice", body, params={"operation": "void"})["Invoice"])


def line_total(lines: list[LineItem]) -> Decimal:
    return sum((ln.amount for ln in lines), to_decimal(0))
