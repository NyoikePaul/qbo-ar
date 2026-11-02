from datetime import date
from decimal import Decimal

import pytest

from qbo_ar.invoices import (
    LineItem,
    build_invoice_payload,
    create_invoice,
    find_by_doc_number,
    line_total,
    open_invoices,
    send_invoice,
    void_invoice,
)
from qbo_ar.models import Invoice


class Recorder:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def query(self, sql):
        self.calls.append(("query", sql))
        return self.responses.pop(0)

    def post(self, endpoint, json=None, **kw):
        self.calls.append(("post", endpoint, json, kw))
        return self.responses.pop(0)

    def request(self, method, endpoint, **kw):
        self.calls.append((method, endpoint, kw))
        return self.responses.pop(0)

    def get(self, endpoint, **kw):
        self.calls.append(("get", endpoint))
        return self.responses.pop(0)


INV = {"Id": "7", "DocNumber": "CO-1", "Balance": 100, "TotalAmt": 100, "SyncToken": "0",
       "CustomerRef": {"value": "5", "name": "Acme"}, "DueDate": "2026-11-30"}
LINES = [LineItem("11", Decimal("30800"), Decimal("1.5"), "Basil plugs", "NON")]


def test_open_invoices_query_and_parsing():
    rec = Recorder({"QueryResponse": {"Invoice": [INV]}})
    out = open_invoices(rec, customer_id="5", due_before=date(2026, 12, 1))
    assert out[0].doc_number == "CO-1"
    sql = rec.calls[0][1]
    assert "Balance > '0'" in sql and "CustomerRef = '5'" in sql
    assert "DueDate < '2026-12-01'" in sql and "ORDERBY DueDate ASC" in sql


def test_find_by_doc_number_none_when_missing():
    assert find_by_doc_number(Recorder({"QueryResponse": {}}), "X") is None


def test_payload_shape_and_due_date():
    p = build_invoice_payload(
        "5", LINES, txn_date=date(2026, 10, 26), terms_days=14, doc_number="D1"
    )
    assert p["DueDate"] == "2026-11-09" and p["DocNumber"] == "D1"
    line = p["Line"][0]
    assert line["Amount"] == 46200.0 and line["Description"] == "Basil plugs"
    assert line["SalesItemLineDetail"]["TaxCodeRef"] == {"value": "NON"}


def test_line_amount_rounds_half_up():
    assert LineItem("1", Decimal("3"), Decimal("0.335")).amount == Decimal("1.01")
    assert line_total(LINES) == Decimal("46200.00")


def test_empty_invoice_rejected():
    with pytest.raises(ValueError):
        build_invoice_payload("5", [], txn_date=date(2026, 1, 1))


def test_create_invoice_is_idempotent_by_content():
    rec = Recorder({"Invoice": INV}, {"Invoice": INV})
    a = create_invoice(rec, "5", LINES, txn_date=date(2026, 10, 26), doc_number="D1")
    create_invoice(rec, "5", LINES, txn_date=date(2026, 10, 26), doc_number="D1")
    assert isinstance(a, Invoice)
    assert rec.calls[0][3]["request_id"] == rec.calls[1][3]["request_id"]
    changed = [LineItem("11", Decimal("30800"), Decimal("2"))]
    rec2 = Recorder({"Invoice": INV})
    create_invoice(rec2, "5", changed, txn_date=date(2026, 10, 26), doc_number="D1")
    assert rec2.calls[0][3]["request_id"] != rec.calls[0][3]["request_id"]


def test_send_uses_octet_stream_and_optional_recipient():
    rec = Recorder({"Invoice": INV})
    send_invoice(rec, "7", "pay@acme.co")
    method, endpoint, kw = rec.calls[0]
    assert (method, endpoint) == ("POST", "invoice/7/send")
    assert kw["params"] == {"sendTo": "pay@acme.co"}
    assert kw["headers"]["Content-Type"] == "application/octet-stream"


def test_void_sends_sync_token():
    rec = Recorder({"Invoice": INV})
    void_invoice(rec, Invoice.from_api(INV))
    _, endpoint, body, kw = rec.calls[0]
    assert endpoint == "invoice" and body == {"Id": "7", "SyncToken": "0"}
    assert kw["params"] == {"operation": "void"}
