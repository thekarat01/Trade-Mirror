from __future__ import annotations

from datetime import date
from html import escape
from typing import Any, Mapping

from dashboard.pages.common import page_header, safe_dataframe
from dashboard.pre_trade_checkins import (
    ASSET_TYPES,
    CURRENT_STATUSES,
    OUTCOMES,
    PLAN_FOLLOWED_OPTIONS,
    TRADE_PURPOSES,
    THESIS_STATUSES,
    add_demo_session_checkin,
    checkin_progress_rows,
    checkin_summary,
    create_checkin,
    complete_review,
    decisions_to_review_rows,
    demo_session_checkins,
    load_checkins,
    review_reminder,
    review_summary_for_confirmation,
    summary_for_confirmation,
    update_checkin,
    update_demo_session_review,
    validate_checkin,
    validate_review,
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
    render_decision_reviews(st, data)


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


def render_decision_reviews(st: Any, data: Any) -> None:
    demo_mode = str(getattr(data, "source_label", "")).strip().casefold() == "demo data"
    rows = demo_session_checkins(st.session_state) if demo_mode else load_checkins()
    st.subheader("Decisions to review")
    st.markdown(
        "<div class='tm-note'>Reassess decisions against the plan you wrote down. A disciplined decision can lose money, "
        "and an undisciplined decision can profit.</div>",
        unsafe_allow_html=True,
    )
    safe_dataframe(st, decisions_to_review_rows(rows), empty_message="No completed check-ins are ready to review yet.")
    reviewable = [row for row in rows if row.get("status") in {"completed", "reviewed"}]
    if not reviewable:
        return
    labels = [_review_label(row) for row in reviewable]
    selected_label = st.selectbox("Decision to review", labels)
    selected = reviewable[labels.index(selected_label)]
    _render_timing_correction(st, selected, demo_mode=demo_mode)
    reminder = review_reminder(selected)
    is_upcoming = reminder.startswith("upcoming")
    review_open = not is_upcoming or bool(st.session_state.get(f"review_early_{selected['id']}"))
    if is_upcoming and not review_open:
        st.info(f"This review is {reminder}. Keep it simple for now, or review early if the trigger already occurred.")
        if st.button("Review early", use_container_width=True):
            st.session_state[f"review_early_{selected['id']}"] = True
        return
    values = _review_form_values(st)
    issues = validate_review(values)
    with st.expander("Review summary"):
        safe_dataframe(st, review_summary_for_confirmation(values))
    if issues:
        safe_dataframe(st, [{"Field": issue.field, "Status": issue.reason} for issue in issues])
    if st.button("Save decision review", type="primary", use_container_width=True):
        if issues:
            st.error("Complete the required review fields before saving.")
            return
        if demo_mode:
            update_demo_session_review(st.session_state, str(selected["id"]), values)
        else:
            complete_review(str(selected["id"]), values)
        st.success("Decision review saved locally.")


def _form_values(st: Any) -> dict[str, str]:
    today = date.today().isoformat()
    columns = st.columns(2)
    with columns[0]:
        asset_type = st.selectbox("Asset type", list(ASSET_TYPES))
        trade_purpose = st.selectbox("Trade purpose", list(TRADE_PURPOSES))
        entry_timing = _choice(st, "When was this completed?", {"before_entry": "Before entering the position", "after_entry": "After entering the position"})
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
        "entry_timing": entry_timing,
        "entry_rationale": entry_rationale,
        "intended_holding_period": intended_holding_period,
        "profit_exit_condition": profit_exit_condition,
        "loss_invalidation_condition": loss_invalidation_condition,
        "review_date": review_date,
        "personal_note": personal_note,
    }


