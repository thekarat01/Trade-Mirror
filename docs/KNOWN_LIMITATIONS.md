# Known Limitations

## Description Sanitizer Scope

The description sanitizer is designed for currently observed Robinhood CSV
formats. It is not a universal PII-removal system.

Unrecognized labeled account-number formats may require manual privacy review
before any output is shared or committed.

Public exports must receive an additional privacy scan.

Generalized PII detection is deferred to a later hardening sprint.

Raw statements, tax forms, and brokerage exports must never be committed.

## Position Ledger Review Date Context

Some settled-side option reconciliation/review context currently reports the
trade date instead of the settlement date.

This affects diagnostic date context and may make investigation less precise.

It is deferred to a future ledger-hardening sprint.
