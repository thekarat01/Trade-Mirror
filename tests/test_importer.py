from pathlib import Path
import csv
import tempfile
import unittest

from trademirror.importer import import_robinhood_csv, sanitize_description, write_canonical_csv


FIXTURE = Path(__file__).parent / "fixtures" / "robinhood_sample.csv"


class ImporterTests(unittest.TestCase):
    def test_importer_preserves_nonblank_rows_and_skips_blank_rows(self):
        records, report = import_robinhood_csv(FIXTURE)
        self.assertEqual(len(records), 6)
        self.assertEqual(report["canonical_records"], 6)
        self.assertEqual(report["blank_records_skipped"], 1)

    def test_amounts_cusip_option_and_privacy_parsing(self):
        records, report = import_robinhood_csv(FIXTURE)
        buy = records[0]
        option = records[2]
        transfer = records[4]
        self.assertEqual(buy["amount"], "-50.00")
        self.assertEqual(buy["cusip"], "123456789")
        self.assertEqual(option["option_underlying"], "ACME")
        self.assertEqual(option["option_expiration"], "2021-01-15")
        self.assertEqual(option["option_type"], "call")
        self.assertEqual(option["option_strike"], "7.50")
        self.assertTrue(transfer["description_sanitized"].endswith("XXXX"))
        self.assertIs(transfer["external_cash_flow"], True)
        self.assertEqual(report["privacy_sensitive_descriptions_sanitized"], 1)

    def test_identical_fills_are_flagged_not_deleted(self):
        records, report = import_robinhood_csv(FIXTURE)
        options = [record for record in records if record["transaction_code_raw"] == "BTO"]
        self.assertEqual(len(options), 2)
        self.assertEqual(
            options[0]["potential_duplicate_group"], options[1]["potential_duplicate_group"]
        )
        self.assertEqual(options[0]["duplicate_group_size"], 2)
        self.assertIn("potential_identical_fill", options[0]["review_reasons"])
        self.assertEqual(report["potential_duplicate_groups"], 1)

    def test_unknown_codes_and_quantity_suffixes_enter_review_queue(self):
        records, report = import_robinhood_csv(FIXTURE)
        unknown = records[-1]
        self.assertEqual(unknown["quantity_numeric"], "1")
        self.assertEqual(unknown["quantity_suffix"], "S")
        self.assertEqual(unknown["review_status"], "review")
        self.assertIn("unknown_transaction_code", unknown["review_reasons"])
        self.assertEqual(report["quantity_suffix_rows"], 1)

    def test_privacy_redaction_patterns(self):
        cases = [
            (
                "Instant bank transfer - account ending in 1234",
                "Instant bank transfer - account ending in XXXX",
            ),
            (
                "Bank account ending 9876 for holder demo@example.com",
                "Bank account ending XXXX for holder [REDACTED_EMAIL]",
            ),
            ("Tax ID 000-00-0000", "Tax ID [REDACTED_TAX_ID]"),
            (
                "Account Number: ABCD-1234-XYZ",
                "Account Number: [REDACTED]",
            ),
            ("Account No. 123456789012", "Account No. [REDACTED]"),
            ("Account # ZZ99-88-77", "Account # [REDACTED]"),
            ("Account Number: 123456789", "Account Number: [REDACTED]"),
            ("Account Number: 123 456 789", "Account Number: [REDACTED]"),
            ("Account Number: 123-456-789", "Account Number: [REDACTED]"),
            ("Account Number: ABC123456", "Account Number: [REDACTED]"),
            ("Account Number: ABC 123 456", "Account Number: [REDACTED]"),
            (
                "Account Number: 123456789 CUSIP: 644535106",
                "Account Number: [REDACTED] CUSIP: 644535106",
            ),
            (
                "Individual Account #: ABC123456 Security information follows",
                "Individual Account #: [REDACTED] Security information follows",
            ),
            (
                "Account Number: 123 456 789\nCUSIP: 644535106",
                "Account Number: [REDACTED]\nCUSIP: 644535106",
            ),
            (
                "Account Number: 123 456 789 01/15/2026",
                "Account Number: [REDACTED] 01/15/2026",
            ),
            (
                "Account #: ABC123456 2026-01-15",
                "Account #: [REDACTED] 2026-01-15",
            ),
            (
                "Individual Account #: 123-456-789 Statement Date: 01/15/2026",
                "Individual Account #: [REDACTED] Statement Date: 01/15/2026",
            ),
        ]
        for raw, expected in cases:
            with self.subTest(raw=raw):
                self.assertEqual(sanitize_description(raw), expected)

    def test_privacy_redaction_preserves_market_terms(self):
        descriptions = [
            "ACME 1/15/2021 Call $7.50 CUSIP: 123456789 quantity 1 amount $50.00",
            "Acme Corp\nCUSIP: 644535106",
            "CUSIP: 644535106",
        ]
        for raw in descriptions:
            with self.subTest(raw=raw):
                self.assertEqual(sanitize_description(raw), raw)

    def test_canonical_csv_is_sanitized_by_default(self):
        records, _ = import_robinhood_csv(FIXTURE)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "canonical.csv"
            write_canonical_csv(records, output)
            with output.open("r", newline="", encoding="utf-8") as stream:
                row = next(csv.DictReader(stream))
            self.assertNotIn("description_raw", row)
            self.assertNotIn("raw_row_json", row)
            self.assertIn("description_sanitized", row)

    def test_canonical_csv_include_raw_is_explicit_opt_in(self):
        records, _ = import_robinhood_csv(FIXTURE)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "canonical.csv"
            write_canonical_csv(records, output, include_raw=True)
            with output.open("r", newline="", encoding="utf-8") as stream:
                row = next(csv.DictReader(stream))
            self.assertIn("description_raw", row)
            self.assertIn("raw_row_json", row)


if __name__ == "__main__":
    unittest.main()
