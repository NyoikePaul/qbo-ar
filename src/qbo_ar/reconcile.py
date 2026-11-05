"""Match bank / M-Pesa statement lines to open invoices.

A line is only auto-applied when its reference matches an invoice number exactly (after
normalisation). Amount-only matches are reported as `probable` for a human to confirm, and
anything else lands in `unmatched`, the suspense queue.
"""

from __future__ import annotations

import csv
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, TextIO

from .models import Invoice
from .payments import PaymentResult, _oldest_first_key, record_payment
from .util import JsonlLog

DEFAULT_COLUMNS = {
    "txn_id": "Receipt No.",
    "date": "Completion Time",
    "amount": "Paid In",
    "reference": "Account No.",
    "payer": "Details",
}
DATE_FORMATS = ("%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M:%S", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y")


def normalize_ref(value: str | None) -> str:
    """'co-2026-00446 ' -> 'CO202600446': clients mistype case, spaces and hyphens."""
    return re.sub(r"[^A-Z0-9]", "", (value or "").upper())


def _parse_amount(text: str) -> Decimal:
    cleaned = re.sub(r"[^\d.\-]", "", text or "")
    try:
        return Decimal(cleaned) if cleaned else Decimal(0)
    except InvalidOperation:
        return Decimal(0)


def _parse_date(text: str) -> date | None:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime((text or "").strip(), fmt).date()
        except ValueError:
            continue
    return None


@dataclass(frozen=True)
class StatementLine:
    txn_id: str
    date: date | None
    amount: Decimal
    reference: str
    payer: str = ""


def parse_statement(
    source: TextIO, columns: dict[str, str] | None = None
) -> list[StatementLine]:
    cols = {**DEFAULT_COLUMNS, **(columns or {})}
    reader = csv.DictReader(source)
    missing = [c for k, c in cols.items() if k != "payer" and c not in (reader.fieldnames or [])]
    if missing:
        raise ValueError(f"Statement is missing columns: {', '.join(missing)}")
    lines = []
    for row in reader:
        amount = _parse_amount(row.get(cols["amount"], ""))
        if amount <= 0:  # withdrawals, charges and blank rows are not receipts
            continue
        lines.append(
            StatementLine(
                txn_id=(row.get(cols["txn_id"]) or "").strip(),
                date=_parse_date(row.get(cols["date"], "")),
                amount=amount,
                reference=(row.get(cols["reference"]) or "").strip(),
                payer=(row.get(cols["payer"]) or "").strip(),
            )
        )
    return lines


@dataclass(frozen=True)
class MatchResult:
    line: StatementLine
    status: str  # matched | probable | unmatched | duplicate
    invoice: Invoice | None = None
    reason: str = ""
    confidence: int = 0  # 100 exact, 90 partial/over, 85 ref in text, 70 amount only


def _invoice_in_text(
    texts: Iterable[str | None], by_ref: dict[str, Invoice], ambiguous: set[str]
) -> Invoice | None:
    """An invoice number typed inside free text ('INV CO-2026-00446 thanks'), if exactly one."""
    found: set[str] = set()
    for text in texts:
        for token in re.split(r"[\s,;:/|]+", text or ""):
            key = normalize_ref(token)
            if key and key in by_ref and key not in ambiguous:
                found.add(key)
    return by_ref[next(iter(found))] if len(found) == 1 else None


def match_lines(
    lines: Iterable[StatementLine],
    invoices: Iterable[Invoice],
    *,
    already_processed: Callable[[str], bool] = lambda _txn: False,
    tolerance: Decimal | int | str = 0,
) -> list[MatchResult]:
    tolerance = Decimal(str(tolerance))
    open_ = [i for i in invoices if i.balance > 0]
    by_ref: dict[str, Invoice] = {}
    ambiguous: set[str] = set()
    for candidate in open_:
        key = normalize_ref(candidate.doc_number)
        if not key:
            continue
        if key in by_ref:
            ambiguous.add(key)
        by_ref[key] = candidate

    lines = list(lines)
    emitted: set[str] = set()
    results: list[MatchResult] = []

    for ln in lines:
        if ln.txn_id and (already_processed(ln.txn_id) or ln.txn_id in emitted):
            results.append(MatchResult(ln, "duplicate", reason="transaction already processed"))
            continue
        if ln.txn_id:
            emitted.add(ln.txn_id)  # the first occurrence wins; repeats are flagged above

        key = normalize_ref(ln.reference)
        inv = by_ref.get(key) if key and key not in ambiguous else None
        if inv is not None:
            if ln.amount == inv.balance:
                note, score = "reference and amount match", 100
            elif ln.amount < inv.balance:
                short = inv.balance - ln.amount
                if short <= tolerance:
                    note = f"within tolerance: short by {short}; residual stays open"
                    score = 95
                else:
                    note = f"partial: paid {ln.amount} of {inv.balance}; {short} remains"
                    score = 90
            else:
                note = f"overpaid by {ln.amount - inv.balance}; excess stays as credit"
                score = 90
            results.append(MatchResult(ln, "matched", inv, note, score))
            continue

        in_text = _invoice_in_text((ln.reference, ln.payer), by_ref, ambiguous)
        if in_text is not None:
            results.append(
                MatchResult(ln, "probable", in_text, "invoice number found in reference text", 85)
            )
            continue

        same_amount = [i for i in open_ if i.balance == ln.amount]
        if len(same_amount) == 1:
            results.append(
                MatchResult(
                    ln, "probable", same_amount[0], "unique open invoice with this amount", 70
                )
            )
        else:
            why = "ambiguous reference" if key in ambiguous else "no matching reference"
            results.append(MatchResult(ln, "unmatched", None, why))
    return results


@dataclass
class ApplySummary:
    posted: list[tuple[MatchResult, PaymentResult]] = field(default_factory=list)
    skipped: list[MatchResult] = field(default_factory=list)


def apply_matches(
    client: Any,
    results: Iterable[MatchResult],
    log: JsonlLog,
    *,
    deposit_account_id: str | None = None,
    all_open: Iterable[Invoice] | None = None,
    rollover: bool = False,
    tolerance: Decimal | int | str = 0,
) -> ApplySummary:
    """Post only `matched` lines. probable/unmatched/duplicate are left for a human."""
    tolerance = Decimal(str(tolerance))
    all_open = list(all_open or [])
    summary = ApplySummary()
    for res in results:
        if res.status != "matched" or res.invoice is None or res.invoice.customer_id is None:
            summary.skipped.append(res)
            continue
        pool = [res.invoice]
        strategy = "oldest_first"
        targets: dict[str, Decimal | None] | None = None
        excess = res.line.amount - res.invoice.balance
        if rollover and excess > tolerance:
            inv0 = res.invoice
            others = sorted(
                (
                    i for i in all_open
                    if i.customer_id == inv0.customer_id and i.id != inv0.id
                    and i.currency == inv0.currency and i.balance > 0
                ),
                key=_oldest_first_key,
            )
            targets = {inv0.id: None}
            left = excess
            for other in others:
                if left <= 0:
                    break
                take = min(left, other.balance)
                targets[other.id] = take
                left -= take
            pool, strategy = [inv0, *others], "specific"
        payment = record_payment(
            client,
            res.invoice.customer_id,
            res.line.amount,
            pool,
            strategy=strategy,
            targets=targets,
            reference=res.line.txn_id,
            txn_date=res.line.date or date.today(),
            note=f"Statement ref: {res.line.reference}",
            deposit_account_id=deposit_account_id,
            log=log,
        )
        summary.posted.append((res, payment))
    return summary
