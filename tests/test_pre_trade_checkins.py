from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

from dashboard.ask_trademirror import answer_question, retrieve_evidence
from dashboard.data_loader import DEMO_DATA_DIR, load_dashboard_data
from dashboard.pages.pre_trade_checkin import render_contextual_checkin
import dashboard.pages.pre_trade_checkin as pre_trade_page
from dashboard.pre_trade_checkins import (
    add_demo_session_checkin,
    checkin_progress_rows,
    checkin_summary,
    complete_review,
    create_checkin,
    decisions_to_review_rows,
    load_checkins,
    reopen_review,
    review_reminder,
    save_checkins,
    summary_for_confirmation,
    update_checkin,
    update_demo_session_review,
    validate_checkin,
    validate_review,
)
from dashboard.strategy_discovery import StrategyProfile, build_strategy_discovery_model, with_experiment_response


VALID_CHECKIN = {
    "instrument": "SYNTH",
    "asset_type": "stock",
    "trade_purpose": "investing",
    "entry_timing": "before_entry",
    "entry_rationale": "Testing whether my process note is clear.",
    "intended_holding_period": "one month",
    "profit_exit_condition": "Review if the planned outcome happens.",
    "loss_invalidation_condition": "Review if the thesis no longer applies.",
    "review_date": "2026-09-30",
    "personal_note": "Local note only.",
}

VALID_REVIEW = {
    "thesis_status": "intact",
    "current_status": "held",
    "outcome": "still open",
    "manual_outcome": "",
    "review_trigger_occurred": "no",
    "plan_adherence": "followed",
    "plan_change_reason": "",
    "decision_review_date": "2026-09-30",
    "review_notes": "Private review note.",
    "option_underlying": "PRIV_OPT_UNDERLYING",
    "option_call_put": "call",
    "option_strike": "321.45",
    "option_expiration": "2026-12-18",
    "option_premium_paid": "7.89",
    "option_quantity": "11",
}


