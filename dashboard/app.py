from __future__ import annotations

import logging
import os
import sys
from functools import partial
from pathlib import Path
from typing import Any, Callable

import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dashboard.data_loader import (
    DEMO_DATA_DIR,
    DashboardData,
    DashboardValidationError,
    ValidationIssue,
    load_dashboard_data,
    load_validated_dashboard_data,
)
from dashboard.pages import ask_trademirror, cash_positions, data_quality, my_patterns, overview, realized_pnl


logging.basicConfig(level=logging.INFO)
LOGGER = logging.getLogger("trademirror.dashboard")

PageRenderer = Callable[[Any, DashboardData], None]
PAGE_DEFINITIONS: tuple[tuple[str, str, PageRenderer], ...] = (
    ("Overview", "overview", overview.render),
    ("My Patterns", "my-patterns", my_patterns.render),
    ("Ask TradeMirror", "ask-trademirror", ask_trademirror.render),
    ("Cash & Positions", "cash-positions", cash_positions.render),
    ("Realized P&L", "realized-pnl", realized_pnl.render),
    ("Data Quality", "data-quality", data_quality.render),
)


def main() -> None:
    st.set_page_config(
        page_title="TradeMirror Dashboard",
        page_icon=None,
        layout="wide",
        initial_sidebar_state="expanded",
    )
    _apply_theme()
    data = _load_data()
    st.navigation(build_pages(data)).run()


def build_pages(data: DashboardData, streamlit_module: Any = st) -> list[Any]:
    return [
        streamlit_module.Page(
            partial(_render_page, renderer, streamlit_module, data),
            title=title,
            url_path=url_path,
        )
        for title, url_path, renderer in PAGE_DEFINITIONS
    ]


def _render_page(renderer: PageRenderer, streamlit_module: Any, data: DashboardData) -> None:
    if data.validation_issues:
        _render_validation_error(data.validation_issues, streamlit_module=streamlit_module)
        return
    renderer(streamlit_module, data)


def _load_data() -> DashboardData:
    root = _configured_data_root()
    source_label = "Demo data" if root.resolve() == DEMO_DATA_DIR.resolve() else "Sanitized local data"
    source_indicator = "Synthetic demo" if source_label == "Demo data" else "Configured sanitized output"
    st.sidebar.caption(f"Data source: {source_indicator}")
    try:
        data = load_validated_dashboard_data(root, source_label=source_label)
    except DashboardValidationError as exc:
        LOGGER.warning("Dashboard data validation failed")
        return DashboardData(
            root=root,
            csv_files={},
            json_files={},
            source_label=source_label,
            validation_issues=exc.issues,
        )
    except Exception as exc:  # pragma: no cover - defensive UI boundary
        LOGGER.exception("Dashboard data load failed")
        st.error("Dashboard data could not be loaded. Check the selected sanitized output directory.")
        return load_dashboard_data(Path("__missing__"), source_label=source_label)
    if data.errors:
        with st.sidebar.expander("Unavailable files", expanded=False):
            for error in data.errors:
                st.write(error)
    return data


def _configured_data_root() -> Path:
    return Path(os.environ.get("TRADEMIRROR_DASHBOARD_DATA", str(DEMO_DATA_DIR)))


def _render_validation_error(issues: tuple[ValidationIssue, ...], *, streamlit_module: Any = st) -> None:
    streamlit_module.error("TradeMirror couldn’t display this data because one or more calculated values are invalid.")
    streamlit_module.subheader("Data Quality")
    streamlit_module.write("Review the generated sanitized outputs and rerun the dashboard after correcting the affected file.")
    rows = [
        {
            "file": issue.filename,
            "field": issue.field,
            "location": issue.location,
            "reason": issue.reason,
        }
        for issue in issues[:20]
    ]
    streamlit_module.dataframe(rows, use_container_width=True, hide_index=True)


def _apply_theme() -> None:
    st.markdown(
        """
        <style>
        :root {
          --tm-navy: #0f172a;
          --tm-slate: #334155;
          --tm-emerald: #047857;
          --tm-red: #b91c1c;
          --tm-soft: #f8fafc;
        }
        .tm-badge {
          display: inline-block;
          line-height: 1.25;
          padding: 0.34rem 0.68rem;
          border-radius: 999px;
          background: #d1fae5;
          color: #065f46;
          font-size: 0.8rem;
          font-weight: 700;
          margin: 0.35rem 0 0.45rem 0;
          border: 1px solid #a7f3d0;
        }
        .tm-note {
          color: var(--tm-slate);
          border-left: 4px solid var(--tm-emerald);
          padding-left: 0.8rem;
          margin: 0.7rem 0 1rem 0;
        }
        .tm-metric {
          border: 1px solid #cbd5e1;
          border-radius: 8px;
          padding: 0.72rem 0.78rem;
          min-height: 6.1rem;
          background: #ffffff;
        }
        .tm-metric-review {
          border-style: dashed;
          border-color: #047857;
          background: #f0fdf4;
        }
        .tm-metric-label {
          color: var(--tm-slate);
          font-size: 0.82rem;
          font-weight: 700;
          margin-bottom: 0.25rem;
        }
        .tm-metric-value {
          color: var(--tm-navy);
          font-size: 1.38rem;
          font-weight: 800;
          line-height: 1.15;
        }
        .tm-metric-help {
          color: var(--tm-slate);
          font-size: 0.78rem;
          margin-top: 0.3rem;
        }
        .tm-loss { color: var(--tm-red); font-weight: 700; }
        .tm-gain { color: var(--tm-emerald); font-weight: 700; }
        .tm-pattern-card {
          border: 1px solid #cbd5e1;
          border-radius: 8px;
          padding: 0.85rem 0.95rem;
          margin: 0.65rem 0;
          background: #ffffff;
        }
        .tm-pattern-card-helped { border-left: 5px solid var(--tm-emerald); }
        .tm-pattern-card-hurt { border-left: 5px solid var(--tm-red); }
        .tm-pattern-card-mixed { border-left: 5px solid #64748b; }
        .tm-pattern-meta {
          display: flex;
          flex-wrap: wrap;
          gap: 0.45rem;
          align-items: center;
          margin-bottom: 0.35rem;
        }
        .tm-direction,
        .tm-confidence {
          display: inline-block;
          border-radius: 999px;
          padding: 0.18rem 0.48rem;
          font-size: 0.72rem;
          font-weight: 800;
        }
        .tm-direction { background: #f1f5f9; color: var(--tm-navy); }
        .tm-confidence { background: #ecfdf5; color: #065f46; }
        .tm-pattern-title {
          color: var(--tm-navy);
          font-size: 1.02rem;
          font-weight: 800;
          margin-bottom: 0.35rem;
        }
        .tm-summary-strip,
        .tm-guardrail-card {
          border: 1px solid #cbd5e1;
          border-radius: 8px;
          padding: 0.85rem 0.95rem;
          margin: 0.65rem 0;
          background: #f8fafc;
        }
        .tm-guardrail-number {
          display: inline-block;
          width: 1.65rem;
          height: 1.65rem;
          line-height: 1.65rem;
          text-align: center;
          border-radius: 999px;
          background: var(--tm-navy);
          color: #ffffff;
          font-weight: 800;
          margin-bottom: 0.45rem;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
