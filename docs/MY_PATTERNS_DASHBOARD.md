# My Patterns Dashboard

The My Patterns page turns Milestone 2 behavioral outputs into a read-only
dashboard experience. It answers what helped, what hurt, what evidence supports
each pattern, and how reliable that evidence is.

## Data Source

The page reads only sanitized behavioral output files:

- `behavioral_summary.json`
- `insight_candidates.json`
- `ranked_insights.json`
- `insight_validation.json`
- `annual_behavior.csv`
- `holding_period_behavior.csv`
- `activity_behavior.csv`
- `reentry_behavior.csv`

Synthetic demo mode uses the committed `demo/behavioral_data` bundle. Private
mode may point directly at an ignored behavioral-output directory or at a parent
directory containing `behavioral_insights/`.

## Privacy

The page must not render symbols, CUSIPs, option contract descriptions, opaque
instrument IDs, raw descriptions, raw rows, account identifiers or internal
security keys. Loader validation rejects prohibited raw/private fields before
rendering, and the page model strips internal insight codes from normal display.

## Evidence Rules

Primary findings use high-confidence completed trades only. Limited-confidence
trades are shown only as sensitivity context. Excluded records do not affect
primary conclusions.

Low-confidence and insufficient-evidence candidates are not shown as primary
patterns. The page shows fewer than three patterns when fewer than three meet
the evidence threshold.

## Guardrails

Guardrails are process prompts tied to aggregate historical evidence. They do
not recommend securities, allocation percentages, trades or expected returns.

## Reliability

The reliability expander summarizes coverage, sample-size rules, confidence
definitions, sensitivity status, reconciliation checks and known limitations.
Historical association is explicitly not treated as causation.
