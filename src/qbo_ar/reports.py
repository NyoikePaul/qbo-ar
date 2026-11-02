"""Fetch live data into an aging report, and tie it out against QuickBooks' own report."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from .aging import AgingReport, build_aging
from .client import QBOError
from .invoices import open_invoices
from .models import CreditMemo, Payment
from .query import Query, paginate
from .util import to_decimal

TOLERANCE = Decimal("0.01")


def _scan(client: Any, entity: str, field: str, model: Any) -> list:
    """Rows with a positive `field`. Tries a server-side filter first and falls back to a
    full scan if QuickBooks does not allow filtering on that property."""
    try:
        rows = list(paginate(client, Query(entity).where(field, ">", 0)))
    except QBOError as exc:
        if exc.status != 400:
            raise
        rows = list(paginate(client, Query(entity)))
    return [m for m in (model.from_api(r) for r in rows) if _amount(m) > 0]


def _amount(item: CreditMemo | Payment) -> Decimal:
    return item.balance if isinstance(item, CreditMemo) else item.unapplied


def fetch_unapplied_credits(client: Any) -> list[CreditMemo | Payment]:
    credits: list[CreditMemo | Payment] = []
    credits.extend(_scan(client, "CreditMemo", "Balance", CreditMemo))
    credits.extend(_scan(client, "Payment", "UnappliedAmt", Payment))
    return credits


def fetch_aging(client: Any, as_of: date, *, include_credits: bool = True) -> AgingReport:
    invoices = open_invoices(client)
    credits = fetch_unapplied_credits(client) if include_credits else []
    return build_aging(invoices, as_of, credits)


def fetch_aged_receivables(client: Any, as_of: date) -> dict[str, Any]:
    return client.report("AgedReceivables", report_date=as_of.isoformat())


def report_grand_total(report: dict[str, Any]) -> Decimal:
    """Pull the grand total out of a QBO report payload (last column of the GrandTotal row)."""
    for row in report.get("Rows", {}).get("Row", []):
        if row.get("group") == "GrandTotal":
            cols = row.get("Summary", {}).get("ColData", [])
            if cols:
                return to_decimal(cols[-1].get("value"))
    raise ValueError("GrandTotal row not found in report")


@dataclass(frozen=True)
class TieOut:
    local: Decimal
    quickbooks: Decimal

    @property
    def difference(self) -> Decimal:
        return self.local - self.quickbooks

    @property
    def ok(self) -> bool:
        return abs(self.difference) <= TOLERANCE


def tie_out(report: AgingReport, qbo_report: dict[str, Any]) -> TieOut:
    """Compare our net A/R with QuickBooks' AgedReceivables total.

    Only meaningful when `report.as_of` is today: invoice balances are current, whereas the
    QBO report would be reconstructed as of a past date.
    """
    return TieOut(local=report.net_total, quickbooks=report_grand_total(qbo_report))
