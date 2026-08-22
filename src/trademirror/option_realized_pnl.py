from __future__ import annotations

import csv
import json
from collections import defaultdict, deque
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping

from .position_ledger import (
    _quantity,
    build_position_ledger,
)


MATCH_FIELDS = [
    "security_key",
    "structural_key",
    "option_cusip",
    "identity_confidence",
    "underlying",
    "option_expiration",
    "option_type",
    "option_strike",
    "position_side",
    "opening_action",
    "closing_action",
    "opening_event_id",
    "closing_event_id",
    "opening_trade_date",
    "closing_trade_date",
    "opening_settle_date",
    "closing_settle_date",
    "matched_quantity",
    "allocated_opening_cost",
    "allocated_opening_credit",
    "allocated_closing_proceeds",
    "allocated_closing_cost",
    "realized_pnl",
    "realized_return_pct",
    "pnl_to_opening_credit_pct",
    "holding_period_days",
    "days_to_expiration_at_open",
    "days_to_expiration_at_close",
    "outcome",
    "basis_transfer_required",
    "basis_status",
    "review_status",
    "review_reason",
]

OPEN_LOT_FIELDS = [
    "security_key",
    "structural_key",
    "option_cusip",
    "identity_confidence",
    "underlying",
    "option_expiration",
    "option_type",
    "option_strike",
    "position_side",
    "opening_action",
    "opening_event_id",
    "opening_trade_date",
    "opening_settle_date",
    "remaining_quantity",
    "remaining_cost",
    "remaining_credit",
    "basis_status",
    "review_status",
    "review_reason",
]

BASIS_TRANSFER_FIELDS = [
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
]

BY_CONTRACT_FIELDS = [
    "security_key",
    "structural_key",
    "option_cusip",
    "identity_confidence",
    "underlying",
    "option_expiration",
    "option_type",
    "option_strike",
    "realized_gain",
    "realized_loss",
    "net_realized_pnl",
    "winning_matches",
    "losing_matches",
    "break_even_matches",
    "unknown_basis_quantity",
    "unmatched_quantity",
]

METHODOLOGY_NOTE = (
    "Analytical option FIFO only. Results may differ from brokerage tax documents "
    "because spreads, tax-lot elections, wash sales, tax treatment, "
    "exercise/assignment stock-basis linkage, and strategy-level ROI are not implemented."
)


