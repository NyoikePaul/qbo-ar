from datetime import date
from decimal import Decimal

import pytest

from qbo_ar.client import QBOError
from qbo_ar.reports import (
    fetch_aged_receivables,
    fetch_aging,
    fetch_unapplied_credits,
    report_grand_total,
    tie_out,
)


def qbo_report(total):
    return {"Rows": {"Row": [
        {"type": "Data", "ColData": [{"value": "Acme"}, {"value": "10.00"}]},
        {"type": "Section", "group": "GrandTotal",
         "Summary": {"ColData": [{"value": "TOTAL"}, {"value": "5.00"}, {"value": total}]}},
    ]}}


class FakeClient:
    """Answers queries by entity name; optionally rejects filters like QBO sometimes does."""

    def __init__(self, data, reject_filter_on=()):
        self.data, self.reject, self.sql, self.reports = data, set(reject_filter_on), [], []

    def query(self, sql):
        self.sql.append(sql)
        entity = sql.split("FROM ")[1].split()[0]
        if entity in self.reject and "WHERE" in sql:
            raise QBOError(400, "Property not queryable")
        return {"QueryResponse": {entity: self.data.get(entity, [])}}

    def report(self, name, **params):
        self.reports.append((name, params))
        return qbo_report("1234.50")


DATA = {
    "Invoice": [{"Id": "1", "Balance": 100, "DueDate": "2026-10-01",
                 "CustomerRef": {"value": "A", "name": "Acme"}}],
    "CreditMemo": [{"Id": "2", "Balance": 30, "CustomerRef": {"value": "A", "name": "Acme"}},
                   {"Id": "3", "Balance": 0, "CustomerRef": {"value": "A", "name": "Acme"}}],
    "Payment": [{"Id": "4", "UnappliedAmt": 20, "CustomerRef": {"value": "A", "name": "Acme"}},
                {"Id": "5", "UnappliedAmt": 0, "CustomerRef": {"value": "A", "name": "Acme"}}],
}


def test_fetch_aging_includes_credits():
    report = fetch_aging(FakeClient(DATA), date(2026, 11, 1))
    assert report.grand_total == Decimal("100") and report.unapplied == Decimal("50")
    assert report.net_total == Decimal("50")


def test_fetch_aging_without_credits_skips_those_queries():
    client = FakeClient(DATA)
    fetch_aging(client, date(2026, 11, 1), include_credits=False)
    assert len(client.sql) == 1


def test_falls_back_to_full_scan_when_filter_rejected():
    client = FakeClient(DATA, reject_filter_on={"Payment"})
    credits = fetch_unapplied_credits(client)
    assert sum(c.balance if hasattr(c, "balance") else c.unapplied for c in credits) == 50
    assert any("FROM Payment" in s and "WHERE" not in s for s in client.sql)


def test_non_400_errors_are_not_swallowed():
    class Boom:
        def query(self, sql):
            raise QBOError(500, "server")

    with pytest.raises(QBOError):
        fetch_unapplied_credits(Boom())


def test_grand_total_parsing_and_missing_row():
    assert report_grand_total(qbo_report("99.99")) == Decimal("99.99")
    with pytest.raises(ValueError):
        report_grand_total({"Rows": {"Row": []}})


def test_tie_out_ok_within_a_cent_and_flags_drift():
    client = FakeClient(DATA)
    local = fetch_aging(client, date(2026, 11, 1))
    good = tie_out(local, qbo_report("50.00"))
    assert good.ok and good.difference == 0
    bad = tie_out(local, qbo_report("75.00"))
    assert not bad.ok and bad.difference == Decimal("-25.00")


def test_fetch_report_passes_iso_date():
    client = FakeClient(DATA)
    fetch_aged_receivables(client, date(2026, 11, 1))
    assert client.reports == [("AgedReceivables", {"report_date": "2026-11-01"})]
