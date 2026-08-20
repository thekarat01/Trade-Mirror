# TradeMirror Product Brief

## Problem

Retail investors can see balances and trade history, but they struggle to
explain why their results persist. Existing dashboards emphasize outcomes;
generic AI assistants may invent causes when the underlying ledger is
incomplete or inconsistent.

## Target user

An active self-directed investor who mixes investing and trading strategies,
has several years of brokerage history, and wants evidence-backed feedback on
decision quality rather than another source of trade ideas.

## Job to be done

> When I review years of trades, help me separate luck from repeatable behavior
> so I can identify which habits, strategies, and risk choices need to change.

## MVP questions

1. How much value was created or destroyed after external cash flows and fees?
2. Which strategies contributed most to gains, losses, and volatility?
3. How did behavior change across market regimes and asset types?
4. Which recurring weaknesses are supported by transaction evidence?
5. Which measurable guardrails would have reduced those weaknesses?

## Non-goals

- Stock recommendations or price predictions
- Autonomous execution or Robinhood order placement
- Personalized tax advice
- Claims about investor psychology without supporting behavioral evidence
- Treating deposits, withdrawals, or internal transfers as investment returns

## Product principles

- **Truth before intelligence:** deterministic accounting precedes AI analysis.
- **Evidence with every claim:** behavioral observations cite underlying events.
- **Confidence is visible:** partial history is labeled, not concealed.
- **No silent correction:** ambiguous rows enter a review queue.
- **Privacy by default:** raw files and identifiers never enter the public repo.

## Sprint 1 success criteria

- Robinhood CSV importer processes all valid records.
- Stock, option, fee, income, funding, and corporate-action events are classified.
- Trade and settlement ledgers remain distinct.
- Privacy-sensitive transfer descriptions receive a sanitized representation.
- December 2020 and July 2026 cash anchors reconcile with documented adjustments.

