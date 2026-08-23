from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping


TRUSTED_TRADE_FIELDS = [
    "trade_id",
    "instrument_id",
    "asset_type",
    "open_date",
    "close_date",
    "holding_period_days",
    "matched_quantity",
    "cost_basis",
    "proceeds",
    "realized_pnl",
    "return_percentage",
    "confidence",
    "reason_codes",
]

EXCLUDED_REASONS = {
    "unknown_basis_closure",
    "unknown_basis",
    "oversell_empty_inventory",
    "oversell_without_available_long_lots",
    "unmatched_option_close",
    "unmatched_option_lifecycle",
    "oversized_option_close",
    "oversized_option_lifecycle",
    "unsupported_corporate_action_for_realized_pnl",
    "basis_transfer_required",
    "ambiguous_option_lifecycle_side",
    "ambiguous_option_contract_identity",
    "multiple_cusips_for_symbol_within_mapping_window",
    "invalid_trade_date",
    "invalid_close_date",
    "invalid_open_date",
    "invalid_amount",
    "invalid_quantity",
    "invalid_realized_pnl",
    "invalid_required_value",
    "realized_pnl_reconciliation_mismatch",
    "unresolved_no_candidate",
    "unresolved_ambiguous_candidates",
    "rejected_malformed",
    "rejected_future_dated",
}

LIMITED_REASONS = {
    "potential_identical_fill",
    "source_review",
}


def build_trusted_trade_dataset(
    *,
    equity_dir: str | Path,
    option_dir: str | Path,
) -> dict[str, Any]:
    equity_path = Path(equity_dir)
    option_path = Path(option_dir)
    classified: list[dict[str, str]] = []
    review_items: list[dict[str, Any]] = []

    for row in _read_csv(equity_path / "equity_lot_matches.csv"):
        trade = _classify_equity_match(row)
        classified.append(trade)
    _extend_review_items(
        review_items,
        _read_json(equity_path / "equity_lot_review.json"),
        asset_type="equity",
        source_type="equity_lot_review",
    )

    for row in _read_csv(option_path / "option_lot_matches.csv"):
        trade = _classify_option_match(row)
        classified.append(trade)
    _extend_review_items(
        review_items,
        _read_json(option_path / "option_lot_review.json"),
        asset_type="option",
        source_type="option_lot_review",
    )
    for row in _read_csv(option_path / "option_basis_transfers.csv"):
        review_items.append(_review_item_from_row(
            row,
            asset_type="option",
            source_type="option_basis_transfers",
            reason_codes=["basis_transfer_required"],
        ))

    classified.sort(key=lambda row: (
        row["close_date"],
        row["open_date"],
        row["asset_type"],
        row["trade_id"],
    ))
    trusted = [row for row in classified if row["confidence"] == "high_confidence"]
    limited = [row for row in classified if row["confidence"] == "limited_confidence"]
    excluded = [row for row in classified if row["confidence"] == "excluded"]
    trusted.sort(key=_output_sort_key)
    limited.sort(key=_output_sort_key)
    excluded.sort(key=_output_sort_key)
    review_items.extend(_excluded_trade_review(row) for row in excluded)
    review_items.sort(key=lambda row: (
        str(row.get("source_type", "")),
        str(row.get("close_date", "")),
        str(row.get("trade_id", "")),
        str(row.get("reason_codes", "")),
    ))
    return {
        "trusted_closed_trades": trusted,
        "limited_confidence_trades": limited,
        "excluded_trades": excluded,
        "coverage_summary": _coverage_summary(classified, trusted, limited, excluded),
        "exclusion_summary": _exclusion_summary(excluded, review_items),
        "review": {
            "review_count": len(review_items),
            "items": review_items,
        },
    }


