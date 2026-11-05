"""Collections: choose a reminder tier per customer and (optionally) resend invoices.

Nothing is sent unless `send_reminders(..., dry_run=False)` is called, and each
(invoice, tier) pair is sent at most once, so re-running a daily job never spams clients.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, TextIO

from .invoices import send_invoice
from .models import Customer, Invoice
from .util import JsonlLog


@dataclass(frozen=True)
class Tier:
    name: str
    min_days_overdue: int
    internal: bool = False  # internal tiers escalate to staff and never email the customer


DEFAULT_TIERS = (
    Tier("friendly", 1),
    Tier("firm", 15),
    Tier("final", 30),
    Tier("escalate", 60, internal=True),
)


@dataclass(frozen=True)
class Reminder:
    customer_id: str | None
    customer_name: str
    tier: Tier
    oldest_days_overdue: int
    overdue_total: Decimal
    invoices: tuple[Invoice, ...]  # overdue invoices, oldest first


def tier_for(days_overdue: int, tiers: Iterable[Tier] = DEFAULT_TIERS) -> Tier | None:
    eligible = [t for t in tiers if days_overdue >= t.min_days_overdue]
    return max(eligible, key=lambda t: t.min_days_overdue) if eligible else None


def build_reminders(
    invoices: Iterable[Invoice],
    as_of: date,
    tiers: Iterable[Tier] = DEFAULT_TIERS,
    min_balance: Decimal = Decimal(0),
) -> list[Reminder]:
    """One reminder per customer, tiered by their oldest overdue invoice, worst first."""
    tiers = tuple(tiers)
    grouped: dict[str | None, list[Invoice]] = defaultdict(list)
    for inv in invoices:
        if inv.balance > 0 and inv.days_overdue(as_of) > 0:
            grouped[inv.customer_id].append(inv)

    reminders = []
    for cust_id, items in grouped.items():
        items.sort(key=lambda i: i.days_overdue(as_of), reverse=True)
        total = sum((i.home_balance for i in items), Decimal(0))
        tier = tier_for(items[0].days_overdue(as_of), tiers)
        if tier is None or total < min_balance:
            continue
        reminders.append(
            Reminder(cust_id, items[0].customer_name or "(unknown)", tier,
                     items[0].days_overdue(as_of), total, tuple(items))
        )
    return sorted(reminders, key=lambda r: (-r.oldest_days_overdue, -r.overdue_total))


def render_email(reminder: Reminder, company: str = "Accounts Receivable") -> tuple[str, str]:
    """Plain-text subject and body for your own mailer or a mail-merge export."""
    r = reminder
    lines = "\n".join(
        f"  - {i.doc_number or i.id}: {i.home_balance:,.2f} due {i.effective_due_date}"
        for i in r.invoices
    )
    tone = {
        "friendly": "This is a friendly reminder that the following invoices are now past due.",
        "firm": "Our records show the following invoices remain unpaid past their due date.",
        "final": "This is a final notice: the following invoices are seriously overdue.",
        "escalate": "Escalation: customer is over 60 days past due. Review credit terms.",
    }.get(r.tier.name, "The following invoices are past due.")
    subject = f"[{r.tier.name.upper()}] Overdue balance {r.overdue_total:,.2f}"
    body = (
        f"Dear {r.customer_name},\n\n{tone}\n\n{lines}\n\n"
        f"Total overdue: {r.overdue_total:,.2f}\n\nRegards,\n{company}\n"
    )
    return subject, body


def write_csv(reminders: Iterable[Reminder], out: TextIO) -> None:
    writer = csv.writer(out)
    writer.writerow(["customer_id", "customer", "tier", "oldest_days_overdue",
                     "overdue_total", "invoice_count", "invoices"])
    for r in reminders:
        writer.writerow([
            r.customer_id, r.customer_name, r.tier.name, r.oldest_days_overdue,
            f"{r.overdue_total:.2f}", len(r.invoices),
            ";".join(i.doc_number or i.id for i in r.invoices),
        ])


@dataclass(frozen=True)
class SendOutcome:
    reminder: Reminder
    invoice_id: str | None
    action: str  # sent | would_send | skipped_internal | skipped_already_sent | skipped_no_email


def _email_for(customers: dict[str, Customer] | None, customer_id: str | None) -> str | None:
    customer = (customers or {}).get(customer_id or "")
    return customer.email if customer else None


def send_reminders(
    client: Any,
    reminders: Iterable[Reminder],
    log: JsonlLog,
    customers: dict[str, Customer] | None = None,
    *,
    dry_run: bool = True,
    audit: Any | None = None,
) -> list[SendOutcome]:
    """Resend the oldest overdue invoice via QuickBooks (its own template) once per tier."""
    outcomes = []
    for r in reminders:
        oldest = r.invoices[0]
        key = f"dunning:{oldest.id}:{r.tier.name}"
        if r.tier.internal:
            outcomes.append(SendOutcome(r, None, "skipped_internal"))
        elif log.seen(key):
            outcomes.append(SendOutcome(r, oldest.id, "skipped_already_sent"))
        elif customers is not None and not _email_for(customers, r.customer_id):
            outcomes.append(SendOutcome(r, oldest.id, "skipped_no_email"))
        elif dry_run:
            outcomes.append(SendOutcome(r, oldest.id, "would_send"))
        else:
            if audit is not None:
                audit.record(
                    "dunning_attempt",
                    customer_id=r.customer_id,
                    tier=r.tier.name,
                    invoice_id=oldest.id,
                )
            send_invoice(client, oldest.id, _email_for(customers, r.customer_id))
            log.add(key, customer_id=r.customer_id, tier=r.tier.name)
            outcomes.append(SendOutcome(r, oldest.id, "sent"))
        if audit is not None:
            audit.record(
                "dunning_decision",
                customer_id=r.customer_id,
                tier=r.tier.name,
                invoice_id=outcomes[-1].invoice_id,
                action=outcomes[-1].action,
            )
    return outcomes
