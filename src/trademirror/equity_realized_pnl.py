from __future__ import annotations

import csv
import json
from collections import defaultdict, deque
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping

from .position_ledger import _parse_anchors, _quantity, _security_identity


MATCH_FIELDS = [
    "security_key",
    "symbol",
    "identity_confidence",
    "opening_event_id",
    "closing_event_id",
    "opening_trade_date",
    "closing_trade_date",
    "opening_settle_date",
    "closing_settle_date",
    "matched_quantity",
    "allocated_opening_cost",
    "allocated_closing_proceeds",
    "realized_pnl",
    "realized_return_pct",
    "holding_period_days",
    "basis_status",
    "review_status",
    "review_reason",
]

OPEN_LOT_FIELDS = [
    "security_key",
    "symbol",
    "identity_confidence",
    "opening_event_id",
    "opening_trade_date",
    "opening_settle_date",
    "remaining_quantity",
    "remaining_cost",
    "basis_status",
    "review_status",
    "review_reason",
]

BY_SECURITY_FIELDS = [
    "security_key",
    "symbol",
    "identity_confidence",
    "realized_gain",
    "realized_loss",
    "net_realized_pnl",
    "winning_matches",
    "losing_matches",
    "break_even_matches",
    "unknown_basis_quantity",
    "unmatched_quantity",
]

REVIEW_REASON_CORPORATE_ACTION = "unsupported_corporate_action_for_realized_pnl"
METHODOLOGY_NOTE = (
    "Analytical FIFO only. Results may differ from Robinhood tax documents "
    "because tax-lot elections, wash sales, and basis adjustments are not implemented."
)


