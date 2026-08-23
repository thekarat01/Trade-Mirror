from datetime import date
import json
import unittest

from trademirror.equity_identity import (
    compare_statement_positions,
    is_valid_cusip,
    normalize_cusip,
    resolve_equity_anchors,
)
from trademirror.equity_realized_pnl import build_equity_realized_pnl
from trademirror.position_ledger import build_position_ledger


def equity_record(
    source_row_id,
    activity_date,
    event_type="buy",
    quantity="1",
    amount="-10",
    *,
    symbol="ACME",
    cusip="037833100",
):
    code = "Buy" if event_type == "buy" else "Sell"
    if event_type == "sell" and amount == "-10":
        amount = "10"
    return {
        "source_row_id": source_row_id,
        "activity_date": activity_date,
        "settle_date": activity_date,
        "transaction_code_raw": code,
        "transaction_family": "trade",
        "event_type": event_type,
        "asset_type": "equity",
        "quantity_numeric": quantity,
        "amount": amount,
        "instrument": symbol,
        "cusip": cusip,
        "review_status": "validated",
        "review_reasons": "",
    }


def anchor(anchor_date="2021-01-01", *, symbol="ACME", cusip="", quantity="5"):
    return {
        "anchor_date": anchor_date,
        "asset_type": "equity",
        "symbol": symbol,
        "cusip": cusip,
        "quantity": quantity,
    }


