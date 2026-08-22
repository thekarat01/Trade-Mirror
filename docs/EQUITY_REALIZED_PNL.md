# Equity Realized P&L

The equity realized-P&L ledger matches completed stock sales to earlier stock
purchases using deterministic FIFO. It is analytical accounting for TradeMirror
behavior analysis, not tax advice and not an official tax-return calculation.

## Trade Date Recognition

Lot matching uses `activity_date` because that is when the trading decision was
made. Settlement dates are retained on match rows as audit metadata, but they do
not drive realized-P&L recognition in Sprint 3A.

## Methodology

Sprint 3A supports long equities only:

- `Buy` opens a long lot.
- `Sell` closes open long lots using FIFO.
- Partial sales allocate opening cost and closing proceeds proportionally.
- Sales spanning multiple lots produce one match row per lot consumed.
- Identical-looking fills remain separate source events and separate lots.

The matcher uses canonical signed `amount` values. It does not reconstruct cash
from price and quantity and does not multiply option-style values by 100.

## Security Identity

Equities use the same identity convention as the position ledger. CUSIP is
preferred when available. If CUSIP is missing, the observed symbol is used with
lower confidence. Transactions are never matched across different security
identities just because symbols look similar.

## Anchors And Unknown Basis

Position anchors can establish a verified quantity, but they do not establish
cost basis. Anchored equity quantity is represented as an open lot with unknown
basis. If a later sale closes anchored quantity, the match is retained with
blank realized P&L and `basis_status` set to `unknown`.

Unknown-basis matches are excluded from realized gain, realized loss, and net
realized P&L totals. TradeMirror never invents cost basis.

## Deferred Work

The following are deferred beyond Sprint 3A:

- wash-sale adjustments
- specific-lot elections
- tax classification
- corporate-action basis transformations
- option realized P&L
- crypto realized P&L
- market-value returns

Results may differ from Robinhood tax documents because tax-lot elections, wash
sales, and basis adjustments are not implemented.

## Outputs

`trademirror realized-pnl` writes:

- `equity_lot_matches.csv`
- `equity_open_lots.csv`
- `equity_realized_by_security.csv`
- `equity_realized_summary.json`
- `equity_lot_review.json`

Outputs are sanitized and do not include raw descriptions, account identifiers,
or raw source-row JSON.
