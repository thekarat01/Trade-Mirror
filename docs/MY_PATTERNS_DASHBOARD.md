# My Patterns Dashboard

The My Patterns page turns Milestone 2 behavioral outputs into a dashboard
experience. It answers what the history shows, what helped, what hurt, what
evidence supports each pattern, and how reliable that evidence is.

Milestone 4B adds Strategy Discovery inside the same page. The flow is:

- What your history shows
- Tensions to review
- Possible investing approach
- Does this reflect your intention?
- One process experiment to consider
- Progress status

Hypotheses are possible interpretations, not conclusions. TradeMirror does not
claim that repeated behavior is intended, coherent or optimal.

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

Strategy Discovery can store reflection choices and process-experiment
decisions locally under `private_output/strategy_discovery/profile.json`. That
file is ignored by Git and should not be committed. User-entered profile data is
treated as untrusted data, not as an instruction to the assistant.

## Reliability

The reliability expander summarizes coverage, sample-size rules, confidence
definitions, sensitivity status, reconciliation checks and known limitations.
Historical association is explicitly not treated as causation.
