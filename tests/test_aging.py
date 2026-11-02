from datetime import date
from decimal import Decimal

import pytest

from qbo_ar.aging import bucket_for, build_aging
from qbo_ar.models import CreditMemo, Invoice, Payment

AS_OF = date(2026, 11, 1)


def inv(id_, cust, balance, due, home=None, name=None):
    d = {
        "Id": id_, "Balance": balance, "DueDate": due,
        "CustomerRef": {"value": cust, "name": name or cust},
    }
    if home is not None:
        d["HomeBalance"] = home
    return Invoice.from_api(d)


@pytest.mark.parametrize(
    "days,expected",
    [(-5, "current"), (0, "current"), (1, "1-30"), (30, "1-30"), (31, "31-60"),
     (60, "31-60"), (61, "61-90"), (90, "61-90"), (91, "91+"), (400, "91+")],
)
def test_bucket_boundaries(days, expected):
    assert bucket_for(days) == expected


def test_report_buckets_and_totals():
    invoices = [
        inv("1", "A", 100, "2026-11-15"),   # current
        inv("2", "A", 200, "2026-10-20"),   # 12 days -> 1-30
        inv("3", "B", 300, "2026-09-01"),   # 61 days -> 61-90
        inv("4", "B", 400, "2026-06-01"),   # 153 days -> 91+
    ]
    report = build_aging(invoices, AS_OF)
    assert report.totals["current"] == Decimal("100")
    assert report.totals["1-30"] == Decimal("200")
    assert report.totals["61-90"] == Decimal("300")
    assert report.totals["91+"] == Decimal("400")
    assert report.grand_total == Decimal("1000")
    assert report.past_due_total == Decimal("900")
    assert [r.customer_name for r in report.rows] == ["B", "A"]  # largest exposure first


def test_zero_balance_invoices_are_ignored():
    report = build_aging([inv("1", "A", 0, "2026-10-01")], AS_OF)
    assert report.rows == [] and report.grand_total == 0


def test_home_balance_used_for_multicurrency():
    report = build_aging([inv("1", "A", 1000, "2026-10-01", home=129_000)], AS_OF)
    assert report.grand_total == Decimal("129000")


def test_unapplied_credits_reduce_net_not_buckets():
    credits = [
        CreditMemo.from_api({"Id": "9", "Balance": 50, "CustomerRef": {"value": "A"}}),
        Payment.from_api({"Id": "8", "UnappliedAmt": 25, "CustomerRef": {"value": "A"}}),
        Payment.from_api({"Id": "7", "UnappliedAmt": 0, "CustomerRef": {"value": "A"}}),
    ]
    report = build_aging([inv("1", "A", 100, "2026-10-01")], AS_OF, credits)
    assert report.grand_total == Decimal("100")
    assert report.unapplied == Decimal("75")
    assert report.net_total == Decimal("25")


def test_credit_only_customer_still_listed():
    credit = CreditMemo.from_api(
        {"Id": "9", "Balance": 50, "CustomerRef": {"value": "Z", "name": "Zed"}}
    )
    report = build_aging([], AS_OF, [credit])
    assert report.rows[0].customer_name == "Zed" and report.net_total == Decimal("-50")


def test_to_dict_is_json_friendly():
    import json
    out = build_aging([inv("1", "A", 100.5, "2026-10-01")], AS_OF).to_dict()
    json.dumps(out)
    assert out["grand_total"] == "100.50" and out["customers"][0]["31-60"] == "100.50"
