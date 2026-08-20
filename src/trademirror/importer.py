from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable


EXPECTED_COLUMNS = {
    "Activity Date",
    "Process Date",
    "Settle Date",
    "Instrument",
    "Description",
    "Trans Code",
    "Quantity",
    "Price",
    "Amount",
}

OPTION_CODES = {"BTO", "STC", "STO", "BTC", "OEXP", "OASGN", "OEXCS", "OCA"}

CODE_MAP: dict[str, tuple[str, str, str]] = {
    "Buy": ("trade", "buy", "equity"),
    "Sell": ("trade", "sell", "equity"),
    "BTO": ("option_trade", "buy_to_open", "option"),
    "STC": ("option_trade", "sell_to_close", "option"),
    "STO": ("option_trade", "sell_to_open", "option"),
    "BTC": ("option_trade", "buy_to_close", "option"),
    "OEXP": ("option_lifecycle", "expiration", "option"),
    "OASGN": ("option_lifecycle", "assignment", "option"),
    "OEXCS": ("option_lifecycle", "exercise", "option"),
    "OCA": ("option_lifecycle", "option_adjustment", "option"),
    "ACH": ("funding", "bank_transfer", "cash"),
    "RTP": ("funding", "instant_bank_transfer", "cash"),
    "FUTSWP": ("internal_transfer", "event_contract_transfer", "event_contract"),
    "GOLD": ("fee", "subscription_fee", "cash"),
    "INT": ("income", "cash_interest", "cash"),
    "GDBP": ("income", "deposit_boost", "cash"),
    "MINT": ("financing", "margin_interest", "cash"),
    "MTM": ("event_contract", "mark_to_market", "event_contract"),
    "MDIV": ("income", "manufactured_dividend", "equity"),
    "CDIV": ("income", "cash_dividend", "equity"),
    "AFEE": ("fee", "adr_fee", "equity"),
    "DFEE": ("fee", "depositary_fee", "equity"),
    "CIL": ("corporate_action", "cash_in_lieu", "equity"),
    "SPR": ("corporate_action", "split_or_reorganization", "equity"),
    "MRGS": ("corporate_action", "merger", "equity"),
    "BC": ("corporate_action", "basis_change", "equity"),
    "SS": ("corporate_action", "stock_split", "equity"),
    "REC": ("corporate_action", "reclassification", "equity"),
    "SXCH": ("corporate_action", "security_exchange", "equity"),
    "WRLS": ("corporate_action", "worthless_security", "equity"),
}

