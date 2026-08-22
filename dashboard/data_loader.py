from __future__ import annotations

import csv
import json
from datetime import date
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping

from .formatters import parse_decimal


DEMO_DATA_DIR = Path(__file__).resolve().parents[1] / "demo" / "dashboard_data"

PROHIBITED_OUTPUT_FIELDS = {
    "description_raw",
    "raw_row_json",
    "account_number",
    "account_id",
}

CSV_SCHEMAS: dict[str, tuple[str, ...]] = {
    "cash_ledger/cash_ledger_daily.csv": (
        "date",
        "opening_cash",
        "external_inflows",
        "external_outflows",
        "trading_cash_flow",
        "income",
        "fees",
        "financing_costs",
        "internal_transfers",
        "other_cash_flow",
        "net_cash_movement",
        "closing_cash",
        "balance_confidence",
        "cash_position_type",
    ),
    "cash_ledger/cash_ledger_events.csv": (
        "source_row_id",
        "activity_date",
        "settle_date",
        "transaction_code",
        "event_type",
        "signed_amount",
        "cash_category",
        "external_cash_flow",
        "internal_transfer",
        "confidence",
        "review_status",
        "review_reason",
    ),
    "position_ledger/positions_as_of.csv": (
        "security_key",
        "asset_type",
        "trade_date_quantity",
        "settled_quantity",
        "trade_date_long_quantity",
        "trade_date_short_quantity",
        "settled_long_quantity",
        "settled_short_quantity",
        "confidence",
        "review_status",
    ),
    "position_ledger/position_history.csv": (
        "date",
        "security_key",
        "trade_date_quantity",
        "settled_quantity",
        "confidence",
        "review_status",
    ),
    "position_ledger/pending_position_settlement.csv": (
        "source_row_id",
        "activity_date",
        "settle_date",
        "transaction_code",
        "event_type",
        "security_key",
        "signed_quantity",
        "position_side",
    ),
    "realized_pnl/equity_lot_matches.csv": (
        "security_key",
        "symbol",
        "closing_trade_date",
        "matched_quantity",
        "allocated_opening_cost",
        "allocated_closing_proceeds",
        "realized_pnl",
        "realized_return_pct",
        "holding_period_days",
        "basis_status",
    ),
    "realized_pnl/equity_realized_by_security.csv": (
        "security_key",
        "symbol",
        "net_realized_pnl",
        "winning_matches",
        "losing_matches",
        "unknown_basis_quantity",
        "unmatched_quantity",
    ),
    "option_realized_pnl/option_lot_matches.csv": (
        "security_key",
        "underlying",
        "option_expiration",
        "option_type",
        "option_strike",
        "position_side",
        "closing_trade_date",
        "matched_quantity",
        "realized_pnl",
        "holding_period_days",
        "outcome",
        "basis_transfer_required",
        "basis_status",
    ),
    "option_realized_pnl/option_realized_by_contract.csv": (
        "security_key",
        "underlying",
        "option_expiration",
        "option_type",
        "option_strike",
        "net_realized_pnl",
        "winning_matches",
        "losing_matches",
        "unknown_basis_quantity",
        "unmatched_quantity",
    ),
    "option_realized_pnl/option_basis_transfers.csv": (
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
    ),
}

JSON_FILES = (
    "cash_ledger/cash_ledger_summary.json",
    "cash_ledger/cash_ledger_review.json",
    "position_ledger/position_summary.json",
    "position_ledger/position_review.json",
    "realized_pnl/equity_realized_summary.json",
    "realized_pnl/equity_lot_review.json",
    "option_realized_pnl/option_realized_summary.json",
    "option_realized_pnl/option_lot_review.json",
)

REQUIRED = "required"
OPTIONAL = "optional"