def build_option_realized_pnl(
    records: Iterable[Mapping[str, Any]],
    *,
    as_of: date | None = None,
    anchors: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    record_list = list(records)
    anchor_list = list(anchors)
    cusip_registry: dict[str, set[str]] = defaultdict(set)
    parsed_anchors, anchor_review = _parse_option_anchors(anchor_list, as_of=as_of)
    review: list[dict[str, str]] = [
        _anchor_review_issue(issue) for issue in anchor_review
    ]
    lots: dict[str, dict[str, deque[dict[str, Any]]]] = defaultdict(_side_lots)
    contract_info: dict[str, dict[str, str]] = {}
    matches: list[dict[str, Any]] = []
    basis_transfers: list[dict[str, Any]] = []
    unmatched_quantities: dict[str, Decimal] = defaultdict(Decimal)
    unmatched_contexts: dict[str, Mapping[str, str]] = {}

    applicable_anchors = sorted(
        (
            anchor for anchor in parsed_anchors
            if anchor["asset_type"] == "option"
            and (as_of is None or anchor["anchor_date"] <= as_of)
        ),
        key=lambda item: (item["anchor_date"], item["security_key"]),
    )
    next_anchor = 0
    applied_anchors: set[tuple[str, date]] = set()
    ordered_records = sorted(
        record_list,
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
                contract_info,
                applicable_anchors,
                applied_anchors,
                next_anchor,
                activity_date,
                cusip_registry,
                review,
            )

        event = _parse_option_event(record, as_of=as_of, cusip_registry=cusip_registry)
        if event is None:
            continue
        if event["review_reason"]:
            contract_info[event["security_key"]] = _contract_context(event)
            if "ambiguous_option_cusip_fallback" in event["review_reason"] and event["action"] not in {"BTO", "STO"}:
                unmatched_quantities[event["security_key"]] += event["quantity"]
                unmatched_contexts.setdefault(event["security_key"], _contract_context(event))
            review.append(_record_review_issue(record, event["review_reason"], event))
            continue

        _migrate_structural_lots(lots, contract_info, event)
        contract_info[event["security_key"]] = _contract_context(event)
        if event["action"] in {"BTO", "STO"}:
            lots[event["security_key"]][event["position_side"]].append(_opening_lot(event))
            continue

        side, side_reason = _closing_side(event, lots[event["security_key"]])
        if side_reason:
            review.append(_record_review_issue(record, side_reason, event))
            unmatched_quantities[event["security_key"]] += event["quantity"]
            unmatched_contexts.setdefault(event["security_key"], _contract_context(event))
            continue

        close_matches, close_review = _match_close_fifo(lots[event["security_key"]][side], event, side)
        matches.extend(close_matches)
        basis_transfers.extend(match for match in close_matches if match["basis_transfer_required"])
        if close_review:
            review.append(_oversize_review_issue(record, close_review, event, side))
            unmatched_quantities[event["security_key"]] += close_review["unmatched_quantity"]
            unmatched_contexts.setdefault(event["security_key"], _contract_context(event))
        if close_matches and any(match["basis_status"] == "unknown" for match in close_matches):
            review.append(_unknown_basis_review_issue(record, event, close_matches))

    _apply_eligible_anchors(
        lots,
        contract_info,
        applicable_anchors,
        applied_anchors,
        next_anchor,
        as_of,
        cusip_registry,
        review,
    )

    open_lots = _open_lot_rows(lots, contract_info)
    match_rows = [_format_match(match) for match in matches]
    transfer_rows = [_format_basis_transfer(match) for match in basis_transfers]
    by_contract = _realized_by_contract(match_rows, contract_info, unmatched_quantities, unmatched_contexts)
    review.extend(_reconciliation_issues(record_list, as_of, anchor_list, open_lots, contract_info))
    summary = _summary(match_rows, by_contract, open_lots, transfer_rows, review, parsed_anchors)
    return {
        "matches": match_rows,
        "open_lots": open_lots,
        "basis_transfers": transfer_rows,
        "realized_by_contract": by_contract,
        "summary": summary,
        "review": {
            "review_count": len(review),
            "issues": review,
        },
    }


def write_option_realized_pnl_outputs(result: Mapping[str, Any], output_dir: str | Path) -> None:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    _write_csv(destination / "option_lot_matches.csv", MATCH_FIELDS, result["matches"])
    _write_csv(destination / "option_open_lots.csv", OPEN_LOT_FIELDS, result["open_lots"])
    _write_csv(destination / "option_basis_transfers.csv", BASIS_TRANSFER_FIELDS, result["basis_transfers"])
    _write_csv(destination / "option_realized_by_contract.csv", BY_CONTRACT_FIELDS, result["realized_by_contract"])
    (destination / "option_realized_summary.json").write_text(
        json.dumps(result["summary"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (destination / "option_lot_review.json").write_text(
        json.dumps(result["review"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _parse_option_event(
    record: Mapping[str, Any],
    *,
    as_of: date | None,
    cusip_registry: dict[str, set[str]],
) -> dict[str, Any] | None:
    if str(record.get("asset_type") or "") != "option":
        return None
    family = str(record.get("transaction_family") or "")
    event_type = str(record.get("event_type") or "")
    if family not in {"option_trade", "option_lifecycle"}:
        return None
    action = _action(record)
    if action not in {"BTO", "STO", "STC", "BTC", "EXP", "EXERCISE", "ASSIGNMENT"}:
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
    if activity_valid and as_of is not None and activity_date > as_of:
        return None

    identity = _option_identity(record, cusip_registry)
    reasons.extend(identity["review_reasons"])
    if not identity["valid"] and "option_identity_incomplete" in identity["review_reasons"]:
        reasons.append("missing_option_identity")

    opening_cost: Decimal | None = None
    opening_credit: Decimal | None = None
    closing_proceeds: Decimal | None = None
    closing_cost: Decimal | None = None
    if action in {"BTO", "STO", "STC", "BTC"}:
        if not amount_valid or amount is None:
            reasons.append("invalid_or_missing_amount")
        elif action == "BTO":
            if amount >= 0:
                reasons.append("bto_amount_not_outflow")
            opening_cost = -amount
        elif action == "STO":
            if amount <= 0:
                reasons.append("sto_amount_not_inflow")
            opening_credit = amount
        elif action == "STC":
            if amount <= 0:
                reasons.append("stc_amount_not_inflow")
            closing_proceeds = amount
        elif action == "BTC":
            if amount >= 0:
                reasons.append("btc_amount_not_outflow")
            closing_cost = -amount

    return {
        "source_row_id": str(record.get("source_row_id", "")),
        "activity_date": activity_date,
        "settle_date": settle_date,
        "transaction_code": str(record.get("transaction_code_raw") or ""),
        "event_type": event_type,
        "action": action,
        "security_key": identity["security_key"],
        "structural_key": identity["structural_key"],
        "option_cusip": identity["option_cusip"],
        "identity_confidence": identity["confidence"],
        "underlying": identity["option_underlying"],
        "option_expiration": identity["option_expiration"],
        "option_type": identity["option_type"],
        "option_strike": identity["option_strike"],
        "position_side": _action_side(action),
        "quantity": quantity or Decimal("0"),
        "amount": amount or Decimal("0"),
        "opening_cost": opening_cost,
        "opening_credit": opening_credit,
        "closing_proceeds": closing_proceeds,
        "closing_cost": closing_cost,
        "explicit_side": _explicit_position_side(record),
        "review_reason": "|".join(sorted(set(reasons))),
        "source_review_reason": "|".join(
            reason for reason in (source_review_reason, settlement_review_reason)
            if reason
        ),
    }


def _parse_option_anchors(
    anchors: Iterable[Mapping[str, Any]],
    *,
    as_of: date | None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    parsed: list[dict[str, Any]] = []
    review: list[dict[str, str]] = []
    for anchor in anchors:
        if str(anchor.get("asset_type") or "") != "option":
            continue
        anchor_date, date_valid = _parse_date(anchor.get("anchor_date") or anchor.get("date"))
        quantity, quantity_valid = _parse_decimal(anchor.get("quantity") or anchor.get("verified_quantity"))
        if date_valid and as_of is not None and anchor_date and anchor_date > as_of:
            continue
        identity = _option_identity(anchor, {}, register_cusip=False, resolve_missing_cusip=False)
        reasons = list(identity["review_reasons"])
        if not date_valid:
            reasons.append("invalid_or_missing_anchor_date")
        if not quantity_valid:
            reasons.append("invalid_or_missing_anchor_quantity")
        if not identity["valid"] and "option_identity_incomplete" in identity["review_reasons"]:
            reasons.append("missing_option_identity")
        if reasons:
            review.append({
                "security_key": identity["security_key"],
                "structural_key": identity["structural_key"],
                "option_cusip": identity["option_cusip"],
                "review_reason": "|".join(sorted(set(reasons))),
            })
            continue
        parsed.append({
            "anchor_date": anchor_date,
            "asset_type": "option",
            "security_key": identity["security_key"],
            "structural_key": identity["structural_key"],
            "option_cusip": identity["option_cusip"],
            "confidence": identity["confidence"],
            "option_underlying": identity["option_underlying"],
            "option_expiration": identity["option_expiration"],
            "option_type": identity["option_type"],
            "option_strike": identity["option_strike"],
            "quantity": quantity,
            "raw_anchor": anchor,
        })
    return parsed, review


def _option_identity(
    record: Mapping[str, Any],
    cusip_registry: Mapping[str, set[str]],
    *,
    register_cusip: bool = True,
    resolve_missing_cusip: bool = True,
) -> dict[str, Any]:
    underlying = str(record.get("option_underlying") or record.get("underlying") or record.get("instrument") or "").strip().upper()
    expiration = str(record.get("option_expiration") or record.get("expiration") or "").strip()
    option_type = str(record.get("option_type") or "").strip().lower()
    strike = str(record.get("option_strike") or record.get("strike") or "").strip()
    structural_key = _option_structural_key(underlying, expiration, option_type, strike)
    option_cusip = _normalize_option_cusip(record.get("option_cusip") or record.get("cusip"))
    reasons: list[str] = []
    valid = bool(underlying and expiration and option_type and strike)
    if not valid:
        reasons.append("option_identity_incomplete")
    if option_cusip and valid:
        if register_cusip:
            cusip_registry[structural_key].add(option_cusip)
        security_key = _option_cusip_key(structural_key, option_cusip)
        confidence = "deterministic_cusip"
    elif valid and resolve_missing_cusip:
        candidates = sorted(cusip_registry.get(structural_key, set()))
        if len(candidates) == 1:
            option_cusip = candidates[0]
            security_key = _option_cusip_key(structural_key, option_cusip)
            confidence = "reduced_structural_cusip_fallback"
        elif len(candidates) > 1:
            security_key = structural_key
            confidence = "review"
            reasons.append("ambiguous_option_cusip_fallback")
            valid = False
        else:
            security_key = structural_key
            confidence = "lower_structural_only"
    elif valid:
        security_key = structural_key
        confidence = "lower_structural_only"
    else:
        security_key = structural_key
        confidence = "review"
    return {
        "security_key": security_key,
        "structural_key": structural_key,
        "option_cusip": option_cusip,
        "option_underlying": underlying,
        "option_expiration": expiration,
        "option_type": option_type,
        "option_strike": strike,
        "confidence": confidence,
        "valid": valid,
        "review_reasons": reasons,
    }


def _option_structural_key(underlying: str, expiration: str, option_type: str, strike: str) -> str:
    return f"option:{underlying}:{expiration}:{option_type}:{strike}"


def _option_cusip_key(structural_key: str, option_cusip: str) -> str:
    return f"{structural_key}:cusip:{option_cusip}"


def _normalize_option_cusip(value: Any) -> str:
    return str(value or "").strip().upper()


def _action(record: Mapping[str, Any]) -> str:
    code = str(record.get("transaction_code_raw") or "")
    event_type = str(record.get("event_type") or "")
    if code == "BTO" or event_type == "buy_to_open":
        return "BTO"
    if code == "STO" or event_type == "sell_to_open":
        return "STO"
    if code == "STC" or event_type == "sell_to_close":
        return "STC"
    if code == "BTC" or event_type == "buy_to_close":
        return "BTC"
    if event_type == "expiration":
        return "EXP"
    if event_type == "exercise":
        return "EXERCISE"
    if event_type == "assignment":
        return "ASSIGNMENT"
    return ""


def _action_side(action: str) -> str:
    if action in {"BTO", "STC", "EXERCISE"}:
        return "long"
    if action in {"STO", "BTC", "ASSIGNMENT"}:
        return "short"
    return ""


def _closing_side(event: Mapping[str, Any], side_lots: Mapping[str, deque[dict[str, Any]]]) -> tuple[str, str]:
    action = event["action"]
    if action in {"STC", "EXERCISE"}:
        return "long", ""
    if action in {"BTC", "ASSIGNMENT"}:
        return "short", ""
    if action == "EXP":
        if event.get("explicit_side") in {"long", "short"}:
            return str(event["explicit_side"]), ""
        long_open = _available_quantity(side_lots["long"]) > 0
        short_open = _available_quantity(side_lots["short"]) > 0
        if long_open and not short_open:
            return "long", ""
        if short_open and not long_open:
            return "short", ""
        if long_open and short_open:
            return "ambiguous", "ambiguous_option_expiration_side"
        return "ambiguous", "unmatched_option_expiration"
    return "", "unsupported_option_close"


def _match_close_fifo(
    lots: deque[dict[str, Any]],
    event: Mapping[str, Any],
    side: str,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    available = _available_quantity(lots)
    remaining = event["quantity"]
    original_quantity = event["quantity"]
    rows: list[dict[str, Any]] = []
    matched_contexts: list[dict[str, str]] = []
    while remaining > 0 and lots:
        lot = lots[0]
        matched = min(remaining, lot["remaining_quantity"])
        row = _match_row(lot, event, side, matched, original_quantity)
        rows.append(row)
        matched_contexts.append(_lot_identity_context(lot))
        if lot["basis_status"] == "known":
            if side == "long":
                lot["remaining_cost"] -= row["allocated_opening_cost"] or Decimal("0")
            else:
                lot["remaining_credit"] -= row["allocated_opening_credit"] or Decimal("0")
        lot["remaining_quantity"] -= matched
        remaining -= matched
        if lot["remaining_quantity"] == 0:
            lots.popleft()

    if remaining > 0:
        reason = "unmatched_option_close" if available == 0 else "oversized_option_close"
        return rows, {
            "reason": reason,
            "available_quantity": available,
            "matched_quantity": original_quantity - remaining,
            "unmatched_quantity": remaining,
            "matched_contexts": matched_contexts,
            "resolution_method": _close_resolution_method(event, matched_contexts),
        }
    return rows, None


def _lot_identity_context(lot: Mapping[str, Any]) -> dict[str, str]:
    return {
        "security_key": str(lot.get("security_key") or ""),
        "structural_key": str(lot.get("structural_key") or ""),
        "option_cusip": str(lot.get("option_cusip") or ""),
        "identity_confidence": str(lot.get("identity_confidence") or ""),
    }


def _close_resolution_method(event: Mapping[str, Any], contexts: Iterable[Mapping[str, str]]) -> str:
    context_list = list(contexts)
    if not context_list:
        return "no_available_inventory"
    event_cusip = str(event.get("option_cusip") or "")
    event_security_key = str(event.get("security_key") or "")
    event_structural_key = str(event.get("structural_key") or "")
    if event_cusip and any(
        context.get("security_key") == event_structural_key
        and not context.get("option_cusip")
        for context in context_list
    ):
        return "mixed_cusip_structural"
    if all(
        context.get("security_key") == event_security_key
        and context.get("option_cusip", "") == event_cusip
        for context in context_list
    ):
        return "same_identity"
    return "fifo_mixed_identity"


def _match_row(
    lot: Mapping[str, Any],
    event: Mapping[str, Any],
    side: str,
    matched: Decimal,
    original_quantity: Decimal,
) -> dict[str, Any]:
    basis_status = lot["basis_status"]
    outcome = _outcome(event["action"])
    basis_transfer_required = event["action"] in {"EXERCISE", "ASSIGNMENT"}
    allocated_opening_cost: Decimal | None = None
    allocated_opening_credit: Decimal | None = None
    allocated_closing_proceeds: Decimal | None = None
    allocated_closing_cost: Decimal | None = None
    realized_pnl: Decimal | None = None
    realized_return_pct: Decimal | None = None
    pnl_to_opening_credit_pct: Decimal | None = None

    if basis_status == "known":
        if side == "long":
            allocated_opening_cost = _allocate(lot["remaining_cost"], matched, lot["remaining_quantity"])
            if event["action"] == "STC":
                allocated_closing_proceeds = _allocate(event["closing_proceeds"], matched, original_quantity)
                realized_pnl = allocated_closing_proceeds - allocated_opening_cost
                if allocated_opening_cost != 0:
                    realized_return_pct = (realized_pnl / allocated_opening_cost) * Decimal("100")
            elif event["action"] == "EXP":
                allocated_closing_proceeds = Decimal("0")
                realized_pnl = -allocated_opening_cost
        else:
            allocated_opening_credit = _allocate(lot["remaining_credit"], matched, lot["remaining_quantity"])
            if event["action"] == "BTC":
                allocated_closing_cost = _allocate(event["closing_cost"], matched, original_quantity)
                realized_pnl = allocated_opening_credit - allocated_closing_cost
                if allocated_opening_credit != 0:
                    pnl_to_opening_credit_pct = (realized_pnl / allocated_opening_credit) * Decimal("100")
            elif event["action"] == "EXP":
                allocated_closing_cost = Decimal("0")
                realized_pnl = allocated_opening_credit

    return {
        "security_key": event["security_key"],
        "structural_key": event["structural_key"],
        "option_cusip": event["option_cusip"],
        "identity_confidence": _match_identity_confidence(lot, event),
        "underlying": event["underlying"],
        "option_expiration": event["option_expiration"],
        "option_type": event["option_type"],
        "option_strike": event["option_strike"],
        "position_side": side,
        "opening_action": lot["opening_action"],
        "closing_action": event["action"],
        "opening_event_id": lot["opening_event_id"],
        "closing_event_id": event["source_row_id"],
        "opening_trade_date": lot["opening_trade_date"],
        "closing_trade_date": event["activity_date"],
        "opening_settle_date": lot["opening_settle_date"],
        "closing_settle_date": event["settle_date"],
        "matched_quantity": matched,
        "allocated_opening_cost": allocated_opening_cost,
        "allocated_opening_credit": allocated_opening_credit,
        "allocated_closing_proceeds": allocated_closing_proceeds,
        "allocated_closing_cost": allocated_closing_cost,
        "realized_pnl": realized_pnl,
        "realized_return_pct": realized_return_pct,
        "pnl_to_opening_credit_pct": pnl_to_opening_credit_pct,
        "holding_period_days": _days_between(lot["opening_trade_date"], event["activity_date"]),
        "days_to_expiration_at_open": _days_to_expiration(lot["opening_trade_date"], event["option_expiration"]),
        "days_to_expiration_at_close": _days_to_expiration(event["activity_date"], event["option_expiration"]),
        "outcome": outcome,
        "basis_transfer_required": basis_transfer_required,
        "basis_status": basis_status,
        "review_status": _match_review_status(lot, event, basis_status, basis_transfer_required),
        "review_reason": _match_review_reason(lot, event, basis_status, basis_transfer_required),
    }


def _match_identity_confidence(lot: Mapping[str, Any], event: Mapping[str, Any]) -> str:
    lot_cusip = str(lot.get("option_cusip") or "")
    event_cusip = str(event.get("option_cusip") or "")
    if lot_cusip != event_cusip:
        return "reduced_structural_cusip_fallback"
    if "reduced_structural_cusip_fallback" in {
        str(lot.get("identity_confidence") or ""),
        str(event.get("identity_confidence") or ""),
    }:
        return "reduced_structural_cusip_fallback"
    return str(event.get("identity_confidence") or lot.get("identity_confidence") or "")


def _outcome(action: str) -> str:
    return {
        "STC": "closed",
        "BTC": "closed",
        "EXP": "expired",
        "EXERCISE": "exercised",
        "ASSIGNMENT": "assigned",
    }.get(action, "unknown")


def _opening_lot(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "security_key": event["security_key"],
        "structural_key": event["structural_key"],
        "option_cusip": event["option_cusip"],
        "identity_confidence": event["identity_confidence"],
        "underlying": event["underlying"],
        "option_expiration": event["option_expiration"],
        "option_type": event["option_type"],
        "option_strike": event["option_strike"],
        "position_side": event["position_side"],
        "opening_action": event["action"],
        "opening_event_id": event["source_row_id"],
        "opening_trade_date": event["activity_date"],
        "opening_settle_date": event["settle_date"],
        "remaining_quantity": event["quantity"],
        "remaining_cost": event["opening_cost"],
        "remaining_credit": event["opening_credit"],
        "basis_status": "known",
        "review_status": "review" if event["source_review_reason"] else "validated",
        "review_reason": event["source_review_reason"],
    }


def _apply_anchor(
    lots: dict[str, dict[str, deque[dict[str, Any]]]],
    contract_info: dict[str, dict[str, str]],
    anchor: Mapping[str, Any],
) -> None:
    long_quantity = max(anchor["quantity"], Decimal("0"))
    short_quantity = abs(min(anchor["quantity"], Decimal("0")))
    _migrate_structural_lots(lots, contract_info, anchor)
    lots[anchor["security_key"]] = _side_lots()
    if long_quantity > 0:
        lots[anchor["security_key"]]["long"].append(_anchor_lot(anchor, "long", long_quantity))
    if short_quantity > 0:
        lots[anchor["security_key"]]["short"].append(_anchor_lot(anchor, "short", short_quantity))
    contract_info[anchor["security_key"]] = _anchor_contract_context(anchor)


def _migrate_structural_lots(
    lots: dict[str, dict[str, deque[dict[str, Any]]]],
    contract_info: dict[str, dict[str, str]],
    identity: Mapping[str, Any],
) -> None:
    structural_key = str(identity.get("structural_key") or "")
    target_key = str(identity.get("security_key") or "")
    option_cusip = str(identity.get("option_cusip") or "")
    if not structural_key or not target_key or not option_cusip or structural_key == target_key:
        return
    source = lots.get(structural_key)
    if not source:
        return
    if _available_quantity(source["long"]) == 0 and _available_quantity(source["short"]) == 0:
        return
    target = lots[target_key]
    for side in ("long", "short"):
        migrated = []
        for lot in source[side]:
            migrated.append(dict(lot))
        combined = list(target[side]) + migrated
        combined.sort(key=lambda lot: (
            lot.get("opening_trade_date") or date.min,
            str(lot.get("opening_event_id") or ""),
        ))
        target[side] = deque(combined)
    lots.pop(structural_key, None)
    if "identity_confidence" in identity:
        contract_info[target_key] = _contract_context(identity)
    else:
        contract_info[target_key] = _anchor_contract_context(identity)
    contract_info.pop(structural_key, None)


def _anchor_lot(anchor: Mapping[str, Any], side: str, quantity: Decimal) -> dict[str, Any]:
    return {
        "security_key": anchor["security_key"],
        "structural_key": anchor["structural_key"],
        "option_cusip": anchor["option_cusip"],
        "identity_confidence": anchor["confidence"],
        "underlying": anchor["option_underlying"],
        "option_expiration": anchor["option_expiration"],
        "option_type": anchor["option_type"],
        "option_strike": anchor["option_strike"],
        "position_side": side,
        "opening_action": "ANCHOR",
        "opening_event_id": f"anchor:{anchor['anchor_date'].isoformat()}",
        "opening_trade_date": anchor["anchor_date"],
        "opening_settle_date": None,
        "remaining_quantity": quantity,
        "remaining_cost": None,
        "remaining_credit": None,
        "basis_status": "unknown",
        "review_status": "review",
        "review_reason": "unknown_basis_from_position_anchor",
    }


def _apply_eligible_anchors(
    lots: dict[str, dict[str, deque[dict[str, Any]]]],
    contract_info: dict[str, dict[str, str]],
    anchors: list[Mapping[str, Any]],
    applied_anchors: set[tuple[str, date]],
    start_index: int,
    through_date: date | None,
    cusip_registry: dict[str, set[str]],
    review: list[dict[str, str]],
) -> int:
    index = start_index
    while (
        index < len(anchors)
        and (through_date is None or anchors[index]["anchor_date"] <= through_date)
    ):
        anchor = anchors[index]
        anchor_key = (anchor["security_key"], anchor["anchor_date"])
        if anchor_key not in applied_anchors:
            resolved_anchor, anchor_issue = _resolve_anchor_at_effective_date(anchor, cusip_registry)
            if anchor_issue:
                review.append(_anchor_review_issue(anchor_issue))
            elif resolved_anchor is not None:
                _apply_anchor(lots, contract_info, resolved_anchor)
            applied_anchors.add(anchor_key)
        index += 1
    return index


def _resolve_anchor_at_effective_date(
    anchor: Mapping[str, Any],
    cusip_registry: dict[str, set[str]],
) -> tuple[dict[str, Any] | None, dict[str, str] | None]:
    identity = _option_identity(anchor["raw_anchor"], cusip_registry)
    reasons = list(identity["review_reasons"])
    if not identity["valid"] and "option_identity_incomplete" in identity["review_reasons"]:
        reasons.append("missing_option_identity")
    if reasons:
        return None, {
            "security_key": identity["security_key"],
            "structural_key": identity["structural_key"],
            "option_cusip": identity["option_cusip"],
            "review_reason": "|".join(sorted(set(reasons))),
        }
    resolved = dict(anchor)
    resolved.update({
        "security_key": identity["security_key"],
        "structural_key": identity["structural_key"],
        "option_cusip": identity["option_cusip"],
        "confidence": identity["confidence"],
        "option_underlying": identity["option_underlying"],
        "option_expiration": identity["option_expiration"],
        "option_type": identity["option_type"],
        "option_strike": identity["option_strike"],
    })
    return resolved, None


def _side_lots() -> dict[str, deque[dict[str, Any]]]:
    return {"long": deque(), "short": deque()}


def _available_quantity(lots: Iterable[Mapping[str, Any]]) -> Decimal:
    return sum((lot["remaining_quantity"] for lot in lots), Decimal("0"))


def _contract_context(event: Mapping[str, Any]) -> dict[str, str]:
    return {
        "security_key": event["security_key"],
        "structural_key": event["structural_key"],
        "option_cusip": event["option_cusip"],
        "identity_confidence": event["identity_confidence"],
        "underlying": event["underlying"],
        "option_expiration": event["option_expiration"],
        "option_type": event["option_type"],
        "option_strike": event["option_strike"],
    }


def _anchor_contract_context(anchor: Mapping[str, Any]) -> dict[str, str]:
    return {
        "security_key": anchor["security_key"],
        "structural_key": anchor["structural_key"],
        "option_cusip": anchor["option_cusip"],
        "identity_confidence": anchor["confidence"],
        "underlying": anchor["option_underlying"],
        "option_expiration": anchor["option_expiration"],
        "option_type": anchor["option_type"],
        "option_strike": anchor["option_strike"],
    }


def _open_lot_rows(
    lots: Mapping[str, Mapping[str, deque[dict[str, Any]]]],
    contract_info: Mapping[str, Mapping[str, str]],
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for security_key in sorted(lots):
        context = contract_info.get(security_key, {})
        for side in ("long", "short"):
            for lot in lots[security_key][side]:
                rows.append({
                    **_contract_row_context(lot, context),
                    "position_side": side,
                    "opening_action": lot["opening_action"],
                    "opening_event_id": str(lot["opening_event_id"]),
                    "opening_trade_date": _date_text(lot["opening_trade_date"]),
                    "opening_settle_date": _date_text(lot["opening_settle_date"]),
                    "remaining_quantity": _quantity(lot["remaining_quantity"]),
                    "remaining_cost": _money_or_blank(lot["remaining_cost"]),
                    "remaining_credit": _money_or_blank(lot["remaining_credit"]),
                    "basis_status": lot["basis_status"],
                    "review_status": lot["review_status"],
                    "review_reason": lot["review_reason"],
                })
    return rows


def _realized_by_contract(
    matches: Iterable[Mapping[str, str]],
    contract_info: Mapping[str, Mapping[str, str]],
    unmatched_quantities: Mapping[str, Decimal],
    unmatched_contexts: Mapping[str, Mapping[str, str]],
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
    match_context: dict[str, Mapping[str, str]] = {}
    for key, quantity in unmatched_quantities.items():
        totals[key]["unmatched_quantity"] += quantity
    for match in matches:
        key = match["security_key"]
        match_context.setdefault(key, match)
        if match["basis_status"] == "unknown":
            totals[key]["unknown_basis_quantity"] += Decimal(match["matched_quantity"])
            continue
        if match["basis_transfer_required"] == "true":
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
        total = totals[key]
        context = match_context.get(key) or unmatched_contexts.get(key) or contract_info.get(key, {})
        rows.append({
            **_contract_row_context(context, context),
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
    by_contract: Iterable[Mapping[str, str]],
    open_lots: Iterable[Mapping[str, str]],
    basis_transfers: Iterable[Mapping[str, str]],
    review: list[Mapping[str, str]],
    anchors: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    match_rows = list(matches)
    contract_rows = list(by_contract)
    open_rows = list(open_lots)
    transfer_rows = list(basis_transfers)
    included_gain = sum((Decimal(row["realized_gain"]) for row in contract_rows), Decimal("0"))
    included_loss = sum((Decimal(row["realized_loss"]) for row in contract_rows), Decimal("0"))
    return {
        "methodology": "analytical_option_fifo",
        "methodology_note": METHODOLOGY_NOTE,
        "match_count": len(match_rows),
        "open_lot_count": len(open_rows),
        "basis_transfer_count": len(transfer_rows),
        "review_count": len(review),
        "anchor_count": len(list(anchors)),
        "realized_gain": _quantity(included_gain),
        "realized_loss": _quantity(included_loss),
        "net_realized_pnl": _quantity(included_gain + included_loss),
        "winning_matches": sum(int(row["winning_matches"]) for row in contract_rows),
        "losing_matches": sum(int(row["losing_matches"]) for row in contract_rows),
        "break_even_matches": sum(int(row["break_even_matches"]) for row in contract_rows),
        "unknown_basis_quantity": _quantity(
            sum((Decimal(row["unknown_basis_quantity"]) for row in contract_rows), Decimal("0"))
        ),
        "unmatched_quantity": _quantity(
            sum((Decimal(row["unmatched_quantity"]) for row in contract_rows), Decimal("0"))
        ),
        "realized_pnl_by_year": _breakdown(match_rows, "closing_trade_date"),
        "realized_pnl_by_underlying": _breakdown(match_rows, "underlying"),
        "realized_pnl_by_side": _breakdown(match_rows, "position_side"),
        "realized_pnl_by_option_type": _breakdown(match_rows, "option_type"),
        "realized_pnl_by_outcome": _breakdown(match_rows, "outcome"),
    }


def _breakdown(matches: Iterable[Mapping[str, str]], field: str) -> dict[str, str]:
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for match in matches:
        if match["basis_status"] != "known" or match["basis_transfer_required"] == "true":
            continue
        key = match[field][:4] if field == "closing_trade_date" else match[field]
        totals[key] += Decimal(match["realized_pnl"])
    return {key: _quantity(total) for key, total in sorted(totals.items())}


def _format_match(match: Mapping[str, Any]) -> dict[str, str]:
    return {
        **_contract_row_context(match, match),
        "position_side": match["position_side"],
        "opening_action": match["opening_action"],
        "closing_action": match["closing_action"],
        "opening_event_id": str(match["opening_event_id"]),
        "closing_event_id": str(match["closing_event_id"]),
        "opening_trade_date": _date_text(match["opening_trade_date"]),
        "closing_trade_date": _date_text(match["closing_trade_date"]),
        "opening_settle_date": _date_text(match["opening_settle_date"]),
        "closing_settle_date": _date_text(match["closing_settle_date"]),
        "matched_quantity": _quantity(match["matched_quantity"]),
        "allocated_opening_cost": _money_or_blank(match["allocated_opening_cost"]),
        "allocated_opening_credit": _money_or_blank(match["allocated_opening_credit"]),
        "allocated_closing_proceeds": _money_or_blank(match["allocated_closing_proceeds"]),
        "allocated_closing_cost": _money_or_blank(match["allocated_closing_cost"]),
        "realized_pnl": _money_or_blank(match["realized_pnl"]),
        "realized_return_pct": _money_or_blank(match["realized_return_pct"]),
        "pnl_to_opening_credit_pct": _money_or_blank(match["pnl_to_opening_credit_pct"]),
        "holding_period_days": _number_or_blank(match["holding_period_days"]),
        "days_to_expiration_at_open": _number_or_blank(match["days_to_expiration_at_open"]),
        "days_to_expiration_at_close": _number_or_blank(match["days_to_expiration_at_close"]),
        "outcome": match["outcome"],
        "basis_transfer_required": "true" if match["basis_transfer_required"] else "false",
        "basis_status": match["basis_status"],
        "review_status": match["review_status"],
        "review_reason": match["review_reason"],
    }


def _format_basis_transfer(match: Mapping[str, Any]) -> dict[str, str]:
    return {
        **_contract_row_context(match, match),
        "position_side": match["position_side"],
        "outcome": match["outcome"],
        "opening_event_id": str(match["opening_event_id"]),
        "closing_event_id": str(match["closing_event_id"]),
        "closing_trade_date": _date_text(match["closing_trade_date"]),
        "matched_quantity": _quantity(match["matched_quantity"]),
        "premium_cost_to_transfer": _money_or_blank(match["allocated_opening_cost"]),
        "premium_credit_to_transfer": _money_or_blank(match["allocated_opening_credit"]),
        "basis_status": match["basis_status"],
        "review_status": match["review_status"],
        "review_reason": match["review_reason"],
    }


def _contract_row_context(row: Mapping[str, Any], context: Mapping[str, Any]) -> dict[str, str]:
    return {
        "security_key": _context_value(row, context, "security_key"),
        "structural_key": _context_value(row, context, "structural_key"),
        "option_cusip": _context_value(row, context, "option_cusip"),
        "identity_confidence": _context_value(row, context, "identity_confidence"),
        "underlying": _context_value(row, context, "underlying"),
        "option_expiration": _context_value(row, context, "option_expiration"),
        "option_type": _context_value(row, context, "option_type"),
        "option_strike": _context_value(row, context, "option_strike"),
    }


def _context_value(row: Mapping[str, Any], context: Mapping[str, Any], field: str) -> str:
    if field in row:
        return str(row[field] or "")
    return str(context.get(field) or "")


def _match_review_status(
    lot: Mapping[str, Any],
    event: Mapping[str, Any],
    basis_status: str,
    basis_transfer_required: bool,
) -> str:
    return "review" if _match_review_reason(lot, event, basis_status, basis_transfer_required) else "validated"


def _match_review_reason(
    lot: Mapping[str, Any],
    event: Mapping[str, Any],
    basis_status: str,
    basis_transfer_required: bool,
) -> str:
    reasons = []
    if basis_status == "unknown":
        reasons.append("unknown_basis_from_position_anchor")
    if basis_transfer_required:
        reasons.append("basis_transfer_required")
    if lot.get("review_reason"):
        reasons.append(str(lot["review_reason"]))
    if event.get("source_review_reason"):
        reasons.append(str(event["source_review_reason"]))
    return "|".join(sorted(set(reason for reason in reasons if reason)))


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
            **_contract_row_context(event, event),
            "position_side": str(event.get("position_side", "")),
            "quantity": _quantity(event["quantity"]),
            "amount": _quantity(event["amount"]),
        })
    return issue


def _oversize_review_issue(
    record: Mapping[str, Any],
    close_review: Mapping[str, Any],
    event: Mapping[str, Any],
    side: str,
) -> dict[str, str]:
    reason = (
        f"{close_review['reason']}:{event['action']}:"
        f"security_key={event['security_key']}:"
        f"position_side={side}:"
        f"available={_quantity(close_review['available_quantity'])}:"
        f"unmatched={_quantity(close_review['unmatched_quantity'])}:"
        f"event={_quantity(event['quantity'])}"
    )
    issue = _record_review_issue(record, reason, event)
    issue.update({
        "position_side": side,
        "applicable_side": side,
        "requested_quantity": _quantity(event["quantity"]),
        "matched_quantity": _quantity(close_review["matched_quantity"]),
        "available_quantity": _quantity(close_review["available_quantity"]),
        "unmatched_quantity": _quantity(close_review["unmatched_quantity"]),
        "unmatched_reason": str(close_review["reason"]),
        "resolution_method": str(close_review["resolution_method"]),
        "closing_security_key": event["security_key"],
        "closing_structural_key": event["structural_key"],
        "closing_option_cusip": event["option_cusip"],
        "closing_identity_confidence": event["identity_confidence"],
    })
    matched_contexts = list(close_review.get("matched_contexts") or [])
    if matched_contexts:
        issue.update(_matched_lot_review_context(matched_contexts))
    return issue


def _matched_lot_review_context(contexts: list[Mapping[str, str]]) -> dict[str, str]:
    first = contexts[0]
    return {
        "matched_lot_security_key": str(first.get("security_key", "")),
        "matched_lot_structural_key": str(first.get("structural_key", "")),
        "matched_lot_option_cusip": str(first.get("option_cusip", "")),
        "matched_lot_identity_confidence": str(first.get("identity_confidence", "")),
        "matched_lot_security_keys": "|".join(
            _unique_context_values(contexts, "security_key")
        ),
        "matched_lot_identity_confidences": "|".join(
            _unique_context_values(contexts, "identity_confidence")
        ),
    }


def _unique_context_values(contexts: Iterable[Mapping[str, str]], field: str) -> list[str]:
    values: list[str] = []
    seen: set[str] = set()
    for context in contexts:
        value = str(context.get(field, ""))
        if value not in seen:
            values.append(value)
            seen.add(value)
    return values


def _unknown_basis_review_issue(
    record: Mapping[str, Any],
    event: Mapping[str, Any],
    matches: Iterable[Mapping[str, Any]],
) -> dict[str, str]:
    unknown_quantity = sum(
        (match["matched_quantity"] for match in matches if match["basis_status"] == "unknown"),
        Decimal("0"),
    )
    issue = _record_review_issue(record, "unknown_basis_option_closure", event)
    issue["unknown_basis_quantity"] = _quantity(unknown_quantity)
    return issue


def _anchor_review_issue(issue: Mapping[str, str]) -> dict[str, str]:
    return {
        "source_row_id": "",
        "activity_date": "",
        "settle_date": "",
        "transaction_code": "",
        "event_type": "position_anchor",
        "asset_type": "option",
        "security_key": str(issue.get("security_key", "")),
        "structural_key": str(issue.get("structural_key", "")),
        "option_cusip": str(issue.get("option_cusip", "")),
        "review_reason": str(issue.get("review_reason", "")),
    }


def _reconciliation_issues(
    records: Iterable[Mapping[str, Any]],
    as_of: date | None,
    anchors: Iterable[Mapping[str, Any]],
    open_lots: Iterable[Mapping[str, str]],
    contract_info: Mapping[str, Mapping[str, str]],
) -> list[dict[str, str]]:
    expected: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal("0"), Decimal("0")])
    expected_context: dict[str, Mapping[str, str]] = {}
    for row in open_lots:
        index = 0 if row["position_side"] == "long" else 1
        structural_key = row.get("structural_key") or row["security_key"]
        expected[structural_key][index] += Decimal(row["remaining_quantity"])
        expected_context.setdefault(structural_key, row)

    positions = build_position_ledger(records, as_of=as_of, anchors=anchors)["positions_as_of"]
    issues: list[dict[str, str]] = []
    for position in positions:
        if position["asset_type"] != "option":
            continue
        key = position["security_key"]
        actual_long = Decimal(position["trade_date_long_quantity"])
        actual_short = Decimal(position["trade_date_short_quantity"])
        expected_long, expected_short = expected.get(key, [Decimal("0"), Decimal("0")])
        if actual_long == expected_long and actual_short == expected_short:
            continue
        context = expected_context.get(key) or contract_info.get(key, {
            "security_key": key,
            "structural_key": key,
            "option_cusip": "",
            "identity_confidence": position["confidence"],
            "underlying": position["option_underlying"],
            "option_expiration": position["option_expiration"],
            "option_type": position["option_type"],
            "option_strike": position["option_strike"],
        })
        issues.append({
            **_contract_row_context({"security_key": key}, context),
            "source_row_id": "",
            "activity_date": "",
            "settle_date": "",
            "transaction_code": "",
            "event_type": "option_reconciliation",
            "asset_type": "option",
            "expected_long_quantity": _quantity(expected_long),
            "actual_long_quantity": _quantity(actual_long),
            "expected_short_quantity": _quantity(expected_short),
            "actual_short_quantity": _quantity(actual_short),
            "review_reason": "option_realized_side_quantity_reconciliation_failed",
        })
    return issues


def _explicit_position_side(record: Mapping[str, Any]) -> str:
    for field in ("position_side", "option_position_side", "lifecycle_side", "option_side"):
        side = str(record.get(field) or "").strip().lower()
        if side in {"long", "short"}:
            return side
    return ""


def _allocate(total: Decimal | None, quantity: Decimal, original_quantity: Decimal) -> Decimal:
    return (total or Decimal("0")) * quantity / original_quantity


def _days_between(start: date | None, end: date | None) -> int | None:
    if start is None or end is None:
        return None
    return (end - start).days


def _days_to_expiration(start: date | None, expiration: str) -> int | None:
    expiration_date, valid = _parse_date(expiration)
    if start is None or not valid:
        return None
    return (expiration_date - start).days


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


def _number_or_blank(value: int | None) -> str:
    return "" if value is None else str(value)


def _write_csv(path: Path, fields: list[str], rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
