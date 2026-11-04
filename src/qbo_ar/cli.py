"""Command line interface: `qbo-ar aging | open | dunning | reconcile | metrics | auth`."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Sequence
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from . import __version__
from .aging import BUCKETS
from .auth import AuthError, TokenManager, TokenStore
from .client import QBOClient, QBOError
from .config import ConfigError, Settings
from .customers import fetch_customers
from .dunning import build_reminders, send_reminders, write_csv
from .invoices import open_invoices
from .metrics import concentration, period_dso, weighted_days_overdue
from .reconcile import apply_matches, match_lines, parse_statement
from .reports import fetch_aged_receivables, fetch_aging, tie_out
from .util import JsonlLog

DEFAULT_LOG_DIR = Path("~/.qbo_ar").expanduser()


def _table(headers: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    cells = [[str(c) for c in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) if cells else len(h)
              for i, h in enumerate(headers)]
    fmt = "  ".join(f"{{:>{w}}}" if i else f"{{:<{w}}}" for i, w in enumerate(widths))
    lines = [fmt.format(*headers), fmt.format(*("-" * w for w in widths))]
    lines += [fmt.format(*r) for r in cells]
    return "\n".join(lines)


def _money(value: Decimal) -> str:
    return f"{value:,.2f}"


def _parse_date(text: str) -> date:
    return date.fromisoformat(text)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="qbo-ar", description="QuickBooks Online A/R toolkit")
    p.add_argument("--version", action="version", version=f"qbo-ar {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    auth = sub.add_parser("auth", help="manage stored OAuth tokens")
    auth_sub = auth.add_subparsers(dest="auth_command", required=True)
    seed = auth_sub.add_parser("seed", help="store a refresh token (Intuit OAuth playground)")
    seed.add_argument("--refresh-token", required=True)
    auth_sub.add_parser("status", help="show token expiry (never prints secrets)")

    aging = sub.add_parser("aging", help="A/R aging report")
    aging.add_argument("--as-of", type=_parse_date, default=None)
    aging.add_argument("--json", action="store_true")
    aging.add_argument("--no-credits", action="store_true", help="ignore unapplied credits")
    aging.add_argument("--tie-out", action="store_true", help="verify against QuickBooks' report")

    op = sub.add_parser("open", help="list open invoices")
    op.add_argument("--customer", help="QuickBooks customer id")

    dun = sub.add_parser("dunning", help="collections reminders (dry-run unless --send)")
    dun.add_argument("--as-of", type=_parse_date, default=None)
    dun.add_argument("--min-balance", type=Decimal, default=Decimal(0))
    dun.add_argument("--csv", type=argparse.FileType("w"), help="write reminders to a CSV file")
    dun.add_argument("--send", action="store_true", help="actually resend invoices via QuickBooks")

    rec = sub.add_parser("reconcile", help="match a bank/M-Pesa statement CSV to open invoices")
    rec.add_argument("--tolerance", default="0",
                     help="KES band: short-pays inside it are flagged; overpays inside it stay put")
    rec.add_argument("--queue", metavar="CSV",
                     help="write unmatched/probable lines with suggested customers to this CSV")
    rec.add_argument("--rollover", action="store_true",
                     help="with --apply: spread an overpayment over the customer's other open "
                          "invoices, oldest first")
    rec.add_argument("statement", type=argparse.FileType("r"))
    rec.add_argument("--map", action="append", default=[], metavar="FIELD=COLUMN",
                     help="override a statement column (txn_id, date, amount, reference, payer)")
    rec.add_argument("--apply", action="store_true", help="post matched lines as payments")
    rec.add_argument("--deposit-account", help="QuickBooks account id to deposit to")

    met = sub.add_parser("metrics", help="DSO, days overdue and concentration")
    met.add_argument("--days", type=int, default=90)
    met.add_argument("--top", type=int, default=5)
    return p


def make_client(settings: Settings | None = None) -> QBOClient:
    settings = settings or Settings.from_env()
    return QBOClient(settings, TokenManager(settings))


def _cmd_auth(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    if args.auth_command == "seed":
        TokenManager(settings).seed(args.refresh_token)
        print(f"Refresh token stored at {settings.token_path}")
        return 0
    tokens = TokenStore(settings.token_path).load()
    if tokens is None:
        print("No tokens stored.")
        return 1
    now = time.time()
    access = max(0, int(tokens.access_expires_at - now))
    print(f"Access token: {'valid for ' + str(access) + 's' if access else 'expired/refresh due'}")
    if tokens.refresh_expires_at:
        print(f"Refresh token expires in {int((tokens.refresh_expires_at - now) / 86400)} days")
    return 0


def _cmd_aging(client: Any, args: argparse.Namespace, today: date) -> int:
    as_of = args.as_of or today
    report = fetch_aging(client, as_of, include_credits=not args.no_credits)
    status = 0
    check = None
    if args.tie_out:
        check = tie_out(report, fetch_aged_receivables(client, as_of))
        if not check.ok:
            status = 2
    if args.json:
        out = report.to_dict()
        if check:
            out["tie_out"] = {"local": str(check.local), "quickbooks": str(check.quickbooks),
                              "difference": str(check.difference), "ok": check.ok}
        print(json.dumps(out, indent=2))
        return status
    rows = [[r.customer_name, *(_money(r.buckets[b]) for b in BUCKETS), _money(r.total)]
            for r in report.rows]
    rows.append(["TOTAL", *(_money(report.totals[b]) for b in BUCKETS), _money(report.grand_total)])
    print(f"A/R aging as of {as_of}\n")
    print(_table(["Customer", *BUCKETS, "Total"], rows))
    print(f"\nUnapplied credits: {_money(report.unapplied)}   Net A/R: {_money(report.net_total)}")
    if check:
        verdict = "OK" if check.ok else "MISMATCH"
        print(f"Tie-out vs QuickBooks: {verdict} (local {_money(check.local)}, "
              f"QBO {_money(check.quickbooks)}, diff {_money(check.difference)})")
    return status


def _cmd_open(client: Any, args: argparse.Namespace, today: date) -> int:
    invoices = open_invoices(client, customer_id=args.customer)
    rows = [[i.doc_number or i.id, i.customer_name or "", i.effective_due_date or "",
             i.days_overdue(today), _money(i.balance)] for i in invoices]
    print(_table(["Invoice", "Customer", "Due", "Days overdue", "Balance"], rows))
    return 0


def _cmd_dunning(client: Any, args: argparse.Namespace, today: date) -> int:
    as_of = args.as_of or today
    reminders = build_reminders(open_invoices(client), as_of, min_balance=args.min_balance)
    if args.csv:
        write_csv(reminders, args.csv)
    customers = fetch_customers(client, (r.customer_id for r in reminders if r.customer_id))
    log = JsonlLog(DEFAULT_LOG_DIR / "dunning.jsonl")
    outcomes = send_reminders(client, reminders, log, customers, dry_run=not args.send)
    rows = [[o.reminder.customer_name, o.reminder.tier.name, o.reminder.oldest_days_overdue,
             _money(o.reminder.overdue_total), o.action] for o in outcomes]
    print(_table(["Customer", "Tier", "Oldest (days)", "Overdue", "Action"], rows))
    if not args.send:
        print("\nDry run: nothing was sent. Re-run with --send to resend invoices.")
    return 0


def _cmd_reconcile(client: Any, args: argparse.Namespace) -> int:
    columns = dict(m.split("=", 1) for m in args.map)
    lines = parse_statement(args.statement, columns)
    log = JsonlLog(DEFAULT_LOG_DIR / "processed.jsonl")
    invoices = open_invoices(client)
    results = match_lines(lines, invoices, tolerance=args.tolerance,
                          already_processed=lambda t: log.seen(f"payment:{t}"))
    rows = [[r.line.txn_id, _money(r.line.amount), r.line.reference, r.status,
             (r.invoice.doc_number if r.invoice else "") or "", r.reason] for r in results]
    print(_table(["Txn", "Amount", "Reference", "Status", "Invoice", "Note"], rows))
    from .suspense import DEFAULT_PAYER_MAP, PayerMap, learn_from_posted, write_queue

    payers = PayerMap(DEFAULT_PAYER_MAP)
    if args.queue:
        count = write_queue(args.queue, results, invoices, payers)
        print(f"\nWrote {count} line(s) to {args.queue} for review.")
    if args.apply:
        summary = apply_matches(client, results, log, deposit_account_id=args.deposit_account,
                                all_open=invoices, rollover=args.rollover,
                                tolerance=args.tolerance)
        print(f"\nPosted {len(summary.posted)} payment(s); {len(summary.skipped)} left for review.")
        learn_from_posted(payers, summary)
    else:
        matched = sum(r.status == "matched" for r in results)
        print(f"\nDry run: {matched} line(s) would be posted. Re-run with --apply.")
    return 0


def _cmd_metrics(client: Any, args: argparse.Namespace, today: date) -> int:
    report = fetch_aging(client, today)
    invoices = open_invoices(client)
    value = period_dso(client, report, args.days)
    print(f"Net A/R:              {_money(report.net_total)}")
    print(f"Past due:             {_money(report.past_due_total)}")
    print(f"DSO ({args.days}d):           {value if value is not None else 'n/a (no sales)'}")
    print(f"Weighted days overdue: {weighted_days_overdue(invoices, today)}")
    print("\nTop customers by exposure:")
    for c in concentration(report, args.top):
        print(f"  {c.customer:<30} {_money(c.amount):>14}  {c.share * 100:5.1f}%")
    return 0


def main(argv: Sequence[str] | None = None, *, client: Any | None = None,
         today: date | None = None) -> int:
    args = build_parser().parse_args(argv)
    today = today or date.today()
    try:
        if args.command == "auth":
            return _cmd_auth(args)
        client = client or make_client()
        if args.command == "aging":
            return _cmd_aging(client, args, today)
        if args.command == "open":
            return _cmd_open(client, args, today)
        if args.command == "dunning":
            return _cmd_dunning(client, args, today)
        if args.command == "reconcile":
            return _cmd_reconcile(client, args)
        return _cmd_metrics(client, args, today)
    except (ConfigError, AuthError, QBOError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