CSV_VALUE_SCHEMAS: dict[str, Mapping[str, tuple[str, str]]] = {
    "cash_ledger/cash_ledger_daily.csv": {
        "date": ("date", REQUIRED),
        "opening_cash": ("currency", OPTIONAL),
        "external_inflows": ("currency", REQUIRED),
        "external_outflows": ("currency", REQUIRED),
        "trading_cash_flow": ("currency", REQUIRED),
        "income": ("currency", REQUIRED),
        "fees": ("currency", REQUIRED),
        "financing_costs": ("currency", REQUIRED),
        "internal_transfers": ("currency", REQUIRED),
        "other_cash_flow": ("currency", REQUIRED),
        "net_cash_movement": ("currency", REQUIRED),
        "closing_cash": ("currency", OPTIONAL),
    },
    "cash_ledger/cash_ledger_events.csv": {
        "activity_date": ("date", REQUIRED),
        "settle_date": ("date", REQUIRED),
        "signed_amount": ("currency", REQUIRED),
    },
    "position_ledger/positions_as_of.csv": {
        "trade_date_quantity": ("quantity", REQUIRED),
        "settled_quantity": ("quantity", REQUIRED),
        "trade_date_long_quantity": ("quantity", REQUIRED),
        "trade_date_short_quantity": ("quantity", REQUIRED),
        "settled_long_quantity": ("quantity", REQUIRED),
        "settled_short_quantity": ("quantity", REQUIRED),
    },
    "position_ledger/position_history.csv": {
        "date": ("date", REQUIRED),
        "trade_date_quantity": ("quantity", REQUIRED),
        "settled_quantity": ("quantity", REQUIRED),
    },
    "position_ledger/pending_position_settlement.csv": {
        "activity_date": ("date", REQUIRED),
        "settle_date": ("date", REQUIRED),
        "signed_quantity": ("quantity", REQUIRED),
    },
    "realized_pnl/equity_lot_matches.csv": {
        "closing_trade_date": ("date", REQUIRED),
        "matched_quantity": ("quantity", REQUIRED),
        "allocated_opening_cost": ("currency", OPTIONAL),
        "allocated_closing_proceeds": ("currency", REQUIRED),
        "realized_pnl": ("currency", OPTIONAL),
        "realized_return_pct": ("percentage", OPTIONAL),
        "holding_period_days": ("count", OPTIONAL),
    },
    "realized_pnl/equity_realized_by_security.csv": {
        "net_realized_pnl": ("currency", REQUIRED),
        "winning_matches": ("count", REQUIRED),
        "losing_matches": ("count", REQUIRED),
        "unknown_basis_quantity": ("quantity", REQUIRED),
        "unmatched_quantity": ("quantity", REQUIRED),
    },
    "option_realized_pnl/option_lot_matches.csv": {
        "option_strike": ("currency", REQUIRED),
        "closing_trade_date": ("date", REQUIRED),
        "matched_quantity": ("quantity", REQUIRED),
        "realized_pnl": ("currency", OPTIONAL),
        "holding_period_days": ("count", OPTIONAL),
    },
    "option_realized_pnl/option_realized_by_contract.csv": {
        "option_strike": ("currency", REQUIRED),
        "net_realized_pnl": ("currency", REQUIRED),
        "winning_matches": ("count", REQUIRED),
        "losing_matches": ("count", REQUIRED),
        "unknown_basis_quantity": ("quantity", REQUIRED),
        "unmatched_quantity": ("quantity", REQUIRED),
    },
    "option_realized_pnl/option_basis_transfers.csv": {
        "option_strike": ("currency", REQUIRED),
        "matched_quantity": ("quantity", REQUIRED),
    },
}