DATE_FORMATS = ("%m/%d/%Y", "%Y-%m-%d")
OPTION_RE = re.compile(
    r"(?:Option\s+(?:Expiration|Assignment|Exercise)\s+for\s+)?"
    r"(?P<underlying>[A-Z0-9.\-]+)\s+"
    r"(?P<expiration>\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(?P<option_type>Call|Put)\s+\$(?P<strike>[\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)
CUSIP_RE = re.compile(r"CUSIP:\s*([A-Z0-9]{9})", re.IGNORECASE)
BANK_ACCOUNT_ENDING_RE = re.compile(
    r"((?:bank[ \t]+)?account[ \t]+ending[ \t]+(?:in[ \t]+)?)(?:x{0,4})\d{4}\b",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
SSN_ITIN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
ACCOUNT_LABEL_RE = re.compile(
    r"\b((?:Individual[ \t]+)?Account[ \t]+(?:Number|No\.|#)[ \t]*[:#]?[ \t]*)",
    re.IGNORECASE,
)
DATE_TOKEN_RE = re.compile(r"(?:\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})\b")
ACCOUNT_ID_TOKEN_RE = re.compile(r"(?=.*\d)[A-Z0-9]+(?:-[A-Z0-9]+)*\b", re.IGNORECASE)
ALPHA_TOKEN_RE = re.compile(r"[A-Z]+\b", re.IGNORECASE)
LINE_TOKEN_RE = re.compile(r"[^ \t\r\n]+")

CANONICAL_FIELDS = [
    "source_row_id",
    "source_line_number",
    "activity_date",
    "process_date",
    "settle_date",
    "instrument",
    "description_raw",
    "description_sanitized",
    "cusip",
    "transaction_code_raw",
    "transaction_family",
    "event_type",
    "asset_type",
    "quantity_raw",
    "quantity_numeric",
    "quantity_suffix",
    "price",
    "amount",
    "cash_flow_direction",
    "external_cash_flow",
    "internal_transfer",
    "option_underlying",
    "option_expiration",
    "option_type",
    "option_strike",
    "potential_duplicate_group",
    "duplicate_group_size",
    "review_status",
    "review_reasons",
    "raw_row_json",
]


def _clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _parse_date(value: str) -> tuple[str, bool]:
    value = _clean(value)
    if not value:
        return "", True
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(value, fmt).date().isoformat(), True
        except ValueError:
            continue
    return value, False


def _parse_decimal(value: str) -> tuple[Decimal | None, bool]:
    value = _clean(value)
    if not value:
        return None, True
    negative = value.startswith("(") and value.endswith(")")
    normalized = value.strip("()").replace("$", "").replace(",", "")
    try:
        number = Decimal(normalized)
    except InvalidOperation:
        return None, False
    return (-number if negative else number), True


def _decimal_text(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value, "f")


def _parse_quantity(value: str) -> tuple[Decimal | None, str, bool]:
    raw = _clean(value)
    if not raw:
        return None, "", True
    match = re.fullmatch(r"([+-]?[\d,]+(?:\.\d+)?)([A-Za-z]+)?", raw)
    if not match:
        return None, "", False
    try:
        return Decimal(match.group(1).replace(",", "")), match.group(2) or "", True
    except InvalidOperation:
        return None, match.group(2) or "", False


def _parse_option(description: str) -> dict[str, str] | None:
    match = OPTION_RE.search(" ".join(description.split()))
    if not match:
        return None
    expiration, valid = _parse_date(match.group("expiration"))
    if not valid:
        return None
    strike, valid = _parse_decimal(match.group("strike"))
    if not valid or strike is None:
        return None
    return {
        "option_underlying": match.group("underlying").upper(),
        "option_expiration": expiration,
        "option_type": match.group("option_type").lower(),
        "option_strike": _decimal_text(strike),
    }


def sanitize_description(description: str) -> str:
    sanitized = BANK_ACCOUNT_ENDING_RE.sub(r"\1XXXX", description)
    sanitized = EMAIL_RE.sub("[REDACTED_EMAIL]", sanitized)
    sanitized = SSN_ITIN_RE.sub("[REDACTED_TAX_ID]", sanitized)
    return _sanitize_labeled_account_ids(sanitized)


def _sanitize_labeled_account_ids(description: str) -> str:
    def redact_line(line: str) -> str:
        output = []
        position = 0
        for match in ACCOUNT_LABEL_RE.finditer(line):
            if match.start() < position:
                continue
            identifier_end = _labeled_account_identifier_end(line, match.end())
            if identifier_end is None:
                continue
            output.append(line[position:match.end()])
            output.append("[REDACTED]")
            position = identifier_end
        output.append(line[position:])
        return "".join(output)

    return "".join(redact_line(line) for line in description.splitlines(keepends=True))


def _labeled_account_identifier_end(line: str, start: int) -> int | None:
    """Return the end offset of a same-line account ID after an account label."""
    tokens = list(LINE_TOKEN_RE.finditer(line, start))
    consumed_end: int | None = None
    token_index = 0

    if (
        len(tokens) >= 2
        and ALPHA_TOKEN_RE.fullmatch(tokens[0].group(0))
        and ACCOUNT_ID_TOKEN_RE.fullmatch(tokens[1].group(0))
        and not DATE_TOKEN_RE.match(line, tokens[1].start())
    ):
        consumed_end = tokens[1].end()
        token_index = 2

    while token_index < len(tokens):
        token = tokens[token_index]
        if DATE_TOKEN_RE.match(line, token.start()):
            break
        if not ACCOUNT_ID_TOKEN_RE.fullmatch(token.group(0)):
            break
        consumed_end = token.end()
        token_index += 1

    return consumed_end


def _fingerprint(record: dict[str, Any]) -> str:
    keys = (
        "activity_date",
        "process_date",
        "settle_date",
        "instrument",
        "description_raw",
        "transaction_code_raw",
        "quantity_raw",
        "price",
        "amount",
    )
    payload = "\x1f".join(str(record.get(key, "")) for key in keys)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def _is_blank_row(row: dict[str, Any]) -> bool:
    return not any(_clean(row.get(column)) for column in EXPECTED_COLUMNS)


def import_robinhood_csv(path: str | Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Import a Robinhood activity CSV without silently dropping source records."""
    source = Path(path)
    records: list[dict[str, Any]] = []
    blank_rows = 0
    privacy_redactions = 0
    code_counts: Counter[str] = Counter()

    with source.open("r", newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        headers = set(reader.fieldnames or [])
        missing = EXPECTED_COLUMNS - headers
        if missing:
            raise ValueError(f"Missing required Robinhood columns: {sorted(missing)}")

        source_row_id = 0
        for row in reader:
            if _is_blank_row(row):
                blank_rows += 1
                continue
            source_row_id += 1
            reasons: list[str] = []
            activity_date, activity_valid = _parse_date(row.get("Activity Date", ""))
            process_date, process_valid = _parse_date(row.get("Process Date", ""))
            settle_date, settle_valid = _parse_date(row.get("Settle Date", ""))
            if not activity_valid or not activity_date:
                reasons.append("invalid_or_missing_activity_date")
            if not process_valid:
                reasons.append("invalid_process_date")
            if not settle_valid:
                reasons.append("invalid_settle_date")

            quantity, quantity_suffix, quantity_valid = _parse_quantity(row.get("Quantity", ""))
            price, price_valid = _parse_decimal(row.get("Price", ""))
            amount, amount_valid = _parse_decimal(row.get("Amount", ""))
            if not quantity_valid:
                reasons.append("quantity_parse_error")
            if not price_valid:
                reasons.append("price_parse_error")
            if not amount_valid:
                reasons.append("amount_parse_error")

            code = _clean(row.get("Trans Code"))
            code_counts[code] += 1
            family, event_type, asset_type = CODE_MAP.get(
                code, ("unknown", "unknown", "unknown")
            )
            if code not in CODE_MAP:
                reasons.append("unknown_transaction_code")

            if code in {"ACH", "RTP"}:
                event_type = "deposit" if amount is not None and amount > 0 else "withdrawal"

            description_raw = _clean(row.get("Description"))
            description_sanitized = sanitize_description(description_raw)
            if description_sanitized != description_raw:
                privacy_redactions += 1

            option_fields = {
                "option_underlying": "",
                "option_expiration": "",
                "option_type": "",
                "option_strike": "",
            }
            if code in OPTION_CODES:
                parsed_option = _parse_option(description_raw)
                if parsed_option:
                    option_fields.update(parsed_option)
                else:
                    reasons.append("option_parse_error")

            cusip_match = CUSIP_RE.search(description_raw)
            cash_direction = (
                "inflow" if amount is not None and amount > 0
                else "outflow" if amount is not None and amount < 0
                else "none"
            )
            record: dict[str, Any] = {
                "source_row_id": source_row_id,
                "source_line_number": reader.line_num,
                "activity_date": activity_date,
                "process_date": process_date,
                "settle_date": settle_date,
                "instrument": _clean(row.get("Instrument")),
                "description_raw": description_raw,
                "description_sanitized": description_sanitized,
                "cusip": cusip_match.group(1).upper() if cusip_match else "",
                "transaction_code_raw": code,
                "transaction_family": family,
                "event_type": event_type,
                "asset_type": asset_type,
                "quantity_raw": _clean(row.get("Quantity")),
                "quantity_numeric": _decimal_text(quantity),
                "quantity_suffix": quantity_suffix,
                "price": _decimal_text(price),
                "amount": _decimal_text(amount),
                "cash_flow_direction": cash_direction,
                "external_cash_flow": code in {"ACH", "RTP"},
                "internal_transfer": code == "FUTSWP",
                **option_fields,
                "potential_duplicate_group": "",
                "duplicate_group_size": 1,
                "review_status": "validated",
                "review_reasons": "",
                "raw_row_json": json.dumps(row, ensure_ascii=False, sort_keys=True),
                "_review_reasons": reasons,
            }
            record["_fingerprint"] = _fingerprint(record)
            records.append(record)

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[record["_fingerprint"]].append(record)

    duplicate_groups = 0
    duplicate_rows = 0
    for fingerprint, group in groups.items():
        if len(group) <= 1:
            continue
        duplicate_groups += 1
        duplicate_rows += len(group)
        for record in group:
            record["potential_duplicate_group"] = fingerprint
            record["duplicate_group_size"] = len(group)
            record["_review_reasons"].append("potential_identical_fill")

    review_reason_counts: Counter[str] = Counter()
    asset_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    dates = []
    option_rows = 0
    parsed_option_rows = 0
    for record in records:
        reasons = sorted(set(record.pop("_review_reasons")))
        record.pop("_fingerprint", None)
        record["review_reasons"] = "|".join(reasons)
        record["review_status"] = "review" if reasons else "validated"
        review_reason_counts.update(reasons)
        asset_counts[record["asset_type"]] += 1
        family_counts[record["transaction_family"]] += 1
        if record["activity_date"]:
            dates.append(record["activity_date"])
        if record["transaction_code_raw"] in OPTION_CODES:
            option_rows += 1
            if record["option_underlying"]:
                parsed_option_rows += 1

    report = {
        "source_file": source.name,
        "input_records": len(records) + blank_rows,
        "canonical_records": len(records),
        "blank_records_skipped": blank_rows,
        "date_range": {"start": min(dates) if dates else None, "end": max(dates) if dates else None},
        "transaction_code_counts": dict(sorted(code_counts.items())),
        "transaction_family_counts": dict(sorted(family_counts.items())),
        "asset_type_counts": dict(sorted(asset_counts.items())),
        "option_rows": option_rows,
        "option_rows_parsed": parsed_option_rows,
        "option_parse_rate": (parsed_option_rows / option_rows if option_rows else 1.0),
        "quantity_suffix_rows": sum(bool(record["quantity_suffix"]) for record in records),
        "privacy_sensitive_descriptions_sanitized": privacy_redactions,
        "potential_duplicate_groups": duplicate_groups,
        "potential_duplicate_rows": duplicate_rows,
        "review_rows": sum(record["review_status"] == "review" for record in records),
        "review_reason_counts": dict(sorted(review_reason_counts.items())),
    }
    return records, report


def write_canonical_csv(
    records: Iterable[dict[str, Any]], path: str | Path, *, include_raw: bool = False
) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fields = CANONICAL_FIELDS if include_raw else [
        field for field in CANONICAL_FIELDS if field not in {"description_raw", "raw_row_json"}
    ]
    with destination.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(records)


def write_report(report: dict[str, Any], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
