"""Typed views over QBO JSON. Money is always Decimal; dates are `datetime.date`."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from .util import to_decimal


def parse_date(value: str | None) -> date | None:
    return date.fromisoformat(value[:10]) if value else None


def _ref(data: dict[str, Any], key: str) -> tuple[str | None, str | None]:
    ref = data.get(key) or {}
    return ref.get("value"), ref.get("name")


@dataclass(frozen=True)
class Invoice:
    id: str
    doc_number: str | None
    customer_id: str | None
    customer_name: str | None
    txn_date: date | None
    due_date: date | None
    total: Decimal
    balance: Decimal
    home_balance: Decimal
    currency: str | None
    sync_token: str | None
    email_status: str | None = None
    private_note: str | None = None

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> Invoice:
        cust_id, cust_name = _ref(d, "CustomerRef")
        currency, _ = _ref(d, "CurrencyRef")
        balance = to_decimal(d.get("Balance"))
        # HomeBalance is present on multi-currency companies: aging must use it so that
        # foreign-currency invoices are not summed with home-currency ones.
        home = to_decimal(d["HomeBalance"]) if d.get("HomeBalance") is not None else balance
        return cls(
            id=str(d["Id"]),
            doc_number=d.get("DocNumber"),
            customer_id=cust_id,
            customer_name=cust_name,
            txn_date=parse_date(d.get("TxnDate")),
            due_date=parse_date(d.get("DueDate")),
            total=to_decimal(d.get("TotalAmt")),
            balance=balance,
            home_balance=home,
            currency=currency,
            sync_token=d.get("SyncToken"),
            email_status=d.get("EmailStatus"),
            private_note=d.get("PrivateNote"),
        )

    @property
    def is_open(self) -> bool:
        return self.balance > 0

    @property
    def effective_due_date(self) -> date | None:
        return self.due_date or self.txn_date

    def days_overdue(self, as_of: date) -> int:
        """Positive when past due, zero or negative when not yet due."""
        due = self.effective_due_date
        return (as_of - due).days if due else 0


@dataclass(frozen=True)
class Customer:
    id: str
    display_name: str
    email: str | None
    balance: Decimal

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> Customer:
        return cls(
            id=str(d["Id"]),
            display_name=d.get("DisplayName", ""),
            email=(d.get("PrimaryEmailAddr") or {}).get("Address"),
            balance=to_decimal(d.get("Balance")),
        )


@dataclass(frozen=True)
class CreditMemo:
    id: str
    doc_number: str | None
    customer_id: str | None
    customer_name: str | None
    txn_date: date | None
    total: Decimal
    balance: Decimal

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> CreditMemo:
        cust_id, cust_name = _ref(d, "CustomerRef")
        return cls(
            id=str(d["Id"]),
            doc_number=d.get("DocNumber"),
            customer_id=cust_id,
            customer_name=cust_name,
            txn_date=parse_date(d.get("TxnDate")),
            total=to_decimal(d.get("TotalAmt")),
            balance=to_decimal(d.get("Balance")),
        )


@dataclass(frozen=True)
class Payment:
    id: str
    customer_id: str | None
    customer_name: str | None
    txn_date: date | None
    total: Decimal
    unapplied: Decimal
    reference: str | None

    @classmethod
    def from_api(cls, d: dict[str, Any]) -> Payment:
        cust_id, cust_name = _ref(d, "CustomerRef")
        return cls(
            id=str(d["Id"]),
            customer_id=cust_id,
            customer_name=cust_name,
            txn_date=parse_date(d.get("TxnDate")),
            total=to_decimal(d.get("TotalAmt")),
            unapplied=to_decimal(d.get("UnappliedAmt")),
            reference=d.get("PaymentRefNum"),
        )