JSON_VALUE_SCHEMAS: dict[str, Mapping[str, tuple[str, str]]] = {
    "cash_ledger/cash_ledger_summary.json": {
        "as_of": ("date", OPTIONAL),
        "daily_net_cash_movement": ("currency", REQUIRED),
        "ending_cash": ("currency", OPTIONAL),
        "event_count": ("count", REQUIRED),
        "event_net_cash_movement": ("currency", REQUIRED),
        "opening_cash": ("currency", OPTIONAL),
        "opening_date": ("date", OPTIONAL),
        "pending_settlement_count": ("count", REQUIRED),
        "review_count": ("count", REQUIRED),
    },
    "position_ledger/position_summary.json": {
        "anchor_count": ("count", REQUIRED),
        "event_count": ("count", REQUIRED),
        "future_anchor_count": ("count", REQUIRED),
        "history_count": ("count", REQUIRED),
        "pending_settlement_count": ("count", REQUIRED),
        "position_count": ("count", REQUIRED),
        "review_count": ("count", REQUIRED),
    },
    "realized_pnl/equity_realized_summary.json": {
        "anchor_count": ("count", REQUIRED),
        "break_even_matches": ("count", REQUIRED),
        "losing_matches": ("count", REQUIRED),
        "match_count": ("count", REQUIRED),
        "net_realized_pnl": ("currency", REQUIRED),
        "open_lot_count": ("count", REQUIRED),
        "realized_gain": ("currency", REQUIRED),
        "realized_loss": ("currency", REQUIRED),
        "review_count": ("count", REQUIRED),
        "unknown_basis_quantity": ("quantity", REQUIRED),
        "unmatched_quantity": ("quantity", REQUIRED),
        "winning_matches": ("count", REQUIRED),
    },
    "option_realized_pnl/option_realized_summary.json": {
        "basis_transfer_count": ("count", REQUIRED),
        "break_even_matches": ("count", REQUIRED),
        "losing_matches": ("count", REQUIRED),
        "match_count": ("count", REQUIRED),
        "net_realized_pnl": ("currency", REQUIRED),
        "open_lot_count": ("count", REQUIRED),
        "realized_gain": ("currency", REQUIRED),
        "realized_loss": ("currency", REQUIRED),
        "review_count": ("count", REQUIRED),
        "unknown_basis_quantity": ("quantity", REQUIRED),
        "unmatched_quantity": ("quantity", REQUIRED),
        "winning_matches": ("count", REQUIRED),
    },
}


@dataclass(frozen=True)
class LoadedFile:
    path: str
    available: bool
    rows: tuple[dict[str, str], ...] = ()
    data: Mapping[str, Any] | None = None
    error: str = ""


@dataclass(frozen=True)
class ValidationIssue:
    filename: str
    field: str
    location: str
    reason: str

    def message(self) -> str:
        return f"{self.filename}: {self.location}: {self.field}: {self.reason}"


class DashboardValidationError(ValueError):
    def __init__(self, issues: Iterable[ValidationIssue]):
        self.issues = tuple(issues)
        super().__init__("Dashboard data contains invalid calculated values.")

    @property
    def messages(self) -> tuple[str, ...]:
        return tuple(issue.message() for issue in self.issues)


@dataclass(frozen=True)
class DashboardData:
    root: Path
    csv_files: Mapping[str, LoadedFile]
    json_files: Mapping[str, LoadedFile]
    source_label: str = "Demo data"
    validation_issues: tuple[ValidationIssue, ...] = ()

    @property
    def errors(self) -> tuple[str, ...]:
        messages: list[str] = []
        for file in [*self.csv_files.values(), *self.json_files.values()]:
            if file.error:
                messages.append(f"{file.path}: {file.error}")
        return tuple(messages)


def load_dashboard_data(root: str | Path = DEMO_DATA_DIR, *, source_label: str = "Demo data") -> DashboardData:
    base = Path(root)
    csv_files = {name: _load_csv(base, name, columns) for name, columns in CSV_SCHEMAS.items()}
    json_files = {name: _load_json(base, name) for name in JSON_FILES}
    issues = _validate_loaded_values(csv_files, json_files)
    return DashboardData(root=base, csv_files=csv_files, json_files=json_files, source_label=source_label, validation_issues=issues)


