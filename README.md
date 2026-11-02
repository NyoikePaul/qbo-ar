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
idempotent payment posting, M-Pesa/bank statement reconciliation, and collections reminders.

```bash
pip install -e ".[dev]"
cp .env.example .env            # Intuit app credentials, then export them
qbo-ar auth seed --refresh-token <token from the Intuit OAuth playground>

qbo-ar aging --tie-out          # buckets by customer, verified against QuickBooks' report
qbo-ar open                     # open invoices with days overdue
qbo-ar dunning --csv out.csv    # reminder tiers (dry run; --send to resend invoices)
qbo-ar reconcile statement.csv  # match receipts to invoices (dry run; --apply to post)
qbo-ar metrics --days 90        # DSO, weighted days overdue, top exposures
```

Anything that writes to QuickBooks is a dry run unless you pass `--send` or `--apply`.
See [docs/04-integration/accounts-receivable-api.md](docs/04-integration/accounts-receivable-api.md).

```bash
make test    # 130+ unit tests, no network
make lint
```

---

**Author:** Nyoike Paul  
**Started:** June 2026
