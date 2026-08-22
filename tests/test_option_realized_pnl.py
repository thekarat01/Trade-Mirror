from datetime import date
from decimal import Decimal
from pathlib import Path
import csv
import json
import os
import subprocess
import sys
import tempfile
import unittest

from trademirror.option_realized_pnl import build_option_realized_pnl


def option_record(
    source_row_id,
    activity_date,
    settle_date,
    code,
    event_type,
    quantity,
    amount,
    *,
    underlying="ACME",
    expiration="2021-01-15",
    option_type="call",
    strike="7.50",
    cusip="",
    lifecycle_side="",
    review_status="validated",
    review_reasons="",
):
    return {
        "source_row_id": source_row_id,
        "activity_date": activity_date,
        "settle_date": settle_date,
        "transaction_code_raw": code,
        "transaction_family": "option_lifecycle" if event_type in {"expiration", "exercise", "assignment"} else "option_trade",
        "event_type": event_type,
        "asset_type": "option",
        "quantity_numeric": quantity,
        "amount": amount,
        "instrument": underlying,
        "cusip": cusip,
        "review_status": review_status,
        "review_reasons": review_reasons,
        "option_underlying": underlying,
        "option_expiration": expiration,
        "option_type": option_type,
        "option_strike": strike,
        "lifecycle_side": lifecycle_side,
    }


def equity_record(source_row_id):
    return {
        "source_row_id": source_row_id,
        "activity_date": "2021-01-01",
        "settle_date": "2021-01-04",
        "transaction_code_raw": "Buy",
        "transaction_family": "trade",
        "event_type": "buy",
        "asset_type": "equity",
        "quantity_numeric": "1",
        "amount": "-10",
        "instrument": "ACME",
        "cusip": "123456789",
        "review_status": "validated",
        "review_reasons": "",
    }


