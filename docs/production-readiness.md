# Production readiness checklist

`qbo-ar` has been verified against an Intuit sandbox only. Work through these stages in
order, and do not skip one. Every stage keeps the default dry-run behaviour until the stage
that says otherwise.

## 1. Before connecting to the real company
- [ ] Complete Intuit's production-keys process for the app (requirements change, so check
      developer.intuit.com) and keep the Production keys separate from the Development keys.
- [ ] Take a full backup or export of the QuickBooks company per your accountant's procedure.
- [ ] Agree who may run `--apply` and `--send`. Everyone else uses dry runs only.
- [ ] Store `.env` and `~/.qbo_ar/` on an encrypted disk with `chmod 600`. Never commit them.
- [ ] Set `QBO_ENVIRONMENT=production` in a separate `.env`. Do not edit the sandbox one.

## 2. Read-only trial (suggested: two weeks)
- [ ] Run `qbo-ar aging --tie-out` daily. The tie-out must read OK with a diff of 0.00.
- [ ] Run `qbo-ar reconcile STATEMENT.csv` (dry run only) on every real M-Pesa or bank
      statement. Check each `matched` line by hand against the invoice.
- [ ] Review `~/.qbo_ar/audit.jsonl` and the `--queue` CSV for lines that surprise you.
- [ ] Record any statement format problem and fix it before moving on.

## 3. Controlled first posting
- [ ] Post one receipt that a person has already verified: `reconcile --apply` on a
      one-line statement.
- [ ] In QuickBooks, confirm the payment, its allocation and the deposit account.
- [ ] Re-run the same file and confirm it reports a duplicate and posts nothing.
- [ ] Confirm `qbo-ar aging --tie-out` still reads OK.

## 4. Limited volume
- [ ] Apply one statement at a time, and review the summary and audit log after each run.
- [ ] Keep `--rollover` off until finance has reviewed an overpayment example.
- [ ] Do not use `dunning --send` until the reminder tiers and templates are approved.

## 5. Operating rules
- Reference lines the tool marks `probable` are never posted automatically. A person
  confirms them in QuickBooks.
- Rotate the client secret if it appears in a screenshot, chat or log. Refresh tokens expire
  after about 100 days without use; re-seed with a fresh token if you get `invalid_grant`.
- To undo a wrong payment, void it in QuickBooks, then record the reason against its receipt
  number in your own change log. The tool will not re-post a receipt it has already seen.
- Keep the audit log for the period your accountant requires.
