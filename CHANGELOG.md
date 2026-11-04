# Changelog

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Versioning: SemVer.

## [0.2.0] - 2026-11-04
### Added
- `reconcile`: match confidence scores, invoice number detection inside reference text,
  `--tolerance` for short-pays, `--rollover` for overpayments.
- `reconcile --queue`: suspense CSV with suggested customers from a payer map learned only
  from posted payments and from fuzzy payer names.
- MIT license, CI on Python 3.10 to 3.13, README rewritten around the toolkit.