def load_validated_dashboard_data(root: str | Path = DEMO_DATA_DIR, *, source_label: str = "Demo data") -> DashboardData:
    data = load_dashboard_data(root, source_label=source_label)
    if data.validation_issues:
        raise DashboardValidationError(data.validation_issues)
    return data


def _load_csv(base: Path, name: str, required_columns: Iterable[str]) -> LoadedFile:
    path = base / name
    if not path.exists():
        return LoadedFile(path=name, available=False, error="File is unavailable.")
    try:
        with path.open("r", newline="", encoding="utf-8-sig") as stream:
            reader = csv.DictReader(stream)
            fieldnames = tuple(reader.fieldnames or ())
            normalized = [_normalize_header(field) for field in fieldnames]
            if len(normalized) != len(set(normalized)):
                return LoadedFile(path=name, available=False, error="Duplicate or ambiguous columns are present.")
            prohibited = {_normalize_header(field) for field in PROHIBITED_OUTPUT_FIELDS}
            if prohibited.intersection(normalized):
                return LoadedFile(path=name, available=False, error="Prohibited raw/private fields are present.")
            columns = tuple(required_columns)
            header_map = dict(zip(normalized, fieldnames))
            missing = [column for column in columns if _normalize_header(column) not in header_map]
            if missing:
                return LoadedFile(path=name, available=False, error=f"Missing columns: {', '.join(missing)}")
            rows = tuple(
                {
                    column: row.get(header_map[_normalize_header(column)], "")
                    for column in columns
                }
                for row in reader
            )
            return LoadedFile(path=name, available=True, rows=rows)
    except OSError:
        return LoadedFile(path=name, available=False, error="File could not be read.")


def _normalize_header(value: str | None) -> str:
    return str(value or "").lstrip("\ufeff").strip().casefold()


def _load_json(base: Path, name: str) -> LoadedFile:
    path = base / name
    if not path.exists():
        return LoadedFile(path=name, available=False, error="File is unavailable.")
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return LoadedFile(path=name, available=False, error="File could not be read as JSON.")
    if _contains_prohibited_key(data):
        return LoadedFile(path=name, available=False, error="Prohibited raw/private fields are present.")
    return LoadedFile(path=name, available=True, data=data)


def _contains_prohibited_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        if PROHIBITED_OUTPUT_FIELDS.intersection(str(key) for key in value):
            return True
        return any(_contains_prohibited_key(child) for child in value.values())
    if isinstance(value, list):
        return any(_contains_prohibited_key(child) for child in value)
    return False


def _validate_loaded_values(
    csv_files: Mapping[str, LoadedFile],
    json_files: Mapping[str, LoadedFile],
) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    for name, schema in CSV_VALUE_SCHEMAS.items():
        loaded = csv_files.get(name)
        if not loaded or not loaded.available:
            continue
        for row_number, row in enumerate(loaded.rows, start=2):
            for field, (value_type, requirement) in schema.items():
                issues.extend(_validate_value(name, field, row.get(field), value_type, requirement, f"row {row_number}"))
    for name, schema in JSON_VALUE_SCHEMAS.items():
        loaded = json_files.get(name)
        if not loaded or not loaded.available or loaded.data is None:
            continue
        for field, (value_type, requirement) in schema.items():
            issues.extend(_validate_value(name, field, loaded.data.get(field), value_type, requirement, "summary"))
        for section in ("realized_pnl_by_year", "realized_pnl_by_security", "realized_pnl_by_underlying", "realized_pnl_by_option_type", "realized_pnl_by_outcome", "realized_pnl_by_side"):
            values = loaded.data.get(section)
            if not isinstance(values, Mapping):
                continue
            for key, value in values.items():
                issues.extend(_validate_value(name, f"{section}.{key}", value, "currency", REQUIRED, "summary"))
    return tuple(issues)


