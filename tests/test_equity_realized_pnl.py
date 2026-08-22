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

from trademirror.equity_realized_pnl import build_equity_realized_pnl
from trademirror.position_ledger import build_position_ledger


def equity_record(
    source_row_id,
    activity_date,
    settle_date,
    code,
    event_type,
    quantity,
    amount,
    *,
    instrument="ACME",
    cusip="123456789",
    family="trade",
    asset_type="equity",
    review_status="validated",
    review_reasons="",
):
    return {
        "source_row_id": source_row_id,
        "activity_date": activity_date,
        "settle_date": settle_date,
        "transaction_code_raw": code,
        "transaction_family": family,
        "event_type": event_type,
        "asset_type": asset_type,
        "quantity_numeric": quantity,
        "amount": amount,
        "instrument": instrument,
        "cusip": cusip,
        "review_status": review_status,
        "review_reasons": review_reasons,
    }


def option_record(source_row_id):
    row = equity_record(
        source_row_id,
        "2021-01-01",
        "2021-01-04",
        "BTO",
        "buy_to_open",
        "1",
        "-100",
        instrument="ACME",
        cusip="",
        family="option_trade",
        asset_type="option",
    )
    row.update({
        "option_underlying": "ACME",
        "option_expiration": "2021-01-15",
        "option_type": "call",
        "option_strike": "7.50",
    })
    return row


