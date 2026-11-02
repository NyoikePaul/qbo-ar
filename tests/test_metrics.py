from datetime import date
from decimal import Decimal

import pytest

from qbo_ar.aging import build_aging
from qbo_ar.metrics import (
    concentration,
    credit_sales,
    dso,
    period_dso,
    weighted_days_overdue,
)
from qbo_ar.models import Invoice

AS_OF = date(2026, 11, 1)


def inv(id_, cust, balance, due):
    return Invoice.from_api({
        "Id": id_, "Balance": balance, "DueDate": due,
        "CustomerRef": {"value": cust, "name": f"Cust {cust}"},
    })


def test_dso_formula_and_edge_cases():
    assert dso(Decimal("50000"), Decimal("200000"), 90) == Decimal("22.5")
    assert dso(Decimal("1"), Decimal("0"), 90) is None
    with pytest.raises(ValueError):
        dso(Decimal("1"), Decimal("1"), 0)


def test_weighted_days_overdue():
    invoices = [
        inv("1", "A", 100, "2026-10-02"),   # 30 days
        inv("2", "A", 300, "2026-10-22"),   # 10 days
        inv("3", "B", 600, "2026-12-01"),   # not due -> counts 0 days but has weight
    ]
    # (100*30 + 300*10 + 600*0) / 1000 = 6.0
    assert weighted_days_overdue(invoices, AS_OF) == Decimal("6.0")
    assert weighted_days_overdue([], AS_OF) == 0


def test_concentration_top_n_shares():
    report = build_aging(
        [inv("1", "A", 700, "2026-10-01"), inv("2", "B", 200, "2026-10-01"),
         inv("3", "C", 100, "2026-10-01")],
        AS_OF,
    )
    top = concentration(report, top_n=2)
    assert [c.customer for c in top] == ["Cust A", "Cust B"]
    assert top[0].share == Decimal("0.7000") and top[1].share == Decimal("0.2000")
    assert concentration(build_aging([], AS_OF)) == []


class SalesClient:
    def __init__(self):
        self.sql = []

    def query(self, sql):
        self.sql.append(sql)
        return {"QueryResponse": {"Invoice": [{"Id": "1", "TotalAmt": 1500.5},
                                              {"Id": "2", "TotalAmt": 500}]}}


def test_credit_sales_queries_inclusive_date_range():
    client = SalesClient()
    assert credit_sales(client, date(2026, 8, 4), AS_OF) == Decimal("2000.5")
    assert "TxnDate >= '2026-08-04' AND TxnDate <= '2026-11-01'" in client.sql[0]


def test_period_dso_uses_net_ar_and_window():
    report = build_aging([inv("1", "A", 1000, "2026-10-01")], AS_OF)
    client = SalesClient()
    # net AR 1000 / sales 2000.5 * 90 = 44.98 -> 45.0
    assert period_dso(client, report, days=90) == Decimal("45.0")
    assert "TxnDate >= '2026-08-04'" in client.sql[0]  # 90-day window ending 2026-11-01 inclusive
