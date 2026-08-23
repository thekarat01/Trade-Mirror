# TradeMirror

TradeMirror is an evidence-grounded portfolio behavior analyst. It converts raw
brokerage activity into auditable financial events before any AI-generated
interpretation is allowed.

The first milestone is the **Portfolio Truth Engine**:

1. Import Robinhood activity without dropping source rows.
2. Normalize equity, option, cash, fee, income, and corporate-action events.
3. Keep decision dates separate from cash-settlement dates.
4. Flag ambiguous and repeated fills instead of silently changing them.
5. Reconcile the normalized ledger to independent account statements.

TradeMirror is not a stock picker, trading bot, tax calculator, or source of
personalized financial advice.

## Quick start

Requires Python 3.11 or newer.

```bash
python -m pip install -e .
trademirror import path/to/robinhood.csv \
  --output private_output/canonical_transactions.csv \
  --report private_output/data_quality_report.json
trademirror cash-ledger path/to/robinhood.csv \
  --output-dir private_output/cash_ledger \
  --as-of 2026-08-19
trademirror position-ledger path/to/robinhood.csv \
  --output-dir private_output/position_ledger \
  --as-of 2026-08-19
trademirror realized-pnl path/to/robinhood.csv \
  --output-dir private_output/realized_pnl \
  --as-of 2026-08-19
trademirror option-realized-pnl path/to/robinhood.csv \
  --output-dir private_output/option_realized_pnl \
  --as-of 2026-08-19
trademirror trusted-trades \
  --equity-dir private_output/realized_pnl \
  --option-dir private_output/option_realized_pnl \
  --output-dir private_output/trusted_trade_baseline
PYTHONPATH=src python -m unittest discover -s tests -v
```

On Windows, the Python launcher is often available even when `python` is not on
PATH:

```powershell
py -3.11 -m pip install -e .
trademirror import path\to\robinhood.csv `
  --output private_output\canonical_transactions.csv `
  --report private_output\data_quality_report.json
trademirror cash-ledger path\to\robinhood.csv `
  --output-dir private_output\cash_ledger `
  --as-of 2026-08-19
trademirror position-ledger path\to\robinhood.csv `
  --output-dir private_output\position_ledger `
  --as-of 2026-08-19
trademirror realized-pnl path\to\robinhood.csv `
  --output-dir private_output\realized_pnl `
  --as-of 2026-08-19
trademirror option-realized-pnl path\to\robinhood.csv `
  --output-dir private_output\option_realized_pnl `
  --as-of 2026-08-19
trademirror trusted-trades `
  --equity-dir private_output\realized_pnl `
  --option-dir private_output\option_realized_pnl `
  --output-dir private_output\trusted_trade_baseline
$env:PYTHONPATH = "src"
py -3.11 -m unittest discover -s tests -v
```

If your system uses `python3`, the zero-dependency test command is:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -v
```

The project has no runtime dependencies outside the Python standard library.
The local dashboard is optional and requires Streamlit:

```bash
python -m pip install -e ".[dashboard]"
python -m streamlit run dashboard/app.py
```

On Windows:

```powershell
py -3.11 -m pip install -e ".[dashboard]"
py -3.11 -m streamlit run dashboard/app.py
```


Raw brokerage files belong in `private/` or `data/raw/`. Both are ignored by
Git. Never commit statements, tax forms, account identifiers, addresses, or raw
bank-transfer descriptions.

Canonical CSV exports are sanitized by default and omit `description_raw` and
`raw_row_json`. Use `--include-raw` only for private local debugging; it prints a
privacy warning and the output must stay out of Git and shared folders.

Cash-ledger outputs are also sanitized by default. They use settlement dates for
cash accounting, keep activity dates for traceability, separate deposits and
withdrawals from trading activity, and write pending trades separately when
`--as-of` is supplied.

Position-ledger outputs are sanitized by default. They maintain separate
trade-date and settled quantity views, prefer CUSIP identity for equities, keep
observed ticker aliases instead of rewriting history, and send unresolved
corporate actions or unmatched option lifecycle events to review.

Realized-P&L outputs are sanitized by default. `trademirror realized-pnl`
performs analytical FIFO lot matching for long equities only, using trade dates
for recognition and settlement dates as audit metadata. It is not tax advice or
an official tax calculation: wash-sale adjustments, specific-lot elections, tax
classification, corporate-action basis transformations, options, crypto, and
market-value returns are deferred.

Option realized-P&L outputs are sanitized by default. `trademirror
option-realized-pnl` performs analytical FIFO lot matching for options while
keeping long and short inventory separate. Exercise and assignment create
basis-transfer records instead of standalone option P&L. Spreads, tax treatment,
wash sales, exercise/assignment stock-basis linkage, short-option strategy ROI,
and market data are deferred.

Trusted-trade outputs are sanitized by default. `trademirror trusted-trades`
classifies completed equity and option lot matches as high confidence, limited
confidence, or excluded so later behavioral analysis can use only deterministic
inputs. It uses opaque stable instrument identifiers and does not expose raw
CUSIPs, raw descriptions, raw-row JSON, account identifiers, or behavioral
conclusions.

## Repository map

- `src/trademirror/`: importer, schema, ledgers, CLI, and reconciliation logic
- `tests/`: privacy-safe synthetic fixtures and automated tests
- `docs/`: product, data-contract, cash-ledger, position-ledger, realized-P&L,
  and trusted-trade documentation
- `dashboard/`: optional local Streamlit dashboard for sanitized outputs
- `demo/dashboard_data/`: sanitized synthetic dashboard output bundle
- `reports/`: sanitized validation summaries only
- `private/`: local statement anchors; intentionally excluded from Git

## Current acceptance gates

- Every nonblank CSV record produces exactly one canonical record.
- Original records remain traceable through source identifiers.
- Option descriptions are parsed into underlying, expiration, type, and strike.
- Potential identical fills are flagged but never deduplicated automatically.
- Statement cash reconciliation must finish with a zero difference after
  explicitly documented source-system adjustments.
