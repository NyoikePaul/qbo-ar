from datetime import date
from decimal import Decimal

from qbo_ar.models import CreditMemo, Customer, Invoice, Payment, parse_date

RAW = {
    "Id": "130", "DocNumber": "CO-2026-00442", "TxnDate": "2026-10-01", "DueDate": "2026-10-31",
    "TotalAmt": 90000.0, "Balance": 70000.0, "SyncToken": "2",
    "CustomerRef": {"value": "58", "name": "Lawrence Musau"},
    "CurrencyRef": {"value": "KES"}, "EmailStatus": "NotSet",
}


def test_invoice_from_api():
    inv = Invoice.from_api(RAW)
    assert inv.id == "130" and inv.customer_name == "Lawrence Musau"
    assert inv.total == Decimal("90000.0") and inv.balance == Decimal("70000.0")
    assert inv.home_balance == inv.balance
    assert inv.is_open and inv.due_date == date(2026, 10, 31)


def test_home_balance_used_for_multicurrency():
    inv = Invoice.from_api({**RAW, "CurrencyRef": {"value": "USD"}, "HomeBalance": 9100000.0})
    assert inv.home_balance == Decimal("9100000.0") and inv.balance == Decimal("70000.0")


def test_days_overdue_sign_and_fallback_to_txn_date():
    inv = Invoice.from_api(RAW)
    assert inv.days_overdue(date(2026, 11, 10)) == 10
    assert inv.days_overdue(date(2026, 10, 25)) == -6
    no_due = Invoice.from_api({k: v for k, v in RAW.items() if k != "DueDate"})
    assert no_due.days_overdue(date(2026, 10, 11)) == 10


def test_paid_invoice_is_not_open():
    assert not Invoice.from_api({**RAW, "Balance": 0}).is_open


def test_missing_optional_fields_do_not_crash():
    inv = Invoice.from_api({"Id": 1})
    assert inv.customer_id is None and inv.total == 0 and inv.days_overdue(date.today()) == 0


def test_other_models():
    c = Customer.from_api(
        {
            "Id": "5",
            "DisplayName": "Acme",
            "PrimaryEmailAddr": {"Address": "a@b.co"},
            "Balance": 12.5,
        }
    )
    assert c.email == "a@b.co" and c.balance == Decimal("12.5")
    cm = CreditMemo.from_api({"Id": "9", "TotalAmt": 100, "Balance": 40,
                              "CustomerRef": {"value": "5"}})
    assert cm.balance == Decimal("40")
    p = Payment.from_api({"Id": "3", "TotalAmt": 500, "UnappliedAmt": 500, "PaymentRefNum": "QGH1"})
    assert p.unapplied == Decimal("500") and p.reference == "QGH1"


def test_parse_date_handles_timestamps_and_none():
    assert parse_date("2026-10-30T10:00:00Z") == date(2026, 10, 30)
    assert parse_date(None) is None
