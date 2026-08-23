import csv
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from trademirror.trusted_trades import (
    TRUSTED_TRADE_FIELDS,
    build_trusted_trade_dataset,
    write_trusted_trade_outputs,
)


def write_csv(path, fields, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def equity_match(**overrides):
    row = {
        "security_key": "equity:037833100",
        "symbol": "ACME",
        "identity_confidence": "deterministic",
        "opening_event_id": "1",
        "closing_event_id": "2",
        "opening_trade_date": "2021-01-01",
        "closing_trade_date": "2021-01-10",
        "opening_settle_date": "2021-01-04",
        "closing_settle_date": "2021-01-12",
        "matched_quantity": "1",
        "allocated_opening_cost": "10",
        "allocated_closing_proceeds": "15",
        "realized_pnl": "5",
        "realized_return_pct": "50",
        "holding_period_days": "9",
        "basis_status": "known",
        "review_status": "validated",
        "review_reason": "",
    }
    row.update(overrides)
    return row


def option_match(**overrides):
    row = {
        "security_key": "option-cusip:123456789",
        "structural_key": "option:ACME:2021-01-15:call:10.00",
        "option_cusip": "123456789",
        "identity_confidence": "high_cusip",
        "underlying": "ACME",
        "option_expiration": "2021-01-15",
        "option_type": "call",
        "option_strike": "10.00",
        "position_side": "long",
        "opening_action": "BTO",
        "closing_action": "STC",
        "opening_event_id": "3",
        "closing_event_id": "4",
        "opening_trade_date": "2021-01-02",
        "closing_trade_date": "2021-01-11",
        "opening_settle_date": "2021-01-04",
        "closing_settle_date": "2021-01-13",
        "matched_quantity": "1",
        "allocated_opening_cost": "100",
        "allocated_opening_credit": "",
        "allocated_closing_proceeds": "140",
        "allocated_closing_cost": "",
        "realized_pnl": "40",
        "realized_return_pct": "40",
        "pnl_to_opening_credit_pct": "",
        "holding_period_days": "9",
        "days_to_expiration_at_open": "13",
        "days_to_expiration_at_close": "4",
        "outcome": "closed",
        "basis_transfer_required": "false",
        "basis_status": "known",
        "review_status": "validated",
        "review_reason": "",
    }
    row.update(overrides)
    return row


class TrustedTradesTests(unittest.TestCase):
    def build_dataset(self, equity_rows=None, option_rows=None, equity_review=None, option_review=None, transfers=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        equity_dir = root / "equity"
        option_dir = root / "option"
        write_csv(equity_dir / "equity_lot_matches.csv", list(equity_match().keys()), equity_rows or [])
        write_csv(option_dir / "option_lot_matches.csv", list(option_match().keys()), option_rows or [])
        write_csv(option_dir / "option_basis_transfers.csv", [
            "security_key",
            "structural_key",
            "option_cusip",
            "identity_confidence",
            "underlying",
            "option_expiration",
            "option_type",
            "option_strike",
            "position_side",
            "outcome",
            "opening_event_id",
            "closing_event_id",
            "closing_trade_date",
            "matched_quantity",
            "premium_cost_to_transfer",
            "premium_credit_to_transfer",
            "basis_status",
            "review_status",
            "review_reason",
        ], transfers or [])
        (equity_dir / "equity_lot_review.json").write_text(
            json.dumps({"issues": equity_review or []}),
            encoding="utf-8",
        )
        (option_dir / "option_lot_review.json").write_text(
            json.dumps({"issues": option_review or []}),
            encoding="utf-8",
        )
        return root, build_trusted_trade_dataset(equity_dir=equity_dir, option_dir=option_dir)

    def test_high_confidence_equity_and_option_matches(self):
        _, result = self.build_dataset(
            equity_rows=[equity_match()],
            option_rows=[option_match()],
        )
        self.assertEqual(len(result["trusted_closed_trades"]), 2)
        self.assertEqual(result["coverage_summary"]["confidence"]["high_confidence"]["count"], 2)
        self.assertEqual(result["coverage_summary"]["confidence"]["high_confidence"]["realized_pnl"], "45")

    def test_limited_confidence_potential_identical_fill_is_separate(self):
        _, result = self.build_dataset(
            equity_rows=[equity_match(review_status="review", review_reason="potential_identical_fill")],
        )
        self.assertEqual(len(result["trusted_closed_trades"]), 0)
        self.assertEqual(len(result["limited_confidence_trades"]), 1)
        self.assertEqual(result["limited_confidence_trades"][0]["confidence"], "limited_confidence")
        self.assertIn("potential_identical_fill", result["limited_confidence_trades"][0]["reason_codes"])

    def test_unknown_basis_match_is_excluded(self):
        _, result = self.build_dataset(
            equity_rows=[equity_match(basis_status="unknown", allocated_opening_cost="", realized_pnl="")],
        )
        self.assertEqual(result["coverage_summary"]["confidence"]["excluded"]["count"], 1)
        self.assertIn("unknown_basis_closure", result["excluded_trades"][0]["reason_codes"])

    def test_oversell_unmatched_option_close_and_corporate_action_are_review_exclusions(self):
        equity_review = [
            {"review_reason": "oversell_empty_inventory:security_key=equity:037833100", "source_row_id": "1"},
            {"review_reason": "unsupported_corporate_action_for_realized_pnl", "source_row_id": "2"},
        ]
        option_review = [
            {"review_reason": "unmatched_option_close:security_key=option-cusip:123456789", "source_row_id": "3"},
        ]
        _, result = self.build_dataset(equity_review=equity_review, option_review=option_review)
        counts = result["exclusion_summary"]["review_item_counts_by_reason"]
        self.assertEqual(counts["oversell_empty_inventory"], 1)
        self.assertEqual(counts["unsupported_corporate_action_for_realized_pnl"], 1)
        self.assertEqual(counts["unmatched_option_close"], 1)

    def test_basis_transfer_is_excluded_review_item(self):
        _, result = self.build_dataset(
            transfers=[{
                "security_key": "option-cusip:123456789",
                "structural_key": "option:ACME:2021-01-15:call:10.00",
                "option_cusip": "123456789",
                "identity_confidence": "high_cusip",
                "underlying": "ACME",
                "option_expiration": "2021-01-15",
                "option_type": "call",
                "option_strike": "10.00",
                "position_side": "long",
                "outcome": "exercise",
                "opening_event_id": "3",
                "closing_event_id": "4",
                "closing_trade_date": "2021-01-11",
                "matched_quantity": "1",
                "premium_cost_to_transfer": "100",
                "premium_credit_to_transfer": "",
                "basis_status": "known",
                "review_status": "review",
                "review_reason": "basis_transfer_required",
            }]
        )
        self.assertEqual(result["exclusion_summary"]["review_item_counts_by_reason"]["basis_transfer_required"], 1)

    def test_identity_ambiguity_and_invalid_amount_or_date_are_excluded(self):
        _, result = self.build_dataset(equity_rows=[
            equity_match(opening_event_id="1", closing_event_id="2", review_reason="ambiguous_option_contract_identity"),
            equity_match(opening_event_id="3", closing_event_id="4", allocated_closing_proceeds="bad-money"),
            equity_match(opening_event_id="5", closing_event_id="6", closing_trade_date="not-a-date"),
            equity_match(opening_event_id="7", closing_event_id="8", realized_pnl="4"),
        ])
        self.assertEqual(result["coverage_summary"]["confidence"]["excluded"]["count"], 4)
        reasons = "|".join(row["reason_codes"] for row in result["excluded_trades"])
        self.assertIn("ambiguous_option_contract_identity", reasons)
        self.assertIn("invalid_amount", reasons)
        self.assertIn("invalid_close_date", reasons)
        self.assertIn("realized_pnl_reconciliation_mismatch", reasons)

    def test_deterministic_output_ordering_and_aggregate_reconciliation(self):
        _, result = self.build_dataset(equity_rows=[
            equity_match(opening_event_id="2", closing_event_id="2", closing_trade_date="2021-01-12"),
            equity_match(opening_event_id="1", closing_event_id="1", closing_trade_date="2021-01-10"),
        ])
        closes = [row["close_date"] for row in result["trusted_closed_trades"]]
        self.assertEqual(closes, sorted(closes))
        total = sum(section["count"] for section in result["coverage_summary"]["confidence"].values())
        self.assertEqual(total, result["coverage_summary"]["total_completed_matches_evaluated"])
        self.assertTrue(result["coverage_summary"]["high_confidence_pnl_reconciles_to_rows"])

    def test_stable_opaque_instrument_identity_and_privacy_safe_outputs(self):
        root, result = self.build_dataset(
            equity_rows=[equity_match()],
            option_rows=[option_match()],
            equity_review=[{"review_reason": "oversell_empty_inventory:security_key=equity:037833100"}],
        )
        output_dir = root / "trusted"
        write_trusted_trade_outputs(result, output_dir)
        rendered = "\n".join(path.read_text(encoding="utf-8") for path in output_dir.iterdir())
        self.assertNotIn("037833100", rendered)
        self.assertNotIn("123456789", rendered)
        self.assertNotIn("security_key", rendered)
        self.assertNotIn("description_raw", rendered)
        self.assertNotIn("raw_row_json", rendered)
        for row in result["trusted_closed_trades"]:
            self.assertTrue(row["instrument_id"].startswith("instrument_"))
            self.assertNotIn("equity:", row["instrument_id"])

    def test_cli_smoke_writes_expected_outputs(self):
        root, _ = self.build_dataset(equity_rows=[equity_match()], option_rows=[option_match()])
        output_dir = root / "trusted_cli"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "trademirror.cli",
                "trusted-trades",
                "--equity-dir",
                str(root / "equity"),
                "--option-dir",
                str(root / "option"),
                "--output-dir",
                str(output_dir),
            ],
            check=True,
        )
        self.assertEqual(
            sorted(path.name for path in output_dir.iterdir()),
            [
                "coverage_summary.json",
                "exclusion_summary.json",
                "limited_confidence_trades.csv",
                "trusted_closed_trades.csv",
                "trusted_trade_review.json",
            ],
        )
        with (output_dir / "trusted_closed_trades.csv").open(newline="", encoding="utf-8") as handle:
            self.assertEqual(next(csv.reader(handle)), TRUSTED_TRADE_FIELDS)


if __name__ == "__main__":
    unittest.main()