class PreTradeCheckInTests(unittest.TestCase):
    def setUp(self) -> None:
        self.data = load_dashboard_data(DEMO_DATA_DIR)

    def test_required_field_validation_is_sanitized(self):
        values = dict(VALID_CHECKIN)
        values["entry_rationale"] = ""
        values["loss_invalidation_condition"] = "Account Number: 123456789"
        issues = validate_checkin(values)
        rendered = json.dumps([issue.__dict__ for issue in issues]).casefold()
        self.assertIn("entry_rationale", rendered)
        self.assertNotIn("123456789", rendered)
        self.assertNotIn("account number", rendered)

    def test_private_local_persistence_stable_ids_and_editing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkins.json"
            created = create_checkin(
                VALID_CHECKIN,
                path=path,
                id_factory=lambda: "checkin_test_1",
                now=lambda: datetime(2026, 8, 31, 12, tzinfo=timezone.utc),
            )
            self.assertEqual(created["id"], "checkin_test_1")
            loaded = load_checkins(path)
            self.assertEqual(loaded[0]["id"], "checkin_test_1")

            updated = update_checkin(
                "checkin_test_1",
                {"status": "reviewed", "personal_note": "Updated local note."},
                path=path,
                now=lambda: datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
            )
            self.assertEqual(updated["status"], "reviewed")
            self.assertEqual(load_checkins(path)[0]["id"], "checkin_test_1")
            self.assertNotEqual(load_checkins(path)[0]["created_at"], load_checkins(path)[0]["updated_at"])

    def test_completing_and_reopening_review_round_trips_privately(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkins.json"
            create_checkin(
                VALID_CHECKIN,
                path=path,
                id_factory=lambda: "checkin_review_1",
                now=lambda: datetime(2026, 8, 31, 12, tzinfo=timezone.utc),
            )

            reviewed = complete_review(
                "checkin_review_1",
                VALID_REVIEW,
                path=path,
                now=lambda: datetime(2026, 9, 15, 12, tzinfo=timezone.utc),
            )
            self.assertEqual(reviewed["status"], "reviewed")
            self.assertEqual(reviewed["decision_review"]["status"], "completed")

            reopened = reopen_review(
                "checkin_review_1",
                path=path,
                now=lambda: datetime(2026, 9, 16, 12, tzinfo=timezone.utc),
            )
            self.assertEqual(reopened["status"], "completed")
            self.assertEqual(reopened["decision_review"]["status"], "reopened")

    def test_demo_session_checkin_does_not_persist_to_private_storage(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkins.json"
            session_state: dict[str, object] = {}
            add_demo_session_checkin(session_state, VALID_CHECKIN)
            self.assertFalse(path.exists())
            self.assertEqual(session_state["pre_trade_checkins_demo"][0]["id"], "demo-1")

    def test_progress_calculations_and_target_completion_logic(self):
        rows = [
            {"status": "completed", "loss_invalidation_condition": "Exit rule", "review_date": "2026-09-30", "created_at": "2026-08-01T00:00:00Z"}
            for _ in range(19)
        ]
        summary = checkin_summary(rows, today=date(2026, 8, 31))
        self.assertEqual(summary["completed_checkins"], 19)
        self.assertEqual(summary["exit_condition_checkins"], 19)
        self.assertEqual(summary["exit_condition_percent"], 100)
        self.assertEqual(summary["status"], "In progress")

        ready = checkin_summary([*rows, rows[0]], today=date(2026, 8, 31))
        self.assertEqual(ready["completed_checkins"], 20)
        self.assertEqual(ready["status"], "Ready for review")

        elapsed = checkin_summary(rows[:1], today=date(2026, 11, 1))
        self.assertEqual(elapsed["status"], "Ready for review")

    def test_review_reminders_cover_upcoming_due_overdue_and_completed(self):
        base = {**VALID_CHECKIN, "status": "completed"}
        self.assertEqual(review_reminder({**base, "review_date": "2026-09-02"}, today=date(2026, 9, 1)), "upcoming in 1 day(s)")
        self.assertEqual(review_reminder({**base, "review_date": "2026-09-01"}, today=date(2026, 9, 1)), "due today")
        self.assertEqual(review_reminder({**base, "review_date": "2026-08-31"}, today=date(2026, 9, 1)), "overdue by 1 day(s)")
        reviewed = {**base, "decision_review": {"status": "completed", "decision_review_date": "2026-08-30"}}
        self.assertEqual(review_reminder(reviewed, today=date(2026, 9, 1)), "completed")

    def test_decisions_to_review_rows_label_pre_entry_and_after_entry(self):
        rows = decisions_to_review_rows([
            {**VALID_CHECKIN, "id": "before", "status": "completed", "review_date": "2026-09-01", "entry_timing": "before_entry"},
            {**VALID_CHECKIN, "id": "after", "status": "completed", "review_date": "2026-09-02", "entry_timing": "after_entry"},
        ], today=date(2026, 9, 1))
        self.assertEqual(rows[0]["Timing"], "Completed before entry")
        self.assertEqual(rows[0]["Reminder"], "due today")
        self.assertEqual(rows[1]["Timing"], "Completed after entry")
        self.assertEqual(rows[1]["Reminder"], "upcoming in 1 day(s)")

    def test_legacy_unknown_timing_is_not_labeled_or_counted_as_pre_entry(self):
        rows = [
            {**VALID_CHECKIN, "id": "legacy", "status": "completed", "review_date": "2026-09-01", "entry_timing": ""},
            {**VALID_CHECKIN, "id": "explicit", "status": "completed", "review_date": "2026-09-02", "entry_timing": "before_entry"},
        ]
        display = decisions_to_review_rows(rows, today=date(2026, 9, 1))
        self.assertEqual(display[0]["Timing"], "Timing not recorded")
        self.assertEqual(display[1]["Timing"], "Completed before entry")
        summary = checkin_summary(rows, today=date(2026, 9, 1))
        self.assertEqual(summary["completed_checkins"], 2)
        self.assertEqual(summary["pre_entry_checkins"], 1)

    def test_timing_can_be_corrected_without_changing_other_content(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkins.json"
            created = create_checkin(
                {**VALID_CHECKIN, "entry_timing": ""},
                path=path,
                id_factory=lambda: "checkin_timing_1",
                now=lambda: datetime(2026, 8, 31, 12, tzinfo=timezone.utc),
            )
            self.assertEqual(created["entry_timing"], "")
            updated = update_checkin(
                "checkin_timing_1",
                {"entry_timing": "after_entry"},
                path=path,
                now=lambda: datetime(2026, 9, 1, 12, tzinfo=timezone.utc),
            )
            self.assertEqual(updated["entry_timing"], "after_entry")
            self.assertEqual(updated["entry_rationale"], VALID_CHECKIN["entry_rationale"])
            self.assertEqual(updated["loss_invalidation_condition"], VALID_CHECKIN["loss_invalidation_condition"])

    def test_review_form_uses_neutral_defaults_and_requires_explicit_answers(self):
        streamlit = _ContextualCheckInStreamlit()
        values = pre_trade_page._review_form_values(streamlit)
        self.assertEqual(values["thesis_status"], "")
        self.assertEqual(values["current_status"], "")
        self.assertEqual(values["outcome"], "")
        self.assertEqual(values["review_trigger_occurred"], "")
        self.assertEqual(values["plan_adherence"], "")
        issues = validate_review(values)
        fields = {issue.field for issue in issues}
        self.assertGreaterEqual(fields, {"thesis_status", "current_status", "outcome", "review_trigger_occurred", "plan_adherence"})
        rendered = json.dumps(values)
        self.assertNotIn("intact", rendered)
        self.assertNotIn("gain", rendered)
        self.assertNotIn("followed", rendered)

    def test_upcoming_review_is_collapsed_until_review_early_is_selected(self):
        streamlit = _ContextualCheckInStreamlit()
        streamlit.session_state = {}
        private_data = replace(self.data, source_label="Private sanitized data")
        upcoming = {**VALID_CHECKIN, "id": "upcoming", "status": "completed", "review_date": "2099-01-01", "entry_timing": "before_entry"}
        with patch("dashboard.pages.pre_trade_checkin.load_checkins", return_value=[upcoming]):
            pre_trade_page.render_decision_reviews(streamlit, private_data)
        self.assertIn("Review early", streamlit.button_labels)
        self.assertNotIn("Thesis status", streamlit.selectbox_labels)
        self.assertNotIn("Save decision review", streamlit.button_labels)

    def test_due_review_shows_full_form_automatically(self):
        streamlit = _ContextualCheckInStreamlit()
        streamlit.session_state = {}
        private_data = replace(self.data, source_label="Private sanitized data")
        due = {**VALID_CHECKIN, "id": "due", "status": "completed", "review_date": date.today().isoformat(), "entry_timing": "before_entry"}
        with patch("dashboard.pages.pre_trade_checkin.load_checkins", return_value=[due]):
            pre_trade_page.render_decision_reviews(streamlit, private_data)
        self.assertIn("Thesis status", streamlit.selectbox_labels)
        self.assertIn("Save decision review", streamlit.button_labels)

    def test_review_progress_counts_plans_without_using_win_rate(self):
        rows = [
            {
                **VALID_CHECKIN,
                "status": "reviewed",
                "created_at": "2026-08-31T00:00:00Z",
                "decision_review": {
                    **VALID_REVIEW,
                    "status": "completed",
                    "decision_review_date": "2026-09-01",
                    "plan_adherence": "followed",
                },
            },
            {
                **VALID_CHECKIN,
                "status": "reviewed",
                "created_at": "2026-08-31T00:00:00Z",
                "decision_review": {
                    **VALID_REVIEW,
                    "status": "completed",
                    "decision_review_date": "2026-10-01",
                    "plan_adherence": "changed",
                },
            },
        ]
        summary = checkin_summary(rows, today=date(2026, 10, 1))
        self.assertEqual(summary["reviews_completed"], 2)
        self.assertEqual(summary["reviews_on_time"], 1)
        self.assertEqual(summary["plans_followed"], 1)
        self.assertEqual(summary["plans_changed"], 1)
        self.assertEqual(summary["decision_quality_evidence"], "Insufficient evidence")
        rendered = json.dumps(checkin_progress_rows(summary)).casefold()
        self.assertIn("decision-quality evidence", rendered)
        self.assertNotIn("win rate", rendered)

    def test_progress_rows_preserve_unavailable_instead_of_zero(self):
        rows = checkin_progress_rows(checkin_summary([]))
        rendered = json.dumps(rows)
        self.assertIn("Not available", rendered)
        self.assertNotIn("0%", rendered)

    def test_summary_confirmation_uses_plain_language(self):
        rows = summary_for_confirmation(VALID_CHECKIN)
        prompts = {row["Prompt"] for row in rows}
        self.assertEqual(prompts, {"What you intend to do", "Why you intend to do it", "What would invalidate it", "When you will reassess it"})

    def test_review_validation_and_malformed_private_records_are_safe(self):
        invalid = dict(VALID_REVIEW)
        invalid["decision_review_date"] = "not-a-date"
        invalid["review_notes"] = "Account Number: 123456789"
        issues = validate_review(invalid)
        rendered = json.dumps([issue.__dict__ for issue in issues]).casefold()
        self.assertIn("decision_review_date", rendered)
        self.assertNotIn("123456789", rendered)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "checkins.json"
            path.write_text("{not valid json", encoding="utf-8")
            self.assertEqual(load_checkins(path), [])

    def test_option_review_fields_remain_private_and_local_only(self):
        private_checkin = {
            **VALID_CHECKIN,
            "status": "reviewed",
            "created_at": "2026-08-31T00:00:00Z",
            "decision_review": {"status": "completed", **VALID_REVIEW},
        }
        private_data = replace(self.data, source_label="Private sanitized data")
        with patch("dashboard.ask_trademirror.load_checkins", return_value=[private_checkin]):
            evidence = retrieve_evidence(private_data, "How many decision reviews completed?", route="guardrail")
        rendered = json.dumps([item.data for item in evidence], default=str)
        self.assertIn("reviews_completed", rendered)
        self.assertNotIn("PRIV_OPT_UNDERLYING", rendered)
        self.assertNotIn("321.45", rendered)
        self.assertNotIn("7.89", rendered)
        self.assertNotIn("2026-12-18", rendered)
        self.assertNotIn("Private review note", rendered)

    def test_strategy_progress_uses_accepted_pre_entry_experiment(self):
        profile = with_experiment_response(StrategyProfile({}, {}, {}), "pre_entry_exit", "accepted")
        summary = checkin_summary([
            {
                "status": "completed",
                "entry_timing": "before_entry",
                "loss_invalidation_condition": "Exit rule",
                "review_date": "2026-09-30",
                "created_at": "2026-08-31T00:00:00Z",
            }
        ])
        model = build_strategy_discovery_model(self.data, profile=profile, pre_trade_summary=summary)
        self.assertEqual(model["progress"]["completed_pre_trade_checkins"], "1")
        self.assertEqual(model["progress"]["exit_condition_before_entry"], "1 of 1")
        self.assertEqual(model["progress"]["target"], "20 completed trades or 90 days")
        self.assertEqual(model["progress"]["post_adoption_result"], "Do not claim improvement yet.")

    def test_no_individual_trade_content_enters_ask_evidence_packet(self):
        private_checkin = dict(VALID_CHECKIN)
        private_checkin["instrument"] = "PRIVATE_SYMBOL"
        private_checkin["entry_rationale"] = "Private rationale text"
        private_checkin["status"] = "completed"
        private_checkin["created_at"] = "2026-08-31T00:00:00Z"
        with patch("dashboard.ask_trademirror.load_checkins", return_value=[private_checkin]):
            evidence = retrieve_evidence(self.data, "Have I documented exit conditions consistently?", route="guardrail")
        rendered = json.dumps([item.data for item in evidence], default=str)
        self.assertIn("pre_trade_checkin_progress", rendered)
        self.assertNotIn("PRIVATE_SYMBOL", rendered)
        self.assertNotIn("Private rationale text", rendered)

    def test_demo_ask_evidence_does_not_read_private_checkin_storage(self):
        with patch("dashboard.ask_trademirror.load_checkins", side_effect=AssertionError("should not read private check-ins")):
            response = answer_question(self.data, "Have I documented exit conditions consistently?")
        self.assertEqual(response["answer_type"], "guardrail")
        self.assertIn("0 completed check-ins", response["answer"])

    def test_ask_answers_checkin_questions_from_aggregate_counts(self):
        private_checkin = {
            **VALID_CHECKIN,
            "status": "completed",
            "created_at": "2026-08-31T00:00:00Z",
        }
        private_data = replace(self.data, source_label="Private sanitized data")
        with patch("dashboard.ask_trademirror.load_checkins", return_value=[private_checkin]):
            response = answer_question(private_data, "Have I documented exit conditions consistently?")
        self.assertEqual(response["answer_type"], "guardrail")
        self.assertIn("1 completed check-ins", response["answer"])
        self.assertNotIn("SYNTH", json.dumps(response))

    def test_ask_answers_review_questions_from_aggregate_counts(self):
        private_checkin = {
            **VALID_CHECKIN,
            "status": "reviewed",
            "created_at": "2026-08-31T00:00:00Z",
            "decision_review": {"status": "completed", **VALID_REVIEW},
        }
        private_data = replace(self.data, source_label="Private sanitized data")
        with patch("dashboard.ask_trademirror.load_checkins", return_value=[private_checkin]):
            response = answer_question(private_data, "How many decision reviews completed and plans followed?")
        self.assertEqual(response["answer_type"], "guardrail")
        self.assertIn("1 decision reviews are complete", response["answer"])
        self.assertIn("Plans followed: 1", response["answer"])
        self.assertNotIn("SYNTH", json.dumps(response))

    def test_demo_review_updates_stay_in_session_state_only(self):
        session_state: dict[str, object] = {}
        add_demo_session_checkin(session_state, VALID_CHECKIN)
        updated = update_demo_session_review(session_state, "demo-1", VALID_REVIEW)
        self.assertEqual(updated["status"], "reviewed")
        self.assertEqual(updated["decision_review"]["status"], "completed")

    def test_contextual_checkin_query_opens_without_primary_navigation(self):
        streamlit = _ContextualCheckInStreamlit()
        render_contextual_checkin(streamlit, self.data)
        self.assertTrue(streamlit.session_state["show_pre_trade_checkin"])
        self.assertIn("Asset type", streamlit.selectbox_labels)
        self.assertIn("Check in before my next decision", streamlit.button_labels)

    def test_existing_strategy_discovery_behavior_remains(self):
        save_path = None
        with tempfile.TemporaryDirectory() as directory:
            save_path = Path(directory) / "checkins.json"
            save_checkins([], save_path)
            self.assertEqual(load_checkins(save_path), [])


class _NoOpContext:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _ContextualCheckInStreamlit:
    def __init__(self) -> None:
        self.session_state: dict[str, object] = {}
        self.query_params = {"checkin": "1"}
        self.selectbox_labels: list[str] = []
        self.button_labels: list[str] = []

    def subheader(self, *_args, **_kwargs):
        return None

    def markdown(self, *_args, **_kwargs):
        return None

    def info(self, *_args, **_kwargs):
        return None

    def error(self, *_args, **_kwargs):
        return None

    def success(self, *_args, **_kwargs):
        return None

    def button(self, label: str, **_kwargs):
        self.button_labels.append(label)
        return False

    def columns(self, count: int):
        return [_NoOpContext() for _ in range(count)]

    def selectbox(self, label: str, options, **kwargs):
        self.selectbox_labels.append(label)
        values = list(options)
        index = int(kwargs.get("index", 0))
        return values[index]

    def text_input(self, _label: str, value: str = "", **_kwargs):
        return value

    def text_area(self, _label: str, value: str = "", **_kwargs):
        return value

    def expander(self, *_args, **_kwargs):
        return _NoOpContext()

    def dataframe(self, *_args, **_kwargs):
        return None


if __name__ == "__main__":
    unittest.main()
