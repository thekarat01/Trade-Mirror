# Option Realized P&L

The option realized-P&L ledger matches completed option closes and supported
lifecycle events to earlier option-opening transactions using deterministic
FIFO. It is analytical accounting for TradeMirror behavior analysis, not tax
advice and not an official tax-return calculation.

## Trade Date Recognition

Lot matching uses `activity_date` because that is when the trading decision or
lifecycle event occurred. Settlement dates are retained as audit metadata but
do not drive realized-P&L recognition in Sprint 3B.

## Contract Identity

Options are matched only by the canonical option contract identity:

- CUSIP, when present
- underlying
- expiration
- call or put
- strike

CUSIP is treated as the strongest option-contract identifier. If both events
have CUSIPs, the CUSIPs must match. If neither event has a CUSIP, TradeMirror
falls back to the structural contract fields with lower identity confidence. If
only one event has a CUSIP, structural fallback is allowed only when that
structural contract maps unambiguously to one known CUSIP; otherwise the event
is sent to review instead of being guessed.

Different contracts are never matched merely because they share an underlying
symbol or identical displayed terms with different known CUSIPs. CUSIPs are
security identifiers, not account identifiers, so they may appear in sanitized
outputs.

## Side Semantics

Long and short option inventory are maintained separately for each exact
contract:

- `BTO` opens or increases long inventory.
- `STC` closes long inventory using FIFO.
- `STO` opens or increases short inventory.
- `BTC` closes short inventory using FIFO.

Opening trades never consume opposite-side inventory, and closing trades never
consume inventory from the wrong side.

## Realized-P&L Rules

Long option closes calculate P&L as allocated `STC` proceeds minus allocated
`BTO` cost. Short option closes calculate P&L as allocated `STO` opening credit
minus allocated `BTC` closing cost.

Long expirations close with zero proceeds, so the known opening cost becomes a
loss. Short expirations close with zero closing cost, so the known opening
credit becomes a gain.

The ledger uses canonical signed transaction `amount` values. It does not
reconstruct cash from price and quantity and does not multiply by 100.

## Exercise And Assignment

Exercise closes long option inventory only. Assignment closes short option
inventory only.

Exercise and assignment do not create standalone realized option P&L in Sprint
3B. Instead, they create `option_basis_transfers.csv` rows with
`basis_transfer_required` so the known option premium or credit can be linked
to the resulting stock basis or proceeds in a later sprint. TradeMirror does
not attempt that stock linkage yet.

## Anchors And Unknown Basis

Position anchors can establish verified option quantity but do not establish
premium basis unless a later hardening sprint adds trusted basis anchors.
Anchored option quantity is represented as unknown-basis long or short lots.

Closures against unknown-basis anchored quantity are retained with blank
realized P&L and `basis_status` set to `unknown`. Unknown-basis matches are
excluded from realized gain, realized loss, and net realized P&L totals.

## Return Metrics

Known-basis long-option closes report `realized_return_pct` as realized P&L
divided by allocated opening cost.

Short-option P&L is not labeled ROI because collateral, maximum risk, and
strategy context are unknown. For short closes, the output may include
`pnl_to_opening_credit_pct`, a descriptive ratio to the opening credit.

## Deferred Work

The following are deferred beyond Sprint 3B:

- spread grouping and strategy-level P&L
- tax-lot elections
- wash-sale adjustments
- tax treatment
- exercise/assignment stock-basis linkage
- short-option strategy ROI
- market data, Greeks, implied volatility, and probability calculations

Results may differ from brokerage tax documents because tax treatment, tax-lot
elections, wash sales, and basis adjustments are not implemented.

## Outputs

`trademirror option-realized-pnl` writes:

- `option_lot_matches.csv`
- `option_open_lots.csv`
- `option_basis_transfers.csv`
- `option_realized_by_contract.csv`
- `option_realized_summary.json`
- `option_lot_review.json`

Outputs are sanitized and do not include raw descriptions, account identifiers,
or raw source-row JSON.
