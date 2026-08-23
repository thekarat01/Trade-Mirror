from __future__ import annotations

from html import escape
from decimal import Decimal
from typing import Any

from dashboard.formatters import format_currency
from dashboard.pages.common import decimal_chart_rows, metric_card, page_header
from dashboard.patterns_model import PatternValidationError, build_patterns_view_model


def render(st: Any, data: Any) -> None:
    page_header(st, data, "My Patterns", "Evidence-backed patterns from your trusted completed trades.")
    try:
        model = build_patterns_view_model(data)
    except PatternValidationError as exc:
        st.info("Behavioral insights are unavailable for this data.")
        with st.expander("Data Quality detail"):
            for issue in exc.issues[:20]:
                st.write(issue)
        return

    st.markdown(
        f"<div class='tm-note'>{model['disclaimer']}</div>",
        unsafe_allow_html=True,
    )
    st.caption(f"Analysis range: {model['date_range']}")

    _render_coverage(st, model)
    _render_priority(st, model)
    _render_helped_hurt(st, model)
    _render_charts(st, model)
    _render_guardrails(st, model)
    _render_reliability(st, model)


def _render_coverage(st: Any, model: dict[str, Any]) -> None:
    st.subheader("Evidence coverage")
    coverage = model["coverage"]
    columns = st.columns(5)
    labels = [
        "High-confidence completed trades",
        "Limited-confidence trades",
        "Excluded matches",
        "High-confidence coverage",
        "Date range",
    ]
    for column, label in zip(columns, labels):
        with column:
            metric_card(st, label, coverage[label], kind="plain")
    st.write([
        "High-confidence trades drive the findings.",
        "Limited-confidence trades are used only as a separate sensitivity check.",
        "Excluded records do not affect conclusions.",
    ])


def _render_priority(st: Any, model: dict[str, Any]) -> None:
    st.subheader("Priority patterns")
    cards = model["priority_patterns"]
    if not cards:
        st.info("No primary behavioral pattern has enough evidence to show yet.")
        return
    for card in cards:
        _pattern_card(st, card)


def _render_helped_hurt(st: Any, model: dict[str, Any]) -> None:
    left, right = st.columns(2)
    with left:
        st.subheader("What helped")
        if model["what_helped"]:
            for card in model["what_helped"]:
                _pattern_card(st, card, compact=True)
        else:
            st.info("No positive pattern has enough evidence to show as a primary finding.")
    with right:
        st.subheader("What hurt")
        if model["what_hurt"]:
            for card in model["what_hurt"]:
                _pattern_card(st, card, compact=True)
        else:
            st.info("No negative pattern has enough evidence to show as a primary finding.")


