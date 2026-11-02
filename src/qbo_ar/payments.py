"""Payment allocation: decide which invoices a receipt settles, and build the QBO payload."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from .models import Invoice
from .util import quantize_money

ZERO = Decimal("0")
MAX_REF_LEN = 21  # QuickBooks limit for PaymentRefNum


@dataclass(frozen=True)
class Allocation:
    invoice_id: str
    doc_number: str | None
    amount: Decimal


@dataclass(frozen=True)
class AllocationResult:
    allocations: tuple[Allocation, ...]
    unapplied: Decimal  # left on the customer's account as credit

    @property
    def applied(self) -> Decimal:
        return sum((a.amount for a in self.allocations), ZERO)


def _oldest_first_key(inv: Invoice) -> tuple:
    due = inv.effective_due_date or date.max
    return (due, inv.txn_date or date.max, inv.doc_number or "", inv.id)


def allocate(
    amount: Decimal,
    invoices: Iterable[Invoice],
    *,
    strategy: str = "oldest_first",
    targets: Mapping[str, Decimal | None] | None = None,
) -> AllocationResult:
    """Split `amount` across open invoices.

    `oldest_first` clears the earliest due dates first (partial on the last one touched).
    `specific` applies to the invoices named in `targets` ({invoice_id: amount or None for
    the full balance}). Whatever is left over is returned as `unapplied`.
    """
    amount = quantize_money(Decimal(amount))
    if amount <= 0:
        raise ValueError("Payment amount must be positive")
    open_ = [i for i in invoices if i.balance > 0]
    if len({i.currency for i in open_}) > 1:
        raise ValueError("Cannot allocate one payment across invoices in different currencies")

    allocations: list[Allocation] = []
    remaining = amount

    if strategy == "oldest_first":
        for inv in sorted(open_, key=_oldest_first_key):
            if remaining <= 0:
                break
            applied = min(remaining, inv.balance)
            allocations.append(Allocation(inv.id, inv.doc_number, applied))
            remaining -= applied
    elif strategy == "specific":
        if not targets:
            raise ValueError("The 'specific' strategy needs targets")
        by_id = {i.id: i for i in open_}
        for inv_id, wanted in targets.items():
            inv = by_id.get(inv_id)
            if inv is None:
                raise ValueError(f"Invoice {inv_id} is not open for this customer")
            applied = inv.balance if wanted is None else quantize_money(Decimal(wanted))
            if applied <= 0 or applied > inv.balance:
                raise ValueError(
                    f"Invalid amount {applied} for invoice {inv_id} (balance {inv.balance})"
                )
            if applied > remaining:
                raise ValueError("Targets exceed the payment amount")
            allocations.append(Allocation(inv.id, inv.doc_number, applied))
            remaining -= applied
    else:
        raise ValueError(f"Unknown strategy: {strategy!r}")

    return AllocationResult(tuple(allocations), remaining)


def build_payment_payload(
    customer_id: str,
    amount: Decimal,
    allocations: Iterable[Allocation],
    *,
    txn_date: date,
    reference: str | None = None,
    note: str | None = None,
    deposit_account_id: str | None = None,
    payment_method_id: str | None = None,
) -> dict[str, Any]:
    """Payment body. Applied lines link to invoices; any remainder stays as unapplied credit."""
    payload: dict[str, Any] = {
        "CustomerRef": {"value": customer_id},
        "TotalAmt": float(quantize_money(Decimal(amount))),
        "TxnDate": txn_date.isoformat(),
        "Line": [
            {
                "Amount": float(a.amount),
                "LinkedTxn": [{"TxnId": a.invoice_id, "TxnType": "Invoice"}],
            }
            for a in allocations
        ],
    }
    if reference:
        payload["PaymentRefNum"] = reference[:MAX_REF_LEN]
        full_note = f"Ref: {reference}" + (f" | {note}" if note else "")
        payload["PrivateNote"] = full_note  # keeps the untruncated reference
    elif note:
        payload["PrivateNote"] = note
    if deposit_account_id:
        payload["DepositToAccountRef"] = {"value": deposit_account_id}
    if payment_method_id:
        payload["PaymentMethodRef"] = {"value": payment_method_id}
    return payload