def _validate_value(
    filename: str,
    field: str,
    value: object,
    value_type: str,
    requirement: str,
    location: str,
) -> tuple[ValidationIssue, ...]:
    if _is_blank(value):
        if requirement == OPTIONAL:
            return ()
        return (ValidationIssue(filename, field, location, "required value missing"),)
    if value_type == "date":
        return _validate_date(filename, field, value, location)
    return _validate_decimal(filename, field, value, value_type, location)


def _validate_date(filename: str, field: str, value: object, location: str) -> tuple[ValidationIssue, ...]:
    if not isinstance(value, str):
        return (ValidationIssue(filename, field, location, "invalid type"),)
    try:
        date.fromisoformat(value.strip())
    except ValueError:
        return (ValidationIssue(filename, field, location, "malformed value"),)
    return ()


def _validate_decimal(filename: str, field: str, value: object, value_type: str, location: str) -> tuple[ValidationIssue, ...]:
    try:
        parsed = parse_decimal(value)
    except ValueError as exc:
        reason = "nonfinite value" if "nonfinite" in str(exc) else "malformed value"
        if "type" in str(exc):
            reason = "invalid type"
        return (ValidationIssue(filename, field, location, reason),)
    if parsed is None:
        return (ValidationIssue(filename, field, location, "required value missing"),)
    if value_type == "count" and parsed != parsed.to_integral_value():
        return (ValidationIssue(filename, field, location, "malformed value"),)
    return ()


def _is_blank(value: object) -> bool:
    return value is None or (isinstance(value, str) and value.strip() == "")


def csv_rows(data: DashboardData, name: str) -> tuple[dict[str, str], ...]:
    return data.csv_files.get(name, LoadedFile(name, False)).rows


def json_data(data: DashboardData, name: str) -> Mapping[str, Any]:
    loaded = data.json_files.get(name)
    if not loaded or not loaded.available or not loaded.data:
        return {}
    return loaded.data


def decimal_field(row: Mapping[str, Any], field: str) -> Decimal | None:
    return parse_decimal(row.get(field))


def decimal_summary(data: DashboardData, name: str, field: str) -> Decimal:
    value = json_data(data, name).get(field)
    parsed = parse_decimal(value)
    return parsed or Decimal("0")


def overview_metrics(data: DashboardData) -> dict[str, Decimal | int | str]:
    equity_summary = json_data(data, "realized_pnl/equity_realized_summary.json")
    option_summary = json_data(data, "option_realized_pnl/option_realized_summary.json")
    position_summary = json_data(data, "position_ledger/position_summary.json")
    cash_summary = json_data(data, "cash_ledger/cash_ledger_summary.json")
    equity_pnl = decimal_summary(data, "realized_pnl/equity_realized_summary.json", "net_realized_pnl")
    option_pnl = decimal_summary(data, "option_realized_pnl/option_realized_summary.json", "net_realized_pnl")
    wins = _summary_int(equity_summary, "winning_matches") + _summary_int(option_summary, "winning_matches")
    losses = _summary_int(equity_summary, "losing_matches") + _summary_int(option_summary, "losing_matches")
    breakeven = _summary_int(equity_summary, "break_even_matches") + _summary_int(option_summary, "break_even_matches")
    denominator = wins + losses + breakeven
    win_rate = (Decimal(wins) / Decimal(denominator) * Decimal("100")) if denominator else Decimal("0")
    review_count = (
        _summary_int(cash_summary, "review_count")
        + _summary_int(position_summary, "review_count")
        + _summary_int(equity_summary, "review_count")
        + _summary_int(option_summary, "review_count")
    )
    return {
        "included_net_realized_pnl": equity_pnl + option_pnl,
        "equity_realized_pnl": equity_pnl,
        "option_realized_pnl": option_pnl,
        "known_basis_win_rate": win_rate,
        "open_positions": _summary_int(position_summary, "position_count"),
        "review_count": review_count,
        "as_of": str(cash_summary.get("as_of") or position_summary.get("as_of") or ""),
    }


