"""Suspense queue: suggest a customer for statement lines that did not auto-match.

Suggestions are advisory only; nothing here posts to QuickBooks.
"""

from __future__ import annotations

import csv
import difflib
import json
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from .models import Invoice
from .payments import _oldest_first_key
from .reconcile import ApplySummary, MatchResult

DEFAULT_PAYER_MAP = "~/.qbo_ar/payers.json"
FUZZY_CUTOFF = 0.8
LEARNED_SCORE = 80  # capped: masked numbers can collide across payers
FUZZY_MAX = 75
QUEUE_HEADER = ["Txn", "Date", "Amount", "Payer", "Reference", "Status",
                "Suggested customer", "Suggested invoice", "Score", "Why"]

_PREFIX = re.compile(
    r"^\s*(?:(?:pay\s*bill|buy\s*goods|customer\s*payment)\b\s*)?(?:(?:from|by)\b\s*)?",
    re.I,
)
_PHONE = re.compile(r"\d{3,}\*+\d{2,}|\d{9,12}")


def split_payer(details: str | None) -> tuple[str, str]:
    """'Pay Bill from 254722123456 - JOHN MELTON' -> ('254722123456', 'JOHN MELTON')."""
    text = _PREFIX.sub("", details or "")
    match = _PHONE.search(text)
    phone = match.group(0) if match else ""
    name = re.sub(r"[^A-Za-z ]", " ", _PHONE.sub(" ", text))
    return phone, " ".join(name.split()).upper()


def payer_keys(details: str | None) -> list[str]:
    phone, name = split_payer(details)
    return ([f"tel:{phone}"] if phone else []) + ([f"name:{name}"] if name else [])


class PayerMap:
    """payer key -> customer, learned only from payments that really posted."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self._data: dict[str, dict] | None = None

    def _load(self) -> dict[str, dict]:
        if self._data is None:
            self._data = json.loads(self.path.read_text()) if self.path.exists() else {}
        return self._data

    def get(self, key: str) -> dict | None:
        return self._load().get(key)

    def learn(self, key: str, customer_id: str | None, customer_name: str | None) -> None:
        if not key or not customer_id:
            return
        data = self._load()
        cur = data.get(key)
        if cur and cur["customer_id"] == customer_id:
            cur["count"] += 1
        else:  # first sight, or the payer now pays for someone else: restart the count
            data[key] = {"customer_id": customer_id, "customer_name": customer_name or "",
                         "count": 1}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        tmp.chmod(0o600)
        os.replace(tmp, self.path)


@dataclass(frozen=True)
class Suggestion:
    customer: str
    invoice: str
    score: int
    why: str


def suggest(res: MatchResult, invoices: Iterable[Invoice], payers: PayerMap) -> Suggestion | None:
    if res.status == "probable" and res.invoice is not None:
        inv = res.invoice
        return Suggestion(inv.customer_name or "", inv.doc_number or "", res.confidence, res.reason)

    oldest: dict[str, Invoice] = {}
    for inv in sorted((i for i in invoices if i.balance > 0), key=_oldest_first_key):
        if inv.customer_id and inv.customer_id not in oldest:
            oldest[inv.customer_id] = inv

    for key in payer_keys(res.line.payer):
        hit = payers.get(key)
        if hit:
            inv = oldest.get(hit["customer_id"])
            why = f"learned from {hit['count']} prior payment(s)"
            return Suggestion(hit["customer_name"], (inv.doc_number or "") if inv else "",
                              LEARNED_SCORE, why)

    _, name = split_payer(res.line.payer)
    if name:
        by_name = {i.customer_name.upper(): i for i in oldest.values() if i.customer_name}
        close = difflib.get_close_matches(name, list(by_name), n=1, cutoff=FUZZY_CUTOFF)
        if close:
            ratio = difflib.SequenceMatcher(None, name, close[0]).ratio()
            inv = by_name[close[0]]
            why = f"payer name resembles customer ({ratio:.0%})"
            return Suggestion(
                inv.customer_name or "", inv.doc_number or "", int(ratio * FUZZY_MAX), why
            )
    return None


def _cell(value: str) -> str:
    """Stop spreadsheet formula injection from statement text."""
    return "'" + value if value[:1] in ("=", "+", "-", "@") else value


def write_queue(path: str | Path, results: Iterable[MatchResult], invoices: Iterable[Invoice],
                payers: PayerMap) -> int:
    invoices = list(invoices)
    rows = []
    for res in results:
        if res.status not in ("unmatched", "probable"):
            continue
        s = suggest(res, invoices, payers)
        ln = res.line
        rows.append([
            ln.txn_id, ln.date.isoformat() if ln.date else "", str(ln.amount),
            _cell(ln.payer), _cell(ln.reference), res.status,
            s.customer if s else "", s.invoice if s else "", s.score if s else 0,
            s.why if s else res.reason,
        ])
    out = Path(path).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(QUEUE_HEADER)
        writer.writerows(rows)
    return len(rows)


def learn_from_posted(payers: PayerMap, summary: ApplySummary) -> None:
    for res, _payment in summary.posted:
        if res.invoice is None:
            continue
        for key in payer_keys(res.line.payer):
            payers.learn(key, res.invoice.customer_id, res.invoice.customer_name)
