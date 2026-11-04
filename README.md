# QuickBooks Learning Hub

Structured learning repository for **QuickBooks Online** and **QuickBooks Desktop** aimed at Finance / ERP professionals (especially those working with D365).

**Focus Areas**: Daily accounting, best practices, reporting, VAT (Kenya), and integration patterns.

## Learning Roadmap

### Phase 1: Fundamentals
- Company Setup & Preferences
- Chart of Accounts (Best Practices)
- Customers, Vendors, Items
- Banking & Reconciliation

### Phase 2: Core Operations
- Sales Cycle
- Purchase Cycle
- Journal Entries & Adjustments
- Tax & VAT Handling

### Phase 3: Advanced
- Reports & Custom Reports
- Accountant Tools
- Budgeting
- Multi-currency

### Phase 4: Integration
- QuickBooks API
- Data Import/Export
- Integration with D365 / Power Automate

## AR Toolkit (`qbo-ar`)

A tested Python toolkit for QuickBooks Online **Accounts Receivable**: aging with tie-out,
idempotent payment posting, M-Pesa/bank statement reconciliation with confidence scoring, a
suspense queue with payer suggestions, and collections reminders.

### Setup (Linux / WSL, Python 3.10+)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env            # fill in the four values below, then: chmod 600 .env
set -a; source .env; set +a     # load them into the shell
qbo-ar auth seed --refresh-token "<token from the Intuit OAuth 2.0 playground>"
qbo-ar aging --tie-out          # first real call: confirms the token and keys work
```

`.env` takes `QBO_CLIENT_ID`, `QBO_CLIENT_SECRET`, `QBO_REALM_ID` and
`QBO_ENVIRONMENT` (`sandbox` or `production`). Write plain values: no quotes, no `<` `>`, no
spaces around `=`. Use the **Development** keys with a sandbox company first. `auth seed` only
stores the token and does not validate it, so run `aging` straight after; `invalid_grant` means
the token is stale or mistyped, so generate a fresh one in the playground. Intuit rotates
refresh tokens, and the toolkit keeps the current one in `~/.qbo_ar/tokens.json`.

### Commands

```bash
qbo-ar aging --tie-out          # buckets by customer, verified against QuickBooks' report
qbo-ar open                     # open invoices with days overdue
qbo-ar dunning --csv out.csv    # reminder tiers (dry run; --send to resend invoices)
qbo-ar reconcile statement.csv  # match receipts to invoices (dry run; --apply to post)
qbo-ar metrics --days 90        # DSO, weighted days overdue, top exposures
```

Anything that writes to QuickBooks is a dry run unless you pass `--send` or `--apply`.

### Reconciling M-Pesa / bank statements

The statement is a CSV with `Receipt No.`, `Completion Time`, `Details`, `Paid In` and
`Account No.` columns (the standard M-Pesa layout; remap with `--map`, see
`qbo-ar reconcile --help`). Only lines with money in are considered.

| Status | Meaning | Confidence | Posted by `--apply`? |
|---|---|---|---|
| `matched` | reference equals an invoice number (case, spaces and hyphens ignored) | 100 full, 95 short within tolerance, 90 partial/over | yes |
| `probable` | invoice number found inside the reference text | 85 | no |
| `probable` | exactly one open invoice has this amount | 70 | no |
| `unmatched` | nothing fits: goes to the suspense queue | 0 | no |
| `duplicate` | receipt number already processed | n/a | no |

```bash
qbo-ar reconcile statement.csv --tolerance 5          # flag short-pays within KES 5
qbo-ar reconcile statement.csv --apply --rollover     # spread an overpayment oldest-first
qbo-ar reconcile statement.csv --queue review.csv     # export unmatched/probable lines
```

- `--tolerance KES`: a short-pay inside the band is flagged "within tolerance". The residual
  stays open in QuickBooks; nothing is written off automatically.
- `--rollover` (with `--apply`): an overpayment clears the referenced invoice, then the same
  customer's other open invoices oldest-first. Any remainder stays as an unapplied credit.
- `--queue CSV`: writes `unmatched` and `probable` lines with a suggested customer and score.
  Suggestions come from a payer map learned **only from payments that actually posted**
  (`~/.qbo_ar/payers.json`) and from fuzzy payer-name matching. They are advisory and never
  auto-post. Masked M-Pesa numbers (`2547***222`) can collide between payers, so learned
  suggestions are capped at 80 and show how many prior payments they rest on.

### Safety

- Dry run by default; `--apply` and `--send` are the only commands that write.
- Posting is idempotent per receipt number (local log plus a QuickBooks `requestid`), so
  re-running the same statement posts nothing twice.
- Keep `.env` and `~/.qbo_ar/` out of git. Try new statements against a sandbox first.

See [docs/04-integration/accounts-receivable-api.md](docs/04-integration/accounts-receivable-api.md).

```bash
make test    # 164 unit tests, no network
make lint
```

---

**Author:** Nyoike Paul  
**Started:** June 2026