def _review_form_values(st: Any) -> dict[str, str]:
    today = date.today().isoformat()
    columns = st.columns(2)
    with columns[0]:
        thesis_status = _choice_with_placeholder(st, "Thesis status", THESIS_STATUSES)
        current_status = _choice_with_placeholder(st, "Current status", CURRENT_STATUSES)
        outcome = _choice_with_placeholder(st, "Outcome", OUTCOMES)
        plan_adherence = _choice_with_placeholder(st, "Did you follow the original plan?", PLAN_FOLLOWED_OPTIONS)
    with columns[1]:
        review_trigger_occurred = _choice(st, "Did the original review trigger occur?", {"": "Select an answer", "yes": "Yes", "no": "No", "uncertain": "Uncertain"})
        decision_review_date = st.text_input("Decision review date", value=today)
        manual_outcome = st.text_input("Optional outcome amount or percent", value="")
        plan_change_reason = st.text_input("Reason for changing the plan", value="")
    with st.expander("Optional notes"):
        review_notes = _text_area(st, "Private review notes", "")
    with st.expander("Optional option details"):
        option_underlying = st.text_input("Underlying", value="")
        option_call_put = _choice(st, "Call or put", {"": "Not provided", "call": "Call", "put": "Put"})
        option_strike = st.text_input("Strike", value="")
        option_expiration = st.text_input("Expiration", value="")
        option_premium_paid = st.text_input("Premium paid", value="")
        option_quantity = st.text_input("Quantity", value="")
    return {
        "thesis_status": thesis_status,
        "current_status": current_status,
        "outcome": outcome,
        "manual_outcome": manual_outcome,
        "review_trigger_occurred": review_trigger_occurred,
        "plan_adherence": plan_adherence,
        "plan_change_reason": plan_change_reason,
        "decision_review_date": decision_review_date,
        "review_notes": review_notes,
        "option_underlying": option_underlying,
        "option_call_put": option_call_put,
        "option_strike": option_strike,
        "option_expiration": option_expiration,
        "option_premium_paid": option_premium_paid,
        "option_quantity": option_quantity,
    }


def _render_timing_correction(st: Any, selected: Mapping[str, Any], *, demo_mode: bool) -> None:
    current = str(selected.get("entry_timing") or "")
    labels = {
        "before_entry": "Before entering the position",
        "after_entry": "After entering the position",
        "unspecified": "Timing not recorded",
        "": "Timing not recorded",
    }
    options = ["Timing not recorded", "Before entering the position", "After entering the position"]
    selected_label = labels.get(current, "Timing not recorded")
    choice = st.selectbox("Check-in timing", options, index=options.index(selected_label))
    new_value = {
        "Before entering the position": "before_entry",
        "After entering the position": "after_entry",
        "Timing not recorded": "unspecified",
    }[choice]
    if new_value != (current or "unspecified") and st.button("Update timing", use_container_width=True):
        if demo_mode:
            rows = demo_session_checkins(st.session_state)
            updated = []
            for row in rows:
                if row.get("id") == selected.get("id"):
                    updated.append({**row, "entry_timing": new_value})
                else:
                    updated.append(row)
            st.session_state["pre_trade_checkins_demo"] = updated
        else:
            update_checkin(str(selected["id"]), {"entry_timing": new_value})
        st.success("Timing updated locally.")


def _text_area(st: Any, label: str, value: str) -> str:
    if hasattr(st, "text_area"):
        return st.text_area(label, value=value)
    return st.text_input(label, value=value)


def _choice(st: Any, label: str, choices: dict[str, str]) -> str:
    display = st.selectbox(label, list(choices.values()))
    for key, value in choices.items():
        if value == display:
            return key
    return next(iter(choices))


def _choice_with_placeholder(st: Any, label: str, values: tuple[str, ...]) -> str:
    labels = ["Select an answer", *[_title_label(value) for value in values]]
    selected = st.selectbox(label, labels)
    if selected == "Select an answer":
        return ""
    for value in values:
        if _title_label(value) == selected:
            return value
    return ""


def _title_label(value: str) -> str:
    return value.replace("_", " ").title()


def _review_label(row: Mapping[str, Any]) -> str:
    review_date = str(row.get("review_date") or "No date")
    asset = str(row.get("asset_type") or "decision")
    identifier = str(row.get("instrument") or "unlabeled")
    return f"{review_date} - {asset} - {identifier}"


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
