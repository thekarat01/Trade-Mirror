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

from trademirror.position_ledger import build_position_ledger, _build_summary


def record(
    source_row_id,
    activity_date,
    settle_date,
    code,
    event_type,
    asset_type,
    quantity,
    *,
    instrument="ACME",
    cusip="123456789",
    family=None,
    review_status="validated",
    review_reasons="",
    option_underlying="",
    option_expiration="",
    option_type="",
    option_strike="",
):
    return {
        "source_row_id": source_row_id,
        "activity_date": activity_date,
        "settle_date": settle_date,
        "transaction_code_raw": code,
        "transaction_family": family or ("option_trade" if asset_type == "option" else "trade"),
        "event_type": event_type,
        "asset_type": asset_type,
        "quantity_numeric": quantity,
        "instrument": instrument,
        "cusip": cusip,
        "review_status": review_status,
        "review_reasons": review_reasons,
        "option_underlying": option_underlying,
        "option_expiration": option_expiration,
        "option_type": option_type,
        "option_strike": option_strike,
    }


def option_record(source_row_id, activity_date, settle_date, code, event_type, quantity):
    return record(
        source_row_id,
        activity_date,
        settle_date,
        code,
        event_type,
        "option",
        quantity,
        instrument="ACME",
        cusip="",
        family="option_lifecycle" if event_type in {"expiration", "exercise", "assignment"} else "option_trade",
        option_underlying="ACME",
        option_expiration="2021-01-15",
        option_type="call",
        option_strike="7.50",
    )


