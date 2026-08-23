# Behavioral Insights

The behavioral-insights engine summarizes historical aggregate patterns from
the Milestone 1 trusted-trade dataset. It does not predict prices, recommend
securities, generate buy or sell instructions, claim causation, or use excluded
trades in primary metrics.

## Inputs

Primary findings use only `trusted_closed_trades.csv`, which contains
high-confidence completed trades. `limited_confidence_trades.csv` is loaded only
for a separate sensitivity section. Coverage and exclusion summaries are used to
describe limitations.

The engine does not require raw brokerage exports, raw descriptions, raw-row
JSON, statements, account identifiers, symbols, CUSIPs, or option contract
descriptions.

## Metrics

The engine calculates:

- overall completed-trade count, realized P&L, gross gains, gross losses, win
  rate, average and median gain, average and median loss, gain/loss ratio,
  profit factor, and largest gain/loss shares;
- equity and option aggregates without recalculating option multipliers;
- holding-period bins using calendar days only;
- loss concentration by largest losing trades, opaque instrument group, asset
  type, and year;
- monthly activity using the nearest-rank 75th percentile trade-count threshold;
- same-opaque-instrument re-entry after a completed loss within 7 and 30 days;
- annual aggregates by high-confidence close year.

All monetary arithmetic uses `Decimal`.

## Evidence Safeguards

Sample-size thresholds are intentionally conservative:

- 20 high-confidence completed trades for overall findings;
- 10 eligible trades per compared segment;
- 5 eligible months for activity comparisons;
- 5 eligible re-entry events for re-entry findings.

When a threshold is not met, the candidate is labeled
`insufficient_evidence` and cannot appear as a primary finding.

Confidence labels are deterministic:

- `high`: sample is comfortably above threshold, coverage is not materially
  limited, and limited-confidence sensitivity does not change direction;
- `medium`: sample passes the threshold but is closer to the minimum or has
  sensitivity limits;
- `low`: excluded coverage could materially affect the conclusion;
- `insufficient_evidence`: minimum sample rules are not met.

## Guardrails

Guardrails are educational process checks tied to aggregate evidence. They are
not personalized financial advice and do not mention securities, allocation
percentages, or specific trades.

## Outputs

`trademirror behavioral-insights` writes:

- `behavioral_summary.json`
- `insight_candidates.json`
- `ranked_insights.json`
- `annual_behavior.csv`
- `holding_period_behavior.csv`
- `activity_behavior.csv`
- `reentry_behavior.csv`
- `insight_validation.json`

Example:

```bash
trademirror behavioral-insights \
  --trusted-dir private_output/trusted_trade_baseline \
  --output-dir private_output/behavioral_insights_baseline
```

On Windows:

```powershell
trademirror behavioral-insights `
  --trusted-dir private_output\trusted_trade_baseline `
  --output-dir private_output\behavioral_insights_baseline
```

The private output directory must remain Git-ignored.
