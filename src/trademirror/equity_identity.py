from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable, Mapping


DEFAULT_EQUITY_MAPPING_WINDOW_DAYS = 90

ACCEPTED_EQUITY_ANCHOR_STATUSES = {
    "direct_cusip_accepted",
    "unique_symbol_to_cusip_mapped",
    "symbol_only_retained_canonical_lacks_cusip",
}


def normalize_equity_symbol(value: Any) -> str:
    return str(value or "").strip().upper()


def normalize_cusip(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text if is_valid_cusip(text) else ""


def is_valid_cusip(value: Any) -> bool:
    text = str(value or "").strip().upper()
    if len(text) != 9:
        return False
    if not text[-1].isdigit():
        return False
    if any(_cusip_char_value(char) is None for char in text[:8]):
        return False
    if text in {"000000000", "999999999"}:
        return False
    total = 0
    for index, char in enumerate(text[:8]):
        value = _cusip_char_value(char)
        if value is None:
            return False
        if index % 2 == 1:
            value *= 2
        total += value // 10 + value % 10
    check_digit = (10 - (total % 10)) % 10
    return check_digit == int(text[-1])


def equity_security_key(*, cusip: Any = "", symbol: Any = "") -> str:
    normalized_cusip = normalize_cusip(cusip)
    normalized_symbol = normalize_equity_symbol(symbol)
    return f"equity:{normalized_cusip}" if normalized_cusip else f"equity-symbol:{normalized_symbol}"


def is_accepted_equity_anchor_status(status: Any) -> bool:
    return str(status or "") in ACCEPTED_EQUITY_ANCHOR_STATUSES


def resolve_equity_anchors(
    anchors: Iterable[Mapping[str, Any]],
    records: Iterable[Mapping[str, Any]],
    *,
    as_of: date | None = None,
    max_forward_days: int = DEFAULT_EQUITY_MAPPING_WINDOW_DAYS,
) -> dict[str, Any]:
    anchor_list = [dict(anchor) for anchor in anchors]
    record_list = list(records)
    resolved: list[dict[str, Any]] = []
    report: list[dict[str, str]] = []
    for index, anchor in enumerate(anchor_list, start=1):
        if _asset_type(anchor) != "equity":
            resolved.append(anchor)
            continue
        resolved_anchor, report_item = resolve_equity_anchor(
            anchor,
            record_list,
            anchor_index=index,
            as_of=as_of,
            max_forward_days=max_forward_days,
        )
        resolved.append(resolved_anchor)
        report.append(report_item)
    return {"anchors": resolved, "report": report}


def resolve_equity_anchor(
    anchor: Mapping[str, Any],
    records: Iterable[Mapping[str, Any]],
    *,
    anchor_index: int = 1,
    as_of: date | None = None,
    max_forward_days: int = DEFAULT_EQUITY_MAPPING_WINDOW_DAYS,
) -> tuple[dict[str, Any], dict[str, str]]:
    anchor_copy = dict(anchor)
    symbol = normalize_equity_symbol(anchor.get("symbol") or anchor.get("primary_symbol"))
    supplied_cusip = str(anchor.get("cusip") or "").strip()
    cusip = normalize_cusip(supplied_cusip)
    anchor_date, date_valid = _parse_date(anchor.get("anchor_date") or anchor.get("date"))
    base_report = {
        "anchor_index": str(anchor_index),
        "asset_type": "equity",
        "original_symbol": symbol,
        "original_cusip": cusip,
        "resolved_symbol": symbol,
        "resolved_cusip": cusip,
        "original_security_key": equity_security_key(cusip=cusip, symbol=symbol),
        "resolved_security_key": equity_security_key(cusip=cusip, symbol=symbol),
        "mapping_window_days": str(max_forward_days),
    }
    if not date_valid or anchor_date is None:
        return _annotate_anchor(anchor_copy, base_report, "rejected_malformed", "invalid_anchor_date")
    if as_of is not None and anchor_date > as_of:
        return _annotate_anchor(anchor_copy, base_report, "rejected_future_dated", "future_anchor_after_as_of")
    if supplied_cusip and not cusip:
        return _annotate_anchor(anchor_copy, base_report, "rejected_malformed", "invalid_anchor_cusip")
    if cusip:
        anchor_copy["symbol"] = symbol
        anchor_copy["cusip"] = cusip
        report = {
            **base_report,
            "status": "direct_cusip_accepted",
            "confidence": "high_cusip_direct",
            "resolution_reason": "anchor_supplied_cusip",
        }
        return _with_resolution(anchor_copy, report), report

    if not symbol:
        return _annotate_anchor(anchor_copy, base_report, "rejected_malformed", "missing_equity_symbol")

    candidates, symbol_record_count = _cusip_candidates_for_symbol(
        records,
        symbol=symbol,
        anchor_date=anchor_date,
        as_of=as_of,
        max_forward_days=max_forward_days,
    )
    if len(candidates) == 1:
        resolved_cusip = next(iter(candidates))
        anchor_copy["symbol"] = symbol
        anchor_copy["cusip"] = resolved_cusip
        report = {
            **base_report,
            "resolved_cusip": resolved_cusip,
            "resolved_security_key": equity_security_key(cusip=resolved_cusip, symbol=symbol),
            "status": "unique_symbol_to_cusip_mapped",
            "confidence": "resolved_symbol_to_cusip",
            "resolution_reason": "unique_cusip_for_symbol_within_mapping_window",
        }
        return _with_resolution(anchor_copy, report), report
    if len(candidates) > 1:
        return _annotate_anchor(
            anchor_copy,
            base_report,
            "unresolved_ambiguous_candidates",
            "multiple_cusips_for_symbol_within_mapping_window",
        )
    if symbol_record_count > 0:
        return _annotate_anchor(
            anchor_copy,
            base_report,
            "symbol_only_retained_canonical_lacks_cusip",
            "canonical_records_for_symbol_lack_cusip_in_mapping_window",
        )
    return _annotate_anchor(anchor_copy, base_report, "unresolved_no_candidate", "no_symbol_records_in_mapping_window")


def anchor_resolution_review_issues(report: Iterable[Mapping[str, str]]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    for item in report:
        status = item.get("status", "")
        if not (status.startswith("unresolved_") or status.startswith("rejected_")):
            continue
        issues.append({
            "anchor_index": item.get("anchor_index", ""),
            "security_key": item.get("resolved_security_key") or item.get("original_security_key", ""),
            "review_reason": item.get("resolution_reason") or status,
            "anchor_resolution_status": status,
            "original_symbol": item.get("original_symbol", ""),
            "resolved_security_key": item.get("resolved_security_key", ""),
        })
    return issues


def compare_statement_positions(
    statement_positions: Iterable[Mapping[str, Any]],
    calculated_positions: Iterable[Mapping[str, Any]],
    canonical_records: Iterable[Mapping[str, Any]],
    *,
    statement_date: date,
    max_forward_days: int = DEFAULT_EQUITY_MAPPING_WINDOW_DAYS,
) -> dict[str, Any]:
    statement_anchors = []
    for position in statement_positions:
        statement_anchors.append({
            "asset_type": "equity",
            "anchor_date": statement_date.isoformat(),
            "symbol": position.get("symbol") or position.get("primary_symbol") or position.get("instrument") or "",
            "cusip": position.get("cusip") or "",
            "quantity": position.get("quantity") or position.get("verified_quantity") or "0",
        })
    resolution = resolve_equity_anchors(
        statement_anchors,
        canonical_records,
        as_of=statement_date + timedelta(days=max_forward_days),
        max_forward_days=max_forward_days,
    )
    calculated_by_key = {str(row.get("security_key") or ""): row for row in calculated_positions}
    matched = 0
    missing = 0
    quantity_mismatch = 0
    matched_keys: set[str] = set()
    details: list[dict[str, str]] = []
    for anchor, report_item in zip(resolution["anchors"], resolution["report"]):
        if not is_accepted_equity_anchor_status(report_item.get("status")):
            missing += 1
            details.append({
                "security_key": report_item.get("resolved_security_key") or report_item.get("original_security_key", ""),
                "status": "missing",
                "resolution_status": report_item["status"],
            })
            continue
        key = equity_security_key(cusip=anchor.get("cusip"), symbol=anchor.get("symbol") or anchor.get("primary_symbol"))
        expected = _parse_decimal(anchor.get("quantity") or anchor.get("verified_quantity"))
        calculated = calculated_by_key.get(key)
        if calculated is None:
            missing += 1
            details.append({"security_key": key, "status": "missing", "resolution_status": report_item["status"]})
            continue
        matched += 1
        matched_keys.add(key)
        actual = _parse_decimal(calculated.get("trade_date_quantity") or calculated.get("quantity") or "0")
        if expected is None or actual is None or expected != actual:
            quantity_mismatch += 1
            details.append({"security_key": key, "status": "quantity_mismatch", "resolution_status": report_item["status"]})
    extra = len([key for key in calculated_by_key if key not in matched_keys])
    return {
        "matched_count": matched,
        "missing_count": missing,
        "extra_count": extra,
        "quantity_mismatch_count": quantity_mismatch,
        "anchor_resolution_report": resolution["report"],
        "details": details,
    }


def _cusip_candidates_for_symbol(
    records: Iterable[Mapping[str, Any]],
    *,
    symbol: str,
    anchor_date: date,
    as_of: date | None,
    max_forward_days: int,
) -> tuple[set[str], int]:
    end_date = anchor_date + timedelta(days=max_forward_days)
    if as_of is not None:
        end_date = min(end_date, as_of)
    candidates: set[str] = set()
    matching_symbol_rows = 0
    for record in records:
        if _asset_type(record) != "equity":
            continue
        if normalize_equity_symbol(record.get("instrument")) != symbol:
            continue
        record_date, valid = _parse_date(record.get("activity_date"))
        if not valid or record_date is None or record_date < anchor_date or record_date > end_date:
            continue
        matching_symbol_rows += 1
        cusip = normalize_cusip(record.get("cusip"))
        if cusip:
            candidates.add(cusip)
    return candidates, matching_symbol_rows


def _annotate_anchor(
    anchor: dict[str, Any],
    base_report: Mapping[str, str],
    status: str,
    reason: str,
) -> tuple[dict[str, Any], dict[str, str]]:
    symbol = normalize_equity_symbol(anchor.get("symbol") or anchor.get("primary_symbol"))
    anchor["symbol"] = symbol
    anchor["cusip"] = base_report.get("resolved_cusip", "")
    report = {
        **base_report,
        "status": status,
        "confidence": "lower_symbol_only" if symbol else "review",
        "resolution_reason": reason,
    }
    return _with_resolution(anchor, report), report


def _with_resolution(anchor: dict[str, Any], report: Mapping[str, str]) -> dict[str, Any]:
    anchor["original_symbol"] = report.get("original_symbol", "")
    anchor["original_cusip"] = report.get("original_cusip", "")
    anchor["resolved_security_key"] = report.get("resolved_security_key", "")
    anchor["anchor_resolution_status"] = report.get("status", "")
    anchor["anchor_resolution_reason"] = report.get("resolution_reason", "")
    anchor["resolved_identity_confidence"] = report.get("confidence", "")
    return anchor


def _cusip_char_value(char: str) -> int | None:
    if char.isdigit():
        return int(char)
    if "A" <= char <= "Z":
        return ord(char) - ord("A") + 10
    if char == "*":
        return 36
    if char == "@":
        return 37
    if char == "#":
        return 38
    return None


def _asset_type(row: Mapping[str, Any]) -> str:
    return str(row.get("asset_type") or "equity").strip().lower()


def _parse_date(value: Any) -> tuple[date | None, bool]:
    if isinstance(value, date):
        return value, True
    text = str(value or "").strip()
    if not text:
        return None, False
    try:
        return date.fromisoformat(text), True
    except ValueError:
        return None, False


def _parse_decimal(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value).strip())
    except (InvalidOperation, ValueError):
        return None


