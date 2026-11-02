"""Accounts receivable aging, computed locally from open invoices.

Buckets follow QuickBooks' default "age by due date" convention: an invoice is `current`
until its due date passes, then falls into 1-30, 31-60, 61-90 and 91+ days past due.
Amounts use `home_balance`, so multi-currency companies aggregate in home currency.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .models import CreditMemo, Invoice, Payment

BUCKETS = ("current", "1-30", "31-60", "61-90", "91+")
ZERO = Decimal("0")


def bucket_for(days_overdue: int) -> str:
    if days_overdue <= 0:
        return "current"
    if days_overdue <= 30:
        return "1-30"
    if days_overdue <= 60:
        return "31-60"
    if days_overdue <= 90:
        return "61-90"
    return "91+"


def _empty() -> dict[str, Decimal]:
    return {b: ZERO for b in BUCKETS}


@dataclass
class AgingRow:
    customer_id: str | None
    customer_name: str
    buckets: dict[str, Decimal] = field(default_factory=_empty)
    unapplied: Decimal = ZERO  # credit memos and payments not yet applied to an invoice
    invoice_count: int = 0

    @property
    def total(self) -> Decimal:
        return sum(self.buckets.values(), ZERO)

    @property
    def past_due(self) -> Decimal:
        return self.total - self.buckets["current"]

    @property
    def net(self) -> Decimal:
        return self.total - self.unapplied


@dataclass
class AgingReport:
    as_of: date
    rows: list[AgingRow]

    @property
    def totals(self) -> dict[str, Decimal]:
        return {b: sum((r.buckets[b] for r in self.rows), ZERO) for b in BUCKETS}

    @property
    def grand_total(self) -> Decimal:
        return sum(self.totals.values(), ZERO)

    @property
    def unapplied(self) -> Decimal:
        return sum((r.unapplied for r in self.rows), ZERO)

    @property
    def net_total(self) -> Decimal:
        """What QuickBooks' own A/R aging total shows: open invoices less unapplied credits."""
        return self.grand_total - self.unapplied

    @property
    def past_due_total(self) -> Decimal:
        return self.grand_total - self.totals["current"]

    def to_dict(self) -> dict:
        def money(v: Decimal) -> str:
            return f"{v:.2f}"

        return {
            "as_of": self.as_of.isoformat(),
            "totals": {b: money(v) for b, v in self.totals.items()},
            "grand_total": money(self.grand_total),
            "unapplied": money(self.unapplied),
            "net_total": money(self.net_total),
            "customers": [
                {
                    "customer_id": r.customer_id,
                    "customer": r.customer_name,
                    "invoices": r.invoice_count,
                    **{b: money(v) for b, v in r.buckets.items()},
                    "total": money(r.total),
                    "unapplied": money(r.unapplied),
                }
                for r in self.rows
            ],
        }


def build_aging(
    invoices: Iterable[Invoice],
    as_of: date,
    credits: Iterable[CreditMemo | Payment] = (),
) -> AgingReport:
    rows: dict[str | None, AgingRow] = {}

    def row_for(customer_id: str | None, name: str | None) -> AgingRow:
        row = rows.get(customer_id)
        if row is None:
            row = rows[customer_id] = AgingRow(customer_id, name or "(unknown customer)")
        return row

    for inv in invoices:
        if inv.home_balance <= 0:
            continue
        row = row_for(inv.customer_id, inv.customer_name)
        row.buckets[bucket_for(inv.days_overdue(as_of))] += inv.home_balance
        row.invoice_count += 1

    for credit in credits:
        amount = credit.balance if isinstance(credit, CreditMemo) else credit.unapplied
        if amount > 0:
            row_for(credit.customer_id, credit.customer_name).unapplied += amount

    ordered = sorted(rows.values(), key=lambda r: (-r.total, r.customer_name.lower()))
    return AgingReport(as_of=as_of, rows=ordered)
