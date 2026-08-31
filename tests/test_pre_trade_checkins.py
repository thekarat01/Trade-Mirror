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
from dashboard.pre_trade_checkins import (
    add_demo_session_checkin,
    checkin_progress_rows,
    checkin_summary,
    create_checkin,
    load_checkins,
    save_checkins,
    summary_for_confirmation,
    update_checkin,
    validate_checkin,
)
from dashboard.strategy_discovery import StrategyProfile, build_strategy_discovery_model, with_experiment_response


VALID_CHECKIN = {
    "instrument": "SYNTH",
    "asset_type": "stock",
    "trade_purpose": "investing",
    "entry_rationale": "Testing whether my process note is clear.",
    "intended_holding_period": "one month",
    "profit_exit_condition": "Review if the planned outcome happens.",
    "loss_invalidation_condition": "Review if the thesis no longer applies.",
    "review_date": "2026-09-30",
    "personal_note": "Local note only.",
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

    def test_progress_rows_preserve_unavailable_instead_of_zero(self):
        rows = checkin_progress_rows(checkin_summary([]))
        rendered = json.dumps(rows)
        self.assertIn("Not available", rendered)
        self.assertNotIn("0%", rendered)

    def test_summary_confirmation_uses_plain_language(self):
        rows = summary_for_confirmation(VALID_CHECKIN)
        prompts = {row["Prompt"] for row in rows}
        self.assertEqual(prompts, {"What you intend to do", "Why you intend to do it", "What would invalidate it", "When you will reassess it"})

    def test_strategy_progress_uses_accepted_pre_entry_experiment(self):
        profile = with_experiment_response(StrategyProfile({}, {}, {}), "pre_entry_exit", "accepted")
        summary = checkin_summary([
            {"status": "completed", "loss_invalidation_condition": "Exit rule", "review_date": "2026-09-30", "created_at": "2026-08-31T00:00:00Z"}
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

    def selectbox(self, label: str, options):
        self.selectbox_labels.append(label)
        return list(options)[0]

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
