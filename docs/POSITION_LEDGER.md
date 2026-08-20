# Position Ledger

The position ledger explains how security quantities changed over time from
canonical TradeMirror records. It is deterministic and intentionally narrow: it
does not calculate average cost, tax lots, gains, returns, market value, or
current prices.

## Trade Date Versus Settled Positions

Trade-date positions change on `activity_date`, which reflects when the trading
decision or lifecycle event occurred. Settled positions change on `settle_date`,
which reflects when the security movement is settled.

Both views are kept because they answer different audit questions. A buy can be
owned economically on trade date while still pending settlement for settled
position accounting.

## Security Identity

Equities use CUSIP as the preferred identity when available. If no CUSIP is
present, the ledger falls back to the observed instrument symbol with lower
confidence. That fallback is intentionally cautious because ticker symbols can
change over time and the same ticker text can refer to different securities in
different contexts.

Options use the parsed contract identity:

- underlying
- expiration
- call or put
- strike

The ledger preserves all observed ticker aliases for a security and never merges
securities solely because company names look similar.

## Anchors

Position anchors are privacy-safe verified quantities supplied outside raw
brokerage exports. An anchor contains an anchor date, security identity, and
verified quantity.

Before the anchor date, positions are partial and unanchored. On the anchor
date, the ledger resets the security to the verified quantity. Transactions
before the anchor do not alter or back-calculate that verified quantity. After
the anchor, subsequent transactions derive the position normally.

If an `as_of` date is supplied, future anchors are recorded in the summary but
not applied and do not create future-dated output rows.

## Negative Positions

For equities, a negative quantity is not automatically treated as a genuine
short position. It may simply mean the imported history starts after an earlier
buy or verified opening position. Such rows are marked for review and may need a
position anchor.

For options, long and short direction comes from the transaction code:

- `BTO` increases a long contract position.
- `STC` decreases a long contract position.
- `STO` creates or increases a short contract position.
- `BTC` decreases a short contract position.

Long and short option quantities are tracked separately for the same contract.
An opening transaction never silently consumes inventory on the opposite side.
When both long and short inventory exist for the same contract, lifecycle events
such as expiration, exercise, and assignment are sent to review unless the
applicable side can be determined without guessing.

Expiration, exercise, and assignment close existing contracts only when the
ledger can match them to an open contract quantity. Unmatched lifecycle events
go to review.

## Corporate Actions

Splits, mergers, exchanges, reclassifications, cash-in-lieu, and worthless
security events are identified. The ledger does not automatically transform
quantities unless an explicit ratio or mapping is available. Sprint 2B sends
unresolved corporate actions to review rather than guessing.

## Outputs

`trademirror position-ledger` writes:

- `position_events.csv`
- `positions_as_of.csv`
- `position_history.csv`
- `pending_position_settlement.csv`
- `position_summary.json`
- `position_review.json`

Outputs are sanitized and do not include raw descriptions or raw source rows.
