# Trusted Trade Dataset

The trusted-trade layer classifies completed realized-P&L lot matches for later
behavioral analysis. It does not generate behavioral conclusions.

The classifier consumes sanitized equity and option realized-P&L outputs. It does
not read raw brokerage exports, raw descriptions, raw-row JSON, account
identifiers, names, addresses, or emails.

## Confidence Levels

`high_confidence` means the completed match is deterministic and reproducible:

- opening and closing events are matched;
- matched quantity is positive;
- required dates and money fields are valid;
- basis and proceeds are known;
- realized P&L reconciles to the row values;
- no unresolved identity, anchor, corporate-action, lifecycle, oversell, or
  basis-transfer review condition is attached.

`limited_confidence` means the match is calculable but has reviewable
uncertainty. The current limited-confidence case is a preserved duplicate-fill
or source-review warning. Limited-confidence rows must stay out of default
high-confidence behavioral metrics.

`excluded` means the completed match or related review item cannot support
deterministic behavioral analysis. Exclusions include unknown basis, invalid
required dates or amounts, unmatched or oversized closing quantities,
unsupported corporate actions, unresolved option lifecycle handling, basis
transfers, rejected or unresolved anchors, and identity ambiguity.

## Privacy

Trusted-trade outputs use stable opaque instrument identifiers. They do not
expose raw CUSIPs, security keys, option structural keys, raw descriptions,
raw-row JSON, or account identifiers.

Review output retains sanitized reason codes and aggregate analytical quantities
needed to reconcile coverage. It intentionally avoids unnecessary brokerage
metadata.

## Outputs

`trademirror trusted-trades` writes:

- `trusted_closed_trades.csv`
- `limited_confidence_trades.csv`
- `coverage_summary.json`
- `exclusion_summary.json`
- `trusted_trade_review.json`

The command expects existing sanitized realized-P&L output directories:

```bash
trademirror trusted-trades \
  --equity-dir private_output/equity_realized_pnl \
  --option-dir private_output/option_realized_pnl \
  --output-dir private_output/trusted_trade_baseline
```

On Windows:

```powershell
trademirror trusted-trades `
  --equity-dir private_output\equity_realized_pnl `
  --option-dir private_output\option_realized_pnl `
  --output-dir private_output\trusted_trade_baseline
```

The private output directory must remain Git-ignored.
