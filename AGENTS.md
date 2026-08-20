# TradeMirror Agent Guide

## Purpose

TradeMirror is an evidence-grounded portfolio behavior analyst. Its first job is
to turn brokerage activity into auditable canonical events before any AI
interpretation or performance analysis happens.

## Non-goals

- Do not provide stock recommendations or price predictions.
- Do not automate Robinhood or any brokerage order placement.
- Do not provide personalized tax advice.
- Do not treat deposits, withdrawals, or internal transfers as investment
  returns.

## Privacy Rules

- Raw brokerage exports, statements, tax files, addresses, account numbers, and
  private transfer descriptions must stay out of tracked project files.
- Canonical CSV output is sanitized by default. Raw output requires explicit
  `--include-raw` opt-in and must stay in private local output only.
- Never commit `private/`, `private_output/`, `data/raw/`, statements, tax files,
  or raw brokerage exports.

## Test Command

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

On Windows, use the Python launcher if needed:

```powershell
$env:PYTHONPATH = "src"
py -3.11 -m unittest discover -s tests -v
```

## Definition of Done

- Tests pass.
- Privacy scan passes.
- No private files appear in Git changes.
