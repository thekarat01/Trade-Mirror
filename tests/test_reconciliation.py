from datetime import date
from decimal import Decimal
import unittest

from trademirror.reconciliation import CashAnchor, ReconciliationAdjustment, reconcile_cash


class ReconciliationTests(unittest.TestCase):
    def test_cash_reconciliation_with_explicit_source_adjustment(self):
        records = [
            {"settle_date": "2020-12-03", "amount": "100.00"},
            {"settle_date": "2020-12-04", "amount": "-40.00"},
            {"settle_date": "2021-01-04", "amount": "25.00"},
        ]
        anchor = CashAnchor(
            label="Synthetic month",
            start_date=date(2020, 12, 1),
            end_date=date(2020, 12, 31),
            opening_cash=Decimal("10.00"),
            closing_cash=Decimal("65.00"),
        )
        adjustments = [
            ReconciliationAdjustment(
                label="Missing internal transfer",
                amount=Decimal("-5.00"),
                reason="Source export omitted a documented cross-account movement",
            )
        ]
        result = reconcile_cash(records, anchor, adjustments)
        self.assertIs(result["passed"], True)
        self.assertEqual(result["difference"], "0.00")
        self.assertEqual(result["settled_rows"], 2)
        self.assertEqual(result["invalid_row_count"], 0)
        self.assertEqual(result["review_reasons"], [])

    def test_malformed_settlement_date_does_not_crash_or_pass(self):
        records = [
            {"settle_date": "not-a-date", "amount": "10.00"},
        ]
        anchor = CashAnchor(
            label="Synthetic month",
            start_date=date(2020, 12, 1),
            end_date=date(2020, 12, 31),
            opening_cash=Decimal("0.00"),
            closing_cash=Decimal("0.00"),
        )
        result = reconcile_cash(records, anchor)
        self.assertIs(result["passed"], False)
        self.assertEqual(result["difference"], "0.00")
        self.assertEqual(result["invalid_row_count"], 1)
        self.assertEqual(result["review_reasons"], ["invalid_settle_date"])

    def test_malformed_amount_does_not_crash_or_pass(self):
        records = [
            {"settle_date": "2020-12-03", "amount": "not-money"},
        ]
        anchor = CashAnchor(
            label="Synthetic month",
            start_date=date(2020, 12, 1),
            end_date=date(2020, 12, 31),
            opening_cash=Decimal("0.00"),
            closing_cash=Decimal("0.00"),
        )
        result = reconcile_cash(records, anchor)
        self.assertIs(result["passed"], False)
        self.assertEqual(result["difference"], "0.00")
        self.assertEqual(result["invalid_row_count"], 1)
        self.assertEqual(result["review_reasons"], ["invalid_amount"])

    def test_out_of_range_malformed_amount_is_still_reviewed(self):
        records = [
            {"source_row_id": 99, "settle_date": "2021-01-04", "amount": "not-money"},
        ]
        anchor = CashAnchor(
            label="Synthetic month",
            start_date=date(2020, 12, 1),
            end_date=date(2020, 12, 31),
            opening_cash=Decimal("0.00"),
            closing_cash=Decimal("0.00"),
        )
        result = reconcile_cash(records, anchor)
        self.assertEqual(result["invalid_row_count"], 1)
        self.assertIs(result["passed"], False)
        self.assertEqual(result["review_issues"], [
            {"reason": "invalid_amount", "source_row_id": 99}
        ])
        self.assertEqual(result["imported_net_cash"], "0.00")


if __name__ == "__main__":
    unittest.main()
