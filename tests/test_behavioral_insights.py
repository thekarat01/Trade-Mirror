import csv
import json
import subprocess
import sys
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from trademirror.behavioral_insights import (
    build_behavioral_insights,
    write_behavioral_insights_outputs,
)
from trademirror.trusted_trades import TRUSTED_TRADE_FIELDS


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRUSTED_TRADE_FIELDS)
        writer.writeheader()
        writer.writerows(rows)


def trade(
    index,
    *,
    instrument="instrument_alpha",
    asset_type="equity",
    open_date="2021-01-01",
    close_date="2021-01-02",
    holding="1",
    pnl="10",
    confidence="high_confidence",
    reasons="",
):
    realized = Decimal(pnl)
    cost = Decimal("100")
    proceeds = cost + realized
    return {
        "trade_id": f"trade_{index:03d}",
        "instrument_id": instrument,
        "asset_type": asset_type,
        "open_date": open_date,
        "close_date": close_date,
        "holding_period_days": holding,
        "matched_quantity": "1",
        "cost_basis": str(cost),
        "proceeds": str(proceeds),
        "realized_pnl": str(realized),
        "return_percentage": str(realized),
        "confidence": confidence,
        "reason_codes": reasons,
    }


def build_fixture(root):
    high = [
        trade(1, instrument="instrument_reentry", open_date="2021-01-01", close_date="2021-01-01", holding="0", pnl="-50"),
        trade(2, instrument="instrument_reentry", open_date="2021-01-05", close_date="2021-01-06", holding="1", pnl="-20"),
        trade(3, instrument="instrument_reentry", open_date="2021-01-15", close_date="2021-01-20", holding="5", pnl="30"),
        trade(4, instrument="instrument_reentry", open_date="2021-02-05", close_date="2021-02-20", holding="15", pnl="-40"),
        trade(25, instrument="instrument_reentry", open_date="2021-03-10", close_date="2021-03-20", holding="10", pnl="-35"),
        trade(26, instrument="instrument_reentry", open_date="2021-03-25", close_date="2021-04-04", holding="10", pnl="35"),
        trade(5, instrument="instrument_concentrated", open_date="2021-01-07", close_date="2021-02-15", holding="39", pnl="-500"),
        trade(6, instrument="instrument_concentrated", open_date="2021-02-01", close_date="2021-03-15", holding="42", pnl="-120"),
        trade(7, instrument="instrument_concentrated", open_date="2021-03-01", close_date="2021-04-15", holding="45", pnl="-80"),
        trade(8, instrument="instrument_e1", open_date="2021-02-01", close_date="2021-02-03", holding="2", pnl="25"),
        trade(9, instrument="instrument_e2", open_date="2021-02-04", close_date="2021-02-05", holding="1", pnl="15"),
        trade(10, instrument="instrument_e3", open_date="2021-03-01", close_date="2021-03-05", holding="4", pnl="20"),
        trade(11, instrument="instrument_e4", open_date="2021-03-06", close_date="2021-03-08", holding="2", pnl="15"),
        trade(12, instrument="instrument_e5", open_date="2021-04-01", close_date="2021-04-03", holding="2", pnl="30"),
        trade(13, instrument="instrument_e6", open_date="2021-04-05", close_date="2021-04-07", holding="2", pnl="12"),
        trade(14, instrument="instrument_e7", open_date="2021-05-01", close_date="2021-05-01", holding="0", pnl="18"),
        trade(15, instrument="instrument_e8", open_date="2021-05-02", close_date="2021-05-04", holding="2", pnl="22"),
        trade(16, instrument="instrument_e9", open_date="2021-06-01", close_date="2021-06-10", holding="9", pnl="-30"),
        trade(17, instrument="instrument_e10", open_date="2021-06-02", close_date="2021-06-11", holding="9", pnl="35"),
        trade(18, instrument="instrument_o1", asset_type="option", open_date="2021-06-03", close_date="2021-06-12", holding="9", pnl="-70"),
        trade(19, instrument="instrument_o2", asset_type="option", open_date="2021-06-04", close_date="2021-06-13", holding="9", pnl="-60"),
        trade(20, instrument="instrument_o3", asset_type="option", open_date="2021-07-01", close_date="2021-07-15", holding="14", pnl="-40"),
        trade(21, instrument="instrument_o4", asset_type="option", open_date="2021-07-02", close_date="2021-07-16", holding="14", pnl="25"),
        trade(22, instrument="instrument_o5", asset_type="option", open_date="2021-08-01", close_date="2021-08-10", holding="9", pnl="-35"),
        trade(23, instrument="instrument_e11", open_date="2022-01-01", close_date="2022-01-10", holding="9", pnl="40"),
        trade(24, instrument="instrument_o6", asset_type="option", open_date="2022-02-01", close_date="2022-02-20", holding="19", pnl="-45"),
    ]
    limited = [
        trade(101, instrument="instrument_limited", pnl="10", confidence="limited_confidence", reasons="potential_identical_fill"),
        trade(102, instrument="instrument_limited", pnl="-5", confidence="limited_confidence", reasons="potential_identical_fill"),
    ]
    write_csv(root / "trusted_closed_trades.csv", high)
    write_csv(root / "limited_confidence_trades.csv", limited)
    coverage = {
        "total_completed_matches_evaluated": 30,
        "confidence": {
            "high_confidence": {"count": 26, "cost_basis": "2600", "proceeds": "1797", "realized_pnl": "-803"},
            "limited_confidence": {"count": 2, "cost_basis": "200", "proceeds": "205", "realized_pnl": "5"},
            "excluded": {"count": 2, "cost_basis": "200", "proceeds": "0", "realized_pnl": "0"},
        },
    }
    (root / "coverage_summary.json").write_text(json.dumps(coverage), encoding="utf-8")
    (root / "exclusion_summary.json").write_text(
        json.dumps({
            "excluded_match_count": 2,
            "excluded_match_counts_by_reason": {"unknown_basis_closure": 1, "unmatched_option_close": 1},
            "review_item_count": 2,
            "review_item_counts_by_reason": {"unknown_basis_closure": 1, "unmatched_option_close": 1},
        }),
        encoding="utf-8",
    )
    (root / "trusted_trade_review.json").write_text(json.dumps({"items": []}), encoding="utf-8")


