from __future__ import annotations

from datetime import date
from html import escape
from typing import Any

from dashboard.pages.common import page_header, safe_dataframe
from dashboard.pre_trade_checkins import (
    ASSET_TYPES,
    TRADE_PURPOSES,
    add_demo_session_checkin,
    checkin_progress_rows,
    checkin_summary,
    create_checkin,
    demo_session_checkins,
    load_checkins,
    summary_for_confirmation,
    validate_checkin,
)


def render(st: Any, data: Any) -> None:
    page_header(st, data, "Pre-Trade Check-In", "Document a new position or investment decision before entry.")
    render_checkin_workflow(st, data, show_historical_context=True)


def render_contextual_checkin(st: Any, data: Any) -> None:
    st.subheader("Your current experiment: Define an exit condition before entry")
    st.markdown(
        "<div class='tm-note'>Use this private check-in to document your own reason, invalidation condition, "
        "and review date before a new position or investment decision. TradeMirror does not approve or recommend it.</div>",
        unsafe_allow_html=True,
    )
    if _query_flag(getattr(st, "query_params", {}), "checkin"):
        st.session_state["show_pre_trade_checkin"] = True
    if st.button("Check in before my next decision", use_container_width=True):
        st.session_state["show_pre_trade_checkin"] = True
    if st.session_state.get("show_pre_trade_checkin"):
        render_checkin_workflow(st, data, show_historical_context=False)


def render_checkin_workflow(st: Any, data: Any, *, show_historical_context: bool) -> None:
    demo_mode = str(getattr(data, "source_label", "")).strip().casefold() == "demo data"
    if demo_mode:
        st.info("Synthetic demo mode: check-ins are examples for this browser session only and are not saved.")
    else:
        st.info("Private mode: check-ins are saved locally under an ignored private output directory.")

    st.markdown(
        "<div class='tm-note'>This is behavioral process support. It is not trade approval, investment advice, "
        "a price target, or a return prediction.</div>",
        unsafe_allow_html=True,
    )
    if show_historical_context:
        with st.expander("Historical evidence and methodology"):
            _render_historical_context(st)
    values = _form_values(st)
    issues = validate_checkin(values)
    st.subheader("Check-in summary")
    safe_dataframe(st, summary_for_confirmation(values), empty_message="Complete the form to preview your check-in summary.")
    if issues:
        safe_dataframe(st, [{"Field": issue.field, "Status": issue.reason} for issue in issues])
    if st.button("Save check-in", type="primary", use_container_width=True):
        if issues:
            st.error("Complete the required fields before saving this check-in.")
            return
        if demo_mode:
            add_demo_session_checkin(st.session_state, values, status="completed")
        else:
            create_checkin(values, status="completed")
        st.success("Check-in complete.")

    st.subheader("Progress for accepted experiment")
    rows = demo_session_checkins(st.session_state) if demo_mode else load_checkins()
    safe_dataframe(st, checkin_progress_rows(checkin_summary(rows)))


def _form_values(st: Any) -> dict[str, str]:
    today = date.today().isoformat()
    columns = st.columns(2)
    with columns[0]:
        asset_type = st.selectbox("Asset type", list(ASSET_TYPES))
        trade_purpose = st.selectbox("Trade purpose", list(TRADE_PURPOSES))
        entry_rationale = _text_area(st, "Entry rationale in one or two sentences", "")
    with columns[1]:
        loss_invalidation_condition = st.text_input("Loss or thesis-invalidation condition", value="")
        review_date = st.text_input("Time-based review or exit date", value=today)
    with st.expander("Optional details"):
        instrument = st.text_input("Instrument identifier or symbol", value="")
        intended_holding_period = st.text_input("Intended holding period", value="")
        profit_exit_condition = st.text_input("Profit exit condition", value="")
        personal_note = _text_area(st, "Optional personal note", "")
    return {
        "instrument": instrument,
        "asset_type": asset_type,
        "trade_purpose": trade_purpose,
        "entry_rationale": entry_rationale,
        "intended_holding_period": intended_holding_period,
        "profit_exit_condition": profit_exit_condition,
        "loss_invalidation_condition": loss_invalidation_condition,
        "review_date": review_date,
        "personal_note": personal_note,
    }


def _text_area(st: Any, label: str, value: str) -> str:
    if hasattr(st, "text_area"):
        return st.text_area(label, value=value)
    return st.text_input(label, value=value)


def _query_flag(query_params: Any, name: str) -> bool:
    try:
        value = query_params.get(name)
    except AttributeError:
        return False
    if isinstance(value, list):
        value = value[0] if value else ""
    return str(value).strip().casefold() in {"1", "true", "yes", "open"}


def _render_historical_context(st: Any) -> None:
    context = {
        "Evidence": "Losing trades stayed open longer",
        "Evidence ID": "ev.holding_period",
        "Historical metric": "6 days versus 3 days",
        "Eligible trades": "899",
        "Confidence": "Medium",
        "Limitation": "CSV exports lack intraday ordering; the relationship does not prove causation or predict returns.",
    }
    st.subheader("Historical context")
    st.markdown(
        "<div class='tm-guardrail-card'>"
        + "".join(f"<div><strong>{escape(key)}:</strong> {escape(value)}</div>" for key, value in context.items())
        + "</div>",
        unsafe_allow_html=True,
    )