class EquityIdentityResolverTests(unittest.TestCase):
    def test_cusip_validation_accepts_valid_numeric_and_alphanumeric_values(self):
        self.assertEqual(normalize_cusip("037833100"), "037833100")
        self.assertEqual(normalize_cusip("38259p508"), "38259P508")
        self.assertTrue(is_valid_cusip("594918104"))

    def test_cusip_validation_rejects_malformed_placeholders_and_bad_checksums(self):
        for value in ("", "03783310", "03783310!", "037833101", "000000000", "999999999"):
            with self.subTest(value=value):
                self.assertEqual(normalize_cusip(value), "")
                self.assertFalse(is_valid_cusip(value))
    def test_direct_cusip_anchor_is_accepted_with_high_confidence(self):
        result = resolve_equity_anchors(
            [anchor(cusip=" 037833100 ")],
            [equity_record(1, "2021-01-02")],
            as_of=date(2021, 1, 31),
        )
        report = result["report"][0]
        self.assertEqual(report["status"], "direct_cusip_accepted")
        self.assertEqual(report["resolved_security_key"], "equity:037833100")
        self.assertEqual(report["confidence"], "high_cusip_direct")

    def test_symbol_only_anchor_uniquely_resolves_to_nearby_cusip(self):
        result = resolve_equity_anchors(
            [anchor(symbol=" acme ")],
            [equity_record(1, "2021-01-15", cusip="037833100")],
            as_of=date(2021, 1, 31),
        )
        self.assertEqual(result["anchors"][0]["cusip"], "037833100")
        self.assertEqual(result["report"][0]["status"], "unique_symbol_to_cusip_mapped")
        self.assertEqual(result["report"][0]["resolved_security_key"], "equity:037833100")

    def test_resolved_anchor_does_not_create_parallel_symbol_position(self):
        result = build_position_ledger(
            [equity_record(1, "2021-01-02", "buy", "1", "-10", cusip="037833100")],
            as_of=date(2021, 1, 31),
            anchors=[anchor(quantity="5")],
        )
        keys = [row["security_key"] for row in result["positions_as_of"]]
        self.assertEqual(keys, ["equity:037833100"])
        self.assertEqual(result["positions_as_of"][0]["trade_date_quantity"], "6")
        self.assertEqual(result["anchor_validation"]["anchors"][0]["status"], "unique_symbol_to_cusip_mapped")

    def test_resolved_anchor_inventory_reduces_oversell_and_records_unknown_basis(self):
        result = build_equity_realized_pnl(
            [equity_record(1, "2021-01-02", "sell", "2", "40", cusip="037833100")],
            as_of=date(2021, 1, 31),
            anchors=[anchor(quantity="5")],
        )
        self.assertEqual(result["summary"]["unmatched_quantity"], "0")
        self.assertEqual(result["summary"]["unknown_basis_quantity"], "2")
        self.assertEqual(result["summary"]["net_realized_pnl"], "0")
        self.assertEqual(result["matches"][0]["basis_status"], "unknown")
        self.assertEqual(result["matches"][0]["realized_pnl"], "")
        self.assertEqual(result["open_lots"][0]["remaining_quantity"], "3")
        reasons = "|".join(issue["review_reason"] for issue in result["review"]["issues"])
        self.assertIn("unknown_basis_closure", reasons)

    def test_ambiguous_symbol_with_multiple_cusips_remains_unresolved(self):
        result = build_position_ledger(
            [
                equity_record(1, "2021-01-02", cusip="037833100"),
                equity_record(2, "2021-01-03", cusip="594918104"),
            ],
            as_of=date(2021, 1, 31),
            anchors=[anchor(quantity="5")],
        )
        status = result["anchor_validation"]["anchors"][0]["status"]
        self.assertEqual(status, "unresolved_ambiguous_candidates")
        keys = sorted(row["security_key"] for row in result["positions_as_of"])
        self.assertNotIn("equity-symbol:ACME", keys)
        self.assertEqual(result["summary"]["anchor_count"], 0)
        review = json.dumps(result["review"], sort_keys=True)
        self.assertIn("multiple_cusips_for_symbol_within_mapping_window", review)

    def test_no_candidate_inside_mapping_window_remains_unresolved(self):
        result = resolve_equity_anchors(
            [anchor()],
            [equity_record(1, "2021-04-15", cusip="037833100")],
            as_of=date(2021, 4, 30),
        )
        self.assertEqual(result["report"][0]["status"], "unresolved_no_candidate")

    def test_candidate_outside_default_90_day_window_is_not_used(self):
        result = resolve_equity_anchors(
            [anchor("2021-01-01")],
            [equity_record(1, "2021-04-02", cusip="037833100")],
            as_of=date(2021, 4, 30),
        )
        self.assertEqual(result["report"][0]["status"], "unresolved_no_candidate")
        self.assertEqual(result["anchors"][0].get("cusip", ""), "")

    def test_symbol_only_retained_when_canonical_records_also_lack_cusip(self):
        result = resolve_equity_anchors(
            [anchor()],
            [equity_record(1, "2021-01-02", cusip="")],
            as_of=date(2021, 1, 31),
        )
        self.assertEqual(result["report"][0]["status"], "symbol_only_retained_canonical_lacks_cusip")
        self.assertEqual(result["anchors"][0].get("cusip", ""), "")

    def test_future_anchor_remains_unapplied(self):
        result = build_position_ledger(
            [equity_record(1, "2021-01-02", "buy", "1", "-10")],
            as_of=date(2021, 1, 31),
            anchors=[anchor("2021-02-01", quantity="5")],
        )
        self.assertEqual(result["summary"]["anchor_count"], 0)
        self.assertEqual(result["summary"]["future_anchor_count"], 0)
        self.assertEqual(result["positions_as_of"][0]["trade_date_quantity"], "1")
        self.assertEqual(result["anchor_validation"]["anchors"][0]["status"], "rejected_future_dated")

    def test_position_and_equity_pnl_use_identical_resolution_report(self):
        records = [equity_record(1, "2021-01-02", "sell", "1", "20")]
        anchors = [anchor(quantity="2")]
        positions = build_position_ledger(records, as_of=date(2021, 1, 31), anchors=anchors)
        pnl = build_equity_realized_pnl(records, as_of=date(2021, 1, 31), anchors=anchors)
        self.assertEqual(
            positions["anchor_validation"]["anchors"][0]["resolved_security_key"],
            pnl["anchor_validation"]["anchors"][0]["resolved_security_key"],
        )
        self.assertEqual(
            positions["anchor_validation"]["anchors"][0]["status"],
            pnl["anchor_validation"]["anchors"][0]["status"],
        )

    def test_invalid_direct_cusip_anchor_fails_closed_without_raw_value(self):
        result = build_position_ledger(
            [equity_record(1, "2021-01-02", "buy", "1", "-10", cusip="037833100")],
            as_of=date(2021, 1, 31),
            anchors=[anchor(cusip="bad-cusip", quantity="5")],
        )
        report = result["anchor_validation"]["anchors"][0]
        self.assertEqual(report["status"], "rejected_malformed")
        self.assertEqual(report["resolution_reason"], "invalid_anchor_cusip")
        self.assertEqual(report["original_cusip"], "")
        self.assertNotIn("bad-cusip", json.dumps(result, sort_keys=True))
        self.assertEqual(result["positions_as_of"][0]["security_key"], "equity:037833100")
        self.assertEqual(result["summary"]["anchor_count"], 0)

    def test_zero_accepted_anchors_produces_zero_anchored_opening_quantity(self):
        result = build_equity_realized_pnl(
            [equity_record(1, "2021-01-02", "sell", "2", "40", cusip="037833100")],
            as_of=date(2021, 1, 31),
            anchors=[anchor(symbol="MISSING", quantity="5")],
        )
        self.assertEqual(result["anchor_validation"]["anchors"][0]["status"], "unresolved_no_candidate")
        self.assertEqual(result["summary"]["anchor_count"], 0)
        self.assertEqual(result["summary"]["unknown_basis_quantity"], "0")
        self.assertEqual(result["summary"]["unmatched_quantity"], "2")
        self.assertEqual(result["open_lots"], [])

    def test_unresolved_symbol_anchor_does_not_seed_inventory(self):
        result = build_position_ledger(
            [equity_record(1, "2021-04-15", "buy", "1", "-10", cusip="037833100")],
            as_of=date(2021, 4, 30),
            anchors=[anchor("2021-01-01", quantity="5")],
        )
        self.assertEqual(result["anchor_validation"]["anchors"][0]["status"], "unresolved_no_candidate")
        self.assertEqual(result["summary"]["anchor_count"], 0)
        self.assertEqual(result["positions_as_of"][0]["trade_date_quantity"], "1")
        self.assertIn("no_symbol_records_in_mapping_window", json.dumps(result["review"], sort_keys=True))

    def test_invalid_cusip_anchor_does_not_seed_pnl_inventory(self):
        result = build_equity_realized_pnl(
            [equity_record(1, "2021-01-02", "sell", "2", "40", cusip="037833100")],
            as_of=date(2021, 1, 31),
            anchors=[anchor(cusip="037833101", quantity="5")],
        )
        self.assertEqual(result["anchor_validation"]["anchors"][0]["status"], "rejected_malformed")
        self.assertEqual(result["summary"]["anchor_count"], 0)
        self.assertEqual(result["summary"]["unknown_basis_quantity"], "0")
        self.assertEqual(result["summary"]["unmatched_quantity"], "2")

    def test_ambiguous_anchor_does_not_seed_pnl_inventory(self):
        result = build_equity_realized_pnl(
            [
                equity_record(1, "2021-01-02", "sell", "2", "40", cusip="037833100"),
                equity_record(2, "2021-01-03", "buy", "1", "-10", cusip="594918104"),
            ],
            as_of=date(2021, 1, 31),
            anchors=[anchor(quantity="5")],
        )
        self.assertEqual(result["anchor_validation"]["anchors"][0]["status"], "unresolved_ambiguous_candidates")
        self.assertEqual(result["summary"]["anchor_count"], 0)
        self.assertEqual(result["summary"]["unknown_basis_quantity"], "0")
        self.assertEqual(result["summary"]["unmatched_quantity"], "2")

    def test_valid_direct_cusip_anchor_seeds_inventory(self):
        result = build_position_ledger(
            [],
            as_of=date(2021, 1, 31),
            anchors=[anchor(cusip="037833100", quantity="5")],
        )
        self.assertEqual(result["summary"]["anchor_count"], 1)
        self.assertEqual(result["positions_as_of"][0]["security_key"], "equity:037833100")
        self.assertEqual(result["positions_as_of"][0]["trade_date_quantity"], "5")

    def test_accepted_anchor_quantity_equals_downstream_anchored_opening_quantity(self):
        anchors = [anchor(quantity="5")]
        records = [equity_record(1, "2021-01-02", "sell", "2", "40", cusip="037833100")]
        positions = build_position_ledger(records, as_of=date(2021, 1, 31), anchors=anchors)
        pnl = build_equity_realized_pnl(records, as_of=date(2021, 1, 31), anchors=anchors)
        self.assertEqual(positions["summary"]["anchor_count"], 1)
        self.assertEqual(pnl["summary"]["anchor_count"], 1)
        self.assertEqual(pnl["summary"]["unknown_basis_quantity"], "2")
        self.assertEqual(pnl["open_lots"][0]["remaining_quantity"], "3")

    def test_invalid_transaction_cusip_is_not_mapping_candidate(self):
        result = resolve_equity_anchors(
            [anchor()],
            [equity_record(1, "2021-01-15", cusip="037833101")],
            as_of=date(2021, 1, 31),
        )
        self.assertEqual(result["report"][0]["status"], "symbol_only_retained_canonical_lacks_cusip")
        self.assertEqual(result["anchors"][0].get("cusip", ""), "")

    def test_multiple_candidates_ignores_invalid_candidate_and_maps_unique_valid_one(self):
        result = resolve_equity_anchors(
            [anchor()],
            [
                equity_record(1, "2021-01-15", cusip="037833100"),
                equity_record(2, "2021-01-16", cusip="037833101"),
            ],
            as_of=date(2021, 1, 31),
        )
        self.assertEqual(result["report"][0]["status"], "unique_symbol_to_cusip_mapped")
        self.assertEqual(result["anchors"][0]["cusip"], "037833100")

    def test_no_anchor_backward_compatibility(self):
        result = build_position_ledger([
            equity_record(1, "2021-01-01", "buy", "2", "-20", cusip="123456789"),
        ])
        self.assertEqual(result["anchor_validation"], {"report_count": 0, "anchors": []})
        self.assertEqual(result["positions_as_of"][0]["security_key"], "equity:123456789")
    def test_statement_comparison_matches_through_resolved_identity(self):
        comparison = compare_statement_positions(
            [{"symbol": "ACME", "quantity": "5"}],
            [{"security_key": "equity:037833100", "trade_date_quantity": "5"}],
            [equity_record(1, "2021-01-15", cusip="037833100")],
            statement_date=date(2021, 1, 1),
        )
        self.assertEqual(comparison["matched_count"], 1)
        self.assertEqual(comparison["missing_count"], 0)
        self.assertEqual(comparison["extra_count"], 0)
        self.assertEqual(comparison["quantity_mismatch_count"], 0)

    def test_anchor_validation_report_is_privacy_safe_structural_metadata(self):
        result = resolve_equity_anchors(
            [anchor()],
            [
                {
                    **equity_record(1, "2021-01-02", cusip="037833100"),
                    "description_raw": "Account Number: 123456789 private raw description",
                    "raw_row_json": "{private}",
                }
            ],
            as_of=date(2021, 1, 31),
        )
        rendered = json.dumps(result["report"], sort_keys=True)
        self.assertNotIn("description_raw", rendered)
        self.assertNotIn("raw_row_json", rendered)
        self.assertNotIn("Account Number", rendered)
        self.assertNotIn("private raw", rendered)


if __name__ == "__main__":
    unittest.main()
