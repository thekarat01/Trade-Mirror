from __future__ import annotations

from html import escape
from decimal import Decimal
from typing import Any

from dashboard.data_loader import overview_metrics
from dashboard.formatters import format_currency, format_date
from dashboard.pages.common import decimal_chart_rows, metric_card, page_header, safe_chart, safe_dataframe, safe_structured_write
from dashboard.patterns_model import PatternValidationError, build_patterns_view_model
from dashboard.strategy_discovery import (
    EXPERIMENT_OPTIONS,
    RESPONSE_OPTIONS,
    build_strategy_discovery_model,
    load_strategy_profile,
    save_strategy_profile,
    with_experiment_response,
    with_follow_up_answer,
    with_hypothesis_response,
)


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
    _render_performance_summary(st, model, data)
    _render_strategy_discovery(st, data)
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
    notes = "\n".join(f"- {escape(str(note))}" for note in model.get("coverage_notes", []))
    if notes:
        st.markdown(notes)


def _render_performance_summary(st: Any, model: dict[str, Any], data: Any | None = None) -> None:
    st.subheader("Performance summary")
    summary = model["performance_summary"]
    columns = st.columns(3)
    for column, label in zip(columns, ("Net realized P&L", "High-confidence completed trades", "Win rate")):
        with column:
            metric_card(st, label, summary[label], kind="plain")
    st.markdown(
        f"<div class='tm-note'>{behavioral_pnl_scope_note(model, data)}</div>",
        unsafe_allow_html=True,
    )
    with st.expander("More performance context"):
        rows = [{"Measure": key, "Value": value} for key, value in summary.items()]
        safe_dataframe(st, rows)


def _render_strategy_discovery(st: Any, data: Any) -> None:
    strategy = build_strategy_discovery_model(data)
    st.subheader("What your history shows")
    safe_dataframe(st, strategy["mirror"])

    if strategy["tensions"]:
        st.subheader("Tensions to review")
        safe_dataframe(st, strategy["tensions"])

    st.subheader("Possible investing approach")
    for hypothesis in strategy["hypotheses"]:
        _strategy_hypothesis_card(st, hypothesis)

    st.subheader("Does this reflect your intention?")
    _render_reflection_controls(st, strategy)

    st.subheader("One process experiment to consider")
    experiment = strategy["experiments"][0] if strategy["experiments"] else None
    if experiment:
        experiment = _render_experiment_controls(st, experiment)
        _experiment_card(st, experiment)
    else:
        st.info("No evidence-linked process experiment is available yet.")

    st.subheader("Progress status")
    safe_structured_write(st, strategy["progress"])


