from datetime import date
from decimal import Decimal
import unittest

from trademirror.cash_ledger import build_cash_ledger


def record(
    source_row_id,
    activity_date,
    settle_date,
    code,
    family,
    event_type,
    asset_type,
    amount,
    *,
    external=False,
    internal=False,
):
    return {
        "source_row_id": source_row_id,
        "activity_date": activity_date,
        "settle_date": settle_date,
        "transaction_code_raw": code,
        "transaction_family": family,
        "event_type": event_type,
        "asset_type": asset_type,
        "amount": amount,
        "external_cash_flow": external,
        "internal_transfer": internal,
        "review_status": "validated",
        "review_reasons": "",
    }


class CashLedgerTests(unittest.TestCase):
    def test_cash_events_cover_required_categories(self):
        records = [
            record(1, "2021-01-01", "2021-01-01", "ACH", "funding", "deposit", "cash", "1000.00", external=True),
            record(2, "2021-01-02", "2021-01-02", "ACH", "funding", "withdrawal", "cash", "-100.00", external=True),
            record(3, "2021-01-03", "2021-01-05", "Buy", "trade", "buy", "equity", "-300.00"),
            record(4, "2021-01-06", "2021-01-08", "Sell", "trade", "sell", "equity", "350.00"),
            record(5, "2021-01-09", "2021-01-11", "BTO", "option_trade", "buy_to_open", "option", "-50.00"),
            record(6, "2021-01-12", "2021-01-14", "STC", "option_trade", "sell_to_close", "option", "70.00"),
            record(7, "2021-01-15", "2021-01-15", "CDIV", "income", "cash_dividend", "equity", "5.00"),
            record(8, "2021-01-16", "2021-01-16", "GOLD", "fee", "subscription_fee", "cash", "-5.00"),
            record(9, "2021-01-17", "2021-01-17", "MINT", "financing", "margin_interest", "cash", "-2.00"),
            record(10, "2021-01-18", "2021-01-18", "FUTSWP", "internal_transfer", "event_contract_transfer", "event_contract", "-20.00", internal=True),
            record(11, "2021-01-19", "2021-01-19", "CIL", "corporate_action", "cash_in_lieu", "equity", "1.25"),
        ]
        result = build_cash_ledger(records, opening_cash=Decimal("10.00"), opening_date=date(2021, 1, 1))
        categories = {event["source_row_id"]: event["cash_category"] for event in result["events"]}
        self.assertEqual(categories["1"], "External contribution")
        self.assertEqual(categories["2"], "External withdrawal")
        self.assertEqual(categories["3"], "Equity trade")
        self.assertEqual(categories["4"], "Equity trade")
        self.assertEqual(categories["5"], "Option trade")
        self.assertEqual(categories["6"], "Option trade")
        self.assertEqual(categories["7"], "Dividend or interest income")
        self.assertEqual(categories["8"], "Fee")
        self.assertEqual(categories["9"], "Margin/financing cost")
        self.assertEqual(categories["10"], "Internal Robinhood transfer")
        self.assertEqual(categories["11"], "Corporate-action cash")
        self.assertEqual(result["daily"][0]["balance_confidence"], "verified")
        self.assertEqual(result["daily"][1]["balance_confidence"], "derived")
        self.assertTrue(result["summary"]["daily_reconciles_to_events"])

    def test_pending_settlement_is_excluded_from_settled_cash(self):
        records = [
            record(1, "2021-01-01", "2021-01-01", "ACH", "funding", "deposit", "cash", "100.00", external=True),
            record(2, "2021-01-05", "2021-01-08", "Buy", "trade", "buy", "equity", "-80.00"),
        ]
        result = build_cash_ledger(records, as_of=date(2021, 1, 6))
        self.assertEqual(len(result["pending_settlement"]), 1)
        self.assertEqual(result["pending_settlement"][0]["source_row_id"], "2")
        self.assertEqual(result["summary"]["event_net_cash_movement"], "100.00")
        self.assertEqual(result["daily"][-1]["closing_cash"], "100.00")

    def test_missing_opening_balance_reports_partial_cumulative_change(self):
        records = [
            record(1, "2021-01-03", "2021-01-05", "Buy", "trade", "buy", "equity", "-300.00"),
        ]
        result = build_cash_ledger(records)
        self.assertEqual(result["daily"][0]["opening_cash"], "0.00")
        self.assertEqual(result["daily"][0]["closing_cash"], "-300.00")
        self.assertEqual(result["daily"][0]["balance_confidence"], "partial")
        self.assertEqual(result["daily"][0]["cash_position_type"], "cumulative_change_from_zero")
        self.assertEqual(result["summary"]["balance_confidence"], "partial")

    def test_malformed_date_and_amount_enter_review(self):
        records = [
            record(1, "2021-01-01", "bad-date", "ACH", "funding", "deposit", "cash", "100.00", external=True),
            record(2, "2021-01-02", "2021-01-02", "ACH", "funding", "deposit", "cash", "not-money", external=True),
        ]
        result = build_cash_ledger(records)
        self.assertEqual(result["events"], [])
        self.assertEqual(result["review"]["review_count"], 2)
        reasons = {issue["source_row_id"]: issue["review_reason"] for issue in result["review"]["issues"]}
        self.assertIn("invalid_or_missing_settle_date", reasons["1"])
        self.assertIn("invalid_or_missing_amount", reasons["2"])

    def test_valid_source_review_rows_still_affect_cash(self):
        records = [
            record(1, "2021-01-01", "2021-01-03", "BTO", "option_trade", "buy_to_open", "option", "-50.00"),
        ]
        records[0]["review_status"] = "review"
        records[0]["review_reasons"] = "potential_identical_fill"
        result = build_cash_ledger(records)
        self.assertEqual(result["review"]["review_count"], 0)
        self.assertEqual(result["events"][0]["review_status"], "review")
        self.assertEqual(result["events"][0]["review_reason"], "potential_identical_fill")
        self.assertEqual(result["summary"]["event_net_cash_movement"], "-50.00")

    def test_daily_bucket_totals_reconcile_to_event_total(self):
        records = [
            record(1, "2021-01-01", "2021-01-01", "ACH", "funding", "deposit", "cash", "100.00", external=True),
            record(2, "2021-01-01", "2021-01-01", "Buy", "trade", "buy", "equity", "-25.00"),
            record(3, "2021-01-01", "2021-01-01", "CDIV", "income", "cash_dividend", "equity", "2.00"),
            record(4, "2021-01-01", "2021-01-01", "AFEE", "fee", "adr_fee", "equity", "-1.00"),
        ]
        result = build_cash_ledger(records)
        daily = result["daily"][0]
        self.assertEqual(daily["net_cash_movement"], "76.00")
        self.assertEqual(result["summary"]["event_net_cash_movement"], "76.00")
        self.assertTrue(result["summary"]["daily_reconciles_to_events"])

    def test_verified_opening_balance_starts_on_anchor_date_only(self):
        records = [
            record(1, "2021-12-01", "2021-12-01", "Buy", "trade", "buy", "equity", "-10.00"),
            record(2, "2021-12-02", "2021-12-02", "Buy", "trade", "buy", "equity", "-20.00"),
            record(3, "2021-12-03", "2021-12-03", "Sell", "trade", "sell", "equity", "5.00"),
        ]
        result = build_cash_ledger(
            records,
            opening_cash=Decimal("100.00"),
            opening_date=date(2021, 12, 2),
        )
        by_date = {row["date"]: row for row in result["daily"]}
        self.assertEqual(by_date["2021-12-01"]["net_cash_movement"], "-10.00")
        self.assertEqual(by_date["2021-12-01"]["opening_cash"], "")
        self.assertEqual(by_date["2021-12-01"]["closing_cash"], "")
        self.assertEqual(by_date["2021-12-01"]["balance_confidence"], "partial/unanchored")
        self.assertEqual(by_date["2021-12-01"]["cash_position_type"], "pre_anchor_cumulative_change")
        self.assertEqual(by_date["2021-12-02"]["opening_cash"], "100.00")
        self.assertEqual(by_date["2021-12-02"]["closing_cash"], "80.00")
        self.assertEqual(by_date["2021-12-02"]["balance_confidence"], "verified")
        self.assertEqual(by_date["2021-12-03"]["opening_cash"], "80.00")
        self.assertEqual(by_date["2021-12-03"]["closing_cash"], "85.00")
        self.assertEqual(result["summary"]["anchor_date"], "2021-12-02")
        self.assertEqual(result["summary"]["anchor_confidence"], "verified")
        self.assertEqual(result["summary"]["anchor_status"], "applied")
        self.assertIs(result["summary"]["anchor_applied"], True)
        self.assertTrue(result["summary"]["daily_reconciles_to_events"])

    def test_opening_cash_requires_opening_date(self):
        with self.assertRaisesRegex(ValueError, "opening_cash and opening_date"):
            build_cash_ledger([], opening_cash=Decimal("100.00"))

    def test_opening_date_requires_opening_cash(self):
        with self.assertRaisesRegex(ValueError, "opening_cash and opening_date"):
            build_cash_ledger([], opening_date=date(2021, 12, 1))

    def test_opening_date_before_first_ledger_event(self):
        records = [
            record(1, "2021-12-03", "2021-12-03", "Sell", "trade", "sell", "equity", "5.00"),
        ]
        result = build_cash_ledger(
            records,
            opening_cash=Decimal("100.00"),
            opening_date=date(2021, 12, 1),
        )
        by_date = {row["date"]: row for row in result["daily"]}
        self.assertEqual(by_date["2021-12-01"]["opening_cash"], "100.00")
        self.assertEqual(by_date["2021-12-01"]["closing_cash"], "100.00")
        self.assertEqual(by_date["2021-12-03"]["opening_cash"], "100.00")
        self.assertEqual(by_date["2021-12-03"]["closing_cash"], "105.00")

    def test_opening_date_after_final_ledger_event(self):
        records = [
            record(1, "2021-12-01", "2021-12-01", "Buy", "trade", "buy", "equity", "-10.00"),
        ]
        result = build_cash_ledger(
            records,
            opening_cash=Decimal("100.00"),
            opening_date=date(2021, 12, 3),
        )
        by_date = {row["date"]: row for row in result["daily"]}
        self.assertEqual(by_date["2021-12-01"]["opening_cash"], "")
        self.assertEqual(by_date["2021-12-01"]["closing_cash"], "")
        self.assertEqual(by_date["2021-12-01"]["net_cash_movement"], "-10.00")
        self.assertEqual(by_date["2021-12-03"]["opening_cash"], "100.00")
        self.assertEqual(by_date["2021-12-03"]["closing_cash"], "100.00")

    def test_future_anchor_after_as_of_is_not_applied(self):
        records = [
            record(1, "2020-12-01", "2020-12-01", "Buy", "trade", "buy", "equity", "-10.00"),
        ]
        result = build_cash_ledger(
            records,
            as_of=date(2020, 12, 31),
            opening_cash=Decimal("100.00"),
            opening_date=date(2021, 1, 5),
        )
        dates = [row["date"] for row in result["daily"]]
        self.assertNotIn("2021-01-05", dates)
        self.assertTrue(all(row["date"] <= "2020-12-31" for row in result["daily"]))
        self.assertTrue(all(row["balance_confidence"] == "partial" for row in result["daily"]))
        self.assertEqual(result["summary"]["anchor_status"], "future_unapplied")
        self.assertIs(result["summary"]["anchor_applied"], False)
        self.assertEqual(result["summary"]["ending_cash"], "-10.00")

    def test_opening_date_equal_to_as_of_is_applied(self):
        records = [
            record(1, "2020-12-31", "2020-12-31", "Sell", "trade", "sell", "equity", "5.00"),
        ]
        result = build_cash_ledger(
            records,
            as_of=date(2020, 12, 31),
            opening_cash=Decimal("100.00"),
            opening_date=date(2020, 12, 31),
        )
        self.assertEqual(result["daily"][0]["date"], "2020-12-31")
        self.assertEqual(result["daily"][0]["opening_cash"], "100.00")
        self.assertEqual(result["daily"][0]["closing_cash"], "105.00")
        self.assertEqual(result["summary"]["anchor_status"], "applied")
        self.assertIs(result["summary"]["anchor_applied"], True)

    def test_opening_date_before_as_of_is_applied(self):
        records = [
            record(1, "2020-12-15", "2020-12-15", "Sell", "trade", "sell", "equity", "5.00"),
        ]
        result = build_cash_ledger(
            records,
            as_of=date(2020, 12, 31),
            opening_cash=Decimal("100.00"),
            opening_date=date(2020, 12, 1),
        )
        self.assertTrue(all(row["date"] <= "2020-12-31" for row in result["daily"]))
        self.assertEqual(result["daily"][0]["date"], "2020-12-01")
        self.assertEqual(result["daily"][0]["opening_cash"], "100.00")
        self.assertEqual(result["summary"]["anchor_status"], "applied")
        self.assertIs(result["summary"]["anchor_applied"], True)

    def test_opening_date_after_final_event_before_as_of_creates_anchor_row(self):
        records = [
            record(1, "2020-12-01", "2020-12-01", "Buy", "trade", "buy", "equity", "-10.00"),
        ]
        result = build_cash_ledger(
            records,
            as_of=date(2020, 12, 31),
            opening_cash=Decimal("100.00"),
            opening_date=date(2020, 12, 15),
        )
        by_date = {row["date"]: row for row in result["daily"]}
        self.assertIn("2020-12-15", by_date)
        self.assertEqual(by_date["2020-12-15"]["opening_cash"], "100.00")
        self.assertEqual(by_date["2020-12-15"]["closing_cash"], "100.00")
        self.assertTrue(all(row["date"] <= "2020-12-31" for row in result["daily"]))

    def test_negative_verified_opening_balance(self):
        records = [
            record(1, "2021-12-02", "2021-12-02", "Sell", "trade", "sell", "equity", "5.00"),
        ]
        result = build_cash_ledger(
            records,
            opening_cash=Decimal("-25.00"),
            opening_date=date(2021, 12, 2),
        )
        self.assertEqual(result["daily"][0]["opening_cash"], "-25.00")
        self.assertEqual(result["daily"][0]["closing_cash"], "-20.00")
        self.assertTrue(result["summary"]["daily_reconciles_to_events"])


if __name__ == "__main__":
    unittest.main()