def build_equity_realized_pnl(
    records: Iterable[Mapping[str, Any]],
    *,
    as_of: date | None = None,
    anchors: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    parsed_anchors, anchor_review = _parse_anchors(anchors, as_of=as_of)
    review: list[dict[str, str]] = [
        _anchor_review_issue(issue) for issue in anchor_review
    ]
    lots: dict[str, deque[dict[str, Any]]] = defaultdict(deque)
    security_info: dict[str, dict[str, str]] = {}
    matches: list[dict[str, Any]] = []
    unmatched_quantities: dict[str, Decimal] = defaultdict(Decimal)

    applicable_anchors = sorted(
        (
            anchor for anchor in parsed_anchors
            if anchor["asset_type"] == "equity"
            and (as_of is None or anchor["anchor_date"] <= as_of)
        ),
        key=lambda item: (item["anchor_date"], item["security_key"]),
    )
    next_anchor = 0
    applied_anchors: set[tuple[str, date]] = set()
    ordered_records = sorted(
        records,
        key=lambda record: (
            str(record.get("activity_date") or ""),
            int(record.get("source_row_id") or 0),
        ),
    )

    for record in ordered_records:
        activity_date, activity_valid = _parse_date(record.get("activity_date"))
        if activity_valid:
            next_anchor = _apply_eligible_anchors(
                lots,
                security_info,
                applicable_anchors,
                applied_anchors,
                next_anchor,
                activity_date,
            )

        parsed = _parse_equity_trade(record, as_of=as_of)
        if parsed is None:
            if _is_unsupported_corporate_action(record, activity_valid, activity_date, as_of):
                review.append(_record_review_issue(record, REVIEW_REASON_CORPORATE_ACTION))
            continue
        if parsed["review_reason"]:
            review.append(_record_review_issue(record, parsed["review_reason"], parsed))
            continue

        security_info[parsed["security_key"]] = _security_context(parsed)
        if parsed["event_type"] == "buy":
            lots[parsed["security_key"]].append(_opening_lot(parsed))
        elif parsed["event_type"] == "sell":
            sale_matches, sale_review = _match_sale_fifo(lots[parsed["security_key"]], parsed)
            matches.extend(sale_matches)
            if sale_review:
                review.append(_oversell_review_issue(record, sale_review, parsed))
                unmatched_quantities[parsed["security_key"]] += sale_review["unmatched_quantity"]
            if sale_matches and any(match["basis_status"] == "unknown" for match in sale_matches):
                review.append(_unknown_basis_review_issue(record, parsed, sale_matches))

    _apply_eligible_anchors(
        lots,
        security_info,
        applicable_anchors,
        applied_anchors,
        next_anchor,
        as_of,
    )

    open_lots = _open_lot_rows(lots, security_info)
    match_rows = [_format_match(match) for match in matches]
    by_security = _realized_by_security(match_rows, security_info, unmatched_quantities)
    summary = _summary(match_rows, by_security, open_lots, review, parsed_anchors)
    return {
        "matches": match_rows,
        "open_lots": open_lots,
        "realized_by_security": by_security,
        "summary": summary,
        "review": {
            "review_count": len(review),
            "issues": review,
        },
    }


def write_equity_realized_pnl_outputs(result: Mapping[str, Any], output_dir: str | Path) -> None:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    _write_csv(destination / "equity_lot_matches.csv", MATCH_FIELDS, result["matches"])
    _write_csv(destination / "equity_open_lots.csv", OPEN_LOT_FIELDS, result["open_lots"])
    _write_csv(destination / "equity_realized_by_security.csv", BY_SECURITY_FIELDS, result["realized_by_security"])
    (destination / "equity_realized_summary.json").write_text(
        json.dumps(result["summary"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (destination / "equity_lot_review.json").write_text(
        json.dumps(result["review"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _parse_equity_trade(record: Mapping[str, Any], *, as_of: date | None) -> dict[str, Any] | None:
    if str(record.get("asset_type") or "") != "equity":
        return None
    if str(record.get("transaction_family") or "") != "trade":
        return None
    event_type = str(record.get("event_type") or "")
    if event_type not in {"buy", "sell"}:
        return None

    source_review_reason = "|".join(_split_reasons(record.get("review_reasons")))
    reasons: list[str] = []
    activity_date, activity_valid = _parse_date(record.get("activity_date"))
    settle_date, settle_valid = _parse_date(record.get("settle_date"))
    quantity, quantity_valid = _parse_decimal(record.get("quantity_numeric"))
    amount, amount_valid = _parse_decimal(record.get("amount"))
    if not activity_valid:
        reasons.append("invalid_or_missing_activity_date")
    settlement_review_reason = ""
    if str(record.get("settle_date") or "").strip() and not settle_valid:
        settlement_review_reason = "invalid_settle_date_metadata"
    if not quantity_valid or quantity is None or quantity <= 0:
        reasons.append("invalid_or_missing_quantity")
    if not amount_valid or amount is None:
        reasons.append("invalid_or_missing_amount")
    if activity_valid and as_of is not None and activity_date > as_of:
        return None

    identity = _security_identity(record)
    reasons.extend(identity["review_reasons"])
    if not identity["valid"]:
        reasons.append("missing_security_identity")

    cost = Decimal("0")
    proceeds = Decimal("0")
    if amount_valid and amount is not None:
        if event_type == "buy":
            if amount >= 0:
                reasons.append("buy_amount_not_outflow")
            cost = -amount
        else:
            if amount <= 0:
                reasons.append("sell_amount_not_inflow")
            proceeds = amount

    return {
        "source_row_id": str(record.get("source_row_id", "")),
        "activity_date": activity_date,
        "settle_date": settle_date,
        "transaction_code": str(record.get("transaction_code_raw") or ""),
        "event_type": event_type,
        "security_key": identity["security_key"],
        "symbol": identity["primary_symbol"],
        "identity_confidence": identity["confidence"],
        "quantity": quantity or Decimal("0"),
        "amount": amount or Decimal("0"),
        "cost": cost,
        "proceeds": proceeds,
        "review_reason": "|".join(sorted(set(reasons))),
        "source_review_reason": "|".join(
            reason for reason in (source_review_reason, settlement_review_reason)
            if reason
        ),
    }


def _match_sale_fifo(
    lots: deque[dict[str, Any]],
    sale: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    available = sum((lot["remaining_quantity"] for lot in lots), Decimal("0"))
    remaining = sale["quantity"]
    sale_original_quantity = sale["quantity"]
    rows: list[dict[str, Any]] = []
    while remaining > 0 and lots:
        lot = lots[0]
        matched = min(remaining, lot["remaining_quantity"])
        proceeds = _allocate(sale["proceeds"], matched, sale_original_quantity)
        basis_status = lot["basis_status"]
        allocated_cost: Decimal | None = None
        realized_pnl: Decimal | None = None
        realized_return_pct: Decimal | None = None
        if basis_status == "known":
            allocated_cost = _allocate(lot["remaining_cost"], matched, lot["remaining_quantity"])
            realized_pnl = proceeds - allocated_cost
            if allocated_cost != 0:
                realized_return_pct = (realized_pnl / allocated_cost) * Decimal("100")

        rows.append({
            "security_key": sale["security_key"],
            "symbol": sale["symbol"],
            "identity_confidence": sale["identity_confidence"],
            "opening_event_id": lot["opening_event_id"],
            "closing_event_id": sale["source_row_id"],
            "opening_trade_date": lot["opening_trade_date"],
            "closing_trade_date": sale["activity_date"],
            "opening_settle_date": lot["opening_settle_date"],
            "closing_settle_date": sale["settle_date"],
            "matched_quantity": matched,
            "allocated_opening_cost": allocated_cost,
            "allocated_closing_proceeds": proceeds,
            "realized_pnl": realized_pnl,
            "realized_return_pct": realized_return_pct,
            "holding_period_days": (
                (sale["activity_date"] - lot["opening_trade_date"]).days
                if lot["opening_trade_date"] is not None and sale["activity_date"] is not None
                else None
            ),
            "basis_status": basis_status,
            "review_status": _match_review_status(lot, sale, basis_status),
            "review_reason": _match_review_reason(lot, sale, basis_status),
        })

        if basis_status == "known":
            lot["remaining_cost"] -= allocated_cost
        lot["remaining_quantity"] -= matched
        remaining -= matched
        if lot["remaining_quantity"] == 0:
            lots.popleft()
    if remaining > 0:
        reason = (
            "oversell_empty_inventory"
            if available == 0
            else "oversell_without_available_long_lots"
        )
        return rows, {
            "reason": reason,
            "available_quantity": available,
            "unmatched_quantity": remaining,
        }
    return rows, None


def _apply_anchor(
    lots: dict[str, deque[dict[str, Any]]],
    security_info: dict[str, dict[str, str]],
    anchor: Mapping[str, Any],
) -> None:
    if anchor["quantity"] <= 0:
        lots[anchor["security_key"]] = deque()
        return
    lots[anchor["security_key"]] = deque([
        {
            "security_key": anchor["security_key"],
            "symbol": anchor["primary_symbol"],
            "identity_confidence": anchor["confidence"],
            "opening_event_id": f"anchor:{anchor['anchor_date'].isoformat()}",
            "opening_trade_date": anchor["anchor_date"],
            "opening_settle_date": None,
            "remaining_quantity": anchor["quantity"],
            "remaining_cost": None,
            "basis_status": "unknown",
            "review_status": "review",
            "review_reason": "unknown_basis_from_position_anchor",
        }
    ])
    security_info[anchor["security_key"]] = {
        "security_key": anchor["security_key"],
        "symbol": anchor["primary_symbol"],
        "identity_confidence": anchor["confidence"],
    }


def _apply_eligible_anchors(
    lots: dict[str, deque[dict[str, Any]]],
    security_info: dict[str, dict[str, str]],
    anchors: list[Mapping[str, Any]],
    applied_anchors: set[tuple[str, date]],
    start_index: int,
    through_date: date | None,
) -> int:
    index = start_index
    while (
        index < len(anchors)
        and (through_date is None or anchors[index]["anchor_date"] <= through_date)
    ):
        anchor = anchors[index]
        anchor_key = (anchor["security_key"], anchor["anchor_date"])
        if anchor_key not in applied_anchors:
            _apply_anchor(lots, security_info, anchor)
            applied_anchors.add(anchor_key)
        index += 1
    return index


def _opening_lot(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "security_key": event["security_key"],
        "symbol": event["symbol"],
        "identity_confidence": event["identity_confidence"],
        "opening_event_id": event["source_row_id"],
        "opening_trade_date": event["activity_date"],
        "opening_settle_date": event["settle_date"],
        "remaining_quantity": event["quantity"],
        "remaining_cost": event["cost"],
        "basis_status": "known",
        "review_status": "review" if event["source_review_reason"] else "validated",
        "review_reason": event["source_review_reason"],
    }


def _match_review_status(
    lot: Mapping[str, Any],
    sale: Mapping[str, Any],
    basis_status: str,
) -> str:
    return "review" if _match_review_reason(lot, sale, basis_status) else "validated"


def _match_review_reason(
    lot: Mapping[str, Any],
    sale: Mapping[str, Any],
    basis_status: str,
) -> str:
    reasons = []
    if basis_status == "unknown":
        reasons.append("unknown_basis_from_position_anchor")
    if lot.get("review_reason"):
        reasons.append(str(lot["review_reason"]))
    if sale.get("source_review_reason"):
        reasons.append(str(sale["source_review_reason"]))
    return "|".join(sorted(set(reason for reason in reasons if reason)))


def _security_context(event: Mapping[str, Any]) -> dict[str, str]:
    return {
        "security_key": event["security_key"],
        "symbol": event["symbol"],
        "identity_confidence": event["identity_confidence"],
    }


def _open_lot_rows(
    lots: Mapping[str, deque[dict[str, Any]]],
    security_info: Mapping[str, Mapping[str, str]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for security_key in sorted(lots):
        context = security_info.get(security_key, {})
        for lot in lots[security_key]:
            rows.append({
                "security_key": security_key,
                "symbol": str(lot.get("symbol") or context.get("symbol") or ""),
                "identity_confidence": str(lot.get("identity_confidence") or context.get("identity_confidence") or ""),
                "opening_event_id": str(lot["opening_event_id"]),
                "opening_trade_date": _date_text(lot["opening_trade_date"]),
                "opening_settle_date": _date_text(lot["opening_settle_date"]),
                "remaining_quantity": _quantity(lot["remaining_quantity"]),
                "remaining_cost": _money_or_blank(lot["remaining_cost"]),
                "basis_status": lot["basis_status"],
                "review_status": lot["review_status"],
                "review_reason": lot["review_reason"],
            })
    return rows


def _realized_by_security(
    matches: Iterable[Mapping[str, str]],
    security_info: Mapping[str, Mapping[str, str]],
    unmatched_quantities: Mapping[str, Decimal],
) -> list[dict[str, str]]:
    totals: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "realized_gain": Decimal("0"),
        "realized_loss": Decimal("0"),
        "net_realized_pnl": Decimal("0"),
        "winning_matches": 0,
        "losing_matches": 0,
        "break_even_matches": 0,
        "unknown_basis_quantity": Decimal("0"),
        "unmatched_quantity": Decimal("0"),
    })
    for key, quantity in unmatched_quantities.items():
        totals[key]["unmatched_quantity"] += quantity
    for match in matches:
        key = match["security_key"]
        if match["basis_status"] == "unknown":
            totals[key]["unknown_basis_quantity"] += Decimal(match["matched_quantity"])
            continue
        pnl = Decimal(match["realized_pnl"])
        totals[key]["net_realized_pnl"] += pnl
        if pnl > 0:
            totals[key]["realized_gain"] += pnl
            totals[key]["winning_matches"] += 1
        elif pnl < 0:
            totals[key]["realized_loss"] += pnl
            totals[key]["losing_matches"] += 1
        else:
            totals[key]["break_even_matches"] += 1

    rows: list[dict[str, str]] = []
    for key in sorted(totals):
        context = security_info.get(key, {})
        total = totals[key]
        rows.append({
            "security_key": key,
            "symbol": str(context.get("symbol") or ""),
            "identity_confidence": str(context.get("identity_confidence") or ""),
            "realized_gain": _quantity(total["realized_gain"]),
            "realized_loss": _quantity(total["realized_loss"]),
            "net_realized_pnl": _quantity(total["net_realized_pnl"]),
            "winning_matches": str(total["winning_matches"]),
            "losing_matches": str(total["losing_matches"]),
            "break_even_matches": str(total["break_even_matches"]),
            "unknown_basis_quantity": _quantity(total["unknown_basis_quantity"]),
            "unmatched_quantity": _quantity(total["unmatched_quantity"]),
        })
    return rows


def _summary(
    matches: Iterable[Mapping[str, str]],
    by_security: Iterable[Mapping[str, str]],
    open_lots: Iterable[Mapping[str, str]],
    review: list[Mapping[str, str]],
    anchors: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    match_rows = list(matches)
    security_rows = list(by_security)
    included_gain = sum((Decimal(row["realized_gain"]) for row in security_rows), Decimal("0"))
    included_loss = sum((Decimal(row["realized_loss"]) for row in security_rows), Decimal("0"))
    by_year: dict[str, Decimal] = defaultdict(Decimal)
    for match in match_rows:
        if match["basis_status"] == "known":
            by_year[match["closing_trade_date"][:4]] += Decimal(match["realized_pnl"])
    return {
        "methodology": "analytical_fifo",
        "methodology_note": METHODOLOGY_NOTE,
        "match_count": len(match_rows),
        "open_lot_count": len(list(open_lots)),
        "review_count": len(review),
        "anchor_count": len(list(anchors)),
        "realized_gain": _quantity(included_gain),
        "realized_loss": _quantity(included_loss),
        "net_realized_pnl": _quantity(included_gain + included_loss),
        "winning_matches": sum(int(row["winning_matches"]) for row in security_rows),
        "losing_matches": sum(int(row["losing_matches"]) for row in security_rows),
        "break_even_matches": sum(int(row["break_even_matches"]) for row in security_rows),
        "unknown_basis_quantity": _quantity(
            sum((Decimal(row["unknown_basis_quantity"]) for row in security_rows), Decimal("0"))
        ),
        "unmatched_quantity": _quantity(
            sum((Decimal(row["unmatched_quantity"]) for row in security_rows), Decimal("0"))
        ),
        "realized_pnl_by_year": {year: _quantity(total) for year, total in sorted(by_year.items())},
        "realized_pnl_by_security": {
            row["security_key"]: row["net_realized_pnl"] for row in security_rows
        },
    }


def _format_match(match: Mapping[str, Any]) -> dict[str, str]:
    return {
        "security_key": match["security_key"],
        "symbol": match["symbol"],
        "identity_confidence": match["identity_confidence"],
        "opening_event_id": str(match["opening_event_id"]),
        "closing_event_id": str(match["closing_event_id"]),
        "opening_trade_date": _date_text(match["opening_trade_date"]),
        "closing_trade_date": _date_text(match["closing_trade_date"]),
        "opening_settle_date": _date_text(match["opening_settle_date"]),
        "closing_settle_date": _date_text(match["closing_settle_date"]),
        "matched_quantity": _quantity(match["matched_quantity"]),
        "allocated_opening_cost": _money_or_blank(match["allocated_opening_cost"]),
        "allocated_closing_proceeds": _money_or_blank(match["allocated_closing_proceeds"]),
        "realized_pnl": _money_or_blank(match["realized_pnl"]),
        "realized_return_pct": _money_or_blank(match["realized_return_pct"]),
        "holding_period_days": "" if match["holding_period_days"] is None else str(match["holding_period_days"]),
        "basis_status": match["basis_status"],
        "review_status": match["review_status"],
        "review_reason": match["review_reason"],
    }


def _record_review_issue(
    record: Mapping[str, Any],
    reason: str,
    event: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    issue = {
        "source_row_id": str(record.get("source_row_id", "")),
        "activity_date": str(record.get("activity_date", "")),
        "settle_date": str(record.get("settle_date", "")),
        "transaction_code": str(record.get("transaction_code_raw", "")),
        "event_type": str(record.get("event_type", "")),
        "asset_type": str(record.get("asset_type", "")),
        "review_reason": reason,
    }
    if event:
        issue.update({
            "security_key": str(event.get("security_key", "")),
            "symbol": str(event.get("symbol", "")),
            "identity_confidence": str(event.get("identity_confidence", "")),
            "quantity": _quantity(event["quantity"]),
            "amount": _quantity(event["amount"]),
        })
    return issue


def _unknown_basis_review_issue(
    record: Mapping[str, Any],
    sale: Mapping[str, Any],
    matches: Iterable[Mapping[str, Any]],
) -> dict[str, str]:
    unknown_quantity = sum(
        match["matched_quantity"] for match in matches
        if match["basis_status"] == "unknown"
    )
    issue = _record_review_issue(record, "unknown_basis_closure", sale)
    issue["unknown_basis_quantity"] = _quantity(unknown_quantity)
    return issue


def _oversell_review_issue(
    record: Mapping[str, Any],
    oversell: Mapping[str, Any],
    sale: Mapping[str, Any],
) -> dict[str, str]:
    reason = (
        f"{oversell['reason']}:"
        f"available={_quantity(oversell['available_quantity'])}:"
        f"unmatched={_quantity(oversell['unmatched_quantity'])}:"
        f"event={_quantity(sale['quantity'])}:"
        f"security_key={sale['security_key']}"
    )
    issue = _record_review_issue(record, reason, sale)
    issue.update({
        "trade_date": sale["activity_date"].isoformat() if sale.get("activity_date") else "",
        "sale_quantity": _quantity(sale["quantity"]),
        "available_quantity": _quantity(oversell["available_quantity"]),
        "unmatched_quantity": _quantity(oversell["unmatched_quantity"]),
    })
    return issue


def _anchor_review_issue(issue: Mapping[str, str]) -> dict[str, str]:
    return {
        "source_row_id": "",
        "activity_date": "",
        "settle_date": "",
        "transaction_code": "",
        "event_type": "position_anchor",
        "asset_type": "",
        "security_key": str(issue.get("security_key", "")),
        "review_reason": str(issue.get("review_reason", "")),
    }


def _is_unsupported_corporate_action(
    record: Mapping[str, Any],
    activity_valid: bool,
    activity_date: date | None,
    as_of: date | None,
) -> bool:
    if str(record.get("asset_type") or "") != "equity":
        return False
    if str(record.get("transaction_family") or "") != "corporate_action":
        return False
    return not (activity_valid and as_of is not None and activity_date > as_of)


def _allocate(total: Decimal, quantity: Decimal, original_quantity: Decimal) -> Decimal:
    return total * quantity / original_quantity


def _parse_date(value: Any) -> tuple[date | None, bool]:
    text = str(value or "").strip()
    if not text:
        return None, False
    try:
        return date.fromisoformat(text), True
    except ValueError:
        return None, False


def _parse_decimal(value: Any) -> tuple[Decimal | None, bool]:
    text = str(value or "").strip()
    if not text:
        return None, False
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None, False
    return (number, True) if number.is_finite() else (None, False)


def _split_reasons(value: Any) -> list[str]:
    text = str(value or "").strip()
    return [item for item in text.split("|") if item]


def _date_text(value: date | None) -> str:
    return "" if value is None else value.isoformat()


def _money_or_blank(value: Decimal | None) -> str:
    return "" if value is None else _quantity(value)


def _write_csv(path: Path, fields: list[str], rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