class PositionLedgerTests(unittest.TestCase):
    def position_by_key(self, result, key):
        return {row["security_key"]: row for row in result["positions_as_of"]}[key]

    def summary_option_event(self, source_row_id, signed_quantity, position_side):
        return {
            "source_row_id": source_row_id,
            "activity_date": date(2021, 1, source_row_id),
            "settle_date": date(2021, 1, source_row_id),
            "transaction_code": "",
            "event_type": "option_trade",
            "security_key": "option:ACME:2021-01-15:call:7.50",
            "cusip": "",
            "primary_symbol": "ACME",
            "asset_type": "option",
            "option_underlying": "ACME",
            "option_expiration": "2021-01-15",
            "option_type": "call",
            "option_strike": "7.50",
            "signed_quantity": Decimal(signed_quantity),
            "position_side": position_side,
        }

    def summary_position(self, asset_type, trade_quantity, settled_quantity, **overrides):
        row = {
            "security_key": "option:ACME:2021-01-15:call:7.50" if asset_type == "option" else "equity:123456789",
            "cusip": "" if asset_type == "option" else "123456789",
            "primary_observed_symbol": "ACME",
            "asset_type": asset_type,
            "option_underlying": "ACME" if asset_type == "option" else "",
            "option_expiration": "2021-01-15" if asset_type == "option" else "",
            "option_type": "call" if asset_type == "option" else "",
            "option_strike": "7.50" if asset_type == "option" else "",
            "trade_date_quantity": trade_quantity,
            "settled_quantity": settled_quantity,
            "trade_date_long_quantity": "0",
            "trade_date_short_quantity": "0",
            "settled_long_quantity": "0",
            "settled_short_quantity": "0",
            "anchor_date": "",
        }
        row.update(overrides)
        return row

    def test_equity_buy_and_sale(self):
        result = build_position_ledger([
            record(1, "2021-01-01", "2021-01-04", "Buy", "buy", "equity", "10"),
            record(2, "2021-01-05", "2021-01-07", "Sell", "sell", "equity", "3"),
        ])
        position = self.position_by_key(result, "equity:123456789")
        self.assertEqual(position["trade_date_quantity"], "7")
        self.assertEqual(position["settled_quantity"], "7")
        self.assertEqual(position["review_status"], "validated")
        self.assertTrue(result["summary"]["trade_date_reconciles_to_events"])

    def test_sale_before_known_opening_position_requires_review(self):
        result = build_position_ledger([
            record(1, "2021-01-01", "2021-01-04", "Sell", "sell", "equity", "3"),
        ])
        position = self.position_by_key(result, "equity:123456789")
        self.assertEqual(position["trade_date_quantity"], "-3")
        self.assertIn("negative_equity_quantity_requires_review", position["review_reasons"])
        self.assertIn("sale_before_known_opening_position", result["events"][0]["review_reason"])

    def test_verified_position_anchor_and_after_anchor_transaction(self):
        anchors = [{
            "anchor_date": "2021-01-02",
            "asset_type": "equity",
            "cusip": "123456789",
            "symbol": "ACME",
            "quantity": "100",
        }]
        result = build_position_ledger([
            record(1, "2021-01-01", "2021-01-01", "Sell", "sell", "equity", "10"),
            record(2, "2021-01-03", "2021-01-03", "Buy", "buy", "equity", "5"),
        ], anchors=anchors)
        position = self.position_by_key(result, "equity:123456789")
        self.assertEqual(position["trade_date_quantity"], "105")
        self.assertEqual(position["settled_quantity"], "105")
        self.assertEqual(position["anchor_date"], "2021-01-02")
        self.assertEqual(position["confidence"], "verified")

    def test_anchor_date_activity_is_reflected_in_history(self):
        anchors = [{
            "anchor_date": "2021-01-02",
            "asset_type": "equity",
            "cusip": "123456789",
            "symbol": "ACME",
            "quantity": "100",
        }]
        result = build_position_ledger([
            record(1, "2021-01-02", "2021-01-02", "Buy", "buy", "equity", "5"),
        ], anchors=anchors)
        position = self.position_by_key(result, "equity:123456789")
        self.assertEqual(position["trade_date_quantity"], "105")
        self.assertEqual(position["settled_quantity"], "105")
        history = {
            (row["date"], row["security_key"]): row
            for row in result["history"]
        }
        anchor_day = history[("2021-01-02", "equity:123456789")]
        self.assertEqual(anchor_day["trade_date_quantity"], "105")
        self.assertEqual(anchor_day["settled_quantity"], "105")
        self.assertEqual(anchor_day["confidence"], "verified")

    def test_future_anchor_relative_to_as_of_is_not_applied(self):
        anchors = [{
            "anchor_date": "2021-02-01",
            "asset_type": "equity",
            "cusip": "123456789",
            "symbol": "ACME",
            "quantity": "100",
        }]
        result = build_position_ledger(
            [record(1, "2021-01-01", "2021-01-04", "Buy", "buy", "equity", "1")],
            as_of=date(2021, 1, 31),
            anchors=anchors,
        )
        position = self.position_by_key(result, "equity:123456789")
        self.assertEqual(position["trade_date_quantity"], "1")
        self.assertEqual(position["anchor_date"], "")
        self.assertEqual(result["summary"]["future_anchor_count"], 1)
        self.assertTrue(all(row["date"] <= "2021-01-31" for row in result["history"]))

    def test_cusip_identity_preserves_aliases_and_distinguishes_same_ticker(self):
        result = build_position_ledger([
            record(1, "2021-01-01", "2021-01-01", "Buy", "buy", "equity", "1", instrument="OLD", cusip="111111111"),
            record(2, "2021-01-02", "2021-01-02", "Buy", "buy", "equity", "2", instrument="NEW", cusip="111111111"),
            record(3, "2021-01-03", "2021-01-03", "Buy", "buy", "equity", "3", instrument="SAME", cusip="222222222"),
            record(4, "2021-01-04", "2021-01-04", "Buy", "buy", "equity", "4", instrument="SAME", cusip="333333333"),
        ])
        merged = self.position_by_key(result, "equity:111111111")
        self.assertEqual(merged["trade_date_quantity"], "3")
        self.assertEqual(merged["ticker_aliases"], "NEW|OLD")
        self.assertIn("equity:222222222", {row["security_key"] for row in result["positions_as_of"]})
        self.assertIn("equity:333333333", {row["security_key"] for row in result["positions_as_of"]})

    def test_missing_cusip_uses_symbol_with_lower_confidence(self):
        result = build_position_ledger([
            record(1, "2021-01-01", "2021-01-01", "Buy", "buy", "equity", "2", instrument="NOC", cusip=""),
        ])
        position = self.position_by_key(result, "equity-symbol:NOC")
        self.assertEqual(position["trade_date_quantity"], "2")
        self.assertEqual(position["confidence"], "lower_symbol_only")
        self.assertEqual(result["events"][0]["confidence"], "lower_symbol_only")

    def test_option_open_close_and_lifecycle_events(self):
        result = build_position_ledger([
            option_record(1, "2021-01-01", "2021-01-04", "BTO", "buy_to_open", "2"),
            option_record(2, "2021-01-05", "2021-01-06", "STC", "sell_to_close", "1"),
            option_record(3, "2021-01-07", "2021-01-08", "OEXP", "expiration", "1"),
            option_record(4, "2021-02-01", "2021-02-02", "STO", "sell_to_open", "3"),
            option_record(5, "2021-02-03", "2021-02-04", "BTC", "buy_to_close", "1"),
            option_record(6, "2021-02-05", "2021-02-06", "OASGN", "assignment", "2"),
            option_record(7, "2021-03-01", "2021-03-02", "BTO", "buy_to_open", "1"),
            option_record(8, "2021-03-03", "2021-03-04", "OEXCS", "exercise", "1"),
        ])
        position = self.position_by_key(result, "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(position["trade_date_quantity"], "0")
        self.assertEqual(position["settled_quantity"], "0")

    def test_unmatched_option_lifecycle_event_enters_review(self):
        result = build_position_ledger([
            option_record(1, "2021-01-07", "2021-01-08", "OEXP", "expiration", "1"),
        ])
        self.assertEqual(result["events"], [])
        self.assertEqual(result["review"]["review_count"], 1)
        self.assertIn("unmatched_option_lifecycle:expiration", result["review"]["issues"][0]["review_reason"])

    def test_oversized_option_lifecycle_event_enters_review_without_truncating(self):
        result = build_position_ledger([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "2"),
            option_record(2, "2021-01-02", "2021-01-02", "OEXP", "expiration", "5"),
        ])
        position = self.position_by_key(result, "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(position["trade_date_quantity"], "2")
        self.assertEqual(position["settled_quantity"], "2")
        self.assertEqual(len(result["events"]), 1)
        self.assertEqual(result["review"]["review_count"], 1)
        issue = result["review"]["issues"][0]
        self.assertEqual(issue["source_row_id"], "2")
        self.assertIn("oversized_option_close:expiration", issue["review_reason"])
        self.assertIn("available=2", issue["review_reason"])
        self.assertIn("event=5", issue["review_reason"])

    def test_option_anchor_allows_valid_lifecycle_close(self):
        anchors = [{
            "anchor_date": "2021-01-01",
            "asset_type": "option",
            "option_underlying": "ACME",
            "option_expiration": "2021-01-15",
            "option_type": "call",
            "option_strike": "7.50",
            "quantity": "2",
        }]
        result = build_position_ledger([
            option_record(1, "2021-01-03", "2021-01-03", "OEXP", "expiration", "2"),
        ], anchors=anchors)
        position = self.position_by_key(result, "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(position["trade_date_quantity"], "0")
        self.assertEqual(position["settled_quantity"], "0")
        self.assertEqual(result["review"]["review_count"], 0)
        self.assertEqual(result["events"][0]["signed_quantity"], "-2")
        self.assertTrue(result["summary"]["trade_date_reconciles_to_events"])

    def test_bto_does_not_net_against_existing_short_inventory(self):
        result = build_position_ledger([
            option_record(1, "2021-01-01", "2021-01-01", "STO", "sell_to_open", "2"),
            option_record(2, "2021-01-02", "2021-01-02", "BTO", "buy_to_open", "1"),
        ])
        position = self.position_by_key(result, "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(position["trade_date_quantity"], "-1")
        self.assertEqual(position["trade_date_long_quantity"], "1")
        self.assertEqual(position["trade_date_short_quantity"], "2")
        self.assertEqual(result["events"][1]["position_side"], "long")
        self.assertEqual(result["review"]["review_count"], 0)

    def test_sto_does_not_net_against_existing_long_inventory(self):
        result = build_position_ledger([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "2"),
            option_record(2, "2021-01-02", "2021-01-02", "STO", "sell_to_open", "1"),
        ])
        position = self.position_by_key(result, "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(position["trade_date_quantity"], "1")
        self.assertEqual(position["trade_date_long_quantity"], "2")
        self.assertEqual(position["trade_date_short_quantity"], "1")
        self.assertEqual(result["events"][1]["position_side"], "short")
        self.assertEqual(result["review"]["review_count"], 0)

    def test_stc_reduces_only_long_inventory(self):
        result = build_position_ledger([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "2"),
            option_record(2, "2021-01-02", "2021-01-02", "STO", "sell_to_open", "1"),
            option_record(3, "2021-01-03", "2021-01-03", "STC", "sell_to_close", "1"),
        ])
        position = self.position_by_key(result, "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(position["trade_date_quantity"], "0")
        self.assertEqual(position["trade_date_long_quantity"], "1")
        self.assertEqual(position["trade_date_short_quantity"], "1")
        self.assertEqual(result["events"][2]["position_side"], "long")

    def test_btc_reduces_only_short_inventory(self):
        result = build_position_ledger([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "2"),
            option_record(2, "2021-01-02", "2021-01-02", "STO", "sell_to_open", "3"),
            option_record(3, "2021-01-03", "2021-01-03", "BTC", "buy_to_close", "1"),
        ])
        position = self.position_by_key(result, "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(position["trade_date_quantity"], "0")
        self.assertEqual(position["trade_date_long_quantity"], "2")
        self.assertEqual(position["trade_date_short_quantity"], "2")
        self.assertEqual(result["events"][2]["position_side"], "short")

    def test_oversized_stc_does_not_consume_short_inventory(self):
        result = build_position_ledger([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "1"),
            option_record(2, "2021-01-02", "2021-01-02", "STO", "sell_to_open", "5"),
            option_record(3, "2021-01-03", "2021-01-03", "STC", "sell_to_close", "2"),
        ])
        position = self.position_by_key(result, "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(position["trade_date_long_quantity"], "1")
        self.assertEqual(position["trade_date_short_quantity"], "5")
        self.assertEqual(len(result["events"]), 2)
        issue = result["review"]["issues"][0]
        self.assertEqual(issue["position_side"], "long")
        self.assertEqual(issue["available_quantity"], "1")
        self.assertEqual(issue["source_event_quantity"], "2")
        self.assertIn("oversized_option_close:sell_to_close", issue["review_reason"])

    def test_ambiguous_lifecycle_with_long_and_short_inventory_enters_review(self):
        result = build_position_ledger([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "2"),
            option_record(2, "2021-01-02", "2021-01-02", "STO", "sell_to_open", "1"),
            option_record(3, "2021-01-03", "2021-01-03", "OEXP", "expiration", "1"),
        ])
        position = self.position_by_key(result, "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(position["trade_date_long_quantity"], "2")
        self.assertEqual(position["trade_date_short_quantity"], "1")
        issue = result["review"]["issues"][0]
        self.assertEqual(issue["position_side"], "ambiguous")
        self.assertIn("ambiguous_option_lifecycle_side:expiration", issue["review_reason"])

    def test_exercise_closes_long_inventory_only(self):
        result = build_position_ledger([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "2"),
            option_record(2, "2021-01-02", "2021-01-02", "OEXCS", "exercise", "1"),
            option_record(3, "2021-01-03", "2021-01-03", "STO", "sell_to_open", "4"),
            option_record(4, "2021-01-04", "2021-01-04", "OEXCS", "exercise", "2"),
        ])
        position = self.position_by_key(result, "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(position["trade_date_long_quantity"], "1")
        self.assertEqual(position["trade_date_short_quantity"], "4")
        self.assertEqual(len(result["events"]), 3)
        issue = result["review"]["issues"][0]
        self.assertEqual(issue["position_side"], "long")
        self.assertEqual(issue["available_quantity"], "1")
        self.assertEqual(issue["source_event_quantity"], "2")
        self.assertIn("oversized_option_close:exercise", issue["review_reason"])

    def test_assignment_closes_short_inventory_only(self):
        result = build_position_ledger([
            option_record(1, "2021-01-01", "2021-01-01", "STO", "sell_to_open", "2"),
            option_record(2, "2021-01-02", "2021-01-02", "OASGN", "assignment", "1"),
            option_record(3, "2021-01-03", "2021-01-03", "BTO", "buy_to_open", "4"),
            option_record(4, "2021-01-04", "2021-01-04", "OASGN", "assignment", "2"),
        ])
        position = self.position_by_key(result, "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(position["trade_date_long_quantity"], "4")
        self.assertEqual(position["trade_date_short_quantity"], "1")
        self.assertEqual(len(result["events"]), 3)
        issue = result["review"]["issues"][0]
        self.assertEqual(issue["position_side"], "short")
        self.assertEqual(issue["available_quantity"], "1")
        self.assertEqual(issue["source_event_quantity"], "2")
        self.assertIn("oversized_option_close:assignment", issue["review_reason"])

    def test_expiration_uses_explicit_long_side_metadata(self):
        expiration = option_record(3, "2021-01-03", "2021-01-03", "OEXP", "expiration", "1")
        expiration["position_side"] = "long"
        result = build_position_ledger([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "2"),
            option_record(2, "2021-01-02", "2021-01-02", "STO", "sell_to_open", "3"),
            expiration,
        ])
        position = self.position_by_key(result, "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(position["trade_date_long_quantity"], "1")
        self.assertEqual(position["trade_date_short_quantity"], "3")
        self.assertEqual(result["events"][-1]["position_side"], "long")
        self.assertEqual(result["review"]["review_count"], 0)

    def test_expiration_uses_explicit_short_side_metadata(self):
        expiration = option_record(3, "2021-01-03", "2021-01-03", "OEXP", "expiration", "2")
        expiration["option_position_side"] = "short"
        result = build_position_ledger([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "2"),
            option_record(2, "2021-01-02", "2021-01-02", "STO", "sell_to_open", "3"),
            expiration,
        ])
        position = self.position_by_key(result, "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(position["trade_date_long_quantity"], "2")
        self.assertEqual(position["trade_date_short_quantity"], "1")
        self.assertEqual(result["events"][-1]["position_side"], "short")
        self.assertEqual(result["review"]["review_count"], 0)

    def test_expiration_without_side_uses_only_available_side(self):
        long_result = build_position_ledger([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "2"),
            option_record(2, "2021-01-02", "2021-01-02", "OEXP", "expiration", "1"),
        ])
        short_result = build_position_ledger([
            option_record(1, "2021-01-01", "2021-01-01", "STO", "sell_to_open", "2"),
            option_record(2, "2021-01-02", "2021-01-02", "OEXP", "expiration", "1"),
        ])
        self.assertEqual(long_result["events"][1]["position_side"], "long")
        self.assertEqual(short_result["events"][1]["position_side"], "short")

    def test_lifecycle_events_do_not_consume_inapplicable_side(self):
        assignment_against_long = build_position_ledger([
            option_record(1, "2021-01-01", "2021-01-01", "BTO", "buy_to_open", "2"),
            option_record(2, "2021-01-02", "2021-01-02", "OASGN", "assignment", "1"),
        ])
        exercise_against_short = build_position_ledger([
            option_record(1, "2021-01-01", "2021-01-01", "STO", "sell_to_open", "2"),
            option_record(2, "2021-01-02", "2021-01-02", "OEXCS", "exercise", "1"),
        ])
        long_position = self.position_by_key(assignment_against_long, "option:ACME:2021-01-15:call:7.50")
        short_position = self.position_by_key(exercise_against_short, "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(long_position["trade_date_long_quantity"], "2")
        self.assertEqual(long_position["trade_date_short_quantity"], "0")
        self.assertEqual(short_position["trade_date_long_quantity"], "0")
        self.assertEqual(short_position["trade_date_short_quantity"], "2")
        self.assertIn("unmatched_option_close:assignment", assignment_against_long["review"]["issues"][0]["review_reason"])
        self.assertIn("unmatched_option_close:exercise", exercise_against_short["review"]["issues"][0]["review_reason"])

    def test_option_side_reconciliation_flags_matching_net_with_wrong_sides(self):
        review = []
        summary = _build_summary(
            [
                self.summary_option_event(1, "2", "long"),
                self.summary_option_event(2, "-2", "short"),
            ],
            [
                self.summary_position(
                    "option",
                    "0",
                    "0",
                    trade_date_long_quantity="1",
                    trade_date_short_quantity="1",
                    settled_long_quantity="1",
                    settled_short_quantity="1",
                ),
            ],
            [],
            [],
            review,
            [],
        )
        self.assertTrue(summary["net_trade_date_reconciles_to_events"])
        self.assertFalse(summary["trade_date_reconciles_to_events"])
        self.assertFalse(summary["option_trade_date_sides_reconcile_to_events"])
        self.assertEqual(review[0]["security_key"], "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(review[0]["expected_long_quantity"], "2")
        self.assertEqual(review[0]["actual_long_quantity"], "1")
        self.assertEqual(review[0]["expected_short_quantity"], "2")
        self.assertEqual(review[0]["actual_short_quantity"], "1")
        self.assertEqual(review[0]["comparison_date"], "2021-01-02")
        self.assertIn("option_side_quantity_reconciliation_failed", review[0]["review_reason"])

    def test_option_side_reconciliation_passes_when_both_sides_match(self):
        review = []
        summary = _build_summary(
            [
                self.summary_option_event(1, "2", "long"),
                self.summary_option_event(2, "-2", "short"),
            ],
            [
                self.summary_position(
                    "option",
                    "0",
                    "0",
                    trade_date_long_quantity="2",
                    trade_date_short_quantity="2",
                    settled_long_quantity="2",
                    settled_short_quantity="2",
                ),
            ],
            [],
            [],
            review,
            [],
        )
        self.assertTrue(summary["trade_date_reconciles_to_events"])
        self.assertTrue(summary["option_trade_date_sides_reconcile_to_events"])
        self.assertTrue(summary["option_settled_sides_reconcile_to_events"])
        self.assertEqual(review, [])

    def test_option_side_reconciliation_flags_long_side_mismatch(self):
        review = []
        summary = _build_summary(
            [
                self.summary_option_event(1, "2", "long"),
                self.summary_option_event(2, "-2", "short"),
            ],
            [
                self.summary_position(
                    "option",
                    "0",
                    "0",
                    trade_date_long_quantity="1",
                    trade_date_short_quantity="2",
                    settled_long_quantity="1",
                    settled_short_quantity="2",
                ),
            ],
            [],
            [],
            review,
            [],
        )
        self.assertFalse(summary["option_trade_date_sides_reconcile_to_events"])
        self.assertEqual(review[0]["expected_long_quantity"], "2")
        self.assertEqual(review[0]["actual_long_quantity"], "1")
        self.assertEqual(review[0]["expected_short_quantity"], "2")
        self.assertEqual(review[0]["actual_short_quantity"], "2")

    def test_option_side_reconciliation_flags_short_side_mismatch(self):
        review = []
        summary = _build_summary(
            [
                self.summary_option_event(1, "2", "long"),
                self.summary_option_event(2, "-2", "short"),
            ],
            [
                self.summary_position(
                    "option",
                    "0",
                    "0",
                    trade_date_long_quantity="2",
                    trade_date_short_quantity="1",
                    settled_long_quantity="2",
                    settled_short_quantity="1",
                ),
            ],
            [],
            [],
            review,
            [],
        )
        self.assertFalse(summary["option_trade_date_sides_reconcile_to_events"])
        self.assertEqual(review[0]["expected_long_quantity"], "2")
        self.assertEqual(review[0]["actual_long_quantity"], "2")
        self.assertEqual(review[0]["expected_short_quantity"], "2")
        self.assertEqual(review[0]["actual_short_quantity"], "1")

    def test_equity_reconciliation_remains_net_quantity_based(self):
        review = []
        summary = _build_summary(
            [
                {
                    "source_row_id": 1,
                    "activity_date": date(2021, 1, 1),
                    "settle_date": date(2021, 1, 1),
                    "security_key": "equity:123456789",
                    "asset_type": "equity",
                    "signed_quantity": Decimal("3"),
                    "position_side": "",
                },
            ],
            [self.summary_position("equity", "3", "3")],
            [],
            [],
            review,
            [],
        )
        self.assertTrue(summary["trade_date_reconciles_to_events"])
        self.assertTrue(summary["net_trade_date_reconciles_to_events"])
        self.assertEqual(review, [])

    def test_option_anchor_oversized_lifecycle_close_enters_review(self):
        anchors = [{
            "anchor_date": "2021-01-01",
            "asset_type": "option",
            "option_underlying": "ACME",
            "option_expiration": "2021-01-15",
            "option_type": "call",
            "option_strike": "7.50",
            "quantity": "2",
        }]
        result = build_position_ledger([
            option_record(1, "2021-01-03", "2021-01-03", "OEXP", "expiration", "5"),
        ], anchors=anchors)
        position = self.position_by_key(result, "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(position["trade_date_quantity"], "2")
        self.assertEqual(position["settled_quantity"], "2")
        self.assertEqual(result["events"], [])
        issue = result["review"]["issues"][0]
        self.assertEqual(issue["security_key"], "option:ACME:2021-01-15:call:7.50")
        self.assertEqual(issue["anchor_date"], "2021-01-01")
        self.assertEqual(issue["anchor_quantity"], "2")
        self.assertEqual(issue["available_quantity"], "2")
        self.assertEqual(issue["source_event_quantity"], "5")
        self.assertIn("oversized_option_close:expiration", issue["review_reason"])

    def test_anchored_equity_oversell_enters_review_without_negative_position(self):
        anchors = [{
            "anchor_date": "2021-01-01",
            "asset_type": "equity",
            "cusip": "123456789",
            "symbol": "ACME",
            "quantity": "3",
        }]
        result = build_position_ledger([
            record(1, "2021-01-02", "2021-01-02", "Sell", "sell", "equity", "5"),
        ], anchors=anchors)
        position = self.position_by_key(result, "equity:123456789")
        self.assertEqual(position["trade_date_quantity"], "3")
        self.assertEqual(position["settled_quantity"], "3")
        self.assertEqual(result["events"], [])
        issue = result["review"]["issues"][0]
        self.assertEqual(issue["security_key"], "equity:123456789")
        self.assertEqual(issue["anchor_date"], "2021-01-01")
        self.assertEqual(issue["anchor_quantity"], "3")
        self.assertEqual(issue["available_quantity"], "3")
        self.assertEqual(issue["source_event_quantity"], "5")
        self.assertIn("oversized_equity_close:sell", issue["review_reason"])

    def test_future_option_anchor_does_not_match_earlier_lifecycle_as_of(self):
        anchors = [{
            "anchor_date": "2021-02-01",
            "asset_type": "option",
            "option_underlying": "ACME",
            "option_expiration": "2021-01-15",
            "option_type": "call",
            "option_strike": "7.50",
            "quantity": "2",
        }]
        result = build_position_ledger(
            [option_record(1, "2021-01-03", "2021-01-03", "OEXP", "expiration", "2")],
            as_of=date(2021, 1, 31),
            anchors=anchors,
        )
        self.assertEqual(result["events"], [])
        self.assertEqual(result["review"]["review_count"], 1)
        self.assertIn("unmatched_option_lifecycle:expiration", result["review"]["issues"][0]["review_reason"])
        self.assertEqual(result["summary"]["future_anchor_count"], 1)

    def test_pending_settlement_excluded_from_settled_quantity(self):
        result = build_position_ledger([
            record(1, "2021-01-05", "2021-01-08", "Buy", "buy", "equity", "10"),
        ], as_of=date(2021, 1, 6))
        position = self.position_by_key(result, "equity:123456789")
        self.assertEqual(position["trade_date_quantity"], "10")
        self.assertEqual(position["settled_quantity"], "0")
        self.assertEqual(result["pending_settlement"][0]["source_row_id"], "1")

    def test_duplicate_looking_fills_are_preserved_with_review_status(self):
        rows = [
            record(1, "2021-01-01", "2021-01-01", "Buy", "buy", "equity", "1", review_status="review", review_reasons="potential_identical_fill"),
            record(2, "2021-01-01", "2021-01-01", "Buy", "buy", "equity", "1", review_status="review", review_reasons="potential_identical_fill"),
        ]
        result = build_position_ledger(rows)
        self.assertEqual(len(result["events"]), 2)
        self.assertEqual(self.position_by_key(result, "equity:123456789")["trade_date_quantity"], "2")
        self.assertTrue(all(event["review_status"] == "review" for event in result["events"]))

    def test_unresolved_split_or_merger_enters_review(self):
        result = build_position_ledger([
            record(1, "2021-01-01", "2021-01-01", "SS", "stock_split", "equity", "2", family="corporate_action"),
            record(2, "2021-01-02", "2021-01-02", "MRGS", "merger", "equity", "1", family="corporate_action"),
        ])
        self.assertEqual(result["events"], [])
        self.assertEqual(result["review"]["review_count"], 2)
        reasons = "|".join(issue["review_reason"] for issue in result["review"]["issues"])
        self.assertIn("unresolved_corporate_action:stock_split", reasons)
        self.assertIn("unresolved_corporate_action:merger", reasons)

    def test_malformed_dates_and_quantities_enter_review(self):
        result = build_position_ledger([
            record(1, "bad-date", "2021-01-01", "Buy", "buy", "equity", "1"),
            record(2, "2021-01-01", "2021-01-01", "Buy", "buy", "equity", "not-a-number"),
        ])
        self.assertEqual(result["events"], [])
        self.assertEqual(result["review"]["review_count"], 2)

    def test_cli_smoke_writes_expected_outputs(self):
        headers = [
            "Activity Date", "Process Date", "Settle Date", "Instrument", "Description",
            "Trans Code", "Quantity", "Price", "Amount",
        ]
        rows = [
            ["01/01/2021", "01/01/2021", "01/04/2021", "ACME", "Buy ACME CUSIP: 123456789", "Buy", "2", "$10.00", "($20.00)"],
            ["01/05/2021", "01/05/2021", "01/08/2021", "ACME", "Sell ACME CUSIP: 123456789", "Sell", "1", "$11.00", "$11.00"],
        ]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "synthetic_robinhood.csv"
            output = Path(directory) / "positions"
            with source.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(headers)
                writer.writerows(rows)
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "trademirror.cli",
                    "position-ledger",
                    str(source),
                    "--output-dir",
                    str(output),
                    "--as-of",
                    "2021-01-06",
                ],
                check=True,
                env={**os.environ, "PYTHONPATH": "src"},
                cwd=Path(__file__).parents[1],
                capture_output=True,
                text=True,
            )
            expected = {
                "position_events.csv",
                "positions_as_of.csv",
                "position_history.csv",
                "pending_position_settlement.csv",
                "position_summary.json",
                "position_review.json",
            }
            self.assertEqual(expected, {path.name for path in output.iterdir()})
            summary = json.loads((output / "position_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["pending_settlement_count"], 1)


if __name__ == "__main__":
    unittest.main()
