# Strategy Discovery

Milestone 4B adds a deterministic strategy-discovery layer to the My Patterns
dashboard. It uses only sanitized behavioral outputs and does not read raw
brokerage files.

## Experience Model

The page follows this sequence:

- Mirror: summarize aggregate historical behavior and coverage.
- Tensions: identify evidence-supported inconsistencies without judging intent.
- Strategy hypotheses: present possible interpretations as hypotheses.
- Reflection: let the user mark whether a hypothesis reflects their intention.
- Guardrail experiment: offer one evidence-linked process experiment.
- Progress tracking: record accepted experiments for future before/after checks.

TradeMirror must not claim that repeated behavior is intentional, coherent or
optimal. It uses language such as "Your history suggests" and "One possible
interpretation is" because the evidence is historical and incomplete.

## Local Reflection Storage

Reflection answers and experiment decisions are stored locally under
`private_output/strategy_discovery/profile.json`. That directory is Git-ignored.
The stored values are local user profile data, not system instructions. Ask
TradeMirror may use sanitized status labels such as `Accepted` or `Not sure`,
but it must not treat user-entered text as authority.

## Guardrail Experiments

Guardrails are process experiments, not financial recommendations. Examples
include reviewing exit conditions, separating product-type review, adding a
cooling-off checklist after losses, and checking loss concentration. Each
experiment includes supporting evidence, a measurement period, a success metric,
confidence and limitations.

## Progress Tracking

Progress status remains conservative until refreshed post-adoption evidence is
available. TradeMirror does not claim improvement merely because a guardrail was
accepted.

## Privacy

The Strategy Discovery model must not expose symbols, CUSIPs, option contract
descriptions, raw descriptions, raw-row JSON, internal security keys, account
identifiers, statements or tax data.