def _strategy_hypothesis_card(st: Any, hypothesis: dict[str, str]) -> None:
    safe = {key: escape(str(value)) for key, value in hypothesis.items()}
    st.markdown(
        f"""
        <div class='tm-pattern-card tm-pattern-card-mixed'>
          <div class='tm-pattern-meta'>
            <span class='tm-direction'>HYPOTHESIS</span>
            <span class='tm-confidence'>Confidence: {safe['confidence']}</span>
          </div>
          <div class='tm-pattern-title'>{safe['title']}</div>
          <div>{safe['hypothesis']}</div>
          <div><strong>Your reflection:</strong> {safe['user_response']}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_reflection_controls(st: Any, strategy: dict[str, Any]) -> None:
    if not hasattr(st, "selectbox") or not hasattr(st, "button"):
        st.info("Reflection controls are unavailable in this display environment.")
        return
    profile = load_strategy_profile()
    hypotheses = strategy["hypotheses"]
    labels = [hypothesis["title"] for hypothesis in hypotheses]
    selected_label = st.selectbox("Hypothesis", labels)
    selected = next(item for item in hypotheses if item["title"] == selected_label)
    response_labels = list(RESPONSE_OPTIONS.values())
    response_label = st.selectbox("Reflection", response_labels)
    response_key = next(key for key, value in RESPONSE_OPTIONS.items() if value == response_label)
    answer = ""
    questions = strategy.get("reflection_questions", [])
    if hasattr(st, "text_input") and questions:
        answer = st.text_input(questions[0]["question"], value="")
    if st.button("Save reflection"):
        updated = with_hypothesis_response(profile, selected["id"], response_key)
        if answer:
            updated = with_follow_up_answer(updated, questions[0]["id"], answer)
        save_strategy_profile(updated)
        st.success("Reflection saved locally.")


def _experiment_card(st: Any, experiment: dict[str, str]) -> None:
    safe = {key: escape(str(value)) for key, value in experiment.items()}
    st.markdown(
        f"""
        <div class='tm-guardrail-card'>
          <div class='tm-pattern-title'>{safe['title']}</div>
          <div><strong>Behavior addressed:</strong> {safe['behavior']}</div>
          <div><strong>Why it may be relevant:</strong> {safe['why_relevant']}</div>
          <div><strong>Measurement period:</strong> {safe['measurement_period']}</div>
          <div><strong>Success metric:</strong> {safe['success_metric']}</div>
          <div><strong>Confidence:</strong> {safe['confidence']}</div>
          <div><strong>Limitation:</strong> {safe['limitation']}</div>
          <div><strong>Status:</strong> {safe['status']}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _render_experiment_controls(st: Any, experiment: dict[str, str]) -> dict[str, str]:
    if not hasattr(st, "selectbox") or not hasattr(st, "button"):
        return experiment
    profile = load_strategy_profile()
    status_label = st.selectbox("Experiment decision", list(EXPERIMENT_OPTIONS.values()))
    status_key = next(key for key, value in EXPERIMENT_OPTIONS.items() if value == status_label)
    if st.button("Save experiment decision"):
        save_strategy_profile(with_experiment_response(profile, experiment["id"], status_key))
        experiment = dict(experiment)
        experiment["status"] = EXPERIMENT_OPTIONS[status_key]
        st.success("Experiment decision saved locally.")
    return experiment


def _render_priority(st: Any, model: dict[str, Any]) -> None:
    st.subheader("Priority patterns")
    cards = model["priority_patterns"]
    if not cards:
        st.info("No primary behavioral pattern has enough evidence to show yet.")
        return
    for card in cards:
        _pattern_card(st, card)


def _render_helped_hurt(st: Any, model: dict[str, Any]) -> None:
    helped = model["what_helped"]
    hurt = model["what_hurt"]
    observations = model.get("observations", [])
    if not helped and not hurt:
        if observations:
            st.subheader("Other observations")
            for card in observations:
                _pattern_card(st, card, compact=True)
        return
    if helped and hurt:
        left, right = st.columns(2)
        with left:
            st.subheader("What helped")
            for card in helped:
                _pattern_card(st, card, compact=True)
        with right:
            st.subheader("What hurt")
            for card in hurt:
                _pattern_card(st, card, compact=True)
    else:
        st.subheader("What helped" if helped else "What hurt")
        cards = helped or hurt
        for card in cards:
            _pattern_card(st, card, compact=True)
        if not helped:
            st.info("No positive pattern has enough additional evidence beyond the priority patterns.")
        if not hurt:
            st.info("No negative pattern has enough additional evidence beyond the priority patterns.")
    if observations:
        st.subheader("Other observations")
        for card in observations:
            _pattern_card(st, card, compact=True)


def _pattern_card(st: Any, card: dict[str, Any], *, compact: bool = False) -> None:
    safe = {key: escape(str(value)) for key, value in card.items() if key != "supporting_evidence"}
    direction_class = {
        "HELPED": "tm-pattern-card-helped",
        "HURT": "tm-pattern-card-hurt",
    }.get(card.get("direction"), "tm-pattern-card-mixed")
    st.markdown(
        f"""
        <div class='tm-pattern-card {direction_class}'>
          <div class='tm-pattern-meta'>
            <span class='tm-direction'>{safe['direction']}</span>
            <span class='tm-confidence'>Confidence: {safe['confidence']}</span>
          </div>
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
            safe_structured_write(st, card["supporting_evidence"])


def _render_charts(st: Any, model: dict[str, Any]) -> None:
    st.subheader("Behavioral evidence")
    charts = model["charts"]
    left, right = st.columns(2)
    with left:
        st.markdown("**Equity versus option historical results**")
        _bar_chart(st, charts["asset_results"], x="Asset type", y="Net realized P&L")
        st.markdown("**Results by holding-period band**")
        _bar_chart(st, charts["holding_period_results"], x="Holding period", y="Net P&L")
    with right:
        st.markdown("**Annual high-confidence trade results**")
        _bar_chart(st, charts["annual_results"], x="Year", y="High-confidence P&L")
        st.markdown("**Loss concentration**")
        _bar_chart(st, charts["loss_concentration"], x="Group", y="Share of gross losses (%)", value_format=".0f")
    with st.expander("Additional evidence"):
        st.markdown("**Monthly activity versus historical results**")
        _point_chart(st, charts["monthly_activity"], x="Month", y="Average P&L", color="Activity segment")
        if charts["reentry"]:
            st.markdown("**Re-entry-after-loss evidence**")
            _reentry_evidence(st, charts["reentry"])
        else:
            st.info("No re-entry-after-loss pattern has enough evidence to chart.")


def _render_guardrails(st: Any, model: dict[str, Any]) -> None:
    st.subheader("Priority guardrails")
    if model["guardrails"]:
        columns = st.columns(len(model["guardrails"]))
        for column, row in zip(columns, model["guardrails"]):
            with column:
                st.markdown(
                    f"""
                    <div class='tm-guardrail-card'>
                      <div class='tm-guardrail-number'>{escape(row['Number'])}</div>
                      <div class='tm-pattern-title'>{escape(row['Pattern'])}</div>
                      <div><strong>Guardrail:</strong> {escape(row['Process guardrail'])}</div>
                      <div><strong>Supporting metric:</strong> {escape(row['Supporting metric'])}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
    else:
        st.info("No evidence-linked guardrail has enough support to prioritize yet.")


def _render_reliability(st: Any, model: dict[str, Any]) -> None:
    reliability = model["reliability"]
    with st.expander("How reliable is this?"):
        st.write("Historical association is not causation. This page summarizes completed-trade evidence only.")
        st.write("Coverage")
        safe_dataframe(st, [{"Measure": key, "Value": value} for key, value in reliability["coverage"].items()])
        st.write("Sample-size rules")
        safe_dataframe(st, [{"Rule": key, "Minimum": value} for key, value in reliability["sample_rules"].items()])
        st.write("Confidence definitions")
        safe_dataframe(st, [{"Confidence": key, "Meaning": value} for key, value in reliability["confidence_definitions"].items()])
        st.write("Sensitivity analysis")
        safe_dataframe(st, [{"Check": key, "Result": value} for key, value in reliability["sensitivity"].items()])
        st.write("Validation")
        safe_dataframe(st, [{"Check": key, "Result": value} for key, value in reliability["validation"].items()])
        if reliability["limitations"]:
            st.write("Limitations")
            safe_structured_write(st, reliability["limitations"])
        st.caption("See Data Quality for unresolved records and methodology boundaries.")


def _bar_chart(st: Any, rows: list[dict[str, Any]], *, x: str, y: str, value_format: str | None = None) -> None:
    rows = [row for row in rows if row.get(y) is not None]
    if not rows:
        st.info("This evidence chart is unavailable for the selected data.")
        return
    import altair as alt

    format_code = value_format or ("$,.0f" if "P&L" in y else ".2f")
    tooltip_format = value_format or ("$,.2f" if "P&L" in y else ".2f")
    tooltips = [
        alt.Tooltip(f"{x}:N", title=x),
        alt.Tooltip(f"{y}:Q", title=y, format=tooltip_format),
    ]
    if rows and "Trade count" in rows[0]:
        tooltips.append(alt.Tooltip("Trade count:Q", title="Eligible trades", format=".0f"))
    chart_rows = _chart_rows(rows)
    chart = (
        alt.Chart(alt.Data(values=chart_rows))
        .mark_bar()
        .encode(
            x=alt.X(f"{x}:N", title=x, sort=None),
            y=alt.Y(f"{y}:Q", title=y, axis=alt.Axis(format=format_code)),
            color=alt.Color(
                "Result:N",
                title="Result",
                scale=alt.Scale(
                    domain=["Positive", "Negative", "Neutral", "Unavailable"],
                    range=["#047857", "#b91c1c", "#64748b", "#94a3b8"],
                ),
            ),
            tooltip=tooltips,
        )
    )
    safe_chart(
        st,
        lambda: st.altair_chart(chart, use_container_width=True),
        fallback_rows=chart_rows,
    )


def _point_chart(st: Any, rows: list[dict[str, Any]], *, x: str, y: str, color: str) -> None:
    rows = [row for row in rows if row.get(y) is not None]
    if not rows:
        st.info("This evidence chart is unavailable for the selected data.")
        return
    import altair as alt

    chart_rows = _chart_rows(rows)
    chart = (
        alt.Chart(alt.Data(values=chart_rows))
        .mark_circle(size=70)
        .encode(
            x=alt.X(f"{x}:N", title=x, sort=None),
            y=alt.Y(f"{y}:Q", title=y, axis=alt.Axis(format="$,.0f")),
            color=alt.Color(f"{color}:N", title=color),
            tooltip=[
                alt.Tooltip(f"{x}:N", title=x),
                alt.Tooltip(f"{y}:Q", title=y, format="$,.2f"),
                alt.Tooltip(f"{color}:N", title=color),
                alt.Tooltip("Trade count:Q", title="Eligible trades", format=".0f"),
            ],
        )
    )
    safe_chart(
        st,
        lambda: st.altair_chart(chart, use_container_width=True),
        fallback_rows=chart_rows,
    )


def _reentry_evidence(st: Any, rows: list[dict[str, Any]]) -> None:
    if len(rows) == 1:
        row = rows[0]
        st.markdown(
            f"""
            <div class='tm-summary-strip'>
              <strong>{escape(str(row['Window']))} after a prior loss:</strong>
              {escape(format_currency(row['Net P&L']))} across {escape(str(row['Trades after prior loss']))} eligible trades.
              Comparison group: {escape(format_currency(row['Comparison P&L']))}.
            </div>
            """,
            unsafe_allow_html=True,
        )
        return
    _bar_chart(st, rows, x="Window", y="Net P&L")


def _chart_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    decimal_fields = {
        key
        for row in rows
        for key, value in row.items()
        if isinstance(value, Decimal)
    }
    return decimal_chart_rows(rows, decimal_fields)


def behavioral_pnl_scope_note(model: dict[str, Any], data: Any | None = None) -> str:
    accounting_scope = ""
    if data is not None:
        accounting_as_of = format_date(overview_metrics(data)["as_of"])
        accounting_scope = (
            f" Overview uses included accounting realized P&L through {accounting_as_of}, so the two values can differ."
        )
    return (
        f"Net realized P&L here is the high-confidence completed-trade behavioral sample for {model['date_range']}. "
        "Limited-confidence trades, excluded matches, unknown-basis records and review-only records stay outside "
        f"this behavioral metric.{accounting_scope} Inspect Supporting evidence, Realized P&L and Data Quality for the source details."
    )