class BehavioralInsightsTests(unittest.TestCase):
    def build_result(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        build_fixture(root)
        return root, build_behavioral_insights(trusted_dir=root)

    def test_exact_reconciliation_and_no_double_counting(self):
        _, result = self.build_result()
        summary = result["behavioral_summary"]
        self.assertEqual(summary["overall"]["trade_count"], 26)
        self.assertEqual(summary["overall"]["net_realized_pnl"], "-803")
        self.assertTrue(result["insight_validation"]["high_confidence_pnl_reconciles_to_trusted_dataset"])
        counted = sum(int(row["trade_count"]) for row in result["annual_behavior"])
        self.assertEqual(counted, 26)

    def test_zero_gains_losses_and_denominators(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            write_csv(root / "trusted_closed_trades.csv", [
                trade(1, pnl="0"),
                trade(2, pnl="0"),
            ])
            write_csv(root / "limited_confidence_trades.csv", [])
            (root / "coverage_summary.json").write_text(json.dumps({
                "confidence": {
                    "high_confidence": {"count": 2, "realized_pnl": "0"},
                    "limited_confidence": {"count": 0},
                    "excluded": {"count": 0},
                }
            }), encoding="utf-8")
            (root / "exclusion_summary.json").write_text(json.dumps({}), encoding="utf-8")
            result = build_behavioral_insights(trusted_dir=root)
        self.assertEqual(result["behavioral_summary"]["overall"]["profit_factor"], "")
        self.assertEqual(result["behavioral_summary"]["overall"]["win_rate"], "0")

    def test_equity_option_segmentation_and_loss_contribution(self):
        _, result = self.build_result()
        asset = result["behavioral_summary"]["asset_type"]
        self.assertEqual(asset["equity"]["trade_count"], 20)
        self.assertEqual(asset["option"]["trade_count"], 6)
        self.assertLess(Decimal(asset["option"]["net_pnl"]), Decimal("0"))
        self.assertGreater(Decimal(asset["option"]["share_of_overall_included_losses"]), Decimal("0"))

    def test_holding_period_bins_and_loser_duration_comparison(self):
        _, result = self.build_result()
        bins = {row["holding_period_bin"]: row for row in result["holding_period_behavior"]}
        self.assertEqual(bins["same_day"]["trade_count"], "2")
        self.assertEqual(bins["1_7_days"]["trade_count"], "9")
        self.assertEqual(bins["8_30_days"]["trade_count"], "12")
        self.assertEqual(bins["31_90_days"]["trade_count"], "3")
        self.assertEqual(result["behavioral_summary"]["holding_period"]["losers_generally_held_longer"], True)

    def test_top_loss_concentration_and_offset_count(self):
        _, result = self.build_result()
        concentration = result["behavioral_summary"]["loss_concentration"]
        self.assertGreater(Decimal(concentration["largest_1_loss_share"]), Decimal("0.4"))
        self.assertGreater(Decimal(concentration["largest_opaque_instrument_loss_share"]), Decimal("0.5"))
        self.assertNotEqual(concentration["profitable_trades_required_to_offset_largest_loss"], "")

    def test_monthly_activity_thresholding(self):
        _, result = self.build_result()
        activity = result["behavioral_summary"]["trading_activity"]
        self.assertEqual(activity["threshold_rule"], "months with trade count at or above nearest-rank 75th percentile")
        self.assertGreaterEqual(activity["eligible_month_count"], 5)
        self.assertTrue(any(row["activity_segment"] == "high_activity" for row in result["activity_behavior"]))

    def test_reentry_windows_and_same_day_boundary(self):
        _, result = self.build_result()
        rows = {row["window_days"]: row for row in result["reentry_behavior"]}
        self.assertGreaterEqual(int(rows["30"]["eligible_trade_count"]), 5)
        self.assertEqual(rows["7"]["confidence"], "insufficient_evidence")
        self.assertIn("insufficient_reentry_7d", [row["insight_code"] for row in result["insight_candidates"]])

    def test_sample_size_suppression_and_confidence_ranking(self):
        _, result = self.build_result()
        insufficient = [row for row in result["insight_candidates"] if row["confidence"] == "insufficient_evidence"]
        self.assertTrue(insufficient)
        promoted = result["ranked_insights"]["what_hurt"] + result["ranked_insights"]["what_helped"]
        self.assertTrue(all(row["confidence"] not in {"low", "insufficient_evidence"} for row in promoted))
        self.assertLessEqual(len(result["ranked_insights"]["what_hurt"]), 3)
        self.assertLessEqual(len(result["ranked_insights"]["what_helped"]), 3)

    def test_limited_confidence_sensitivity_is_separate(self):
        _, result = self.build_result()
        sensitivity = result["behavioral_summary"]["limited_confidence_sensitivity"]
        self.assertEqual(sensitivity["limited_confidence_trade_count"], 2)
        self.assertTrue(sensitivity["kept_separate_from_primary"])
        self.assertEqual(result["insight_validation"]["excluded_records_used_in_primary_metrics"], False)

    def test_controlled_templates_and_guardrails_are_not_recommendations(self):
        _, result = self.build_result()
        rendered = json.dumps(result["insight_candidates"], sort_keys=True).lower()
        self.assertIn("without claiming causation", rendered)
        self.assertNotIn("buy ", rendered)
        self.assertNotIn("sell ", rendered)
        self.assertNotIn("recommend", rendered)

    def test_privacy_safe_output_schema_and_opaque_identifiers(self):
        root, result = self.build_result()
        output_dir = root / "insights"
        write_behavioral_insights_outputs(result, output_dir)
        rendered = "\n".join(path.read_text(encoding="utf-8") for path in output_dir.iterdir())
        self.assertNotIn("description_raw", rendered)
        self.assertNotIn("raw_row_json", rendered)
        self.assertNotIn("security_key", rendered)
        self.assertNotIn("equity:", rendered)
        self.assertNotIn("option:", rendered)
        self.assertNotIn("option_cusip", rendered)
        self.assertNotIn("structural_key", rendered)
        self.assertNotIn("CUSIP", rendered)
        self.assertIn("instrument_", rendered)
        self.assertNotIn("ACME", rendered)

    def test_repeated_runs_are_identical(self):
        root, first = self.build_result()
        second = build_behavioral_insights(trusted_dir=root)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

    def test_cli_smoke_writes_expected_outputs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            trusted = root / "trusted"
            build_fixture(trusted)
            output_dir = root / "out"
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "trademirror.cli",
                    "behavioral-insights",
                    "--trusted-dir",
                    str(trusted),
                    "--output-dir",
                    str(output_dir),
                ],
                check=True,
            )
            self.assertEqual(
                sorted(path.name for path in output_dir.iterdir()),
                [
                    "activity_behavior.csv",
                    "annual_behavior.csv",
                    "behavioral_summary.json",
                    "holding_period_behavior.csv",
                    "insight_candidates.json",
                    "insight_validation.json",
                    "ranked_insights.json",
                    "reentry_behavior.csv",
                ],
            )


if __name__ == "__main__":
    unittest.main()