def annual_realized_rows(data: DashboardData) -> list[dict[str, Any]]:
    equity = json_data(data, "realized_pnl/equity_realized_summary.json").get("realized_pnl_by_year", {})
    options = json_data(data, "option_realized_pnl/option_realized_summary.json").get("realized_pnl_by_year", {})
    years = sorted(set(equity) | set(options))
    return [
        {
            "year": year,
            "equity": decimal_or_zero(equity.get(year)),
            "options": decimal_or_zero(options.get(year)),
        }
        for year in years
    ]


def annual_realized_chart_rows(data: DashboardData) -> list[dict[str, Any]]:
    return [
        {
            "Year": row["year"],
            "Equity": row["equity"],
            "Options": row["options"],
        }
        for row in annual_realized_rows(data)
    ]


def attention_display_rows(data: DashboardData) -> list[dict[str, str]]:
    return [
        {
            "Issue": row["item"],
            "Count": row["count"],
            "Next step": row["next_step"],
        }
        for row in attention_items(data)
    ]


def attention_items(data: DashboardData) -> list[dict[str, str]]:
    equity = json_data(data, "realized_pnl/equity_realized_summary.json")
    options = json_data(data, "option_realized_pnl/option_realized_summary.json")
    position = json_data(data, "position_ledger/position_summary.json")
    cash = json_data(data, "cash_ledger/cash_ledger_summary.json")
    return [
        {"item": "Equity quantity with unknown cost basis", "count": str(equity.get("unknown_basis_quantity", "0")), "next_step": "Review Equity P&L details in Data Quality."},
        {"item": "Option quantity with unknown cost basis", "count": str(options.get("unknown_basis_quantity", "0")), "next_step": "Review Option P&L details in Data Quality."},
        {"item": "Unmatched equity quantity", "count": str(equity.get("unmatched_quantity", "0")), "next_step": "Check unmatched lot events in Data Quality."},
        {"item": "Unmatched option quantity", "count": str(options.get("unmatched_quantity", "0")), "next_step": "Check unmatched option events in Data Quality."},
        {"item": "Option basis-transfer events", "count": str(options.get("basis_transfer_count", "0")), "next_step": "Review exercise or assignment basis-transfer notes."},
        {"item": "Cash ledger review items", "count": str(cash.get("review_count", "0")), "next_step": "Inspect cash review details."},
        {"item": "Position ledger review items", "count": str(position.get("review_count", "0")), "next_step": "Inspect position review details."},
    ]


def review_display_rows(data: DashboardData) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for area, issue in _iter_review_issues(data):
        reason = str(issue.get("review_reason", "review"))
        rows.append({
            "Area": area,
            "Severity": review_severity(reason),
            "Category": review_category(reason),
            "Summary": plain_review_summary(reason),
            "Security / contract": compact_security_label(issue),
        })
    return rows


def technical_review_rows(data: DashboardData) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for area, issue in _iter_review_issues(data):
        rows.append({
            "Area": area,
            "Technical reason": str(issue.get("review_reason", "")),
            "Security / contract": compact_security_label(issue),
            "Source row": str(issue.get("source_row_id", "")),
            "Activity date": str(issue.get("activity_date", "")),
            "Settle date": str(issue.get("settle_date", "")),
            "Quantity": str(issue.get("unmatched_quantity") or issue.get("unknown_basis_quantity") or issue.get("quantity") or ""),
        })
    return rows


def plain_review_summary(reason: str) -> str:
    if "unmatched_option_close" in reason or "oversized_option_lifecycle" in reason:
        return "A closing option transaction exceeded the matching open quantity."
    if reason == "unknown_basis_closure":
        return "Cost basis was unavailable for part of this equity sale."
    if reason == "unknown_basis_option_closure":
        return "Cost basis was unavailable for part of this option close."
    if reason == "basis_transfer_required" or "basis_transfer" in reason:
        return "This option lifecycle event requires basis information to be transferred."
    if "ambiguous" in reason:
        return "The security or contract identity needs review before it can be matched confidently."
    if "invalid" in reason or "malformed" in reason:
        return "A source row contained an invalid value that needs review."
    if "unknown_basis" in reason:
        return "Cost basis was unavailable for part of this activity."
    return "This item needs review before it can be treated as fully validated."


