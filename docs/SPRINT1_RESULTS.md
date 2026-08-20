# Sprint 1 Validation Results

## Import integrity

| Check | Result |
|---|---:|
| Valid source records imported | 4,824 |
| Blank source records skipped | 2 |
| Earliest activity date | 2020-09-01 |
| Latest activity date | 2026-08-19 |
| Option events | 1,480 |
| Option events parsed | 1,480 |
| Quantity-suffix events preserved | 231 |
| Sensitive transfer descriptions sanitized | 8 |

Every nonblank record produced one canonical event. No transaction was silently
discarded or automatically deduplicated.

## Review queue

The importer identified 217 identical-looking groups containing 580 total rows.
All were retained and marked `potential_identical_fill`. Because the source
export does not provide a unique order or execution ID, automated deletion would
be unsafe.

No other parsing errors or unknown transaction codes remain in the review queue.

## Statement reconciliation

| Anchor | Settled CSV rows | Documented adjustments | Difference | Status |
|---|---:|---:|---:|---|
| December 2020 | 183 | 2 | $0.00 | Pass |
| July 2026 | 73 | 0 | $0.00 | Pass |

The December adjustments are explicit source-system differences: omitted
brokerage-to-crypto movements and statement aggregation rounding. They are not
hidden balancing entries. July reconciles directly from the CSV settlement
ledger.

## Product implications

1. Behavioral analytics must use activity date, while cash accounting uses
   settlement date.
2. Statement-reported pending trades need a separate bucket.
3. Current ticker labels cannot be assumed to represent historical identity;
   CUSIP and corporate-action mappings are required.
4. Deposits, withdrawals, margin interest, and internal transfers must be
   separated before performance is calculated.
5. AI explanations should only reference metrics produced by this validated
   deterministic layer.

## Next sprint

Sprint 2 will create position and cash ledgers, establish historical security
identity, and calculate the first strategy-level performance metrics.

