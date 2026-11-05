# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: SemVer.

## [Unreleased]
### Changed
- `reconcile --apply` summary separates duplicates (no action needed) from lines that need
  review.

## [0.3.1] - 2026-11-05
### Fixed
- `reconcile` now recognises invoice numbers written with INV/INVOICE/REF/NO prefixes
  (e.g. `INV-1027`) in the reference text.

## [0.3.0] - 2026-11-05
### Added
- `dunning` writes to the same audit trail (attempt recorded before each resend).
- Audit trail (`~/.qbo_ar/audit.jsonl`): run id, UTC timestamps, every match decision, and
  payment attempts recorded before posting; secrets redacted, payer text never logged.

### Security
- QuickBooks Desktop connector parses XML with `defusedxml` (DTDs and entities rejected).
- SOAP faults no longer echo unexpected internal error text; details are logged locally.

## [0.2.1] - 2026-11-05
### Fixed
- Package metadata said 0.1.0 while the 0.2.0 tag was published; a test now guards this.
### Added
- Security baseline: SECURITY.md, CodeQL, Dependabot, secret scanning with push protection.
- QuickBooks Desktop Web Connector: durable request queue, SOAP service and sessions (`qbd_ar`).
- Coverage floor (93%) and a dependency audit job in CI.

## [0.2.0] - 2026-11-04
### Added
- `reconcile`: match confidence scores, invoice number detection inside reference text,
  `--tolerance` for short-pays, `--rollover` for overpayments.
- `reconcile --queue`: suspense CSV with suggested customers from a payer map learned only
  from posted payments and from fuzzy payer names.
- MIT license, CI on Python 3.10 to 3.13, README rewritten around the toolkit.