def compact_security_label(issue: Mapping[str, Any]) -> str:
    underlying = str(issue.get("underlying") or "").strip()
    expiration = str(issue.get("option_expiration") or "").strip()
    option_type = str(issue.get("option_type") or "").strip()
    strike = str(issue.get("option_strike") or "").strip()
    if underlying and expiration and option_type and strike:
        return f"{underlying} {expiration} {option_type.upper()} {strike}"
    symbol = str(issue.get("symbol") or "").strip()
    if symbol:
        return symbol
    security_key = str(issue.get("security_key") or issue.get("closing_security_key") or "").strip()
    if security_key.startswith("equity:"):
        return security_key.removeprefix("equity:")
    if security_key.startswith("option:"):
        parts = security_key.split(":")
        if len(parts) >= 5:
            return f"{parts[1]} {parts[2]} {parts[3].upper()} {parts[4]}"
    return security_key


def _iter_review_issues(data: DashboardData) -> Iterable[tuple[str, Mapping[str, Any]]]:
    for label, name in (
        ("Cash", "cash_ledger/cash_ledger_review.json"),
        ("Position", "position_ledger/position_review.json"),
        ("Equity P&L", "realized_pnl/equity_lot_review.json"),
        ("Option P&L", "option_realized_pnl/option_lot_review.json"),
    ):
        issues = json_data(data, name).get("issues", [])
        for issue in issues:
            if isinstance(issue, Mapping):
                yield label, issue
    for row in csv_rows(data, "option_realized_pnl/option_basis_transfers.csv"):
        if row.get("review_status") == "review" or row.get("review_reason"):
            yield "Option P&L", row


def known_basis_security_rows(data: DashboardData) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in csv_rows(data, "realized_pnl/equity_realized_by_security.csv"):
        rows.append({"asset_type": "Equity", "name": row.get("symbol") or row.get("security_key"), "pnl": decimal_or_zero(row.get("net_realized_pnl"))})
    for row in csv_rows(data, "option_realized_pnl/option_realized_by_contract.csv"):
        label = f"{row.get('underlying')} {row.get('option_expiration')} {row.get('option_type')} {row.get('option_strike')}"
        rows.append({"asset_type": "Option", "name": label, "pnl": decimal_or_zero(row.get("net_realized_pnl"))})
    return rows


def review_rows(data: DashboardData) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for label, issue in _iter_review_issues(data):
        reason = str(issue.get("review_reason", "review"))
        rows.append({
            "area": label,
            "severity": review_severity(reason),
            "category": review_category(reason),
            "reason": reason,
            "security": compact_security_label(issue),
            "quantity": str(issue.get("unmatched_quantity") or issue.get("unknown_basis_quantity") or ""),
        })
    return rows


def review_category(reason: str) -> str:
    if "unknown_basis" in reason:
        return "Unknown basis"
    if "unmatched" in reason or "oversized" in reason:
        return "Unmatched quantity"
    if "ambiguous" in reason:
        return "Ambiguous identity"
    if "basis_transfer" in reason:
        return "Basis transfer"
    if "invalid" in reason or "malformed" in reason:
        return "Malformed source row"
    return "Other review"


def review_severity(reason: str) -> str:
    if "invalid" in reason or "ambiguous" in reason or "oversized" in reason:
        return "Needs review"
    if "unknown_basis" in reason or "basis_transfer" in reason:
        return "Informational"
    return "Review"


def decimal_or_zero(value: object) -> Decimal:
    parsed = parse_decimal(value)
    return parsed or Decimal("0")


def _summary_int(summary: Mapping[str, Any], field: str) -> int:
    try:
        return int(summary.get(field) or 0)
    except (TypeError, ValueError):
        return 0
