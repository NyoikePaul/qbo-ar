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


def test_short_pay_within_tolerance_is_flagged():
    ln = StatementLine("UJ8", date(2026, 10, 30), Decimal("44998"), "CO-2026-00446")
    res = match_lines([ln], INVOICES, tolerance=5)[0]
    assert res.status == "matched" and res.confidence == 95
    assert "tolerance" in res.reason


def test_partial_beyond_tolerance_reports_remaining():
    ln = StatementLine("UJ7", date(2026, 10, 30), Decimal("500"), "CO202600447")
    res = match_lines([ln], INVOICES, tolerance=5)[0]
    assert res.confidence == 90 and "500 remains" in res.reason


def _inv(id_, doc, balance, due, cust="5"):
    return Invoice.from_api({
        "Id": id_, "DocNumber": doc, "Balance": balance, "CustomerRef": {"value": cust},
        "DueDate": due,
    })


def _run_apply(monkeypatch, amount, **kwargs):
    from qbo_ar.payments import PaymentResult
    from qbo_ar.reconcile import MatchResult

    seen = {}

    def fake_record_payment(client, customer_id, amt, invoices, **kw):
        seen.update(amount=amt, pool=[i.id for i in invoices], **kw)
        return PaymentResult("P1", (), Decimal(0))

    monkeypatch.setattr("qbo_ar.reconcile.record_payment", fake_record_payment)
    target = _inv("1", "CO-1", 100, "2026-11-30")
    others = [
        _inv("2", "CO-2", 60, "2026-10-01"),
        _inv("3", "CO-3", 200, "2026-10-15"),
        _inv("4", "CO-4", 500, "2026-09-01", cust="9"),  # other customer: never touched
    ]
    line = StatementLine("UJX", date(2026, 11, 4), Decimal(amount), "CO-1")
    res = MatchResult(line, "matched", target, "", 90)
    apply_matches(None, [res], None, all_open=[target, *others], **kwargs)
    return seen


def test_overpay_rolls_over_oldest_first_same_customer_only(monkeypatch):
    seen = _run_apply(monkeypatch, "250", rollover=True)
    assert seen["strategy"] == "specific"
    assert seen["targets"] == {"1": None, "2": Decimal("60"), "3": Decimal("90")}
    assert "4" not in seen["pool"]


def test_overpay_without_rollover_keeps_default_allocation(monkeypatch):
    seen = _run_apply(monkeypatch, "250")
    assert seen["strategy"] == "oldest_first" and seen["targets"] is None
    assert seen["pool"] == ["1"]


def test_overpay_within_tolerance_is_not_rolled_over(monkeypatch):
    seen = _run_apply(monkeypatch, "103", rollover=True, tolerance=5)
    assert seen["strategy"] == "oldest_first"


class _Recorder:
    def __init__(self):
        self.events = []

    def record(self, event, **fields):
        self.events.append((event, fields))


def test_apply_matches_audits_attempt_then_posted(monkeypatch):
    audit = _Recorder()
    _run_apply(monkeypatch, "100", audit=audit)
    assert [e for e, _ in audit.events] == ["payment_attempt", "payment_posted"]
    assert audit.events[0][1]["txn_id"] == "UJX"


def test_prefixed_invoice_number_in_reference_is_found():
    ln = StatementLine("UJP1", date(2026, 11, 5), Decimal("999"), "INV-CO-2026-00500")
    res = match_lines([ln], INVOICES)[0]
    assert res.status == "probable"
    assert res.invoice.doc_number == "CO-2026-00500"
    assert res.confidence == 85
