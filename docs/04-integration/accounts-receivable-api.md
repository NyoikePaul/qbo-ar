# Accounts Receivable with the QuickBooks Online API

How the AR cycle you run in the QuickBooks UI maps to the API, and how `qbo-ar` automates it.

## 1. UI process to API call

| Step in QuickBooks | API entity / call | `qbo-ar` |
|---|---|---|
| Create invoice | `POST /invoice` | `invoices.create_invoice` (content-derived `requestid`) |
| Email invoice | `POST /invoice/{id}/send` | `invoices.send_invoice` |
| Receive payment | `POST /payment` with `LinkedTxn` lines | `payments.record_payment` |
| Customer credit | `CreditMemo` | counted in `reports.fetch_unapplied_credits` |
| A/R Aging report | `GET /reports/AgedReceivables` | `aging.build_aging` + `reports.tie_out` |
| Void | `POST /invoice?operation=void` | `invoices.void_invoice` |
| Chase overdue | manual | `dunning.build_reminders` |

## 2. Aging rules

- Age **by due date**: current until the due date passes, then 1-30, 31-60, 61-90, 91+.
- Amounts use `HomeBalance` when present (multi-currency), so totals are in home currency.
- QuickBooks' own A/R total is **open invoices minus unapplied payments and credit memos**.
  `qbo-ar aging --tie-out` compares our net figure with the report and exits `2` on a mismatch.
  Run it for today's date only; invoice balances are current, not historical.

## 3. Applying payments safely

1. **Reference = transaction id.** The bank or M-Pesa receipt number is the idempotency key.
2. **Two layers against double posting**: a local fsynced log (written only after QuickBooks
   confirms) and a `requestid` derived from the reference so a retried HTTP call is de-duplicated.
3. **Allocation**: oldest-first by default, or `specific` invoices. Overpayments stay as
   unapplied credit on the customer; underpayments leave a partial balance.
4. **`PaymentRefNum` is limited to 21 characters**; the full reference is kept in `PrivateNote`.

### Paybill / M-Pesa flow

```
statement CSV  ->  normalise reference  ->  exact invoice-number match  ->  post payment
                                         \->  amount-only match   -> "probable" (human confirms)
                                         \->  no match            -> "unmatched" (suspense)
```

Clients mistype references, so `co-2026-00446`, `CO 2026 00446` and `CO202600446` all match.
Only exact reference matches are auto-posted. Run `qbo-ar reconcile statement.csv` first (dry
run), then `--apply`.

## 4. Reliability

- **Rate limit**: QuickBooks allows about 500 requests/minute per company; the client throttles at 450.
- **Retries**: exponential backoff with jitter, `Retry-After` honoured. A POST is retried on a
  timeout or 5xx **only** when it carries a `requestid`; a 429 is always safe to retry.
- **Refresh tokens rotate**: every refresh may return a new refresh token and invalidate the old
  one, so the new pair is saved atomically (mode 0600) before use.
- **Stale objects** (error 5010): re-read the entity for a fresh `SyncToken`, then retry.
- Failures keep the `intuit_tid` header on the exception, which Intuit support asks for.

## 5. Limits to know

- `send` uses QuickBooks' own email template; the body cannot be customised. `dunning` exports
  custom text (`render_email`, CSV) for your own mailer.
- Credit memo and payment balances are not always filterable server-side; the client falls back
  to a full scan, which is slower on large companies.
- Tests use mocked responses. Run against an Intuit **sandbox** company before production.
