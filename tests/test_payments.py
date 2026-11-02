from datetime import date
from decimal import Decimal

import pytest

from qbo_ar.models import Invoice
from qbo_ar.payments import Allocation, allocate, build_payment_payload


def inv(id_, balance, due, doc=None, currency="KES", txn="2026-09-01"):
    return Invoice.from_api({
        "Id": id_, "DocNumber": doc or f"INV-{id_}", "Balance": balance, "TotalAmt": balance,
        "DueDate": due, "TxnDate": txn, "CustomerRef": {"value": "5"},
        "CurrencyRef": {"value": currency},
    })


INVOICES = [
    inv("3", 300, "2026-11-30"),
    inv("1", 100, "2026-10-01"),
    inv("2", 200, "2026-10-15"),
]


def test_oldest_first_clears_earliest_then_partial():
    res = allocate(Decimal("250"), INVOICES)
    assert [(a.invoice_id, a.amount) for a in res.allocations] == [
        ("1", Decimal("100")), ("2", Decimal("150.00")),
    ]
    assert res.unapplied == 0 and res.applied == Decimal("250.00")


def test_overpayment_leaves_unapplied_credit():
    res = allocate(Decimal("700"), INVOICES)
    assert res.applied == Decimal("600") and res.unapplied == Decimal("100.00")


def test_underpayment_is_partial_on_oldest():
    res = allocate(Decimal("40"), INVOICES)
    assert len(res.allocations) == 1 and res.allocations[0].amount == Decimal("40.00")


def test_ties_broken_by_txn_date_then_doc_number():
    a = inv("8", 50, "2026-10-01", doc="B", txn="2026-09-02")
    b = inv("9", 50, "2026-10-01", doc="A", txn="2026-09-02")
    c = inv("7", 50, "2026-10-01", doc="Z", txn="2026-09-01")
    res = allocate(Decimal("60"), [a, b, c])
    assert [x.invoice_id for x in res.allocations] == ["7", "9"]


def test_paid_invoices_skipped():
    res = allocate(Decimal("10"), [inv("1", 0, "2026-10-01"), inv("2", 50, "2026-10-02")])
    assert res.allocations[0].invoice_id == "2"


def test_specific_strategy_full_and_partial():
    res = allocate(
        Decimal("400"), INVOICES, strategy="specific", targets={"3": None, "1": Decimal("50")}
    )
    assert {a.invoice_id: a.amount for a in res.allocations} == {
        "3": Decimal("300.00"),
        "1": Decimal("50.00"),
    }
    assert res.unapplied == Decimal("50.00")


def test_specific_validations():
    with pytest.raises(ValueError, match="not open"):
        allocate(Decimal("10"), INVOICES, strategy="specific", targets={"99": None})
    with pytest.raises(ValueError, match="exceed"):
        allocate(Decimal("10"), INVOICES, strategy="specific", targets={"1": None})
    with pytest.raises(ValueError, match="Invalid amount"):
        allocate(Decimal("500"), INVOICES, strategy="specific", targets={"1": Decimal("150")})
    with pytest.raises(ValueError, match="needs targets"):
        allocate(Decimal("10"), INVOICES, strategy="specific")


def test_rejects_bad_inputs():
    with pytest.raises(ValueError):
        allocate(Decimal("0"), INVOICES)
    with pytest.raises(ValueError, match="Unknown strategy"):
        allocate(Decimal("5"), INVOICES, strategy="newest")
    with pytest.raises(ValueError, match="currencies"):
        mixed = [inv("1", 10, "2026-10-01"), inv("2", 10, "2026-10-01", currency="USD")]
        allocate(Decimal("5"), mixed)


def test_amount_rounded_to_cents():
    res = allocate(Decimal("10.005"), [inv("1", 100, "2026-10-01")])
    assert res.allocations[0].amount == Decimal("10.01")


def test_payload_links_invoices_and_truncates_reference():
    allocations = [
        Allocation("1", "INV-1", Decimal("100")),
        Allocation("2", "INV-2", Decimal("150")),
    ]
    payload = build_payment_payload(
        "5", Decimal("250"), allocations, txn_date=date(2026, 10, 30),
        reference="UJ12ABCD34-EXTRA-LONG-REF", deposit_account_id="35",
    )
    assert payload["TotalAmt"] == 250.0
    assert payload["Line"][0]["LinkedTxn"] == [{"TxnId": "1", "TxnType": "Invoice"}]
    assert payload["PaymentRefNum"] == "UJ12ABCD34-EXTRA-LONG"
    assert len(payload["PaymentRefNum"]) == 21
    assert "UJ12ABCD34-EXTRA-LONG-REF" in payload["PrivateNote"]
    assert payload["DepositToAccountRef"] == {"value": "35"}


def test_unapplied_only_payload_has_no_lines():
    payload = build_payment_payload("5", Decimal("80"), [], txn_date=date(2026, 10, 30))
    assert payload["Line"] == [] and "PaymentRefNum" not in payload
