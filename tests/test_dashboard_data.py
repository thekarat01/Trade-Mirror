from __future__ import annotations

import csv
import inspect
import importlib
import json
import os
import sys
import ast
from decimal import Decimal
from datetime import date
from pathlib import Path
import tempfile
import types
import unittest

from dashboard.data_loader import (
    BEHAVIORAL_CSV_SCHEMAS,
    BEHAVIORAL_DEMO_DATA_DIR,
    CSV_SCHEMAS,
    DEMO_DATA_DIR,
    annual_realized_chart_rows,
    attention_display_rows,
    DashboardValidationError,
    annual_realized_rows,
    review_display_rows,
    csv_rows,
    decimal_summary,
    load_dashboard_data,
    load_validated_dashboard_data,
    overview_metrics,
    technical_review_rows,
)
from dashboard.formatters import format_currency, format_percent, format_quantity
from dashboard.patterns_model import PatternValidationError, build_patterns_view_model
from dashboard.pages.cash_positions import (
    cash_balance_chart,
    cash_balance_chart_rows,
    cash_balance_y_domain,
    cash_movement_chart,
    cash_history_chart_rows,
    cash_movement_chart_rows,
    cash_summary_metrics,
    render as render_cash_positions,
)
from dashboard.pages import ask_trademirror, cash_positions, data_quality, my_patterns, overview, realized_pnl
from dashboard.pages.common import page_header, safe_chart, safe_dataframe, safe_structured_write


