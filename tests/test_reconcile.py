import io
from datetime import date
from decimal import Decimal

import pytest

from qbo_ar.models import Invoice
from qbo_ar.reconcile import (
    StatementLine,
    apply_matches,
    match_lines,
    normalize_ref,
    parse_statement,
)
from qbo_ar.util import JsonlLog

CSV = """Receipt No.,Completion Time,Details,Paid In,Withdrawn,Account No.
UJ1AAA,2026-10-30 09:15:00,Pay Bill from 2547***411,"45,000.00",,co-2026-00446
UJ2BBB,2026-10-30 10:00:00,Pay Bill from 2547***999,"500.00",,CO202600447
UJ3CCC,2026-10-30 11:00:00,Pay Bill charge,,"35.00",
UJ4DDD,2026-10-30 12:00:00,Pay Bill from 2547***123,"2,000.00",,wrong ref
"""


def invoice(id_, doc, balance, cust="5"):
    return Invoice.from_api({
        "Id": id_, "DocNumber": doc, "Balance": balance, "CustomerRef": {"value": cust},
        "DueDate": "2026-11-30",
    })


INVOICES = [invoice("1", "CO-2026-00446", 45000), invoice("2", "CO-2026-00447", 1000),
            invoice("3", "CO-2026-00500", 2000)]


def lines():
    return parse_statement(io.StringIO(CSV))


@pytest.mark.parametrize("raw,expected", [
    ("co-2026-00446 ", "CO202600446"), ("CO 2026 00446", "CO202600446"), (None, ""),
])
def test_normalize_ref(raw, expected):
    assert normalize_ref(raw) == expected


def test_parse_skips_withdrawals_and_parses_amounts_and_dates():
    parsed = lines()
    assert [ln.txn_id for ln in parsed] == ["UJ1AAA", "UJ2BBB", "UJ4DDD"]
    assert parsed[0].amount == Decimal("45000.00") and parsed[0].date == date(2026, 10, 30)


def test_parse_reports_missing_columns_and_supports_mapping():
    with pytest.raises(ValueError, match="Receipt No."):
        parse_statement(io.StringIO("a,b\n1,2\n"))
    custom = "TransID,Time,Amount,BillRef\nX1,2026-10-30,100,INV1\n"
    out = parse_statement(
        io.StringIO(custom),
        {"txn_id": "TransID", "date": "Time", "amount": "Amount", "reference": "BillRef"},
    )
    assert out[0].txn_id == "X1" and out[0].reference == "INV1"


def test_matching_statuses():
    res = {r.line.txn_id: r for r in match_lines(lines(), INVOICES)}
    assert res["UJ1AAA"].status == "matched" and res["UJ1AAA"].invoice.id == "1"
    assert "reference and amount match" in res["UJ1AAA"].reason
    assert res["UJ2BBB"].status == "matched" and "partial" in res["UJ2BBB"].reason
    # wrong reference, but KES 2,000 equals exactly one open invoice -> needs a human
    assert res["UJ4DDD"].status == "probable" and res["UJ4DDD"].invoice.id == "3"


def test_overpayment_note():
    line = StatementLine("T1", date(2026, 10, 30), Decimal("1500"), "CO-2026-00447")
    out = match_lines([line], INVOICES)[0]
    assert out.status == "matched" and "overpaid by 500" in out.reason


def test_unmatched_when_nothing_fits():
    line = StatementLine("T2", None, Decimal("123.45"), "nonsense")
    out = match_lines([line], INVOICES)[0]
    assert out.status == "unmatched" and out.invoice is None


def test_ambiguous_invoice_numbers_are_never_auto_matched():
    dupes = [invoice("1", "INV-1", 100), invoice("2", "INV 1", 300)]
    out = match_lines([StatementLine("T3", None, Decimal("50"), "inv1")], dupes)[0]
    assert out.status == "unmatched" and out.reason == "ambiguous reference"


def test_duplicates_within_file_and_against_log():
    a = StatementLine("T9", None, Decimal("100"), "CO-2026-00446")
    out = match_lines([a, a], INVOICES)
    assert [r.status for r in out] == ["matched", "duplicate"]
    done = match_lines([a], INVOICES, already_processed=lambda t: t == "T9")
    assert done[0].status == "duplicate"


class PostRecorder:
    def __init__(self):
        self.calls = []

    def post(self, endpoint, json=None, **kw):
        self.calls.append(json)
        return {"Payment": {"Id": str(100 + len(self.calls))}}


def test_apply_posts_only_matched_lines_once(tmp_path):
    log, client = JsonlLog(tmp_path / "p.jsonl"), PostRecorder()
    results = match_lines(lines(), INVOICES, already_processed=lambda t: log.seen(f"payment:{t}"))
    summary = apply_matches(client, results, log, deposit_account_id="35")
    assert len(summary.posted) == 2 and len(summary.skipped) == 1  # UJ4DDD is only probable
    assert client.calls[0]["PaymentRefNum"] == "UJ1AAA"
    assert client.calls[0]["DepositToAccountRef"] == {"value": "35"}
    # re-running the same statement posts nothing new
    rerun = match_lines(lines(), INVOICES, already_processed=lambda t: log.seen(f"payment:{t}"))
    again = apply_matches(client, rerun, log)
    assert again.posted == [] and len(client.calls) == 2


def test_invoice_number_inside_reference_is_probable_not_matched():
    ln = StatementLine("UJ9", date(2026, 10, 30), Decimal("45000"), "INV CO-2026-00446 thanks")
    res = match_lines([ln], INVOICES)[0]
    assert res.status == "probable"
    assert res.invoice.doc_number == "CO-2026-00446"
    assert res.confidence == 85


def test_confidence_scores():
    res = {r.line.txn_id: r for r in match_lines(lines(), INVOICES)}
    assert res["UJ1AAA"].confidence == 100  # exact ref, exact amount
    assert res["UJ2BBB"].confidence == 90  # exact ref, partial payment
    assert res["UJ4DDD"].confidence == 70  # bad ref, unique amount