def _pattern_card(st: Any, card: dict[str, Any], *, compact: bool = False) -> None:
    safe = {key: escape(str(value)) for key, value in card.items() if key != "supporting_evidence"}
    st.markdown(
        f"""
        <div class='tm-pattern-card'>
          <div class='tm-pattern-kicker'>{safe['finding_type']} · Confidence: {safe['confidence']}</div>
          <div class='tm-pattern-title'>{safe['title']}</div>
          <div><strong>What we observed:</strong> {safe['what_we_observed']}</div>
          <div><strong>Supporting metric:</strong> {safe['supporting_metric']} · eligible trades: {safe['eligible_trade_count']}</div>
          <div><strong>Why it matters:</strong> {safe['why_it_matters']}</div>
          <div><strong>Process guardrail:</strong> {safe['guardrail']}</div>
          <div><strong>Limitation:</strong> {safe['limitation']}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if not compact:
        with st.expander("Supporting evidence"):
            st.write(card["supporting_evidence"])


def _render_charts(st: Any, model: dict[str, Any]) -> None:
    st.subheader("Behavioral evidence")
    charts = model["charts"]
    left, right = st.columns(2)
    with left:
        st.markdown("**Equity versus option historical results**")
        _bar_chart(st, charts["asset_results"], x="Asset type", y="Net realized P&L")
        st.markdown("**Results by holding-period band**")
        _bar_chart(st, charts["holding_period_results"], x="Holding period", y="Net P&L")
        st.markdown("**Loss concentration**")
        _bar_chart(st, charts["loss_concentration"], x="Group", y="Share of gross losses")
    with right:
        st.markdown("**Annual high-confidence trade results**")
        _bar_chart(st, charts["annual_results"], x="Year", y="High-confidence P&L")
        st.markdown("**Monthly activity versus historical results**")
        _point_chart(st, charts["monthly_activity"], x="Month", y="Average P&L", color="Activity segment")
        st.markdown("**Re-entry-after-loss results**")
        if charts["reentry"]:
            _bar_chart(st, charts["reentry"], x="Window", y="Net P&L")
        else:
            st.info("No re-entry-after-loss pattern has enough evidence for a primary chart.")


def _render_guardrails(st: Any, model: dict[str, Any]) -> None:
    st.subheader("Priority guardrails")
    if model["guardrails"]:
        st.dataframe(model["guardrails"], hide_index=True, use_container_width=True)
    else:
        st.info("No evidence-linked guardrail has enough support to prioritize yet.")


def _render_reliability(st: Any, model: dict[str, Any]) -> None:
    reliability = model["reliability"]
    with st.expander("How reliable is this?"):
        st.write("Historical association is not causation. This page summarizes completed-trade evidence only.")
        st.write("Coverage")
        st.dataframe([{"Measure": key, "Value": value} for key, value in reliability["coverage"].items()], hide_index=True, use_container_width=True)
        st.write("Sample-size rules")
        st.dataframe([{"Rule": key, "Minimum": value} for key, value in reliability["sample_rules"].items()], hide_index=True, use_container_width=True)
        st.write("Confidence definitions")
        st.dataframe([{"Confidence": key, "Meaning": value} for key, value in reliability["confidence_definitions"].items()], hide_index=True, use_container_width=True)
        st.write("Sensitivity analysis")
        st.dataframe([{"Check": key, "Result": value} for key, value in reliability["sensitivity"].items()], hide_index=True, use_container_width=True)
        st.write("Validation")
        st.dataframe([{"Check": key, "Result": value} for key, value in reliability["validation"].items()], hide_index=True, use_container_width=True)
        if reliability["limitations"]:
            st.write("Limitations")
            st.write(reliability["limitations"])
        st.caption("See Data Quality for unresolved records and methodology boundaries.")


def _bar_chart(st: Any, rows: list[dict[str, Any]], *, x: str, y: str) -> None:
    if not rows:
        st.info("This evidence chart is unavailable for the selected data.")
        return
    import altair as alt

    st.altair_chart(
        alt.Chart(alt.Data(values=_chart_rows(rows)))
        .mark_bar()
        .encode(
            x=alt.X(f"{x}:N", title=x, sort=None),
            y=alt.Y(f"{y}:Q", title=y, axis=alt.Axis(format="$,.0f") if "P&L" in y else alt.Axis(format=".0f")),
            color=alt.Color(f"{x}:N", legend=None),
            tooltip=[alt.Tooltip(f"{x}:N", title=x), alt.Tooltip(f"{y}:Q", title=y, format="$,.2f" if "P&L" in y else ".2f")],
        ),
        use_container_width=True,
    )


def _point_chart(st: Any, rows: list[dict[str, Any]], *, x: str, y: str, color: str) -> None:
    rows = [row for row in rows if row.get(y) is not None]
    if not rows:
        st.info("This evidence chart is unavailable for the selected data.")
        return
    import altair as alt

    st.altair_chart(
        alt.Chart(alt.Data(values=_chart_rows(rows)))
        .mark_circle(size=70)
        .encode(
            x=alt.X(f"{x}:N", title=x, sort=None),
            y=alt.Y(f"{y}:Q", title=y, axis=alt.Axis(format="$,.0f")),
            color=alt.Color(f"{color}:N", title=color),
            tooltip=[
                alt.Tooltip(f"{x}:N", title=x),
                alt.Tooltip(f"{y}:Q", title=y, format="$,.2f"),
                alt.Tooltip(f"{color}:N", title=color),
            ],
        ),
        use_container_width=True,
    )


def _chart_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    decimal_fields = {
        key
        for row in rows
        for key, value in row.items()
        if isinstance(value, Decimal)
    }
    return decimal_chart_rows(rows, decimal_fields)
