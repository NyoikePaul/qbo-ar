"""A/R health metrics: DSO, weighted days overdue, and customer concentration."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from .aging import AgingReport
from .models import Invoice
from .query import Query, paginate
from .util import to_decimal

ZERO = Decimal(0)


def dso(ar_balance: Decimal, credit_sales: Decimal, days: int) -> Decimal | None:
    """Days Sales Outstanding = A/R / credit sales x days in the period.

    Returns None when there were no sales in the period (the ratio is undefined).
    """
    if days <= 0:
        raise ValueError("days must be positive")
    if credit_sales <= 0:
        return None
    return (ar_balance / credit_sales * days).quantize(Decimal("0.1"))


def weighted_days_overdue(invoices: Iterable[Invoice], as_of: date) -> Decimal:
    """Average days past due, weighted by balance. Not-yet-due invoices count as zero."""
    weight = total = ZERO
    for inv in invoices:
        if inv.home_balance <= 0:
            continue
        weight += inv.home_balance
        total += inv.home_balance * max(0, inv.days_overdue(as_of))
    return (total / weight).quantize(Decimal("0.1")) if weight else ZERO


@dataclass(frozen=True)
class Concentration:
    customer: str
    amount: Decimal
    share: Decimal  # fraction of total A/R, 0..1


def concentration(report: AgingReport, top_n: int = 5) -> list[Concentration]:
    total = report.grand_total
    if total <= 0:
        return []
    return [
        Concentration(r.customer_name, r.total, (r.total / total).quantize(Decimal("0.0001")))
        for r in report.rows[:top_n]
    ]


def credit_sales(client: Any, start: date, end: date) -> Decimal:
    """Total invoiced (TotalAmt) with a transaction date in [start, end]."""
    q = Query("Invoice").where("TxnDate", ">=", start).where("TxnDate", "<=", end)
    return sum((to_decimal(r.get("TotalAmt")) for r in paginate(client, q)), ZERO)


def period_dso(client: Any, report: AgingReport, days: int = 90) -> Decimal | None:
    start = report.as_of - timedelta(days=days - 1)
    return dso(report.net_total, credit_sales(client, start, report.as_of), days)