class OptionRealizedPnlTests(unittest.TestCase):
    def test_profitable_and_losing_long_closes(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-04", "BTO", "buy_to_open", "1", "-10"),
            option_record(2, "2021-01-05", "2021-01-06", "STC", "sell_to_close", "1", "15"),
            option_record(3, "2021-01-07", "2021-01-08", "BTO", "buy_to_open", "1", "-20"),
            option_record(4, "2021-01-09", "2021-01-11", "STC", "sell_to_close", "1", "5"),
        ])
        self.assertEqual([row["realized_pnl"] for row in result["matches"]], ["5", "-15"])
        self.assertEqual(result["summary"]["realized_gain"], "5")
        self.assertEqual(result["summary"]["realized_loss"], "-15")
        self.assertEqual(result["summary"]["net_realized_pnl"], "-10")
        self.assertEqual(result["matches"][0]["realized_return_pct"], "50")

    def test_profitable_and_losing_short_closes(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-04", "STO", "sell_to_open", "1", "30"),
            option_record(2, "2021-01-05", "2021-01-06", "BTC", "buy_to_close", "1", "-10"),
            option_record(3, "2021-01-07", "2021-01-08", "STO", "sell_to_open", "1", "5"),
            option_record(4, "2021-01-09", "2021-01-11", "BTC", "buy_to_close", "1", "-20"),
        ])
        self.assertEqual([row["position_side"] for row in result["matches"]], ["short", "short"])
        self.assertEqual([row["realized_pnl"] for row in result["matches"]], ["20", "-15"])
        self.assertEqual(result["matches"][0]["realized_return_pct"], "")
        self.assertEqual(result["matches"][0]["pnl_to_opening_credit_pct"], "66.66666666666666666666666667")

    def test_partial_close_and_close_spanning_multiple_lots(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "2", "-20"),
            option_record(2, "2021-01-02", "2021-01-02", "BTO", "buy_to_open", "2", "-60"),
            option_record(3, "2021-01-03", "2021-01-03", "STC", "sell_to_close", "3", "90"),
        ])
        self.assertEqual([row["opening_event_id"] for row in result["matches"]], ["1", "2"])
        self.assertEqual([row["matched_quantity"] for row in result["matches"]], ["2", "1"])
        self.assertEqual([row["allocated_opening_cost"] for row in result["matches"]], ["20", "30"])
        self.assertEqual(result["open_lots"][0]["remaining_quantity"], "1")

    def test_long_and_short_expiration(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "1", "-12"),
            option_record(2, "2021-01-15", "2021-01-15", "OEXP", "expiration", "1", "0"),
            option_record(3, "2021-02-01", "2021-02-01", "STO", "sell_to_open", "1", "8", expiration="2021-02-19"),
            option_record(4, "2021-02-19", "2021-02-19", "OEXP", "expiration", "1", "0", expiration="2021-02-19"),
        ])
        self.assertEqual([row["outcome"] for row in result["matches"]], ["expired", "expired"])
        self.assertEqual([row["realized_pnl"] for row in result["matches"]], ["-12", "8"])

    def test_exercise_and_assignment_create_basis_transfers(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "1", "-12"),
            option_record(2, "2021-01-10", "2021-01-10", "OEXCS", "exercise", "1", "0"),
            option_record(3, "2021-02-01", "2021-02-01", "STO", "sell_to_open", "1", "8", expiration="2021-02-19"),
            option_record(4, "2021-02-10", "2021-02-10", "OASGN", "assignment", "1", "0", expiration="2021-02-19"),
        ])
        self.assertEqual(result["summary"]["basis_transfer_count"], 2)
        self.assertEqual([row["realized_pnl"] for row in result["matches"]], ["", ""])
        self.assertEqual([row["basis_transfer_required"] for row in result["matches"]], ["true", "true"])
        self.assertEqual(result["basis_transfers"][0]["premium_cost_to_transfer"], "12")
        self.assertEqual(result["basis_transfers"][1]["premium_credit_to_transfer"], "8")
        self.assertEqual(result["summary"]["net_realized_pnl"], "0")

    def test_ambiguous_expiration_with_both_sides_open_enters_review(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "1", "-10"),
            option_record(2, "2021-01-02", "2021-01-02", "STO", "sell_to_open", "1", "10"),
            option_record(3, "2021-01-15", "2021-01-15", "OEXP", "expiration", "1", "0"),
        ])
        self.assertEqual(result["matches"], [])
        self.assertEqual(len(result["open_lots"]), 2)
        self.assertIn("ambiguous_option_expiration_side", result["review"]["issues"][0]["review_reason"])

    def test_expiration_uses_explicit_side_when_both_sides_open(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "1", "-10"),
            option_record(2, "2021-01-02", "2021-01-02", "STO", "sell_to_open", "1", "10"),
            option_record(3, "2021-01-15", "2021-01-15", "OEXP", "expiration", "1", "0", lifecycle_side="short"),
        ])
        self.assertEqual(result["matches"][0]["position_side"], "short")
        self.assertEqual(result["matches"][0]["realized_pnl"], "10")
        self.assertEqual(result["open_lots"][0]["position_side"], "long")

    def test_opening_trades_do_not_net_opposite_side_inventory(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "STO", "sell_to_open", "1", "10"),
            option_record(2, "2021-01-02", "2021-01-02", "BTO", "buy_to_open", "1", "-10"),
        ])
        self.assertEqual(result["matches"], [])
        self.assertEqual(
            sorted((row["position_side"], row["remaining_quantity"]) for row in result["open_lots"]),
            [("long", "1"), ("short", "1")],
        )

    def test_oversized_and_empty_inventory_closes_enter_review(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "1", "-10"),
            option_record(2, "2021-01-02", "2021-01-02", "STC", "sell_to_close", "3", "30"),
            option_record(3, "2021-01-03", "2021-01-03", "BTC", "buy_to_close", "2", "-10"),
        ])
        self.assertEqual(result["matches"][0]["matched_quantity"], "1")
        self.assertEqual(result["summary"]["unmatched_quantity"], "4")
        reasons = "|".join(issue["review_reason"] for issue in result["review"]["issues"])
        self.assertIn("oversized_option_close", reasons)
        self.assertIn("unmatched_option_close", reasons)

    def test_cusip_close_oversize_preserves_structural_matched_lot_context(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "1", "-10"),
            option_record(2, "2021-01-02", "2021-01-02", "STC", "sell_to_close", "2", "30", cusip="111111111"),
        ])
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(result["matches"][0]["matched_quantity"], "1")
        self.assertEqual(result["matches"][0]["realized_pnl"], "5")
        issue = next(
            issue for issue in result["review"]["issues"]
            if "oversized_option_close" in issue["review_reason"]
        )
        self.assertEqual(issue["closing_security_key"], "option:ACME:2021-01-15:call:7.50:cusip:111111111")
        self.assertEqual(issue["closing_option_cusip"], "111111111")
        self.assertEqual(issue["matched_lot_security_key"], "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(issue["matched_lot_option_cusip"], "")
        self.assertEqual(issue["matched_lot_identity_confidence"], "lower_structural_only")
        self.assertEqual(issue["requested_quantity"], "2")
        self.assertEqual(issue["matched_quantity"], "1")
        self.assertEqual(issue["unmatched_quantity"], "1")
        self.assertEqual(issue["applicable_side"], "long")
        self.assertEqual(issue["resolution_method"], "mixed_cusip_structural")

    def test_structural_long_oversize_preserves_event_and_matched_lot_context(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "1", "-10"),
            option_record(2, "2021-01-02", "2021-01-02", "STC", "sell_to_close", "2", "30"),
        ])
        issue = next(
            issue for issue in result["review"]["issues"]
            if "oversized_option_close" in issue["review_reason"]
        )
        self.assertEqual(result["matches"][0]["matched_quantity"], "1")
        self.assertEqual(result["matches"][0]["realized_pnl"], "5")
        self.assertEqual(issue["security_key"], "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(issue["underlying"], "ACME")
        self.assertEqual(issue["option_expiration"], "2021-01-15")
        self.assertEqual(issue["option_type"], "call")
        self.assertEqual(issue["option_strike"], "7.50")
        self.assertEqual(issue["identity_confidence"], "lower_structural_only")
        self.assertEqual(issue["matched_lot_security_key"], "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(issue["matched_lot_identity_confidence"], "lower_structural_only")
        self.assertEqual(issue["requested_quantity"], "2")
        self.assertEqual(issue["matched_quantity"], "1")
        self.assertEqual(issue["unmatched_quantity"], "1")
        self.assertEqual(issue["applicable_side"], "long")
        self.assertEqual(issue["unmatched_reason"], "oversized_option_close")
        self.assertEqual(issue["resolution_method"], "same_identity")
        self.assertEqual(result["realized_by_contract"][0]["security_key"], "option:ACME:2021-01-15:call:7.50")

    def test_structural_short_oversize_preserves_event_and_matched_lot_context(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "STO", "sell_to_open", "1", "10"),
            option_record(2, "2021-01-02", "2021-01-02", "BTC", "buy_to_close", "2", "-30"),
        ])
        issue = next(
            issue for issue in result["review"]["issues"]
            if "oversized_option_close" in issue["review_reason"]
        )
        self.assertEqual(result["matches"][0]["matched_quantity"], "1")
        self.assertEqual(result["matches"][0]["realized_pnl"], "-5")
        self.assertEqual(issue["security_key"], "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(issue["underlying"], "ACME")
        self.assertEqual(issue["option_expiration"], "2021-01-15")
        self.assertEqual(issue["option_type"], "call")
        self.assertEqual(issue["option_strike"], "7.50")
        self.assertEqual(issue["identity_confidence"], "lower_structural_only")
        self.assertEqual(issue["matched_lot_security_key"], "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(issue["matched_lot_identity_confidence"], "lower_structural_only")
        self.assertEqual(issue["requested_quantity"], "2")
        self.assertEqual(issue["matched_quantity"], "1")
        self.assertEqual(issue["unmatched_quantity"], "1")
        self.assertEqual(issue["applicable_side"], "short")
        self.assertEqual(issue["unmatched_reason"], "oversized_option_close")
        self.assertEqual(issue["resolution_method"], "same_identity")

    def test_empty_inventory_structural_close_reports_complete_structural_identity(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-02", "2021-01-02", "STC", "sell_to_close", "1", "15"),
        ])
        issue = result["review"]["issues"][0]
        self.assertEqual(result["matches"], [])
        self.assertEqual(issue["security_key"], "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(issue["underlying"], "ACME")
        self.assertEqual(issue["option_expiration"], "2021-01-15")
        self.assertEqual(issue["option_type"], "call")
        self.assertEqual(issue["option_strike"], "7.50")
        self.assertEqual(issue["identity_confidence"], "lower_structural_only")
        self.assertEqual(issue["requested_quantity"], "1")
        self.assertEqual(issue["matched_quantity"], "0")
        self.assertEqual(issue["unmatched_quantity"], "1")
        self.assertEqual(issue["resolution_method"], "no_available_inventory")
        self.assertNotIn("matched_lot_security_key", issue)
        self.assertEqual(result["realized_by_contract"][0]["security_key"], "option:ACME:2021-01-15:call:7.50")

    def test_empty_inventory_cusip_close_reports_closing_identity(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-02", "2021-01-02", "STC", "sell_to_close", "1", "15", cusip="111111111"),
        ])
        issue = result["review"]["issues"][0]
        self.assertEqual(result["matches"], [])
        self.assertEqual(issue["closing_security_key"], "option:ACME:2021-01-15:call:7.50:cusip:111111111")
        self.assertEqual(issue["closing_option_cusip"], "111111111")
        self.assertEqual(issue["requested_quantity"], "1")
        self.assertEqual(issue["matched_quantity"], "0")
        self.assertEqual(issue["unmatched_quantity"], "1")
        self.assertEqual(issue["resolution_method"], "no_available_inventory")
        self.assertNotIn("matched_lot_security_key", issue)

    def test_same_identity_oversize_reports_same_identity_resolution(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "1", "-10", cusip="111111111"),
            option_record(2, "2021-01-02", "2021-01-02", "STC", "sell_to_close", "2", "30", cusip="111111111"),
        ])
        issue = next(
            issue for issue in result["review"]["issues"]
            if "oversized_option_close" in issue["review_reason"]
        )
        self.assertEqual(issue["matched_lot_security_key"], "option:ACME:2021-01-15:call:7.50:cusip:111111111")
        self.assertEqual(issue["matched_lot_option_cusip"], "111111111")
        self.assertEqual(issue["matched_lot_identity_confidence"], "deterministic_cusip")
        self.assertEqual(issue["requested_quantity"], "2")
        self.assertEqual(issue["matched_quantity"], "1")
        self.assertEqual(issue["unmatched_quantity"], "1")
        self.assertEqual(issue["resolution_method"], "same_identity")

    def test_unmatched_review_items_have_contract_identity_and_reconciled_quantities(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "1", "-10"),
            option_record(2, "2021-01-02", "2021-01-02", "STC", "sell_to_close", "2", "30"),
            option_record(3, "2021-01-03", "2021-01-03", "BTC", "buy_to_close", "1", "-5", cusip="111111111"),
        ])
        unmatched_issues = [
            issue for issue in result["review"]["issues"]
            if issue.get("unmatched_quantity")
        ]
        self.assertGreaterEqual(len(unmatched_issues), 2)
        for issue in unmatched_issues:
            self.assertTrue(issue["security_key"])
            self.assertTrue(issue["structural_key"])
            self.assertTrue(issue["underlying"])
            self.assertTrue(issue["option_expiration"])
            self.assertTrue(issue["option_type"])
            self.assertTrue(issue["option_strike"])
            self.assertTrue(issue["identity_confidence"])
            self.assertEqual(
                Decimal(issue["requested_quantity"]),
                Decimal(issue["matched_quantity"]) + Decimal(issue["unmatched_quantity"]),
            )
            self.assertIn(issue["applicable_side"], {"long", "short"})

    def test_anchor_unknown_basis_and_future_anchor_cutoff(self):
        anchors = [
            {
                "anchor_date": "2021-01-01",
                "asset_type": "option",
                "option_underlying": "ACME",
                "option_expiration": "2021-01-15",
                "option_type": "call",
                "option_strike": "7.50",
                "quantity": "2",
            },
            {
                "anchor_date": "2021-02-01",
                "asset_type": "option",
                "option_underlying": "FUT",
                "option_expiration": "2021-02-19",
                "option_type": "put",
                "option_strike": "5",
                "quantity": "-1",
            },
        ]
        result = build_option_realized_pnl([
            option_record(1, "2021-01-02", "2021-01-02", "STC", "sell_to_close", "1", "10"),
        ], as_of=date(2021, 1, 31), anchors=anchors)
        self.assertEqual(result["matches"][0]["basis_status"], "unknown")
        self.assertEqual(result["matches"][0]["realized_pnl"], "")
        self.assertEqual(result["summary"]["unknown_basis_quantity"], "1")
        self.assertEqual({row["security_key"] for row in result["open_lots"]}, {"option:ACME:2021-01-15:call:7.50"})

    def test_anchor_with_no_transactions_creates_open_lot(self):
        anchors = [{
            "anchor_date": "2021-01-01",
            "asset_type": "option",
            "option_underlying": "ACME",
            "option_expiration": "2021-01-15",
            "option_type": "call",
            "option_strike": "7.50",
            "quantity": "-2",
        }]
        result = build_option_realized_pnl([], as_of=date(2021, 1, 31), anchors=anchors)
        self.assertEqual(result["open_lots"][0]["position_side"], "short")
        self.assertEqual(result["open_lots"][0]["remaining_quantity"], "2")

    def test_missing_and_malformed_settlement_dates_do_not_prevent_matching(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "", "BTO", "buy_to_open", "1", "-10"),
            option_record(2, "2021-01-02", "not-date", "STC", "sell_to_close", "1", "15"),
        ])
        self.assertEqual(result["matches"][0]["realized_pnl"], "5")
        self.assertEqual(result["matches"][0]["opening_settle_date"], "")
        self.assertIn("invalid_settle_date_metadata", result["matches"][0]["review_reason"])

    def test_exact_contract_identity_isolation(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "1", "-10", strike="7.50"),
            option_record(2, "2021-01-02", "2021-01-02", "STC", "sell_to_close", "1", "15", strike="8"),
        ])
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["summary"]["unmatched_quantity"], "1")
        self.assertEqual(result["open_lots"][0]["security_key"], "option:ACME:2021-01-15:call:7.50")

    def test_option_cusip_is_normalized_and_matching_prefers_cusip(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "1", "-10", cusip=" abc123def "),
            option_record(2, "2021-01-02", "2021-01-02", "STC", "sell_to_close", "1", "15", cusip="ABC123DEF"),
        ])
        self.assertEqual(result["matches"][0]["realized_pnl"], "5")
        self.assertEqual(result["matches"][0]["option_cusip"], "ABC123DEF")
        self.assertEqual(result["matches"][0]["identity_confidence"], "deterministic_cusip")

    def test_same_structural_option_with_different_cusips_remains_separate(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "1", "-10", cusip="111111111"),
            option_record(2, "2021-01-02", "2021-01-02", "STC", "sell_to_close", "1", "15", cusip="222222222"),
        ])
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["summary"]["unmatched_quantity"], "1")
        self.assertEqual({row["option_cusip"] for row in result["open_lots"]}, {"111111111"})
        self.assertIn("unmatched_option_close", result["review"]["issues"][0]["review_reason"])

    def test_structural_fallback_when_both_option_events_lack_cusip(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "1", "-10"),
            option_record(2, "2021-01-02", "2021-01-02", "STC", "sell_to_close", "1", "15"),
        ])
        self.assertEqual(result["matches"][0]["realized_pnl"], "5")
        self.assertEqual(result["matches"][0]["option_cusip"], "")
        self.assertEqual(result["matches"][0]["identity_confidence"], "lower_structural_only")
        self.assertEqual(result["realized_by_contract"][0]["security_key"], "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(result["realized_by_contract"][0]["underlying"], "ACME")
        self.assertEqual(result["realized_by_contract"][0]["option_expiration"], "2021-01-15")
        self.assertEqual(result["realized_by_contract"][0]["option_type"], "call")
        self.assertEqual(result["realized_by_contract"][0]["option_strike"], "7.50")
        self.assertEqual(result["realized_by_contract"][0]["option_cusip"], "")
        self.assertEqual(result["realized_by_contract"][0]["identity_confidence"], "lower_structural_only")

    def test_missing_option_cusip_uses_single_unambiguous_structural_candidate(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "1", "-10", cusip="111111111"),
            option_record(2, "2021-01-02", "2021-01-02", "STC", "sell_to_close", "1", "15"),
        ])
        self.assertEqual(result["matches"][0]["realized_pnl"], "5")
        self.assertEqual(result["matches"][0]["option_cusip"], "111111111")
        self.assertEqual(result["matches"][0]["identity_confidence"], "reduced_structural_cusip_fallback")

    def test_missing_option_cusip_with_multiple_candidates_enters_review(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "1", "-10", cusip="111111111"),
            option_record(2, "2021-01-02", "2021-01-02", "BTO", "buy_to_open", "1", "-20", cusip="222222222"),
            option_record(3, "2021-01-03", "2021-01-03", "STC", "sell_to_close", "1", "15"),
        ])
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["summary"]["unmatched_quantity"], "1")
        self.assertEqual(len(result["open_lots"]), 2)
        self.assertIn("ambiguous_option_cusip_fallback", result["review"]["issues"][0]["review_reason"])

    def test_cusip_aware_anchor_matches_missing_cusip_close_and_reconciles(self):
        anchors = [{
            "anchor_date": "2021-01-01",
            "asset_type": "option",
            "option_underlying": "ACME",
            "option_expiration": "2021-01-15",
            "option_type": "call",
            "option_strike": "7.50",
            "cusip": "111111111",
            "quantity": "2",
        }]
        result = build_option_realized_pnl([
            option_record(1, "2021-01-02", "2021-01-02", "STC", "sell_to_close", "1", "10"),
        ], as_of=date(2021, 1, 31), anchors=anchors)
        self.assertEqual(result["matches"][0]["option_cusip"], "111111111")
        self.assertEqual(result["matches"][0]["basis_status"], "unknown")
        self.assertEqual(result["open_lots"][0]["option_cusip"], "111111111")
        self.assertFalse(any("reconciliation_failed" in issue["review_reason"] for issue in result["review"]["issues"]))

    def test_no_cusip_trades_before_future_cusip_anchor_keep_original_identity(self):
        anchors = [{
            "anchor_date": "2021-01-10",
            "asset_type": "option",
            "option_underlying": "ACME",
            "option_expiration": "2021-01-15",
            "option_type": "call",
            "option_strike": "7.50",
            "cusip": "111111111",
            "quantity": "2",
        }]
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "1", "-10"),
            option_record(2, "2021-01-02", "2021-01-02", "STC", "sell_to_close", "1", "15"),
        ], as_of=date(2021, 1, 31), anchors=anchors)
        self.assertEqual(result["matches"][0]["security_key"], "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(result["matches"][0]["option_cusip"], "")
        self.assertEqual(result["matches"][0]["identity_confidence"], "lower_structural_only")
        self.assertEqual(result["open_lots"][0]["option_cusip"], "111111111")
        self.assertEqual(result["realized_by_contract"][0]["security_key"], "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(result["realized_by_contract"][0]["underlying"], "ACME")
        self.assertEqual(result["realized_by_contract"][0]["option_expiration"], "2021-01-15")
        self.assertEqual(result["realized_by_contract"][0]["option_type"], "call")
        self.assertEqual(result["realized_by_contract"][0]["option_strike"], "7.50")
        self.assertEqual(result["realized_by_contract"][0]["option_cusip"], "")
        self.assertEqual(result["realized_by_contract"][0]["identity_confidence"], "lower_structural_only")

    def test_anchor_cusip_available_before_subsequent_trade(self):
        anchors = [{
            "anchor_date": "2021-01-02",
            "asset_type": "option",
            "option_underlying": "ACME",
            "option_expiration": "2021-01-15",
            "option_type": "call",
            "option_strike": "7.50",
            "cusip": "111111111",
            "quantity": "1",
        }]
        result = build_option_realized_pnl([
            option_record(1, "2021-01-03", "2021-01-03", "STC", "sell_to_close", "1", "10"),
        ], as_of=date(2021, 1, 31), anchors=anchors)
        self.assertEqual(result["matches"][0]["option_cusip"], "111111111")
        self.assertEqual(result["matches"][0]["basis_status"], "unknown")

    def test_future_cusip_anchor_does_not_affect_earlier_as_of_matching(self):
        anchors = [{
            "anchor_date": "2021-02-01",
            "asset_type": "option",
            "option_underlying": "ACME",
            "option_expiration": "2021-01-15",
            "option_type": "call",
            "option_strike": "7.50",
            "cusip": "111111111",
            "quantity": "2",
        }]
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "1", "-10"),
            option_record(2, "2021-01-02", "2021-01-02", "STC", "sell_to_close", "1", "15"),
        ], as_of=date(2021, 1, 31), anchors=anchors)
        self.assertEqual(result["matches"][0]["option_cusip"], "")
        self.assertEqual(result["matches"][0]["security_key"], "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(result["summary"]["anchor_count"], 0)

    def test_as_of_before_future_anchor_is_identical_with_or_without_anchor(self):
        records = [
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "1", "-10"),
            option_record(2, "2021-01-02", "2021-01-02", "STC", "sell_to_close", "1", "15"),
        ]
        anchors = [{
            "anchor_date": "2021-02-01",
            "asset_type": "option",
            "option_underlying": "ACME",
            "option_expiration": "2021-01-15",
            "option_type": "call",
            "option_strike": "7.50",
            "cusip": "111111111",
            "quantity": "2",
        }]
        without_anchor = build_option_realized_pnl(records, as_of=date(2021, 1, 31))
        with_anchor = build_option_realized_pnl(records, as_of=date(2021, 1, 31), anchors=anchors)
        self.assertEqual(with_anchor, without_anchor)

    def test_multiple_future_cusip_anchors_do_not_create_earlier_ambiguity(self):
        anchors = [
            {
                "anchor_date": "2021-02-01",
                "asset_type": "option",
                "option_underlying": "ACME",
                "option_expiration": "2021-01-15",
                "option_type": "call",
                "option_strike": "7.50",
                "cusip": "111111111",
                "quantity": "1",
            },
            {
                "anchor_date": "2021-02-02",
                "asset_type": "option",
                "option_underlying": "ACME",
                "option_expiration": "2021-01-15",
                "option_type": "call",
                "option_strike": "7.50",
                "cusip": "222222222",
                "quantity": "1",
            },
        ]
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "1", "-10"),
            option_record(2, "2021-01-02", "2021-01-02", "STC", "sell_to_close", "1", "15"),
        ], as_of=date(2021, 1, 31), anchors=anchors)
        self.assertEqual(result["matches"][0]["option_cusip"], "")
        self.assertFalse(any("ambiguous_option_cusip_fallback" in issue["review_reason"] for issue in result["review"]["issues"]))

    def test_same_day_anchor_is_available_before_same_day_event(self):
        anchors = [{
            "anchor_date": "2021-01-02",
            "asset_type": "option",
            "option_underlying": "ACME",
            "option_expiration": "2021-01-15",
            "option_type": "call",
            "option_strike": "7.50",
            "cusip": "111111111",
            "quantity": "1",
        }]
        result = build_option_realized_pnl([
            option_record(1, "2021-01-02", "2021-01-02", "STC", "sell_to_close", "1", "10"),
        ], as_of=date(2021, 1, 31), anchors=anchors)
        self.assertEqual(result["matches"][0]["option_cusip"], "111111111")
        self.assertEqual(result["matches"][0]["opening_event_id"], "anchor:2021-01-02")

    def test_later_cusip_evidence_preserves_historical_open_lot_confidence(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "2", "-20"),
            option_record(2, "2021-01-02", "2021-01-02", "STC", "sell_to_close", "1", "15", cusip="111111111"),
        ])
        self.assertEqual(result["matches"][0]["option_cusip"], "111111111")
        self.assertEqual(result["matches"][0]["identity_confidence"], "reduced_structural_cusip_fallback")
        self.assertEqual(result["matches"][0]["closing_event_id"], "2")
        self.assertEqual(result["open_lots"][0]["remaining_quantity"], "1")
        self.assertEqual(result["open_lots"][0]["security_key"], "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(result["open_lots"][0]["option_cusip"], "")
        self.assertEqual(result["open_lots"][0]["identity_confidence"], "lower_structural_only")

    def test_cusip_identified_closed_lots_keep_by_contract_context(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "1", "-10", cusip="111111111"),
            option_record(2, "2021-01-02", "2021-01-02", "STC", "sell_to_close", "1", "15", cusip="111111111"),
        ])
        self.assertEqual(result["matches"][0]["option_cusip"], "111111111")
        self.assertEqual(result["realized_by_contract"][0]["option_cusip"], "111111111")
        self.assertEqual(result["realized_by_contract"][0]["underlying"], "ACME")
        self.assertEqual(result["realized_by_contract"][0]["identity_confidence"], "deterministic_cusip")

    def test_partial_and_final_closes_keep_consistent_contract_context(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "2", "-20"),
            option_record(2, "2021-01-02", "2021-01-02", "STC", "sell_to_close", "1", "15"),
            option_record(3, "2021-01-03", "2021-01-03", "STC", "sell_to_close", "1", "12"),
        ])
        self.assertEqual(result["open_lots"], [])
        self.assertEqual({row["security_key"] for row in result["matches"]}, {"option:ACME:2021-01-15:call:7.50"})
        self.assertEqual(result["realized_by_contract"][0]["security_key"], result["matches"][0]["security_key"])
        self.assertEqual(result["realized_by_contract"][0]["underlying"], result["matches"][0]["underlying"])
        self.assertEqual(result["realized_by_contract"][0]["option_expiration"], result["matches"][0]["option_expiration"])
        self.assertEqual(result["realized_by_contract"][0]["option_type"], result["matches"][0]["option_type"])
        self.assertEqual(result["realized_by_contract"][0]["option_strike"], result["matches"][0]["option_strike"])
        self.assertEqual(result["realized_by_contract"][0]["identity_confidence"], result["matches"][0]["identity_confidence"])

    def test_identical_looking_fills_remain_separate(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "1", "-10", review_status="review", review_reasons="potential_identical_fill"),
            option_record(2, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "1", "-10", review_status="review", review_reasons="potential_identical_fill"),
            option_record(3, "2021-01-02", "2021-01-02", "STC", "sell_to_close", "2", "30"),
        ])
        self.assertEqual([row["opening_event_id"] for row in result["matches"]], ["1", "2"])
        self.assertTrue(all(row["review_status"] == "review" for row in result["matches"]))

    def test_decimal_precision(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "3", "-10"),
            option_record(2, "2021-01-02", "2021-01-02", "STC", "sell_to_close", "1", "4"),
        ])
        self.assertEqual(result["matches"][0]["allocated_opening_cost"], "3.333333333333333333333333333")
        self.assertEqual(result["matches"][0]["realized_pnl"], "0.666666666666666666666666667")

    def test_side_specific_reconciliation_with_position_ledger(self):
        result = build_option_realized_pnl([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "3", "-30"),
            option_record(2, "2021-01-02", "2021-01-02", "STC", "sell_to_close", "1", "15"),
            option_record(3, "2021-01-03", "2021-01-03", "STO", "sell_to_open", "2", "20"),
            option_record(4, "2021-01-04", "2021-01-04", "BTC", "buy_to_close", "1", "-4"),
        ])
        open_by_side = {row["position_side"]: row["remaining_quantity"] for row in result["open_lots"]}
        self.assertEqual(open_by_side, {"long": "2", "short": "1"})
        self.assertFalse(any("reconciliation_failed" in issue["review_reason"] for issue in result["review"]["issues"]))

    def test_equities_and_cash_are_excluded(self):
        cash = equity_record(2)
        cash.update({
            "transaction_code_raw": "ACH",
            "transaction_family": "funding",
            "event_type": "deposit",
            "asset_type": "cash",
            "quantity_numeric": "",
            "amount": "100",
        })
        result = build_option_realized_pnl([equity_record(1), cash])
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["open_lots"], [])
        self.assertEqual(result["review"]["review_count"], 0)

    def test_cli_smoke_writes_expected_outputs(self):
        headers = [
            "Activity Date", "Process Date", "Settle Date", "Instrument", "Description",
            "Trans Code", "Quantity", "Price", "Amount",
        ]
        rows = [
            ["01/01/2021", "01/01/2021", "01/04/2021", "ACME", "ACME 1/15/2021 Call $7.50", "BTO", "1", "$10.00", "($10.00)"],
            ["01/05/2021", "01/05/2021", "01/08/2021", "ACME", "ACME 1/15/2021 Call $7.50", "STC", "1", "$15.00", "$15.00"],
        ]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "synthetic_robinhood.csv"
            output = Path(directory) / "option_realized"
            with source.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(headers)
                writer.writerows(rows)
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "trademirror.cli",
                    "option-realized-pnl",
                    str(source),
                    "--output-dir",
                    str(output),
                    "--as-of",
                    "2021-01-31",
                ],
                check=True,
                env={**os.environ, "PYTHONPATH": "src"},
                cwd=Path(__file__).parents[1],
                capture_output=True,
                text=True,
            )
            expected = {
                "option_lot_matches.csv",
                "option_open_lots.csv",
                "option_basis_transfers.csv",
                "option_realized_by_contract.csv",
                "option_realized_summary.json",
                "option_lot_review.json",
            }
            self.assertEqual(expected, {path.name for path in output.iterdir()})
            summary = json.loads((output / "option_realized_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["net_realized_pnl"], "5")


if __name__ == "__main__":
    unittest.main()