class DashboardDataTests(unittest.TestCase):
    def test_demo_data_loads_and_totals_reconcile_to_summaries(self):
        data = load_dashboard_data(DEMO_DATA_DIR)
        self.assertEqual(data.errors, ())
        metrics = overview_metrics(data)
        equity = decimal_summary(data, "realized_pnl/equity_realized_summary.json", "net_realized_pnl")
        options = decimal_summary(data, "option_realized_pnl/option_realized_summary.json", "net_realized_pnl")
        self.assertEqual(metrics["included_net_realized_pnl"], equity + options)
        self.assertEqual(metrics["equity_realized_pnl"], equity)
        self.assertEqual(metrics["option_realized_pnl"], options)
        annual = annual_realized_rows(data)
        self.assertEqual(sum(row["equity"] for row in annual), equity)
        self.assertEqual(sum(row["options"] for row in annual), options)

    def test_demo_annual_chart_values_match_summary_values(self):
        data = load_dashboard_data(DEMO_DATA_DIR)
        equity = data.json_files["realized_pnl/equity_realized_summary.json"].data or {}
        options = data.json_files["option_realized_pnl/option_realized_summary.json"].data or {}
        equity_by_year = equity.get("realized_pnl_by_year", {})
        option_by_year = options.get("realized_pnl_by_year", {})
        rows = annual_realized_chart_rows(data)
        self.assertEqual(len(rows), 3)
        for row in rows:
            year = row["Year"]
            self.assertEqual(row["Equity"], Decimal(str(equity_by_year.get(year, "0"))))
            self.assertEqual(row["Options"], Decimal(str(option_by_year.get(year, "0"))))

    def test_demo_annual_chart_has_positive_and_negative_yearly_outcomes(self):
        rows = annual_realized_chart_rows(load_dashboard_data(DEMO_DATA_DIR))
        self.assertEqual([row["Year"] for row in rows], ["2019", "2020", "2021"])
        self.assertTrue(any(row["Equity"] > 0 for row in rows))
        self.assertTrue(any(row["Equity"] < 0 for row in rows))
        self.assertTrue(any(row["Options"] > 0 for row in rows))
        self.assertTrue(any(row["Options"] < 0 for row in rows))

    def test_missing_file_is_unavailable_without_fake_zero(self):
        with tempfile.TemporaryDirectory() as directory:
            data = load_dashboard_data(directory)
            daily = data.csv_files["cash_ledger/cash_ledger_daily.csv"]
            self.assertFalse(daily.available)
            self.assertEqual(daily.rows, ())
            self.assertIn("unavailable", daily.error)

    def test_missing_column_is_reported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cash_ledger"
            path.mkdir()
            with (path / "cash_ledger_daily.csv").open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(["date"])
                writer.writerow(["2021-01-01"])
            data = load_dashboard_data(directory)
            loaded = data.csv_files["cash_ledger/cash_ledger_daily.csv"]
            self.assertFalse(loaded.available)
            self.assertIn("Missing columns", loaded.error)

    def test_empty_file_loads_as_empty_rows_when_schema_is_valid(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cash_ledger"
            path.mkdir()
            with (path / "cash_ledger_daily.csv").open("w", newline="", encoding="utf-8") as stream:
                writer = csv.writer(stream)
                writer.writerow(CSV_SCHEMAS["cash_ledger/cash_ledger_daily.csv"])
            data = load_dashboard_data(directory)
            loaded = data.csv_files["cash_ledger/cash_ledger_daily.csv"]
            self.assertTrue(loaded.available)
            self.assertEqual(loaded.rows, ())

    def test_csv_prohibited_headers_are_rejected_after_normalization(self):
        cases = [
            "description_raw",
            " Description_Raw ",
            "DESCRIPTION_RAW",
            "\ufeffdescription_raw",
            " Raw_Row_JSON ",
        ]
        for header in cases:
            with self.subTest(header=header), tempfile.TemporaryDirectory() as directory:
                secret = "private-account-9999"
                self._write_csv_with_columns(
                    directory,
                    "cash_ledger/cash_ledger_daily.csv",
                    [*CSV_SCHEMAS["cash_ledger/cash_ledger_daily.csv"], header],
                    {header: secret},
                )

                data = load_dashboard_data(directory)
                loaded = data.csv_files["cash_ledger/cash_ledger_daily.csv"]

                self.assertFalse(loaded.available)
                self.assertEqual(loaded.rows, ())
                self.assertIn("Prohibited raw/private fields are present.", loaded.error)
                self.assertNotIn(secret, loaded.error)
                self.assertNotIn(header.strip(), loaded.error)
                self.assertNotIn(secret, "\n".join(data.errors))

    def test_csv_duplicate_normalized_headers_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = "private-account-8888"
            columns = [
                *CSV_SCHEMAS["cash_ledger/cash_ledger_daily.csv"],
                " extra_private ",
                "EXTRA_PRIVATE",
            ]
            self._write_csv_with_columns(
                directory,
                "cash_ledger/cash_ledger_daily.csv",
                columns,
                {" extra_private ": secret, "EXTRA_PRIVATE": "another-secret"},
            )

            data = load_dashboard_data(directory)
            loaded = data.csv_files["cash_ledger/cash_ledger_daily.csv"]

            self.assertFalse(loaded.available)
            self.assertEqual(loaded.rows, ())
            self.assertIn("Duplicate or ambiguous columns are present.", loaded.error)
            self.assertNotIn(secret, loaded.error)
            self.assertNotIn("extra_private", loaded.error.casefold())
            self.assertNotIn(secret, "\n".join(data.errors))

    def test_csv_rows_are_limited_to_declared_schema_columns(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = "Account Number: 123456789"
            self._write_csv_with_columns(
                directory,
                "position_ledger/positions_as_of.csv",
                [*CSV_SCHEMAS["position_ledger/positions_as_of.csv"], "broker_private_note"],
                {
                    "security_key": "equity:111111111",
                    "asset_type": "equity",
                    "trade_date_quantity": "1",
                    "settled_quantity": "1",
                    "trade_date_long_quantity": "1",
                    "trade_date_short_quantity": "0",
                    "settled_long_quantity": "1",
                    "settled_short_quantity": "0",
                    "confidence": "deterministic",
                    "review_status": "validated",
                    "broker_private_note": secret,
                },
            )

            data = load_dashboard_data(directory)
            loaded = data.csv_files["position_ledger/positions_as_of.csv"]

            self.assertTrue(loaded.available)
            self.assertEqual(set(loaded.rows[0]), set(CSV_SCHEMAS["position_ledger/positions_as_of.csv"]))
            self.assertNotIn("broker_private_note", loaded.rows[0])
            self.assertNotIn(secret, str(loaded.rows))
            self.assertNotIn(secret, "\n".join(data.errors))

            fake_streamlit = _FakeCashStreamlit()
            render_cash_positions(fake_streamlit, data)
            rendered_tables = "\n".join(str(table) for table in fake_streamlit.dataframes)
            self.assertNotIn("broker_private_note", rendered_tables)
            self.assertNotIn(secret, rendered_tables)

    def test_malformed_decimal_is_not_silently_zeroed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "realized_pnl"
            path.mkdir()
            (path / "equity_realized_summary.json").write_text(
                json.dumps({"net_realized_pnl": "not-money"}),
                encoding="utf-8",
            )
            with self.assertRaises(DashboardValidationError) as context:
                load_validated_dashboard_data(directory)
            issue = self._issue_for(context.exception, "net_realized_pnl")
            self.assertEqual(issue.filename, "realized_pnl/equity_realized_summary.json")
            self.assertEqual(issue.field, "net_realized_pnl")
            self.assertEqual(issue.location, "summary")
            self.assertEqual(issue.reason, "malformed value")

    def test_malformed_quantity_in_csv_is_caught_before_rendering(self):
        with tempfile.TemporaryDirectory() as directory:
            self._write_csv(
                directory,
                "position_ledger/positions_as_of.csv",
                {
                    "security_key": "equity:111111111",
                    "asset_type": "equity",
                    "trade_date_quantity": "not-quantity",
                    "settled_quantity": "1",
                    "trade_date_long_quantity": "0",
                    "trade_date_short_quantity": "0",
                    "settled_long_quantity": "0",
                    "settled_short_quantity": "0",
                    "confidence": "deterministic",
                    "review_status": "validated",
                },
            )
            with self.assertRaises(DashboardValidationError) as context:
                load_validated_dashboard_data(directory)
            issue = context.exception.issues[0]
            self.assertEqual(issue.filename, "position_ledger/positions_as_of.csv")
            self.assertEqual(issue.field, "trade_date_quantity")
            self.assertEqual(issue.location, "row 2")
            self.assertEqual(issue.reason, "malformed value")

    def test_nan_and_infinity_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "realized_pnl"
            path.mkdir()
            (path / "equity_realized_summary.json").write_text(
                '{"net_realized_pnl": NaN, "realized_gain": Infinity}',
                encoding="utf-8",
            )
            with self.assertRaises(DashboardValidationError) as context:
                load_validated_dashboard_data(directory)
            reasons = {issue.field: issue.reason for issue in context.exception.issues}
            self.assertEqual(reasons["net_realized_pnl"], "nonfinite value")
            self.assertEqual(reasons["realized_gain"], "nonfinite value")

    def test_missing_required_numeric_field_is_not_zeroed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "realized_pnl"
            path.mkdir()
            (path / "equity_realized_summary.json").write_text(
                json.dumps({"net_realized_pnl": ""}),
                encoding="utf-8",
            )
            with self.assertRaises(DashboardValidationError) as context:
                load_validated_dashboard_data(directory)
            issue = self._issue_for(context.exception, "net_realized_pnl")
            self.assertEqual(issue.field, "net_realized_pnl")
            self.assertEqual(issue.reason, "required value missing")

    def test_missing_optional_numeric_fields_remain_unavailable(self):
        with tempfile.TemporaryDirectory() as directory:
            self._write_csv(
                directory,
                "cash_ledger/cash_ledger_daily.csv",
                {
                    "date": "2021-01-01",
                    "opening_cash": "",
                    "external_inflows": "0",
                    "external_outflows": "0",
                    "trading_cash_flow": "0",
                    "income": "0",
                    "fees": "0",
                    "financing_costs": "0",
                    "internal_transfers": "0",
                    "other_cash_flow": "0",
                    "net_cash_movement": "0",
                    "closing_cash": "",
                    "balance_confidence": "partial",
                    "cash_position_type": "cumulative_change_from_zero",
                },
            )
            data = load_validated_dashboard_data(directory)
            row = csv_rows(data, "cash_ledger/cash_ledger_daily.csv")[0]
            self.assertEqual(row["opening_cash"], "")
            self.assertEqual(row["closing_cash"], "")

    def test_cash_chart_preserves_real_zero_balance(self):
        rows = cash_balance_chart_rows([
            {"date": "2021-01-01", "closing_cash": "0", "net_cash_movement": "0"},
        ])
        self.assertEqual(rows[0]["Settled cash balance"], Decimal("0"))

    def test_cash_chart_preserves_missing_balance_between_valid_balances(self):
        rows = cash_balance_chart_rows([
            {"date": "2021-01-01", "closing_cash": "10", "net_cash_movement": "10"},
            {"date": "2021-01-02", "closing_cash": "", "net_cash_movement": "5"},
            {"date": "2021-01-03", "closing_cash": "15", "net_cash_movement": "0"},
        ])
        self.assertEqual(rows[0]["Settled cash balance"], Decimal("10"))
        self.assertIsNone(rows[1]["Settled cash balance"])
        self.assertEqual(rows[2]["Settled cash balance"], Decimal("15"))

    def test_cash_chart_preserves_missing_first_and_last_balances(self):
        rows = cash_balance_chart_rows([
            {"date": "2021-01-01", "closing_cash": "", "net_cash_movement": "1"},
            {"date": "2021-01-02", "closing_cash": "7", "net_cash_movement": "6"},
            {"date": "2021-01-03", "closing_cash": "", "net_cash_movement": "-1"},
        ])
        self.assertIsNone(rows[0]["Settled cash balance"])
        self.assertEqual(rows[1]["Settled cash balance"], Decimal("7"))
        self.assertIsNone(rows[2]["Settled cash balance"])

    def test_cash_chart_all_missing_balances_shows_unavailable_message(self):
        data = _DashboardDataStub([
            {"date": "2021-01-01", "closing_cash": "", "net_cash_movement": "1"},
            {"date": "2021-01-02", "closing_cash": "", "net_cash_movement": "-1"},
        ])
        fake_streamlit = _FakeCashStreamlit()
        render_cash_positions(fake_streamlit, data)
        self.assertEqual(fake_streamlit.line_charts, [])
        self.assertIn("Cash-balance history is unavailable for this data.", fake_streamlit.infos)

    def test_cash_chart_does_not_default_zero_or_forward_fill(self):
        rows = cash_balance_chart_rows([
            {"date": "2021-01-01", "closing_cash": "12", "net_cash_movement": "12"},
            {"date": "2021-01-02", "closing_cash": "", "net_cash_movement": "3"},
            {"date": "2021-01-03", "closing_cash": "0", "net_cash_movement": "-15"},
        ])
        chart_rows = cash_positions_decimal_chart_rows(rows, ("Settled cash balance",))
        self.assertEqual(chart_rows[0]["Settled cash balance"], 12.0)
        self.assertIsNone(chart_rows[1]["Settled cash balance"])
        self.assertEqual(chart_rows[2]["Settled cash balance"], 0.0)

    def test_cash_chart_datasets_are_separate_and_use_datetime_dates(self):
        daily = [
            {"date": "2021-01-01", "closing_cash": "12", "net_cash_movement": "12"},
            {"date": "2021-01-02", "closing_cash": "", "net_cash_movement": "-3"},
        ]
        balance = cash_balance_chart_rows(daily)
        movement = cash_movement_chart_rows(daily)
        self.assertEqual(set(balance[0]), {"Date", "Settled cash balance"})
        self.assertEqual(set(movement[0]), {"Date", "Daily cash movement"})
        self.assertEqual(balance[0]["Date"].isoformat(), "2021-01-01")
        self.assertEqual(movement[1]["Date"].isoformat(), "2021-01-02")
        self.assertIsNone(balance[1]["Settled cash balance"])
        self.assertEqual(movement[1]["Daily cash movement"], Decimal("-3"))

    def test_cash_balance_chart_uses_data_driven_currency_axis_and_tooltip(self):
        rows = cash_balance_chart_rows([
            {"date": "2021-01-01", "closing_cash": "19000", "net_cash_movement": "0"},
            {"date": "2021-01-02", "closing_cash": "20000", "net_cash_movement": "1000"},
        ])
        domain = cash_balance_y_domain(rows)
        self.assertIsNotNone(domain)
        self.assertGreater(domain[0], 0)
        spec = cash_balance_chart(rows).to_dict()
        self.assertFalse(spec["encoding"]["y"]["scale"]["zero"])
        self.assertEqual(spec["encoding"]["y"]["title"], "Settled cash balance")
        self.assertEqual(spec["encoding"]["y"]["axis"]["format"], "$,.0f")
        self.assertEqual(spec["encoding"]["tooltip"][1]["format"], "$,.2f")

    def test_cash_chart_rows_exclude_after_as_of_and_include_exact_as_of(self):
        daily = [
            {"date": "2021-12-30", "closing_cash": "19000", "net_cash_movement": "10"},
            {"date": "2021-12-31", "closing_cash": "19005", "net_cash_movement": "5"},
            {"date": "2022-01-01", "closing_cash": "99999", "net_cash_movement": "999"},
        ]
        as_of = date(2021, 12, 31)

        balance = cash_balance_chart_rows(daily, as_of=as_of)
        movement = cash_movement_chart_rows(daily, as_of=as_of)

        self.assertEqual([row["Date"].isoformat() for row in balance], ["2021-12-30", "2021-12-31"])
        self.assertEqual([row["Date"].isoformat() for row in movement], ["2021-12-30", "2021-12-31"])
        self.assertEqual(balance[-1]["Settled cash balance"], Decimal("19005"))
        self.assertEqual(movement[-1]["Daily cash movement"], Decimal("5"))

    def test_cash_movement_chart_domain_ends_at_as_of(self):
        as_of = date(2021, 12, 31)
        rows = cash_movement_chart_rows([
            {"date": "2021-12-30", "closing_cash": "19000", "net_cash_movement": "10"},
            {"date": "2021-12-31", "closing_cash": "19005", "net_cash_movement": "5"},
            {"date": "2022-01-01", "closing_cash": "99999", "net_cash_movement": "999"},
        ], as_of=as_of)

        spec = cash_movement_chart(rows, as_of=as_of).to_dict()

        scale = spec["encoding"]["x"]["scale"]
        self.assertEqual(scale["domain"], ["2021-12-30", "2021-12-31"])
        self.assertFalse(scale["nice"])
        self.assertEqual([row["Date"] for row in spec["data"]["values"]], ["2021-12-30", "2021-12-31"])
        self.assertEqual([row["Daily cash movement"] for row in spec["data"]["values"]], [10.0, 5.0])

    def test_cash_chart_as_of_filter_preserves_missing_optional_balance(self):
        rows = cash_balance_chart_rows([
            {"date": "2021-12-30", "closing_cash": "0", "net_cash_movement": "0"},
            {"date": "2021-12-31", "closing_cash": "", "net_cash_movement": "3"},
            {"date": "2022-01-01", "closing_cash": "", "net_cash_movement": "4"},
        ], as_of=date(2021, 12, 31))

        self.assertEqual(rows[0]["Settled cash balance"], Decimal("0"))
        self.assertIsNone(rows[1]["Settled cash balance"])
        self.assertEqual([row["Date"].isoformat() for row in rows], ["2021-12-30", "2021-12-31"])

    def test_cash_chart_as_of_filter_excludes_malformed_dates(self):
        daily = [
            {"date": "2021-12-30", "closing_cash": "0", "net_cash_movement": "0"},
            {"date": "not-a-date", "closing_cash": "10", "net_cash_movement": "10"},
            {"date": "2021-12-31", "closing_cash": "1", "net_cash_movement": "1"},
        ]

        rows = cash_movement_chart_rows(daily, as_of=date(2021, 12, 31))

        self.assertEqual([row["Date"].isoformat() for row in rows], ["2021-12-30", "2021-12-31"])

    def test_demo_cash_movement_rendered_dataset_is_bounded_and_reconciles(self):
        data = load_dashboard_data(DEMO_DATA_DIR)
        summary = data.json_files["cash_ledger/cash_ledger_summary.json"].data or {}
        as_of = date.fromisoformat(str(summary["as_of"]))
        rows = cash_movement_chart_rows(
            list(csv_rows(data, "cash_ledger/cash_ledger_daily.csv")),
            as_of=as_of,
        )

        spec = cash_movement_chart(rows, as_of=as_of).to_dict()
        values = spec["data"]["values"]

        self.assertTrue(values)
        self.assertTrue(any(row["Daily cash movement"] == 10000.0 for row in values))
        self.assertTrue(any(row["Daily cash movement"] < 0 for row in values))
        self.assertEqual(max(row["Date"] for row in values), "2021-12-31")
        self.assertFalse(spec["encoding"]["x"]["scale"]["nice"])
        self.assertEqual(spec["encoding"]["x"]["scale"]["domain"][1], "2021-12-31")
        rendered_total = sum((Decimal(str(row["Daily cash movement"])) for row in values), Decimal("0"))
        self.assertEqual(rendered_total, Decimal(summary["daily_net_cash_movement"]))

    def test_demo_cash_history_chart_rows_remain_unchanged(self):
        data = load_dashboard_data(DEMO_DATA_DIR)
        daily = list(csv_rows(data, "cash_ledger/cash_ledger_daily.csv"))
        rows = cash_balance_chart_rows(daily)
        self.assertEqual(len(rows), len(daily))
        self.assertEqual(rows[0]["Settled cash balance"], Decimal(daily[0]["closing_cash"]))
        self.assertTrue(all(row["Settled cash balance"] is not None for row in rows))

    def test_demo_cash_events_are_nonzero_and_reconcile_to_summary(self):
        data = load_dashboard_data(DEMO_DATA_DIR)
        events = list(csv_rows(data, "cash_ledger/cash_ledger_events.csv"))
        daily = list(csv_rows(data, "cash_ledger/cash_ledger_daily.csv"))
        summary = data.json_files["cash_ledger/cash_ledger_summary.json"].data or {}
        metrics = cash_summary_metrics(events)
        self.assertGreater(metrics["External inflows"], Decimal("0"))
        self.assertLess(metrics["External outflows"], Decimal("0"))
        self.assertNotEqual(metrics["Trading cash flow"], Decimal("0"))
        self.assertGreater(metrics["Income"], Decimal("0"))
        event_total = sum((Decimal(row["signed_amount"]) for row in events), Decimal("0"))
        daily_total = sum((Decimal(row["net_cash_movement"]) for row in daily), Decimal("0"))
        self.assertEqual(event_total, Decimal(summary["event_net_cash_movement"]))
        self.assertEqual(daily_total, Decimal(summary["daily_net_cash_movement"]))

    def test_validation_messages_are_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            raw_value = "secret-account-123"
            path = Path(directory) / "realized_pnl"
            path.mkdir()
            (path / "equity_realized_summary.json").write_text(
                json.dumps({"net_realized_pnl": raw_value}),
                encoding="utf-8",
            )
            with self.assertRaises(DashboardValidationError) as context:
                load_validated_dashboard_data(directory)
            message = context.exception.messages[0]
            self.assertIn("realized_pnl/equity_realized_summary.json", message)
            self.assertNotIn(str(directory), message)
            self.assertNotIn(raw_value, message)

    def test_app_entrypoint_handles_validation_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "realized_pnl"
            path.mkdir()
            (path / "equity_realized_summary.json").write_text(
                json.dumps({"net_realized_pnl": "not-money"}),
                encoding="utf-8",
            )
            fake_streamlit = _FakeStreamlit(directory)
            previous = sys.modules.get("streamlit")
            previous_root = os.environ.get("TRADEMIRROR_DASHBOARD_DATA")
            sys.modules["streamlit"] = fake_streamlit
            os.environ["TRADEMIRROR_DASHBOARD_DATA"] = directory
            sys.modules.pop("dashboard.app", None)
            try:
                app = importlib.import_module("dashboard.app")
                app.main()
            finally:
                sys.modules.pop("dashboard.app", None)
                if previous_root is None:
                    os.environ.pop("TRADEMIRROR_DASHBOARD_DATA", None)
                else:
                    os.environ["TRADEMIRROR_DASHBOARD_DATA"] = previous_root
                if previous is None:
                    sys.modules.pop("streamlit", None)
                else:
                    sys.modules["streamlit"] = previous
            self.assertTrue(fake_streamlit.errors)
            self.assertIn("couldn’t display this data", fake_streamlit.errors[0])
            self.assertEqual(fake_streamlit.dataframes[0][0]["file"], "realized_pnl/equity_realized_summary.json")

    def test_sidebar_source_indicator_does_not_expose_local_path(self):
        fake_streamlit = _FakeStreamlit(str(DEMO_DATA_DIR))
        previous = sys.modules.get("streamlit")
        sys.modules["streamlit"] = fake_streamlit
        sys.modules.pop("dashboard.app", None)
        try:
            app = importlib.import_module("dashboard.app")
            data = app._load_data()
        finally:
            sys.modules.pop("dashboard.app", None)
            if previous is None:
                sys.modules.pop("streamlit", None)
            else:
                sys.modules["streamlit"] = previous
        rendered_sidebar = "\n".join(fake_streamlit.sidebar.captions)
        self.assertEqual(data.source_label, "Demo data")
        self.assertIn("Data source: Synthetic demo", rendered_sidebar)
        self.assertNotIn(str(DEMO_DATA_DIR), rendered_sidebar)
        self.assertNotIn("C:\\", rendered_sidebar)
        self.assertNotIn("djjos", rendered_sidebar)

    def test_shared_page_header_renders_complete_demo_badge(self):
        fake_streamlit = _FakeStreamlit(str(DEMO_DATA_DIR))
        page_header(fake_streamlit, types.SimpleNamespace(source_label="Demo data"), "Title", "Caption")
        self.assertIn("<div class='tm-badge'>Demo data</div>", fake_streamlit.markdowns[0])
        self.assertEqual(fake_streamlit.titles, ["Title"])
        self.assertEqual(fake_streamlit.captions, ["Caption"])

    def test_safe_dataframe_falls_back_when_native_renderer_dependency_is_blocked(self):
        fake_streamlit = _FakeStreamlit(str(DEMO_DATA_DIR))
        fake_streamlit.dataframe_exception = ImportError(
            "DLL load failed while importing lib: An Application Control policy has blocked this file."
        )
        safe_dataframe(
            fake_streamlit,
            [
                {
                    "<Field>": "<script>alert('private')</script>",
                    "Value": "1 & 2",
                }
            ],
        )
        rendered = "\n".join(fake_streamlit.markdowns)
        self.assertIn("compatibility mode", "\n".join(fake_streamlit.captions))
        self.assertIn("&lt;Field&gt;", rendered)
        self.assertIn("&lt;script&gt;alert(&#x27;private&#x27;)&lt;/script&gt;", rendered)
        self.assertIn("1 &amp; 2", rendered)
        self.assertNotIn("<script>", rendered)
        self.assertNotIn("Traceback", rendered)

    def test_safe_dataframe_uses_native_renderer_when_available(self):
        fake_streamlit = _FakeStreamlit(str(DEMO_DATA_DIR))
        rows = [{"Field": "Count", "Value": "1"}]
        safe_dataframe(fake_streamlit, rows)
        self.assertEqual(fake_streamlit.dataframes, [rows])
        self.assertEqual(fake_streamlit.markdowns, [])

    def test_safe_chart_falls_back_without_traceback_when_renderer_dependency_is_blocked(self):
        fake_streamlit = _FakeStreamlit(str(DEMO_DATA_DIR))
        fake_streamlit.dataframe_exception = ImportError(
            "DLL load failed while importing lib: An Application Control policy has blocked this file."
        )
        safe_chart(
            fake_streamlit,
            lambda: (_ for _ in ()).throw(fake_streamlit.dataframe_exception),
            fallback_rows=[{"Year": "2021", "P&L": "<blocked> & safe"}],
        )
        rendered = "\n".join(fake_streamlit.markdowns)
        self.assertIn("unavailable", "\n".join(fake_streamlit.warnings))
        self.assertIn("compatibility mode", "\n".join(fake_streamlit.captions))
        self.assertIn("&lt;blocked&gt; &amp; safe", rendered)
        self.assertNotIn("<blocked>", rendered)
        self.assertNotIn("Traceback", rendered)

    def test_safe_structured_write_does_not_use_streamlit_dataframe_detection(self):
        fake_streamlit = _FakeStreamlit(str(DEMO_DATA_DIR))
        fake_streamlit.write_exception = ImportError(
            "DLL load failed while importing lib: An Application Control policy has blocked this file."
        )
        safe_structured_write(
            fake_streamlit,
            {
                "detail": "<private> & safe",
                "nested": {"count": "2"},
            },
        )
        rendered = "\n".join(fake_streamlit.markdowns)
        self.assertIn("&lt;private&gt; &amp; safe", rendered)
        self.assertIn("<strong>detail:</strong>", rendered)
        self.assertEqual(fake_streamlit.writes, [])

    def test_dashboard_pages_do_not_write_literal_structured_values_directly(self):
        for module in (overview, my_patterns, ask_trademirror, cash_positions, realized_pnl, data_quality):
            tree = ast.parse(inspect.getsource(module))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if not isinstance(node.func, ast.Attribute) or node.func.attr != "write":
                    continue
                if not node.args:
                    continue
                self.assertNotIsInstance(
                    node.args[0],
                    (ast.Dict, ast.List, ast.Tuple),
                    f"{module.__name__} should route structured writes through safe_structured_write",
                )

    def test_all_pages_use_shared_page_header(self):
        for module in (overview, my_patterns, ask_trademirror, cash_positions, realized_pnl, data_quality):
            source = inspect.getsource(module.render)
            self.assertIn("page_header(", source)
            self.assertNotIn("badge(", source)

    def test_registered_dashboard_pages_have_unique_intended_paths(self):
        fake_streamlit = _FakeStreamlit(str(DEMO_DATA_DIR))
        previous = sys.modules.get("streamlit")
        sys.modules["streamlit"] = fake_streamlit
        sys.modules.pop("dashboard.app", None)
        try:
            app = importlib.import_module("dashboard.app")
            pages = app.build_pages(load_dashboard_data(DEMO_DATA_DIR), streamlit_module=fake_streamlit)
        finally:
            sys.modules.pop("dashboard.app", None)
            if previous is None:
                sys.modules.pop("streamlit", None)
            else:
                sys.modules["streamlit"] = previous
        self.assertEqual([page.title for page in pages], ["Overview", "My Patterns", "Ask TradeMirror", "Cash & Positions", "Realized P&L", "Data Quality"])
        self.assertEqual([page.url_path for page in pages], ["overview", "my-patterns", "ask-trademirror", "cash-positions", "realized-pnl", "data-quality"])
        self.assertEqual(len({page.url_path for page in pages}), 6)

    def test_navigation_remains_custom_during_validation_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "realized_pnl"
            path.mkdir()
            (path / "equity_realized_summary.json").write_text(
                json.dumps({"net_realized_pnl": "not-money"}),
                encoding="utf-8",
            )
            fake_streamlit = _FakeStreamlit(directory)
            previous = sys.modules.get("streamlit")
            previous_root = os.environ.get("TRADEMIRROR_DASHBOARD_DATA")
            sys.modules["streamlit"] = fake_streamlit
            os.environ["TRADEMIRROR_DASHBOARD_DATA"] = directory
            sys.modules.pop("dashboard.app", None)
            try:
                app = importlib.import_module("dashboard.app")
                app.main()
            finally:
                sys.modules.pop("dashboard.app", None)
                if previous_root is None:
                    os.environ.pop("TRADEMIRROR_DASHBOARD_DATA", None)
                else:
                    os.environ["TRADEMIRROR_DASHBOARD_DATA"] = previous_root
                if previous is None:
                    sys.modules.pop("streamlit", None)
                else:
                    sys.modules["streamlit"] = previous
            self.assertEqual(fake_streamlit.navigation_titles, ["Overview", "My Patterns", "Ask TradeMirror", "Cash & Positions", "Realized P&L", "Data Quality"])
            self.assertEqual(fake_streamlit.navigation_paths, ["overview", "my-patterns", "ask-trademirror", "cash-positions", "realized-pnl", "data-quality"])
            self.assertNotIn("app", {title.casefold() for title in fake_streamlit.navigation_titles})
            self.assertNotIn("common", {title.casefold() for title in fake_streamlit.navigation_titles})
            self.assertTrue(fake_streamlit.errors)

    def test_behavioral_demo_outputs_load_and_reconcile(self):
        data = load_dashboard_data(DEMO_DATA_DIR)
        self.assertEqual(data.behavioral_root, BEHAVIORAL_DEMO_DATA_DIR)
        model = build_patterns_view_model(data)
        self.assertTrue(model["available"])
        self.assertEqual(model["coverage"]["High-confidence completed trades"], "30")
        self.assertEqual(model["coverage"]["Limited-confidence trades"], "2")
        self.assertEqual(model["coverage"]["Excluded matches"], "2")
        self.assertEqual(model["coverage"]["High-confidence coverage"], "88.24%")
        self.assertEqual(model["date_range"], "2021-01-01 to 2022-06-08")
        self.assertLessEqual(len(model["priority_patterns"]), 3)
        self.assertEqual(model["performance_summary"]["Net realized P&L"], "-$603.00")
        self.assertEqual(model["performance_summary"]["Win rate"], "56.67%")

    def test_behavioral_csv_prohibited_headers_are_rejected_after_normalization(self):
        cases = [
            "description_raw",
            " Description_Raw ",
            "DESCRIPTION_RAW",
            "\ufeffdescription_raw",
            " Raw_Row_JSON ",
        ]
        for header in cases:
            with self.subTest(header=header), tempfile.TemporaryDirectory() as directory:
                secret = "private-account-7777"
                self._write_behavioral_csv_with_columns(
                    directory,
                    "annual_behavior.csv",
                    [*BEHAVIORAL_CSV_SCHEMAS["annual_behavior.csv"], header],
                    {header: secret},
                )
                data = load_dashboard_data(directory)
                loaded = (data.behavioral_csv_files or {})["annual_behavior.csv"]
                self.assertFalse(loaded.available)
                self.assertEqual(loaded.rows, ())
                self.assertIn("Prohibited raw/private fields are present.", loaded.error)
                self.assertNotIn(secret, loaded.error)
                self.assertNotIn(secret, "\n".join(data.errors))

    def test_behavioral_csv_extra_columns_do_not_reach_view_model(self):
        with tempfile.TemporaryDirectory() as directory:
            self._copy_behavioral_demo(directory)
            secret = "Account Number: 123456789"
            annual_path = Path(directory) / "annual_behavior.csv"
            with annual_path.open(encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            columns = [*BEHAVIORAL_CSV_SCHEMAS["annual_behavior.csv"], "private_note"]
            with annual_path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=columns)
                writer.writeheader()
                for row in rows:
                    row["private_note"] = secret
                    writer.writerow(row)
            data = load_dashboard_data(directory)
            loaded = (data.behavioral_csv_files or {})["annual_behavior.csv"]
            self.assertTrue(loaded.available)
            self.assertNotIn("private_note", loaded.rows[0])
            rendered = str(build_patterns_view_model(data))
            self.assertNotIn(secret, rendered)
            self.assertNotIn("private_note", rendered)

    def test_behavioral_malformed_numeric_fails_before_rendering(self):
        with tempfile.TemporaryDirectory() as directory:
            self._copy_behavioral_demo(directory)
            path = Path(directory) / "behavioral_summary.json"
            summary = json.loads(path.read_text(encoding="utf-8"))
            summary["high_confidence_trade_count"] = "not-count"
            path.write_text(json.dumps(summary), encoding="utf-8")
            with self.assertRaises(DashboardValidationError) as context:
                load_validated_dashboard_data(directory)
            issue = self._issue_for(context.exception, "high_confidence_trade_count")
            self.assertEqual(issue.filename, "behavioral_summary.json")
            self.assertEqual(issue.reason, "malformed value")

    def test_behavioral_ranked_findings_use_high_confidence_only_and_suppress_low(self):
        data = load_dashboard_data(DEMO_DATA_DIR)
        model = build_patterns_view_model(data)
        cards = model["priority_patterns"] + model["what_helped"] + model["what_hurt"]
        self.assertTrue(cards)
        self.assertTrue(all(card["confidence"] in {"Medium", "High"} for card in cards))
        self.assertNotIn("Short-window re-entry evidence was limited", str(cards))
        self.assertLessEqual(len(model["what_hurt"]), 3)
        self.assertLessEqual(len(model["what_helped"]), 3)

    def test_behavioral_performance_summary_is_not_ranked_as_pattern(self):
        model = build_patterns_view_model(load_dashboard_data(DEMO_DATA_DIR))
        cards = model["priority_patterns"] + model["what_helped"] + model["what_hurt"]
        titles = [card["title"] for card in cards]
        self.assertNotIn("Overall completed-trade result", titles)
        self.assertEqual(len(titles), len(set(titles)))
        self.assertEqual(model["performance_summary"]["Net realized P&L"], "-$603.00")
        self.assertNotIn("P&L of -603", str(model))

    def test_behavioral_demo_has_positive_pattern_without_weakening_thresholds(self):
        model = build_patterns_view_model(load_dashboard_data(DEMO_DATA_DIR))
        cards = model["priority_patterns"] + model["what_helped"]
        helped = [card for card in cards if card["direction"] == "HELPED"]
        self.assertTrue(helped)
        self.assertTrue(all(int(card["eligible_trade_count"]) >= 10 for card in helped))

    def test_behavioral_coverage_notes_render_as_text_not_python_list(self):
        model = build_patterns_view_model(load_dashboard_data(DEMO_DATA_DIR))
        fake_streamlit = _FakePatternsStreamlit()
        my_patterns._render_coverage(fake_streamlit, model)
        self.assertFalse(fake_streamlit.writes)
        rendered = "\n".join(fake_streamlit.markdowns)
        self.assertIn("- High-confidence trades drive primary findings.", rendered)
        self.assertNotIn("[", rendered)

    def test_behavioral_missing_data_state_is_safe(self):
        with tempfile.TemporaryDirectory() as directory:
            data = load_dashboard_data(directory)
            with self.assertRaises(PatternValidationError) as context:
                build_patterns_view_model(data)
            self.assertTrue(context.exception.issues)
            self.assertNotIn(str(directory), str(context.exception.issues))

    def test_behavioral_zero_and_unavailable_values_are_distinct(self):
        with tempfile.TemporaryDirectory() as directory:
            self._copy_behavioral_demo(directory)
            path = Path(directory) / "activity_behavior.csv"
            with path.open(encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            rows[0]["average_pnl"] = ""
            rows[1]["net_pnl"] = "0"
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=BEHAVIORAL_CSV_SCHEMAS["activity_behavior.csv"])
                writer.writeheader()
                writer.writerows(rows)
            model = build_patterns_view_model(load_dashboard_data(directory))
            activity = model["charts"]["monthly_activity"]
            self.assertIsNone(activity[0]["Average P&L"])
            self.assertEqual(activity[1]["Net P&L"], Decimal("0"))

    def test_behavioral_blank_win_rate_is_allowed_when_denominator_is_zero(self):
        data = load_validated_dashboard_data(DEMO_DATA_DIR)
        holding = [
            row for row in (data.behavioral_csv_files or {})["holding_period_behavior.csv"].rows
            if row["holding_period_bin"] == "more_than_90_days"
        ][0]
        self.assertEqual(holding["trade_count"], "0")
        self.assertEqual(holding["win_rate"], "")
        model = build_patterns_view_model(data)
        self.assertNotIn(
            "More than 90 days",
            {row["Holding period"] for row in model["charts"]["holding_period_results"]},
        )

    def test_behavioral_blank_win_rate_is_rejected_when_denominator_is_positive(self):
        with tempfile.TemporaryDirectory() as directory:
            self._copy_behavioral_demo(directory)
            path = Path(directory) / "holding_period_behavior.csv"
            with path.open(encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            rows[0]["trade_count"] = "1"
            rows[0]["win_rate"] = ""
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=BEHAVIORAL_CSV_SCHEMAS["holding_period_behavior.csv"])
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaises(DashboardValidationError) as context:
                load_validated_dashboard_data(directory)
            issue = self._issue_for(context.exception, "win_rate")
            self.assertEqual(issue.filename, "holding_period_behavior.csv")
            self.assertEqual(issue.reason, "required value missing")

    def test_behavioral_required_base_values_remain_fail_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            self._copy_behavioral_demo(directory)
            path = Path(directory) / "holding_period_behavior.csv"
            with path.open(encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            rows[0]["trade_count"] = ""
            rows[0]["win_rate"] = ""
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=BEHAVIORAL_CSV_SCHEMAS["holding_period_behavior.csv"])
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaises(DashboardValidationError) as context:
                load_validated_dashboard_data(directory)
            issue = self._issue_for(context.exception, "trade_count")
            self.assertEqual(issue.reason, "required value missing")

    def test_behavioral_view_model_has_no_instrument_identifiers_or_codes(self):
        rendered = str(build_patterns_view_model(load_dashboard_data(DEMO_DATA_DIR))).casefold()
        for marker in ("instrument_", "security_key", "option_cusip", "structural_key", "equity:", "option:", "cusip", "insight_code"):
            self.assertNotIn(marker, rendered)
        self.assertNotIn("buy ", rendered)
        self.assertNotIn("sell ", rendered)
        self.assertNotIn("recommend", rendered)

    def test_behavioral_guardrails_are_safe_and_ordered(self):
        model = build_patterns_view_model(load_dashboard_data(DEMO_DATA_DIR))
        guardrails = model["guardrails"]
        self.assertEqual(len(guardrails), 3)
        self.assertEqual([row["Number"] for row in guardrails], ["1", "2", "3"])
        self.assertEqual([row["Pattern"] for row in guardrails], [
            "High-activity months differed",
            "Losses were concentrated",
            "Equity and option outcomes differed",
        ])
        self.assertTrue(all(row["Supporting metric"] for row in guardrails))
        rendered = str(guardrails).lower()
        self.assertNotIn("buy ", rendered)
        self.assertNotIn("sell ", rendered)
        self.assertNotIn("allocation", rendered)

    def test_behavioral_chart_rows_use_semantic_values_and_omit_unavailable_rates(self):
        model = build_patterns_view_model(load_dashboard_data(DEMO_DATA_DIR))
        holding = model["charts"]["holding_period_results"]
        self.assertTrue(any(row["Net P&L"] > 0 and row["Result"] == "Positive" for row in holding))
        self.assertTrue(any(row["Net P&L"] < 0 and row["Result"] == "Negative" for row in holding))
        self.assertTrue(all(row["Win rate"] is not None for row in holding))
        self.assertTrue(all(row["Trade count"] > 0 for row in holding))
        loss = model["charts"]["loss_concentration"]
        self.assertEqual(set(loss[0]), {"Group", "Share of gross losses (%)", "Result"})
        self.assertTrue(all(row["Result"] == "Negative" for row in loss))

    def test_behavioral_reentry_uses_compact_evidence_when_single_point(self):
        model = build_patterns_view_model(load_dashboard_data(DEMO_DATA_DIR))
        self.assertEqual(len(model["charts"]["reentry"]), 1)
        self.assertEqual(model["charts"]["reentry"][0]["Trades after prior loss"], 6)

    def test_behavioral_reconciliation_validation_rejects_inconsistent_annual_totals(self):
        with tempfile.TemporaryDirectory() as directory:
            self._copy_behavioral_demo(directory)
            path = Path(directory) / "annual_behavior.csv"
            with path.open(encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
            rows[0]["net_pnl"] = "999999"
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=BEHAVIORAL_CSV_SCHEMAS["annual_behavior.csv"])
                writer.writeheader()
                writer.writerows(rows)
            with self.assertRaises(PatternValidationError) as context:
                build_patterns_view_model(load_dashboard_data(directory))
            self.assertIn("net P&L does not reconcile", str(context.exception.issues))

    def test_user_facing_table_column_labels(self):
        data = load_dashboard_data(DEMO_DATA_DIR)
        attention = attention_display_rows(data)
        reviews = review_display_rows(data)
        self.assertEqual(set(attention[0]), {"Issue", "Count", "Next step"})
        self.assertEqual(set(reviews[0]), {"Area", "Severity", "Category", "Summary", "Security / contract"})
        self.assertNotIn("item", attention[0])
        self.assertNotIn("reason", reviews[0])

    def test_known_reason_codes_translate_to_plain_language(self):
        summaries = {row["Summary"] for row in review_display_rows(load_dashboard_data(DEMO_DATA_DIR))}
        self.assertIn("Cost basis was unavailable for part of this equity sale.", summaries)
        self.assertIn("Cost basis was unavailable for part of this option close.", summaries)
        self.assertIn("A closing option transaction exceeded the matching open quantity.", summaries)
        self.assertIn("This option lifecycle event requires basis information to be transferred.", summaries)

    def test_technical_review_context_is_preserved(self):
        details = technical_review_rows(load_dashboard_data(DEMO_DATA_DIR))
        reasons = {row["Technical reason"] for row in details}
        self.assertIn("unknown_basis_closure", reasons)
        self.assertIn("basis_transfer_required", reasons)
        self.assertTrue(any("unmatched_option_close" in reason for reason in reasons))
        self.assertTrue(all(row["Security / contract"] for row in details))

    def test_unknown_basis_and_option_basis_transfers_are_excluded_from_included_pnl(self):
        data = load_dashboard_data(DEMO_DATA_DIR)
        equity_summary = data.json_files["realized_pnl/equity_realized_summary.json"].data or {}
        option_summary = data.json_files["option_realized_pnl/option_realized_summary.json"].data or {}
        self.assertEqual(equity_summary["unknown_basis_quantity"], "1")
        self.assertEqual(option_summary["unknown_basis_quantity"], "1")
        self.assertEqual(option_summary["basis_transfer_count"], 1)
        metrics = overview_metrics(data)
        expected = Decimal(equity_summary["net_realized_pnl"]) + Decimal(option_summary["net_realized_pnl"])
        self.assertEqual(metrics["included_net_realized_pnl"], expected)

    def test_formatters_handle_negative_and_missing_values(self):
        self.assertEqual(format_currency("-12.3"), "-$12.30")
        self.assertEqual(format_currency(""), "Unavailable")
        self.assertEqual(format_currency("0"), "$0.00")
        self.assertEqual(format_percent("50.00"), "50%")
        self.assertEqual(format_percent("12.345"), "12.34%")
        self.assertEqual(format_quantity("10.000"), "10")

    def test_demo_files_contain_no_prohibited_raw_or_personal_fields(self):
        prohibited = (
            "description_raw",
            "raw_row_json",
            "Account Number",
            "Account No.",
            "Individual Account",
            "SSN",
            "ITIN",
        )
        for path in DEMO_DATA_DIR.rglob("*"):
            if path.is_file():
                text = path.read_text(encoding="utf-8")
                for marker in prohibited:
                    self.assertNotIn(marker, text, msg=str(path))

    def test_dashboard_import_smoke_without_streamlit_dependency(self):
        importlib.import_module("dashboard.data_loader")
        importlib.import_module("dashboard.formatters")
        importlib.import_module("dashboard.pages.overview")
        importlib.import_module("dashboard.pages.my_patterns")
        importlib.import_module("dashboard.pages.cash_positions")
        importlib.import_module("dashboard.pages.realized_pnl")
        importlib.import_module("dashboard.pages.data_quality")

    def test_required_demo_rows_exist_for_dashboard_pages(self):
        data = load_dashboard_data(DEMO_DATA_DIR)
        self.assertGreater(len(csv_rows(data, "cash_ledger/cash_ledger_daily.csv")), 0)
        self.assertGreater(len(csv_rows(data, "position_ledger/positions_as_of.csv")), 0)
        self.assertGreater(len(csv_rows(data, "realized_pnl/equity_lot_matches.csv")), 0)
        self.assertGreater(len(csv_rows(data, "option_realized_pnl/option_lot_matches.csv")), 0)

    def _write_csv(self, directory: str, name: str, row: dict[str, str]) -> None:
        path = Path(directory) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        columns = CSV_SCHEMAS[name]
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerow({column: row.get(column, "") for column in columns})

    def _write_csv_with_columns(
        self,
        directory: str,
        name: str,
        columns: list[str],
        row: dict[str, str],
    ) -> None:
        path = Path(directory) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerow({column: row.get(column, "") for column in columns})

    def _write_behavioral_csv_with_columns(
        self,
        directory: str,
        name: str,
        columns: list[str],
        row: dict[str, str],
    ) -> None:
        path = Path(directory) / "behavioral_insights" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.DictWriter(stream, fieldnames=columns)
            writer.writeheader()
            writer.writerow({column: row.get(column, "") for column in columns})

    def _copy_behavioral_demo(self, directory: str) -> None:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        for source in BEHAVIORAL_DEMO_DATA_DIR.iterdir():
            if source.is_file():
                (target / source.name).write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    def _issue_for(self, error: DashboardValidationError, field: str):
        for issue in error.issues:
            if issue.field == field:
                return issue
        self.fail(f"No validation issue for {field}")


class _FakeExpander:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


class _FakeSidebar:
    def __init__(self, directory: str):
        self.directory = directory
        self.captions: list[str] = []

    def text_input(self, _label: str, _value: str) -> str:
        return self.directory

    def caption(self, _text: str) -> None:
        self.captions.append(_text)

    def expander(self, *_args, **_kwargs) -> _FakeExpander:
        return _FakeExpander()


class _FakeStreamlit(types.ModuleType):
    def __init__(self, directory: str):
        super().__init__("streamlit")
        self.sidebar = _FakeSidebar(directory)
        self.errors: list[str] = []
        self.dataframes: list[list[dict[str, str]]] = []
        self.markdowns: list[str] = []
        self.titles: list[str] = []
        self.captions: list[str] = []
        self.warnings: list[str] = []
        self.writes: list[object] = []
        self.navigation_titles: list[str] = []
        self.navigation_paths: list[str] = []
        self.dataframe_exception: Exception | None = None
        self.write_exception: Exception | None = None

    def set_page_config(self, **_kwargs) -> None:
        return None

    def markdown(self, *_args, **_kwargs) -> None:
        self.markdowns.append(str(_args[0]) if _args else "")

    def title(self, text: str) -> None:
        self.titles.append(text)

    def caption(self, text: str) -> None:
        self.captions.append(text)

    def error(self, text: str) -> None:
        self.errors.append(text)

    def warning(self, text: str) -> None:
        self.warnings.append(text)

    def subheader(self, _text: str) -> None:
        return None

    def write(self, *_args, **_kwargs) -> None:
        if self.write_exception is not None:
            raise self.write_exception
        self.writes.extend(_args)

    def dataframe(self, rows: list[dict[str, str]], **_kwargs) -> None:
        if self.dataframe_exception is not None:
            raise self.dataframe_exception
        self.dataframes.append(rows)

    def Page(self, page: object, *, title: str, url_path: str):
        return types.SimpleNamespace(page=page, title=title, url_path=url_path)

    def navigation(self, pages: list[object]):
        self.navigation_titles = [page.title for page in pages]
        self.navigation_paths = [page.url_path for page in pages]
        return types.SimpleNamespace(run=lambda: pages[0].page())


class _DashboardDataStub:
    source_label = "Synthetic test data"

    def __init__(self, daily_rows: list[dict[str, str]]):
        self.csv_files = {
            "cash_ledger/cash_ledger_daily.csv": types.SimpleNamespace(rows=tuple(daily_rows)),
            "cash_ledger/cash_ledger_events.csv": types.SimpleNamespace(rows=()),
            "position_ledger/positions_as_of.csv": types.SimpleNamespace(rows=()),
            "position_ledger/pending_position_settlement.csv": types.SimpleNamespace(rows=()),
        }
        self.json_files = {
            "cash_ledger/cash_ledger_summary.json": types.SimpleNamespace(available=True, data={}),
        }


class _FakeColumn:
    def __init__(self, parent=None):
        self.parent = parent

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def metric(self, *_args, **_kwargs) -> None:
        return None

    def markdown(self, text: str, **_kwargs) -> None:
        if self.parent is not None and hasattr(self.parent, "markdowns"):
            self.parent.markdowns.append(text)


class _FakePatternsStreamlit:
    def __init__(self):
        self.markdowns: list[str] = []
        self.writes: list[object] = []

    def columns(self, count: int):
        return [_FakeColumn(self) for _ in range(count)]

    def subheader(self, *_args, **_kwargs) -> None:
        return None

    def markdown(self, text: str, **_kwargs) -> None:
        self.markdowns.append(text)

    def write(self, value: object, **_kwargs) -> None:
        self.writes.append(value)


class _FakeCashStreamlit:
    def __init__(self):
        self.infos: list[str] = []
        self.line_charts: list[object] = []
        self.bar_charts: list[object] = []
        self.altair_charts: list[object] = []
        self.dataframes: list[object] = []

    def markdown(self, *_args, **_kwargs) -> None:
        return None

    def title(self, *_args, **_kwargs) -> None:
        return None

    def caption(self, *_args, **_kwargs) -> None:
        return None

    def subheader(self, *_args, **_kwargs) -> None:
        return None

    def info(self, text: str) -> None:
        self.infos.append(text)

    def line_chart(self, rows: object, **_kwargs) -> None:
        self.line_charts.append(rows)

    def altair_chart(self, rows: object, **_kwargs) -> None:
        self.altair_charts.append(rows)

    def bar_chart(self, rows: object, **_kwargs) -> None:
        self.bar_charts.append(rows)

    def columns(self, count: int):
        return [_FakeColumn() for _ in range(count)]

    def selectbox(self, _label: str, values: list[str], **_kwargs) -> str:
        return values[0]

    def text_input(self, _label: str, value: str) -> str:
        return value

    def dataframe(self, rows: object, **_kwargs) -> None:
        self.dataframes.append(rows)

    def expander(self, *_args, **_kwargs) -> _FakeExpander:
        return _FakeExpander()

    def write(self, *_args, **_kwargs) -> None:
        return None


def cash_positions_decimal_chart_rows(rows, fields=("closing_cash", "net_cash_movement")):
    from dashboard.pages.common import decimal_chart_rows

    return decimal_chart_rows(rows, fields)


if __name__ == "__main__":
    unittest.main()
