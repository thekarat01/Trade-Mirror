# Cash Ledger

The cash ledger explains settled cash movement over time from canonical
transaction records. It is deterministic accounting, not portfolio performance,
position accounting, tax-lot accounting, or investment advice.

## Settlement Date Drives Cash

Trade decisions happen on `activity_date`, but cash changes when the broker
settles the transaction. The ledger therefore uses `settle_date` for daily cash
accounting and retains `activity_date` on each event for traceability.

## External Flows Are Not Returns

Deposits and withdrawals cross the portfolio boundary. They change account cash,
but they are not investment gains or losses. The ledger separates external
contributions and withdrawals before any later performance work.

## Internal Transfers Are Not Returns

Internal Robinhood transfers move money between sub-accounts or product areas.
They may affect cash visible in one ledger view, but they are not investment
returns. They receive their own cash category.

## Confidence Labels

When no verified opening balance is supplied, the daily ledger starts from zero
and reports cumulative cash change only. These balances are labeled `partial`.

When a verified opening balance and opening date are supplied, that first
balance is labeled `verified`. Later daily balances are calculated from the
verified opening balance plus settled cash events and are labeled `derived`.
Rows before the verified opening date preserve their net cash movement, but
their opening and closing balances are blank and labeled `partial/unanchored`
because the verified balance cannot be back-applied.

Individual cash events are labeled `deterministic` when the source row has valid
dates, a valid amount, and a recognized cash category. Valid rows with source
review warnings are retained in cash totals with a `review` label. Malformed
dates or amounts are excluded from totals and written to the review output.

## Pending Settlement

With an as-of date, trades whose `activity_date` is on or before the as-of date
but whose `settle_date` is after it are written to `pending_settlement.csv`.
Pending trades are excluded from settled cash totals until their settlement date.

## Outputs

The cash-ledger command writes:

- `cash_ledger_daily.csv`
- `cash_ledger_events.csv`
- `pending_settlement.csv`
- `cash_ledger_summary.json`
- `cash_ledger_review.json`

Outputs use sanitized canonical fields only. Raw descriptions and raw source-row
JSON are not included.