def write_trusted_trade_outputs(result: Mapping[str, Any], output_dir: str | Path) -> None:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    _write_csv(destination / "trusted_closed_trades.csv", TRUSTED_TRADE_FIELDS, result["trusted_closed_trades"])
    _write_csv(destination / "limited_confidence_trades.csv", TRUSTED_TRADE_FIELDS, result["limited_confidence_trades"])
    (destination / "coverage_summary.json").write_text(
        json.dumps(result["coverage_summary"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (destination / "exclusion_summary.json").write_text(
        json.dumps(result["exclusion_summary"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (destination / "trusted_trade_review.json").write_text(
        json.dumps(result["review"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _classify_equity_match(row: Mapping[str, str]) -> dict[str, str]:
    reasons: list[str] = []
    quantity = _required_decimal(row.get("matched_quantity"), "invalid_quantity", reasons)
    cost_basis = _required_decimal(row.get("allocated_opening_cost"), "invalid_amount", reasons)
    proceeds = _required_decimal(row.get("allocated_closing_proceeds"), "invalid_amount", reasons)
    realized = _required_decimal(row.get("realized_pnl"), "invalid_realized_pnl", reasons)
    open_date = _required_date(row.get("opening_trade_date"), "invalid_open_date", reasons)
    close_date = _required_date(row.get("closing_trade_date"), "invalid_close_date", reasons)
    if quantity is not None and quantity <= 0:
        reasons.append("invalid_quantity")
    if cost_basis is not None and proceeds is not None and realized is not None and proceeds - cost_basis != realized:
        reasons.append("realized_pnl_reconciliation_mismatch")
    reasons.extend(_reason_codes(row.get("review_reason")))
    if row.get("basis_status") != "known":
        reasons.append("unknown_basis_closure")
    return _classified_row(
        row=row,
        asset_type="equity",
        security_material=row.get("security_key", ""),
        opening_event_id=row.get("opening_event_id", ""),
        closing_event_id=row.get("closing_event_id", ""),
        open_date=open_date,
        close_date=close_date,
        holding_period=row.get("holding_period_days", ""),
        quantity=quantity,
        cost_basis=cost_basis,
        proceeds=proceeds,
        realized=realized,
        return_percentage=row.get("realized_return_pct", ""),
        source_review_status=row.get("review_status", ""),
        reasons=reasons,
    )


def _classify_option_match(row: Mapping[str, str]) -> dict[str, str]:
    reasons: list[str] = []
    quantity = _required_decimal(row.get("matched_quantity"), "invalid_quantity", reasons)
    opening_cost = _optional_decimal(row.get("allocated_opening_cost"), "invalid_amount", reasons)
    opening_credit = _optional_decimal(row.get("allocated_opening_credit"), "invalid_amount", reasons)
    closing_proceeds = _optional_decimal(row.get("allocated_closing_proceeds"), "invalid_amount", reasons)
    closing_cost = _optional_decimal(row.get("allocated_closing_cost"), "invalid_amount", reasons)
    realized = _required_decimal(row.get("realized_pnl"), "invalid_realized_pnl", reasons)
    open_date = _required_date(row.get("opening_trade_date"), "invalid_open_date", reasons)
    close_date = _required_date(row.get("closing_trade_date"), "invalid_close_date", reasons)
    cost_basis = _none_to_zero(opening_cost) + _none_to_zero(closing_cost)
    proceeds = _none_to_zero(opening_credit) + _none_to_zero(closing_proceeds)
    if quantity is not None and quantity <= 0:
        reasons.append("invalid_quantity")
    if realized is not None and proceeds - cost_basis != realized:
        reasons.append("realized_pnl_reconciliation_mismatch")
    reasons.extend(_reason_codes(row.get("review_reason")))
    if row.get("basis_status") != "known":
        reasons.append("unknown_basis_closure")
    if str(row.get("basis_transfer_required") or "").lower() == "true":
        reasons.append("basis_transfer_required")
    return _classified_row(
        row=row,
        asset_type="option",
        security_material="|".join([
            row.get("security_key", ""),
            row.get("structural_key", ""),
            row.get("position_side", ""),
        ]),
        opening_event_id=row.get("opening_event_id", ""),
        closing_event_id=row.get("closing_event_id", ""),
        open_date=open_date,
        close_date=close_date,
        holding_period=row.get("holding_period_days", ""),
        quantity=quantity,
        cost_basis=cost_basis,
        proceeds=proceeds,
        realized=realized,
        return_percentage=row.get("realized_return_pct") or row.get("pnl_to_opening_credit_pct", ""),
        source_review_status=row.get("review_status", ""),
        reasons=reasons,
    )


def _classified_row(
    *,
    row: Mapping[str, str],
    asset_type: str,
    security_material: str,
    opening_event_id: str,
    closing_event_id: str,
    open_date: date | None,
    close_date: date | None,
    holding_period: str,
    quantity: Decimal | None,
    cost_basis: Decimal | None,
    proceeds: Decimal | None,
    realized: Decimal | None,
    return_percentage: str,
    source_review_status: str,
    reasons: Iterable[str],
) -> dict[str, str]:
    reason_codes = sorted(set(reason for reason in reasons if reason))
    if any(reason in EXCLUDED_REASONS or reason.startswith("invalid_") for reason in reason_codes):
        confidence = "excluded"
    elif source_review_status == "review" or any(reason in LIMITED_REASONS for reason in reason_codes):
        confidence = "limited_confidence"
        if not reason_codes:
            reason_codes.append("source_review")
    else:
        confidence = "high_confidence"
    trade_material = "|".join([asset_type, opening_event_id, closing_event_id, _date_text(close_date)])
    return {
        "trade_id": _opaque_id("trade", trade_material),
        "instrument_id": _opaque_id("instrument", security_material),
        "asset_type": asset_type,
        "open_date": _date_text(open_date),
        "close_date": _date_text(close_date),
        "holding_period_days": str(holding_period or ""),
        "matched_quantity": _decimal_text(quantity),
        "cost_basis": _decimal_text(cost_basis),
        "proceeds": _decimal_text(proceeds),
        "realized_pnl": _decimal_text(realized),
        "return_percentage": str(return_percentage or ""),
        "confidence": confidence,
        "reason_codes": "|".join(sorted(set(reason_codes))),
    }


def _coverage_summary(
    classified: list[Mapping[str, str]],
    trusted: list[Mapping[str, str]],
    limited: list[Mapping[str, str]],
    excluded: list[Mapping[str, str]],
) -> dict[str, Any]:
    total = len(classified)
    by_confidence = {
        "high_confidence": _confidence_summary(trusted, total),
        "limited_confidence": _confidence_summary(limited, total),
        "excluded": _confidence_summary(excluded, total),
    }
    by_asset_confidence: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    by_year_confidence: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for row in classified:
        by_asset_confidence[row["asset_type"]][row["confidence"]] += 1
        year = row["close_date"][:4] if row["close_date"] else "unknown"
        by_year_confidence[year][row["confidence"]] += 1
    return {
        "total_completed_matches_evaluated": total,
        "confidence": by_confidence,
        "counts_by_asset_type_and_confidence": _nested_counter(by_asset_confidence),
        "counts_by_close_year_and_confidence": _nested_counter(by_year_confidence),
        "high_confidence_pnl_reconciles_to_rows": _sum_decimal(row["realized_pnl"] for row in trusted)
        == by_confidence["high_confidence"]["realized_pnl"],
    }


def _confidence_summary(rows: list[Mapping[str, str]], total: int) -> dict[str, Any]:
    return {
        "count": len(rows),
        "percentage": _percentage(len(rows), total),
        "cost_basis": _sum_decimal(row["cost_basis"] for row in rows),
        "proceeds": _sum_decimal(row["proceeds"] for row in rows),
        "realized_pnl": _sum_decimal(row["realized_pnl"] for row in rows),
    }


def _exclusion_summary(
    excluded: list[Mapping[str, str]],
    review_items: list[Mapping[str, Any]],
) -> dict[str, Any]:
    reason_counts: Counter[str] = Counter()
    for row in excluded:
        for reason in _split_codes(row.get("reason_codes")):
            reason_counts[reason] += 1
    review_reason_counts: Counter[str] = Counter()
    for item in review_items:
        for reason in _split_codes(item.get("reason_codes")):
            review_reason_counts[reason] += 1
    return {
        "excluded_match_count": len(excluded),
        "excluded_match_counts_by_reason": dict(sorted(reason_counts.items())),
        "review_item_count": len(review_items),
        "review_item_counts_by_reason": dict(sorted(review_reason_counts.items())),
    }


def _excluded_trade_review(row: Mapping[str, str]) -> dict[str, Any]:
    return {
        "source_type": "classified_match",
        "asset_type": row["asset_type"],
        "trade_id": row["trade_id"],
        "instrument_id": row["instrument_id"],
        "open_date": row["open_date"],
        "close_date": row["close_date"],
        "matched_quantity": row["matched_quantity"],
        "reason_codes": row["reason_codes"],
    }


def _extend_review_items(
    output: list[dict[str, Any]],
    source: Any,
    *,
    asset_type: str,
    source_type: str,
) -> None:
    for item in _review_items(source):
        output.append(_review_item_from_row(
            item,
            asset_type=asset_type,
            source_type=source_type,
            reason_codes=_reason_codes(item.get("review_reason") or item.get("reason")),
        ))


def _review_item_from_row(
    row: Mapping[str, Any],
    *,
    asset_type: str,
    source_type: str,
    reason_codes: list[str],
) -> dict[str, Any]:
    close_date = str(row.get("closing_trade_date") or row.get("trade_date") or row.get("activity_date") or "")
    event_material = "|".join([
        source_type,
        str(row.get("source_row_id") or ""),
        str(row.get("opening_event_id") or ""),
        str(row.get("closing_event_id") or ""),
        close_date,
    ])
    security_material = "|".join([
        str(row.get("security_key") or ""),
        str(row.get("structural_key") or ""),
        str(row.get("position_side") or ""),
    ])
    return {
        "source_type": source_type,
        "asset_type": asset_type,
        "trade_id": _opaque_id("review", event_material),
        "instrument_id": _opaque_id("instrument", security_material),
        "close_date": close_date,
        "matched_quantity": str(row.get("matched_quantity") or ""),
        "unmatched_quantity": str(row.get("unmatched_quantity") or ""),
        "reason_codes": "|".join(sorted(set(reason_codes))),
    }


def _required_decimal(value: Any, reason: str, reasons: list[str]) -> Decimal | None:
    parsed = _parse_decimal(value)
    if parsed is None:
        reasons.append(reason)
    return parsed


def _optional_decimal(value: Any, reason: str, reasons: list[str]) -> Decimal | None:
    if value in (None, ""):
        return None
    parsed = _parse_decimal(value)
    if parsed is None:
        reasons.append(reason)
    return parsed


def _parse_decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _required_date(value: Any, reason: str, reasons: list[str]) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        reasons.append(reason)
        return None


def _none_to_zero(value: Decimal | None) -> Decimal:
    return value if value is not None else Decimal("0")


def _reason_codes(value: Any) -> list[str]:
    codes: list[str] = []
    for part in _split_codes(value):
        code = part.split(":", 1)[0].split("=", 1)[0].strip()
        if code:
            codes.append(code)
    return codes


def _split_codes(value: Any) -> list[str]:
    text = str(value or "")
    return [part.strip() for part in text.replace(";", "|").split("|") if part.strip()]


def _opaque_id(prefix: str, material: str) -> str:
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _date_text(value: date | None) -> str:
    return value.isoformat() if value is not None else ""


def _decimal_text(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value.normalize(), "f")


def _sum_decimal(values: Iterable[str]) -> str:
    total = sum((_parse_decimal(value) or Decimal("0") for value in values), Decimal("0"))
    return _decimal_text(total)


def _percentage(count: int, total: int) -> str:
    if total == 0:
        return "0"
    return _decimal_text((Decimal(count) / Decimal(total) * Decimal("100")).quantize(Decimal("0.0001")))


def _nested_counter(source: Mapping[str, Mapping[str, int]]) -> dict[str, dict[str, int]]:
    return {
        key: dict(sorted(value.items()))
        for key, value in sorted(source.items())
    }


def _output_sort_key(row: Mapping[str, str]) -> tuple[str, str, str, str]:
    return (row["close_date"], row["open_date"], row["asset_type"], row["trade_id"])


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _review_items(source: Any) -> list[Mapping[str, Any]]:
    if isinstance(source, list):
        return [item for item in source if isinstance(item, Mapping)]
    if isinstance(source, Mapping):
        for key in ("issues", "items", "review_items"):
            value = source.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, Mapping)]
    return []


def _write_csv(path: Path, fields: list[str], rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
