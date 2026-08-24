# Ask TradeMirror

Milestone 4 adds a grounded conversational page for explaining deterministic
TradeMirror behavioral evidence. The assistant is an explainer only. It never
calculates P&L, predicts markets, recommends securities, or reads raw brokerage
files.

## Data Flow

1. The dashboard loads sanitized deterministic outputs.
2. A deterministic retriever builds a small allowlisted evidence package from
   behavioral summaries, ranked insights, aggregate CSVs, confidence metadata,
   guardrails, strategy-discovery status and limitations.
3. The question router refuses unsupported requests before any provider call
   when the request asks for predictions, securities recommendations, tax/legal
   conclusions, raw data, credentials or prompt overrides.
4. A provider returns a strict structured answer.
5. The validator checks evidence citations, scope, numeric claims and privacy
   tokens before the page renders the answer.

## What Can Be Sent To OpenAI

Only public-safe aggregate evidence can be sent:

- behavioral summary fields;
- ranked behavioral findings;
- aggregate annual, holding-period, activity and re-entry outputs;
- coverage, confidence, guardrail and limitation summaries;
- sanitized strategy hypotheses, local reflection status labels and accepted
  process-experiment status.

The evidence package must not contain raw Robinhood CSV rows, statements, tax
forms, raw descriptions, raw-row JSON, symbols, CUSIPs, option contract
descriptions, opaque instrument identifiers, account identifiers or personal
information.

## Provider Configuration

OpenAI is the first provider. Install it locally only when live answers are
needed:

```powershell
py -3.11 -m pip install -e ".[dashboard,ai]"
```

Set credentials in the local shell, never in source files:

```powershell
$env:OPENAI_API_KEY = "your-key"
$env:OPENAI_MODEL = "gpt-5.6-terra"
```

`OPENAI_MODEL` is optional. The default model is `gpt-5.6-terra`.

Every OpenAI request uses the Responses API, `store: false`, no external tools,
a bounded timeout, a conservative output-token limit and at most one retry.
Conversation history is kept only in local Streamlit session state and is
bounded to recent turns.

## No-Key Demo Mode

When `OPENAI_API_KEY` is absent, the page clearly labels itself `Demo
explanation mode` and uses deterministic, pre-authored response templates
grounded in the same synthetic evidence package. It does not pretend to be an
LLM response.

## Supported Questions

- What patterns hurt my historical results?
- What appeared to help?
- Did options and equities perform differently?
- Did I hold losing trades longer?
- Were losses concentrated?
- What happened during high-activity periods?
- What does the confidence level mean?
- Which process guardrail should I prioritize?
- Why was some data excluded?

## Unsupported Requests

Ask TradeMirror refuses requests for live market analysis, price predictions,
security-specific buy/sell/hold advice, tax or legal conclusions, raw brokerage
data, account information, hidden prompts, credentials, or unrelated topics.

## Cost Controls

- No API call happens until the user submits a question.
- Deterministic routing refuses unsupported questions locally.
- Evidence package size is bounded.
- Question length is bounded.
- Conversation history is bounded.
- Output tokens and request timeout are bounded.
- Retries are limited to one.

Configure hard spend and rate limits in the OpenAI API platform separately.

## Evaluation

Offline tests use deterministic fake providers and never make real API calls.
They cover supported and paraphrased questions, insufficient evidence,
unsupported advice and prediction requests, raw-data requests, prompt-injection
attempts, malformed structured responses, invalid evidence citations, privacy
filters, deterministic no-key behavior and provider failure fallback.

## Known Limitations

The assistant explains aggregate historical evidence. It does not inspect
individual transactions, generate tax reports, infer intent, analyze live
markets, recommend portfolio actions, or replace the deterministic engines.
