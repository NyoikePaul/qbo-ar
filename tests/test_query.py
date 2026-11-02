from datetime import date
from decimal import Decimal

import pytest

from qbo_ar.query import Query, count, paginate, quote


class PagedClient:
    def __init__(self, entity, total, per_page):
        self.entity, self.total, self.per_page = entity, total, per_page
        self.sql = []

    def query(self, sql):
        self.sql.append(sql)
        start = int(sql.split("STARTPOSITION ")[1].split()[0])
        rows = [{"Id": str(i)} for i in range(start, min(start + self.per_page, self.total + 1))]
        return {"QueryResponse": {self.entity: rows} if rows else {}}


def test_quote_escapes_quotes_and_backslashes():
    assert quote("O'Brien") == "'O\\'Brien'"
    assert quote("a\\b") == "'a\\\\b'"


def test_quote_types():
    assert quote(True) == "true"
    assert quote(Decimal("10.50")) == "'10.50'"
    assert quote(date(2026, 10, 30)) == "'2026-10-30'"


def test_injection_attempt_is_neutralised():
    sql = Query("Customer").where("DisplayName", "=", "x' OR Id > '0").build()
    assert "DisplayName = 'x\\' OR Id > \\'0'" in sql


def test_build_full_query():
    q = Query("Invoice").where("Balance", ">", 0).where("DueDate", "<", date(2026, 11, 1))
    sql = q.order_by("DueDate").build(start=11, max_results=50)
    assert sql == (
        "SELECT * FROM Invoice WHERE Balance > '0' AND DueDate < '2026-11-01' "
        "ORDERBY DueDate ASC STARTPOSITION 11 MAXRESULTS 50"
    )


def test_in_clause():
    sql = Query("Invoice").where("Id", "IN", ["1", "2"]).build()
    assert "Id IN ('1', '2')" in sql


@pytest.mark.parametrize("bad", ["Id; DROP", "1abc", "a b", ""])
def test_invalid_field_rejected(bad):
    with pytest.raises(ValueError):
        Query("Invoice").where(bad, "=", 1)


def test_invalid_entity_operator_and_bounds():
    with pytest.raises(ValueError):
        Query("Invoice; x")
    with pytest.raises(ValueError):
        Query("Invoice").where("Id", "!=", 1)
    with pytest.raises(ValueError):
        Query("Invoice").build(max_results=1001)
    with pytest.raises(ValueError):
        Query("Invoice").where("Id", "IN", "12")


def test_paginate_walks_all_pages_and_stops_on_short_page():
    client = PagedClient("Invoice", total=25, per_page=10)
    rows = list(paginate(client, Query("Invoice"), page_size=10))
    assert len(rows) == 25
    assert len(client.sql) == 3
    assert "STARTPOSITION 21" in client.sql[-1]


def test_paginate_exact_multiple_makes_one_extra_empty_call():
    client = PagedClient("Invoice", total=20, per_page=10)
    assert len(list(paginate(client, Query("Invoice"), page_size=10))) == 20
    assert len(client.sql) == 3


def test_count():
    class C:
        def query(self, sql):
            assert sql.startswith("SELECT COUNT(*) FROM Invoice WHERE Balance > '0'")
            return {"QueryResponse": {"totalCount": 42}}

    assert count(C(), Query("Invoice").where("Balance", ">", 0)) == 42