class EquityRealizedPnlTests(unittest.TestCase):
    def test_one_profitable_sale(self):
        result = build_equity_realized_pnl([
            equity_record(1, "2021-01-01", "2021-01-04", "Buy", "buy", "10", "-100"),
            equity_record(2, "2021-01-10", "2021-01-12", "Sell", "sell", "10", "150"),
        ])
        self.assertEqual(result["matches"][0]["realized_pnl"], "50")
        self.assertEqual(result["matches"][0]["realized_return_pct"], "50")
        self.assertEqual(result["summary"]["realized_gain"], "50")
        self.assertEqual(result["summary"]["net_realized_pnl"], "50")
        self.assertEqual(result["summary"]["winning_matches"], 1)

    def test_one_losing_sale(self):
        result = build_equity_realized_pnl([
            equity_record(1, "2021-01-01", "2021-01-04", "Buy", "buy", "10", "-100"),
            equity_record(2, "2021-01-10", "2021-01-12", "Sell", "sell", "10", "80"),
        ])
        self.assertEqual(result["matches"][0]["realized_pnl"], "-20")
        self.assertEqual(result["summary"]["realized_loss"], "-20")
        self.assertEqual(result["summary"]["losing_matches"], 1)

    def test_partial_sale_from_one_lot(self):
        result = build_equity_realized_pnl([
            equity_record(1, "2021-01-01", "2021-01-04", "Buy", "buy", "10", "-100"),
            equity_record(2, "2021-01-10", "2021-01-12", "Sell", "sell", "4", "60"),
        ])
        self.assertEqual(result["matches"][0]["matched_quantity"], "4")
        self.assertEqual(result["matches"][0]["allocated_opening_cost"], "40")
        self.assertEqual(result["matches"][0]["allocated_closing_proceeds"], "60")
        self.assertEqual(result["open_lots"][0]["remaining_quantity"], "6")
        self.assertEqual(result["open_lots"][0]["remaining_cost"], "60")

    def test_sale_spanning_multiple_fifo_lots(self):
        result = build_equity_realized_pnl([
            equity_record(1, "2021-01-01", "2021-01-04", "Buy", "buy", "5", "-50"),
            equity_record(2, "2021-01-02", "2021-01-05", "Buy", "buy", "5", "-75"),
            equity_record(3, "2021-01-10", "2021-01-12", "Sell", "sell", "8", "160"),
        ])
        self.assertEqual([match["opening_event_id"] for match in result["matches"]], ["1", "2"])
        self.assertEqual([match["matched_quantity"] for match in result["matches"]], ["5", "3"])
        self.assertEqual([match["allocated_opening_cost"] for match in result["matches"]], ["50", "45"])
        self.assertEqual(result["summary"]["net_realized_pnl"], "65")

    def test_multiple_sales_against_one_lot(self):
        result = build_equity_realized_pnl([
            equity_record(1, "2021-01-01", "2021-01-04", "Buy", "buy", "10", "-100"),
            equity_record(2, "2021-01-10", "2021-01-12", "Sell", "sell", "3", "36"),
            equity_record(3, "2021-01-11", "2021-01-13", "Sell", "sell", "2", "18"),
        ])
        self.assertEqual([match["matched_quantity"] for match in result["matches"]], ["3", "2"])
        self.assertEqual(result["open_lots"][0]["remaining_quantity"], "5")
        self.assertEqual(result["summary"]["net_realized_pnl"], "4")

    def test_identical_looking_fills_remain_separate(self):
        result = build_equity_realized_pnl([
            equity_record(
                1, "2021-01-01", "2021-01-04", "Buy", "buy", "1", "-10",
                review_status="review", review_reasons="potential_identical_fill",
            ),
            equity_record(
                2, "2021-01-01", "2021-01-04", "Buy", "buy", "1", "-10",
                review_status="review", review_reasons="potential_identical_fill",
            ),
            equity_record(3, "2021-01-10", "2021-01-12", "Sell", "sell", "2", "30"),
        ])
        self.assertEqual([match["opening_event_id"] for match in result["matches"]], ["1", "2"])
        self.assertEqual(len(result["matches"]), 2)
        self.assertTrue(all(match["review_status"] == "review" for match in result["matches"]))

    def test_cusip_identity_and_symbol_fallback(self):
        result = build_equity_realized_pnl([
            equity_record(1, "2021-01-01", "2021-01-01", "Buy", "buy", "1", "-10", instrument="SAME", cusip="111111111"),
            equity_record(2, "2021-01-02", "2021-01-02", "Buy", "buy", "1", "-20", instrument="SAME", cusip="222222222"),
            equity_record(3, "2021-01-03", "2021-01-03", "Buy", "buy", "1", "-30", instrument="NOC", cusip=""),
        ])
        keys = {row["security_key"] for row in result["open_lots"]}
        self.assertIn("equity:111111111", keys)
        self.assertIn("equity:222222222", keys)
        self.assertIn("equity-symbol:NOC", keys)
        fallback = [row for row in result["open_lots"] if row["security_key"] == "equity-symbol:NOC"][0]
        self.assertEqual(fallback["identity_confidence"], "lower_symbol_only")

    def test_sell_only_event_enters_review_without_crashing(self):
        result = build_equity_realized_pnl([
            equity_record(1, "2021-01-02", "2021-01-02", "Sell", "sell", "3", "45"),
        ])
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["open_lots"], [])
        self.assertEqual(result["summary"]["unmatched_quantity"], "3")
        self.assertEqual(result["summary"]["review_count"], 1)
        issue = result["review"]["issues"][0]
        self.assertEqual(issue["source_row_id"], "1")
        self.assertEqual(issue["trade_date"], "2021-01-02")
        self.assertEqual(issue["security_key"], "equity:123456789")
        self.assertEqual(issue["sale_quantity"], "3")
        self.assertEqual(issue["available_quantity"], "0")
        self.assertEqual(issue["unmatched_quantity"], "3")
        self.assertIn("oversell_empty_inventory", issue["review_reason"])

    def test_partial_inventory_oversell_matches_available_only(self):
        result = build_equity_realized_pnl([
            equity_record(1, "2021-01-01", "2021-01-01", "Buy", "buy", "2", "-20"),
            equity_record(2, "2021-01-02", "2021-01-02", "Sell", "sell", "3", "45"),
        ])
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(result["matches"][0]["matched_quantity"], "2")
        self.assertEqual(result["matches"][0]["allocated_opening_cost"], "20")
        self.assertEqual(result["matches"][0]["allocated_closing_proceeds"], "30")
        self.assertEqual(result["matches"][0]["realized_pnl"], "10")
        self.assertEqual(result["open_lots"], [])
        self.assertEqual(result["summary"]["unmatched_quantity"], "1")
        issue = result["review"]["issues"][0]
        self.assertEqual(issue["available_quantity"], "2")
        self.assertEqual(issue["unmatched_quantity"], "1")
        self.assertIn("oversell_without_available_long_lots", issue["review_reason"])

    def test_multiple_oversells_reconcile_review_quantities(self):
        result = build_equity_realized_pnl([
            equity_record(1, "2021-01-01", "2021-01-01", "Buy", "buy", "1", "-10"),
            equity_record(2, "2021-01-02", "2021-01-02", "Sell", "sell", "3", "45"),
            equity_record(3, "2021-01-03", "2021-01-03", "Sell", "sell", "2", "20"),
        ])
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(result["matches"][0]["matched_quantity"], "1")
        self.assertEqual(result["summary"]["unmatched_quantity"], "4")
        self.assertEqual(result["summary"]["review_count"], 2)
        issue_quantities = [
            Decimal(issue["unmatched_quantity"])
            for issue in result["review"]["issues"]
            if issue["review_reason"].startswith("oversell_")
        ]
        self.assertEqual(sum(issue_quantities, Decimal("0")), Decimal("4"))

    def test_processing_continues_after_oversell(self):
        result = build_equity_realized_pnl([
            equity_record(1, "2021-01-01", "2021-01-01", "Sell", "sell", "1", "10"),
            equity_record(2, "2021-01-02", "2021-01-02", "Buy", "buy", "2", "-20"),
            equity_record(3, "2021-01-03", "2021-01-03", "Sell", "sell", "1", "15"),
        ])
        self.assertEqual(len(result["matches"]), 1)
        self.assertEqual(result["matches"][0]["closing_event_id"], "3")
        self.assertEqual(result["matches"][0]["realized_pnl"], "5")
        self.assertEqual(result["open_lots"][0]["remaining_quantity"], "1")
        self.assertEqual(result["summary"]["unmatched_quantity"], "1")

    def test_anchor_quantity_has_unknown_basis(self):
        anchors = [{
            "anchor_date": "2021-01-01",
            "asset_type": "equity",
            "cusip": "123456789",
            "symbol": "ACME",
            "quantity": "5",
        }]
        result = build_equity_realized_pnl([
            equity_record(1, "2021-01-02", "2021-01-02", "Sell", "sell", "2", "40"),
        ], anchors=anchors)
        self.assertEqual(result["matches"][0]["basis_status"], "unknown")
        self.assertEqual(result["matches"][0]["realized_pnl"], "")
        self.assertEqual(result["summary"]["net_realized_pnl"], "0")
        self.assertEqual(result["summary"]["unknown_basis_quantity"], "2")
        self.assertIn("unknown_basis_closure", result["review"]["issues"][0]["review_reason"])

    def test_as_of_cutoff_and_future_anchor_exclusion(self):
        anchors = [{
            "anchor_date": "2021-02-01",
            "asset_type": "equity",
            "cusip": "123456789",
            "symbol": "ACME",
            "quantity": "100",
        }]
        result = build_equity_realized_pnl([
            equity_record(1, "2021-01-01", "2021-01-01", "Buy", "buy", "2", "-20"),
            equity_record(2, "2021-02-02", "2021-02-02", "Sell", "sell", "1", "15"),
        ], as_of=date(2021, 1, 31), anchors=anchors)
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["open_lots"][0]["remaining_quantity"], "2")
        self.assertEqual(result["summary"]["anchor_count"], 1)

    def test_applicable_anchor_after_final_transaction_creates_unknown_basis_lot(self):
        anchors = [{
            "anchor_date": "2021-01-10",
            "asset_type": "equity",
            "cusip": "123456789",
            "symbol": "ACME",
            "quantity": "5",
        }]
        result = build_equity_realized_pnl([
            equity_record(1, "2021-01-01", "2021-01-01", "Buy", "buy", "1", "-10"),
        ], as_of=date(2021, 1, 31), anchors=anchors)
        self.assertEqual(len(result["open_lots"]), 1)
        self.assertEqual(result["open_lots"][0]["opening_event_id"], "anchor:2021-01-10")
        self.assertEqual(result["open_lots"][0]["remaining_quantity"], "5")
        self.assertEqual(result["open_lots"][0]["basis_status"], "unknown")

    def test_anchor_with_no_transactions_creates_unknown_basis_lot(self):
        anchors = [{
            "anchor_date": "2021-01-10",
            "asset_type": "equity",
            "cusip": "123456789",
            "symbol": "ACME",
            "quantity": "5",
        }]
        result = build_equity_realized_pnl([], as_of=date(2021, 1, 31), anchors=anchors)
        self.assertEqual(len(result["open_lots"]), 1)
        self.assertEqual(result["open_lots"][0]["remaining_quantity"], "5")
        self.assertEqual(result["summary"]["open_lot_count"], 1)

    def test_future_anchor_after_as_of_is_not_applied(self):
        anchors = [{
            "anchor_date": "2021-02-01",
            "asset_type": "equity",
            "cusip": "123456789",
            "symbol": "ACME",
            "quantity": "5",
        }]
        result = build_equity_realized_pnl([], as_of=date(2021, 1, 31), anchors=anchors)
        self.assertEqual(result["open_lots"], [])
        self.assertEqual(result["summary"]["open_lot_count"], 0)

    def test_missing_settlement_dates_do_not_prevent_matching(self):
        result = build_equity_realized_pnl([
            equity_record(1, "2021-01-01", "", "Buy", "buy", "1", "-10"),
            equity_record(2, "2021-01-02", "", "Sell", "sell", "1", "15"),
        ])
        self.assertEqual(result["matches"][0]["realized_pnl"], "5")
        self.assertEqual(result["matches"][0]["opening_settle_date"], "")
        self.assertEqual(result["matches"][0]["closing_settle_date"], "")
        self.assertEqual(result["review"]["review_count"], 0)

    def test_malformed_settlement_metadata_does_not_prevent_matching(self):
        result = build_equity_realized_pnl([
            equity_record(1, "2021-01-01", "not-date", "Buy", "buy", "1", "-10"),
            equity_record(2, "2021-01-02", "also-bad", "Sell", "sell", "1", "15"),
        ])
        self.assertEqual(result["matches"][0]["realized_pnl"], "5")
        self.assertIn("invalid_settle_date_metadata", result["matches"][0]["review_reason"])
        self.assertEqual(result["summary"]["net_realized_pnl"], "5")

    def test_anchor_is_not_double_applied_after_triggering_transaction(self):
        anchors = [{
            "anchor_date": "2021-01-01",
            "asset_type": "equity",
            "cusip": "123456789",
            "symbol": "ACME",
            "quantity": "5",
        }]
        result = build_equity_realized_pnl([
            equity_record(1, "2021-01-02", "2021-01-02", "Buy", "buy", "1", "-10"),
        ], as_of=date(2021, 1, 31), anchors=anchors)
        quantities = [row["remaining_quantity"] for row in result["open_lots"]]
        self.assertEqual(quantities, ["5", "1"])
        self.assertEqual(result["summary"]["open_lot_count"], 2)

    def test_malformed_amount_and_quantity_enter_review(self):
        result = build_equity_realized_pnl([
            equity_record(1, "2021-01-01", "2021-01-01", "Buy", "buy", "not-qty", "-20"),
            equity_record(2, "2021-01-02", "2021-01-02", "Buy", "buy", "1", "not-money"),
        ])
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["open_lots"], [])
        reasons = "|".join(issue["review_reason"] for issue in result["review"]["issues"])
        self.assertIn("invalid_or_missing_quantity", reasons)
        self.assertIn("invalid_or_missing_amount", reasons)

    def test_options_and_non_trade_cash_are_excluded_and_corporate_actions_reviewed(self):
        result = build_equity_realized_pnl([
            option_record(1),
            equity_record(2, "2021-01-01", "2021-01-01", "ACH", "deposit", "", "100", family="funding", asset_type="cash"),
            equity_record(3, "2021-01-02", "2021-01-02", "SS", "stock_split", "1", "0", family="corporate_action"),
        ])
        self.assertEqual(result["matches"], [])
        self.assertEqual(result["open_lots"], [])
        self.assertEqual(result["review"]["review_count"], 1)
        self.assertIn("unsupported_corporate_action_for_realized_pnl", result["review"]["issues"][0]["review_reason"])

    def test_decimal_precision(self):
        result = build_equity_realized_pnl([
            equity_record(1, "2021-01-01", "2021-01-01", "Buy", "buy", "3", "-10"),
            equity_record(2, "2021-01-02", "2021-01-02", "Sell", "sell", "1", "4"),
        ])
        self.assertEqual(result["matches"][0]["allocated_opening_cost"], "3.333333333333333333333333333")
        self.assertEqual(result["matches"][0]["realized_pnl"], "0.666666666666666666666666667")

    def test_lot_totals_reconcile_to_position_ledger(self):
        records = [
            equity_record(1, "2021-01-01", "2021-01-01", "Buy", "buy", "10", "-100"),
            equity_record(2, "2021-01-02", "2021-01-02", "Sell", "sell", "4", "60"),
            equity_record(3, "2021-01-03", "2021-01-03", "Buy", "buy", "2", "-30"),
        ]
        pnl = build_equity_realized_pnl(records)
        positions = build_position_ledger(records)
        open_quantity = sum(Decimal(row["remaining_quantity"]) for row in pnl["open_lots"])
        position_quantity = Decimal(positions["positions_as_of"][0]["trade_date_quantity"])
        self.assertEqual(open_quantity, position_quantity)

    def test_cli_smoke_writes_expected_outputs(self):
        headers = [
            "Activity Date", "Process Date", "Settle Date", "Instrument", "Description",
            "Trans Code", "Quantity", "Price", "Amount",
        ]
        rows = [
            ["01/01/2021", "01/01/2021", "01/04/2021", "ACME", "Buy ACME CUSIP: 123456789", "Buy", "2", "$10.00", "($20.00)"],
            ["01/05/2021", "01/05/2021", "01/08/2021", "ACME", "Sell ACME CUSIP: 123456789", "Sell", "1", "$15.00", "$15.00"],
        ]
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "synthetic_robinhood.csv"
            output = Path(directory) / "realized"
            with source.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(headers)
                writer.writerows(rows)
            subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "trademirror.cli",
                    "realized-pnl",
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
                "equity_lot_matches.csv",
                "equity_open_lots.csv",
                "equity_realized_by_security.csv",
                "equity_realized_summary.json",
                "equity_lot_review.json",
            }
            self.assertEqual(expected, {path.name for path in output.iterdir()})
            summary = json.loads((output / "equity_realized_summary.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["net_realized_pnl"], "5")


if __name__ == "__main__":
    unittest.main()
