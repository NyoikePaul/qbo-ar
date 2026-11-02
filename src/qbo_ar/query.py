"""Injection-safe builder for the QuickBooks query language, plus pagination."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from typing import Any

_ENTITY = re.compile(r"^[A-Za-z]+$")
_FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_.]*$")
_OPERATORS = {"=", "<", ">", "<=", ">=", "LIKE", "IN"}
MAX_PAGE_SIZE = 1000


def quote(value: Any) -> str:
    """Render a Python value as a QBO literal, escaping backslashes and single quotes."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, datetime):
        value = value.date()
    if isinstance(value, date):
        value = value.isoformat()
    if isinstance(value, (int, float, Decimal)):
        value = str(value)
    text = str(value).replace("\\", "\\\\").replace("'", "\\'")
    return f"'{text}'"


@dataclass(frozen=True)
class Query:
    entity: str
    conditions: tuple[str, ...] = ()
    order: str | None = None

    def __post_init__(self) -> None:
        if not _ENTITY.match(self.entity):
            raise ValueError(f"Invalid entity name: {self.entity!r}")

    def where(self, field: str, op: str, value: Any) -> Query:
        op = op.upper()
        if not _FIELD.match(field):
            raise ValueError(f"Invalid field name: {field!r}")
        if op not in _OPERATORS:
            raise ValueError(f"Unsupported operator: {op!r}")
        if op == "IN":
            if isinstance(value, (str, bytes)) or not isinstance(value, Sequence) or not value:
                raise ValueError("IN requires a non-empty list or tuple")
            rhs = "(" + ", ".join(quote(v) for v in value) + ")"
        else:
            rhs = quote(value)
        return replace(self, conditions=(*self.conditions, f"{field} {op} {rhs}"))

    def order_by(self, field: str, descending: bool = False) -> Query:
        if not _FIELD.match(field):
            raise ValueError(f"Invalid field name: {field!r}")
        return replace(self, order=f"{field} {'DESC' if descending else 'ASC'}")

    def _tail(self) -> str:
        sql = f" WHERE {' AND '.join(self.conditions)}" if self.conditions else ""
        return sql

    def build(self, start: int = 1, max_results: int = MAX_PAGE_SIZE) -> str:
        if start < 1 or not 1 <= max_results <= MAX_PAGE_SIZE:
            raise ValueError("start must be >= 1 and max_results between 1 and 1000")
        order = f" ORDERBY {self.order}" if self.order else ""
        return (
            f"SELECT * FROM {self.entity}{self._tail()}{order} "
            f"STARTPOSITION {start} MAXRESULTS {max_results}"
        )

    def count_sql(self) -> str:
        return f"SELECT COUNT(*) FROM {self.entity}{self._tail()}"


def paginate(client: Any, query: Query, page_size: int = MAX_PAGE_SIZE) -> Iterator[dict]:
    """Yield every row of a query, fetching `page_size` rows at a time."""
    start = 1
    while True:
        data = client.query(query.build(start, page_size))
        rows = data.get("QueryResponse", {}).get(query.entity, [])
        yield from rows
        if len(rows) < page_size:
            return
        start += page_size


def count(client: Any, query: Query) -> int:
    data = client.query(query.count_sql())
    return int(data.get("QueryResponse", {}).get("totalCount", 0))
