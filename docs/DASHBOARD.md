# TradeMirror Dashboard

Sprint 4A adds a local, read-only Streamlit dashboard for inspecting
TradeMirror's deterministic outputs with synthetic demo data.

The dashboard presents facts already calculated by the importer, cash ledger,
position ledger, equity realized-P&L engine and option realized-P&L engine. It
does not recalculate financial accounting in the UI.

## Install

The deterministic CLI remains standard-library only. Streamlit is optional:

```bash
python -m pip install -e ".[dashboard]"
```

On Windows:

```powershell
py -3.11 -m pip install -e ".[dashboard]"
```

## Launch

```bash
python -m streamlit run dashboard/app.py
```

On Windows:

```powershell
py -3.11 -m streamlit run dashboard/app.py
```

The default data directory is `demo/dashboard_data`, which contains sanitized
synthetic outputs generated from `dashboard/generate_demo_data.py`.

## Architecture

- `dashboard/app.py` defines the Streamlit multipage app with `st.Page` and
  `st.navigation`.
- `dashboard/data_loader.py` validates existing CSV and JSON outputs before
  pages render.
- `dashboard/formatters.py` centralizes currency, quantity, date and percentage
  formatting.
- `dashboard/pages/` contains presentation-only pages.
- `demo/dashboard_data/` contains committed synthetic outputs only.

Missing files or columns produce unavailable states instead of fake zeros.
Malformed numeric values are not silently converted to zero.

## Demo Data Regeneration

```bash
PYTHONPATH=src python -m dashboard.generate_demo_data
```

On Windows:

```powershell
$env:PYTHONPATH = "src"
py -3.11 -m dashboard.generate_demo_data
```

The generator uses privacy-safe synthetic canonical records and existing
TradeMirror engines. It does not read Robinhood CSVs, statements, tax forms,
`private/`, `private_output/` or `data/raw/`.

## Privacy Boundaries

Sprint 4A defaults to clearly labeled demo data. Dashboard outputs and demo
files must not contain raw descriptions, raw source-row JSON, account numbers,
addresses, statements, tax files or brokerage exports.

Only sanitized output directories should be selected in the dashboard. Public
exports still require a separate privacy scan.

## Pages

- Overview: included realized P&L, win rate, open positions and review counts.
- Cash & Positions: settlement-date cash, cash-flow categories, equity and
  option position tables, pending settlement.
- Realized P&L: equity versus option comparisons, annual P&L, best and worst
  known-basis securities or contracts, match tables and basis transfers.
- Data Quality: review categories, confidence labels, methodology notes and
  known limitations.

## Known Limitations

The dashboard does not show market value, unrealized return, allocation
percentages, recommendations, predictions, tax advice, spread grouping,
strategy-level option analysis, live prices, uploads, authentication or cloud
deployment. These are out of scope for Sprint 4A.
