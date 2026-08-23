from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping

from .equity_identity import (
    anchor_resolution_review_issues,
    is_accepted_equity_anchor_status,
    resolve_equity_anchors,
)


POSITION_EVENT_FIELDS = [
    "source_row_id",
    "activity_date",
    "settle_date",
    "transaction_code",
    "event_type",
    "security_key",
    "cusip",
    "primary_symbol",
    "ticker_aliases",
    "asset_type",
    "option_underlying",
    "option_expiration",
    "option_type",
    "option_strike",
    "signed_quantity",
    "position_side",
    "confidence",
    "review_status",
    "review_reason",
]

POSITIONS_AS_OF_FIELDS = [
    "security_key",
    "cusip",
    "primary_observed_symbol",
    "ticker_aliases",
    "asset_type",
    "option_underlying",
    "option_expiration",
    "option_type",
    "option_strike",
    "trade_date_quantity",
    "settled_quantity",
    "trade_date_long_quantity",
    "trade_date_short_quantity",
    "settled_long_quantity",
    "settled_short_quantity",
    "anchor_date",
    "confidence",
    "review_status",
    "review_reasons",
]

POSITION_HISTORY_FIELDS = [
    "date",
    "security_key",
    "trade_date_quantity",
    "settled_quantity",
    "trade_date_long_quantity",
    "trade_date_short_quantity",
    "settled_long_quantity",
    "settled_short_quantity",
    "anchor_date",
    "confidence",
    "review_status",
    "review_reasons",
]

PENDING_POSITION_SETTLEMENT_FIELDS = [
    "source_row_id",
    "activity_date",
    "settle_date",
    "transaction_code",
    "event_type",
    "security_key",
    "signed_quantity",
    "position_side",
]

CORPORATE_ACTION_TYPES = {
    "cash_in_lieu",
    "split_or_reorganization",
    "merger",
    "basis_change",
    "stock_split",
    "reclassification",
    "security_exchange",
    "worthless_security",
}


def build_position_ledger(
    records: Iterable[Mapping[str, Any]],
    *,
    as_of: date | None = None,
    anchors: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    record_list = list(records)
    resolution = resolve_equity_anchors(anchors, record_list, as_of=as_of)
    parsed_anchors, anchor_review = _parse_anchors(resolution["anchors"], as_of=as_of)
    events: list[dict[str, Any]] = []
    pending_settlement: list[dict[str, str]] = []
    review_issues: list[dict[str, str]] = list(anchor_review)
    review_issues.extend(anchor_resolution_review_issues(resolution["report"]))

    match_state: dict[str, dict[str, Any]] = {}
    applicable_anchors = sorted(
        (
            anchor for anchor in parsed_anchors
            if as_of is None or anchor["anchor_date"] <= as_of
        ),
        key=lambda item: (item["anchor_date"], item["security_key"]),
    )
    next_anchor = 0
    anchor_events = _anchor_events(parsed_anchors)
    ordered_records = sorted(
        record_list,
        key=lambda record: (
            str(record.get("activity_date") or ""),
            str(record.get("settle_date") or ""),
            int(record.get("source_row_id") or 0),
        ),
    )

    for record in ordered_records:
        record_activity_date, record_activity_valid = _parse_date(record.get("activity_date"))
        if record_activity_valid:
            while (
                next_anchor < len(applicable_anchors)
                and applicable_anchors[next_anchor]["anchor_date"] <= record_activity_date
            ):
                _apply_anchor(match_state, applicable_anchors[next_anchor])
                next_anchor += 1
        parsed = _position_event_from_record(record, match_state, as_of=as_of)
        if parsed is None:
            continue
        if parsed["exclude_from_totals"]:
            review_issues.append(_review_issue(record, parsed["review_reason"], parsed.get("event")))
            continue
        event = parsed["event"]
        events.append(event)
        _ensure_security(match_state, event)
        _apply_trade_date_event(match_state[event["security_key"]], event)
        if as_of and event["activity_date"] <= as_of and event["settle_date"] > as_of:
            pending_settlement.append(_pending_event(event))
        if as_of is None or event["settle_date"] <= as_of:
            _apply_settled_event(match_state[event["security_key"]], event)

    state = _build_position_state(events, parsed_anchors, as_of=as_of)
    history = _build_history(events, anchor_events, parsed_anchors, as_of=as_of)
    positions = _positions_as_of(state)
    summary = _build_summary(events, positions, history, pending_settlement, review_issues, parsed_anchors)
    return {
        "events": [_format_event(event) for event in events],
        "positions_as_of": positions,
        "history": history,
        "pending_settlement": pending_settlement,
        "summary": summary,
        "review": {
            "review_count": len(review_issues),
            "issues": review_issues,
        },
        "anchor_validation": {
            "report_count": len(resolution["report"]),
            "anchors": resolution["report"],
        },
    }


def _build_position_state(
    events: Iterable[Mapping[str, Any]],
    anchors: Iterable[Mapping[str, Any]],
    *,
    as_of: date | None,
) -> dict[str, dict[str, Any]]:
    state: dict[str, dict[str, Any]] = {}
    trade_items: list[tuple[date, int, Mapping[str, Any]]] = []
    settle_items: list[tuple[date, int, Mapping[str, Any]]] = []
    for event in events:
        _ensure_security(state, event)
        trade_items.append((event["activity_date"], 1, event))
        if as_of is None or event["settle_date"] <= as_of:
            settle_items.append((event["settle_date"], 1, event))
    for anchor in anchors:
        if as_of is not None and anchor["anchor_date"] > as_of:
            continue
        _ensure_anchor_security(state, anchor)
        trade_items.append((anchor["anchor_date"], 0, anchor))
        settle_items.append((anchor["anchor_date"], 0, anchor))

    for _, kind, item in sorted(trade_items, key=lambda row: (row[0], row[1], str(row[2]["security_key"]))):
        if kind == 0:
            _apply_anchor_quantity(state[item["security_key"]], item, "trade_date_quantity")
        else:
            _apply_trade_date_event(state[item["security_key"]], item)
    for _, kind, item in sorted(settle_items, key=lambda row: (row[0], row[1], str(row[2]["security_key"]))):
        if kind == 0:
            _apply_anchor_quantity(state[item["security_key"]], item, "settled_quantity")
        else:
            _apply_settled_event(state[item["security_key"]], item)
    return state


def write_position_ledger_outputs(result: Mapping[str, Any], output_dir: str | Path) -> None:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    _write_csv(destination / "position_events.csv", POSITION_EVENT_FIELDS, result["events"])
    _write_csv(destination / "positions_as_of.csv", POSITIONS_AS_OF_FIELDS, result["positions_as_of"])
    _write_csv(destination / "position_history.csv", POSITION_HISTORY_FIELDS, result["history"])
    _write_csv(
        destination / "pending_position_settlement.csv",
        PENDING_POSITION_SETTLEMENT_FIELDS,
        result["pending_settlement"],
    )
    (destination / "position_summary.json").write_text(
        json.dumps(result["summary"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (destination / "position_review.json").write_text(
        json.dumps(result["review"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_position_anchors(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open("r", encoding="utf-8") as stream:
        payload = json.load(stream)
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("anchors"), list):
        return payload["anchors"]
    raise ValueError("Position anchor file must contain a JSON list or an object with an anchors list")


def _position_event_from_record(
    record: Mapping[str, Any],
    state: Mapping[str, Mapping[str, Any]],
    *,
    as_of: date | None,
) -> dict[str, Any] | None:
    family = str(record.get("transaction_family") or "")
    asset_type = str(record.get("asset_type") or "")
    event_type = str(record.get("event_type") or "")
    if family not in {"trade", "option_trade", "option_lifecycle", "corporate_action"}:
        return None

    activity_date, activity_valid = _parse_date(record.get("activity_date"))
    settle_date, settle_valid = _parse_date(record.get("settle_date"))
    quantity, quantity_valid = _parse_decimal(record.get("quantity_numeric"))
    reasons = list(_split_reasons(record.get("review_reasons")))
    if str(record.get("review_status") or "") == "review" and not reasons:
        reasons.append("source_record_in_review")
    if not activity_valid:
        reasons.append("invalid_or_missing_activity_date")
    if not settle_valid:
        reasons.append("invalid_or_missing_settle_date")
    if not quantity_valid:
        reasons.append("invalid_or_missing_quantity")
    if activity_valid and as_of is not None and activity_date > as_of:
        return None

    identity = _security_identity(record)
    reasons.extend(identity["review_reasons"])
    signed_quantity = Decimal("0")
    position_side = ""
    quantity_affects_position = True
    if quantity_valid:
        signed_quantity, position_side, quantity_affects_position, event_reasons = _signed_quantity(
            record,
            quantity,
            identity["security_key"],
            state,
        )
        reasons.extend(event_reasons)
    exclude_from_totals = not (
        activity_valid and settle_valid and quantity_valid and identity["valid"] and quantity_affects_position
    )
    review_reason = "|".join(sorted(set(reasons)))
    review_status = "review" if review_reason else "validated"
    if exclude_from_totals and not review_reason:
        review_reason = "position_event_not_supported"

    event = {
        "source_row_id": record.get("source_row_id", ""),
        "activity_date": activity_date,
        "settle_date": settle_date,
        "transaction_code": str(record.get("transaction_code_raw") or ""),
        "event_type": event_type,
        "security_key": identity["security_key"],
        "cusip": identity["cusip"],
        "primary_symbol": identity["primary_symbol"],
        "ticker_aliases": identity["primary_symbol"],
        "asset_type": identity["asset_type"],
        "option_underlying": identity["option_underlying"],
        "option_expiration": identity["option_expiration"],
        "option_type": identity["option_type"],
        "option_strike": identity["option_strike"],
        "signed_quantity": signed_quantity,
        "position_side": position_side,
        "confidence": "review" if review_status == "review" else identity["confidence"],
        "review_status": review_status,
        "review_reason": review_reason,
    }
    return {
        "event": event,
        "exclude_from_totals": exclude_from_totals,
        "review_reason": review_reason,
    }


def _security_identity(record: Mapping[str, Any]) -> dict[str, Any]:
    asset_type = str(record.get("asset_type") or "")
    symbol = str(record.get("instrument") or "").strip().upper()
    cusip = str(record.get("cusip") or "").strip().upper()
    reasons: list[str] = []
    if asset_type == "option":
        underlying = str(record.get("option_underlying") or "").strip().upper()
        expiration = str(record.get("option_expiration") or "").strip()
        option_type = str(record.get("option_type") or "").strip().lower()
        strike = str(record.get("option_strike") or "").strip()
        valid = bool(underlying and expiration and option_type and strike)
        if not valid:
            reasons.append("option_identity_incomplete")
        return {
            "security_key": f"option:{underlying}:{expiration}:{option_type}:{strike}",
            "cusip": "",
            "primary_symbol": underlying,
            "asset_type": "option",
            "option_underlying": underlying,
            "option_expiration": expiration,
            "option_type": option_type,
            "option_strike": strike,
            "confidence": "deterministic",
            "valid": valid,
            "review_reasons": reasons,
        }
    if asset_type == "equity":
        if cusip:
            return _equity_identity(cusip, symbol, "deterministic", True, reasons)
        if symbol:
            return _equity_identity("", symbol, "lower_symbol_only", True, reasons)
        reasons.append("equity_identity_missing")
        return _equity_identity("", "", "review", False, reasons)
    reasons.append("unsupported_position_asset_type")
    return _equity_identity(cusip, symbol, "review", False, reasons)


def _equity_identity(
    cusip: str,
    symbol: str,
    confidence: str,
    valid: bool,
    reasons: list[str],
) -> dict[str, Any]:
    key = f"equity:{cusip}" if cusip else f"equity-symbol:{symbol}"
    return {
        "security_key": key,
        "cusip": cusip,
        "primary_symbol": symbol,
        "asset_type": "equity",
        "option_underlying": "",
        "option_expiration": "",
        "option_type": "",
        "option_strike": "",
        "confidence": confidence,
        "valid": valid,
        "review_reasons": reasons,
    }


def _signed_quantity(
    record: Mapping[str, Any],
    quantity: Decimal,
    security_key: str,
    state: Mapping[str, Mapping[str, Any]],
) -> tuple[Decimal, str, bool, list[str]]:
    code = str(record.get("transaction_code_raw") or "")
    event_type = str(record.get("event_type") or "")
    family = str(record.get("transaction_family") or "")
    reasons: list[str] = []
    if family == "corporate_action" or event_type in CORPORATE_ACTION_TYPES:
        reasons.append(f"unresolved_corporate_action:{event_type or code}")
        return Decimal("0"), "", False, reasons
    if code == "Buy" or event_type == "buy":
        return quantity, "", True, reasons
    if code == "Sell" or event_type == "sell":
        invalid_reason = _equity_sell_review_reason(
            state,
            security_key,
            quantity,
            event_type or code,
        )
        if invalid_reason:
            reasons.append(invalid_reason)
            return Decimal("0"), "", False, reasons
        if not _has_anchor_or_quantity(state, security_key, "trade_date") and quantity > 0:
            reasons.append("sale_before_known_opening_position")
        return -quantity, "", True, reasons
    if code == "BTO" or event_type == "buy_to_open":
        return quantity, "long", True, reasons
    if code == "STC" or event_type == "sell_to_close":
        invalid_reason = _option_close_review_reason(
            state,
            security_key,
            quantity,
            event_type or code,
            required_side="long",
        )
        if invalid_reason:
            reasons.append(invalid_reason)
            return Decimal("0"), "long", False, reasons
        return -quantity, "long", True, reasons
    if code == "STO" or event_type == "sell_to_open":
        return -quantity, "short", True, reasons
    if code == "BTC" or event_type == "buy_to_close":
        invalid_reason = _option_close_review_reason(
            state,
            security_key,
            quantity,
            event_type or code,
            required_side="short",
        )
        if invalid_reason:
            reasons.append(invalid_reason)
            return Decimal("0"), "short", False, reasons
        return quantity, "short", True, reasons
    if event_type in {"expiration", "exercise", "assignment"}:
        required_side = _lifecycle_required_side(record, event_type, state, security_key)
        invalid_reason = _option_close_review_reason(
            state,
            security_key,
            quantity,
            event_type,
            required_side=required_side,
        )
        if invalid_reason:
            reasons.append(invalid_reason)
            return (
                Decimal("0"),
                required_side if required_side in {"long", "short"} else "",
                False,
                reasons,
            )
        side = required_side if required_side in {"long", "short"} else _option_lifecycle_side(state, security_key)
        signed_quantity = -quantity if side == "long" else quantity
        return signed_quantity, side, True, reasons
    reasons.append("position_event_not_supported")
    return Decimal("0"), "", False, reasons


def _equity_sell_review_reason(
    state: Mapping[str, Mapping[str, Any]],
    security_key: str,
    quantity: Decimal,
    event_type: str,
) -> str:
    item = state.get(security_key, {})
    anchor_date = str(item.get("anchor_date") or "")
    current = Decimal(str(item.get("trade_date_quantity", "0")))
    if not anchor_date:
        return ""
    available = max(current, Decimal("0"))
    if quantity > available:
        return _close_review_reason(
            "oversized_equity_close",
            event_type,
            security_key,
            quantity,
            available,
            anchor_date,
            str(item.get("position_anchor_quantity") or ""),
            "",
        )
    return ""


def _option_close_review_reason(
    state: Mapping[str, Mapping[str, Any]],
    security_key: str,
    quantity: Decimal,
    event_type: str,
    *,
    required_side: str,
) -> str:
    item = state.get(security_key, {})
    long_quantity = Decimal(str(item.get("trade_date_long_quantity", "0")))
    short_quantity = Decimal(str(item.get("trade_date_short_quantity", "0")))
    available = _available_option_quantity(long_quantity, short_quantity, required_side)
    anchor_date = str(item.get("anchor_date") or "")
    anchor_quantity = str(item.get("position_anchor_quantity") or "")
    if required_side == "long" and long_quantity <= 0:
        return _close_review_reason(
            "unmatched_option_close",
            event_type,
            security_key,
            quantity,
            available,
            anchor_date,
            anchor_quantity,
            "long",
        )
    if required_side == "short" and short_quantity <= 0:
        return _close_review_reason(
            "unmatched_option_close",
            event_type,
            security_key,
            quantity,
            available,
            anchor_date,
            anchor_quantity,
            "short",
        )
    if required_side == "either" and long_quantity > 0 and short_quantity > 0:
        return _close_review_reason(
            "ambiguous_option_lifecycle_side",
            event_type,
            security_key,
            quantity,
            available,
            anchor_date,
            anchor_quantity,
            "ambiguous",
        )
    if required_side == "either" and long_quantity == 0 and short_quantity == 0:
        return _close_review_reason(
            "unmatched_option_lifecycle",
            event_type,
            security_key,
            quantity,
            available,
            anchor_date,
            anchor_quantity,
            "ambiguous",
        )
    if quantity > available:
        return _close_review_reason(
            "oversized_option_close",
            event_type,
            security_key,
            quantity,
            available,
            anchor_date,
            anchor_quantity,
            required_side if required_side != "either" else _option_lifecycle_side(state, security_key),
        )
    return ""


def _lifecycle_required_side(
    record: Mapping[str, Any],
    event_type: str,
    state: Mapping[str, Mapping[str, Any]],
    security_key: str,
) -> str:
    if event_type == "exercise":
        return "long"
    if event_type == "assignment":
        return "short"
    explicit_side = _explicit_position_side(record)
    if event_type == "expiration" and explicit_side:
        return explicit_side
    return "either"


def _explicit_position_side(record: Mapping[str, Any]) -> str:
    for field in ("position_side", "option_position_side", "lifecycle_side", "option_side"):
        side = str(record.get(field) or "").strip().lower()
        if side in {"long", "short"}:
            return side
    return ""


def _available_option_quantity(
    long_quantity: Decimal,
    short_quantity: Decimal,
    required_side: str,
) -> Decimal:
    if required_side == "long":
        return long_quantity
    if required_side == "short":
        return short_quantity
    if long_quantity > 0 and short_quantity == 0:
        return long_quantity
    if short_quantity > 0 and long_quantity == 0:
        return short_quantity
    return Decimal("0")


def _option_lifecycle_side(state: Mapping[str, Mapping[str, Any]], security_key: str) -> str:
    item = state.get(security_key, {})
    long_quantity = Decimal(str(item.get("trade_date_long_quantity", "0")))
    short_quantity = Decimal(str(item.get("trade_date_short_quantity", "0")))
    if long_quantity > 0 and short_quantity == 0:
        return "long"
    if short_quantity > 0 and long_quantity == 0:
        return "short"
    return "ambiguous"


def _close_review_reason(
    reason: str,
    event_type: str,
    security_key: str,
    event_quantity: Decimal,
    available_quantity: Decimal,
    anchor_date: str,
    anchor_quantity: str,
    position_side: str,
) -> str:
    parts = [
        reason,
        event_type,
        f"security_key={security_key}",
        f"position_side={position_side}",
        f"available={_quantity(available_quantity)}",
        f"event={_quantity(event_quantity)}",
    ]
    if anchor_date:
        parts.append(f"anchor_date={anchor_date}")
        parts.append(f"anchor_quantity={anchor_quantity}")
    return ":".join(parts)


def _ensure_security(state: dict[str, dict[str, Any]], event: Mapping[str, Any]) -> None:
    item = state.setdefault(
        event["security_key"],
        {
            "security_key": event["security_key"],
            "cusip": event["cusip"],
            "primary_observed_symbol": event["primary_symbol"],
            "ticker_aliases": set(),
            "asset_type": event["asset_type"],
            "option_underlying": event["option_underlying"],
            "option_expiration": event["option_expiration"],
            "option_type": event["option_type"],
            "option_strike": event["option_strike"],
            "trade_date_quantity": Decimal("0"),
            "settled_quantity": Decimal("0"),
            "trade_date_long_quantity": Decimal("0"),
            "trade_date_short_quantity": Decimal("0"),
            "settled_long_quantity": Decimal("0"),
            "settled_short_quantity": Decimal("0"),
            "anchor_date": "",
            "confidence": event["confidence"],
            "review_reasons": set(),
            "trade_anchor_applied": False,
            "settle_anchor_applied": False,
            "position_anchor_quantity": "",
        },
    )
    if event["primary_symbol"]:
        item["ticker_aliases"].add(event["primary_symbol"])
        if not item["primary_observed_symbol"]:
            item["primary_observed_symbol"] = event["primary_symbol"]
    if event["review_status"] == "review":
        item["review_reasons"].update(_split_reasons(event["review_reason"]))
    if item["confidence"] != "review" and event["confidence"] == "lower_symbol_only":
        item["confidence"] = "lower_symbol_only"


def _apply_trade_date_event(item: dict[str, Any], event: Mapping[str, Any]) -> None:
    if item["anchor_date"] and event["activity_date"].isoformat() < item["anchor_date"]:
        item["review_reasons"].add("pre_anchor_history_excluded_from_verified_quantity")
        return
    item["trade_date_quantity"] += event["signed_quantity"]
    _apply_option_side_quantity(item, event, "trade_date")
    _check_negative_equity(item)


def _apply_settled_event(item: dict[str, Any], event: Mapping[str, Any]) -> None:
    if item["anchor_date"] and event["settle_date"].isoformat() < item["anchor_date"]:
        return
    item["settled_quantity"] += event["signed_quantity"]
    _apply_option_side_quantity(item, event, "settled")
    _check_negative_equity(item)


def _apply_option_side_quantity(
    item: dict[str, Any],
    event: Mapping[str, Any],
    prefix: str,
) -> None:
    if item["asset_type"] != "option":
        return
    quantity = event["signed_quantity"]
    side = str(event.get("position_side") or "")
    if side == "long":
        item[f"{prefix}_long_quantity"] += quantity
    elif side == "short":
        item[f"{prefix}_short_quantity"] -= quantity


def _apply_anchor(state: dict[str, dict[str, Any]], anchor: Mapping[str, Any]) -> None:
    item = state.setdefault(
        anchor["security_key"],
        {
            "security_key": anchor["security_key"],
            "cusip": anchor["cusip"],
            "primary_observed_symbol": anchor["primary_symbol"],
            "ticker_aliases": set(),
            "asset_type": anchor["asset_type"],
            "option_underlying": anchor["option_underlying"],
            "option_expiration": anchor["option_expiration"],
            "option_type": anchor["option_type"],
            "option_strike": anchor["option_strike"],
            "trade_date_quantity": Decimal("0"),
            "settled_quantity": Decimal("0"),
            "trade_date_long_quantity": Decimal("0"),
            "trade_date_short_quantity": Decimal("0"),
            "settled_long_quantity": Decimal("0"),
            "settled_short_quantity": Decimal("0"),
            "anchor_date": "",
            "confidence": "verified",
            "review_reasons": set(),
            "trade_anchor_applied": False,
            "settle_anchor_applied": False,
            "position_anchor_quantity": "",
        },
    )
    if anchor["primary_symbol"]:
        item["ticker_aliases"].add(anchor["primary_symbol"])
        if not item["primary_observed_symbol"]:
            item["primary_observed_symbol"] = anchor["primary_symbol"]
    item["trade_date_quantity"] = anchor["quantity"]
    item["settled_quantity"] = anchor["quantity"]
    _set_anchor_side_quantities(item, anchor)
    item["anchor_date"] = anchor["anchor_date"].isoformat()
    item["confidence"] = "verified"
    item["trade_anchor_applied"] = True
    item["settle_anchor_applied"] = True
    item["position_anchor_quantity"] = _quantity(anchor["quantity"])
    _check_negative_equity(item)


def _ensure_anchor_security(state: dict[str, dict[str, Any]], anchor: Mapping[str, Any]) -> None:
    item = state.setdefault(
        anchor["security_key"],
        {
            "security_key": anchor["security_key"],
            "cusip": anchor["cusip"],
            "primary_observed_symbol": anchor["primary_symbol"],
            "ticker_aliases": set(),
            "asset_type": anchor["asset_type"],
            "option_underlying": anchor["option_underlying"],
            "option_expiration": anchor["option_expiration"],
            "option_type": anchor["option_type"],
            "option_strike": anchor["option_strike"],
            "trade_date_quantity": Decimal("0"),
            "settled_quantity": Decimal("0"),
            "trade_date_long_quantity": Decimal("0"),
            "trade_date_short_quantity": Decimal("0"),
            "settled_long_quantity": Decimal("0"),
            "settled_short_quantity": Decimal("0"),
            "anchor_date": "",
            "confidence": "verified",
            "review_reasons": set(),
            "trade_anchor_applied": False,
            "settle_anchor_applied": False,
            "position_anchor_quantity": "",
        },
    )
    if anchor["primary_symbol"]:
        item["ticker_aliases"].add(anchor["primary_symbol"])
        if not item["primary_observed_symbol"]:
            item["primary_observed_symbol"] = anchor["primary_symbol"]


def _apply_anchor_quantity(
    item: dict[str, Any],
    anchor: Mapping[str, Any],
    quantity_field: str,
) -> None:
    item[quantity_field] = anchor["quantity"]
    _set_anchor_side_quantities(item, anchor, quantity_field)
    item["anchor_date"] = anchor["anchor_date"].isoformat()
    item["confidence"] = "verified"
    item["position_anchor_quantity"] = _quantity(anchor["quantity"])
    item["review_reasons"].discard("negative_equity_quantity_requires_review")
    item["review_reasons"].discard("sale_before_known_opening_position")
    _check_negative_equity(item)


def _set_anchor_side_quantities(
    item: dict[str, Any],
    anchor: Mapping[str, Any],
    quantity_field: str | None = None,
) -> None:
    if item["asset_type"] != "option":
        return
    prefixes = ["trade_date", "settled"] if quantity_field is None else [
        "trade_date" if quantity_field == "trade_date_quantity" else "settled"
    ]
    for prefix in prefixes:
        item[f"{prefix}_long_quantity"] = max(anchor["quantity"], Decimal("0"))
        item[f"{prefix}_short_quantity"] = abs(min(anchor["quantity"], Decimal("0")))


def _check_negative_equity(item: dict[str, Any]) -> None:
    if item["asset_type"] != "equity":
        return
    if item["trade_date_quantity"] < 0 or item["settled_quantity"] < 0:
        item["review_reasons"].add("negative_equity_quantity_requires_review")


def _parse_anchors(
    anchors: Iterable[Mapping[str, Any]],
    *,
    as_of: date | None,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    parsed: list[dict[str, Any]] = []
    review: list[dict[str, str]] = []
    for index, anchor in enumerate(anchors, start=1):
        anchor_date, date_valid = _parse_date(anchor.get("anchor_date") or anchor.get("date"))
        quantity, quantity_valid = _parse_decimal(anchor.get("quantity") or anchor.get("verified_quantity"))
        identity = _anchor_identity(anchor)
        reasons = list(identity["review_reasons"])
        if not date_valid:
            reasons.append("invalid_anchor_date")
        if not quantity_valid:
            reasons.append("invalid_anchor_quantity")
        resolution_status = str(anchor.get("anchor_resolution_status") or "")
        if identity["asset_type"] == "equity" and not is_accepted_equity_anchor_status(resolution_status):
            reasons.append(str(anchor.get("anchor_resolution_reason") or resolution_status or "unaccepted_equity_anchor"))
        if reasons:
            review.append({
                "anchor_index": str(index),
                "security_key": identity["security_key"],
                "review_reason": "|".join(sorted(set(reasons))),
            })
            continue
        status = "future_unapplied" if as_of is not None and anchor_date > as_of else "applied"
        parsed.append({
            **identity,
            "anchor_date": anchor_date,
            "quantity": quantity,
            "anchor_resolution_status": str(anchor.get("anchor_resolution_status") or ""),
            "anchor_resolution_reason": str(anchor.get("anchor_resolution_reason") or ""),
            "anchor_status": status,
        })
    return parsed, review


def _anchor_identity(anchor: Mapping[str, Any]) -> dict[str, Any]:
    asset_type = str(anchor.get("asset_type") or "equity").strip().lower()
    row = {
        "asset_type": asset_type,
        "instrument": anchor.get("symbol") or anchor.get("primary_symbol") or "",
        "cusip": anchor.get("cusip") or "",
        "option_underlying": anchor.get("option_underlying") or anchor.get("underlying") or "",
        "option_expiration": anchor.get("option_expiration") or anchor.get("expiration") or "",
        "option_type": anchor.get("option_type") or "",
        "option_strike": anchor.get("option_strike") or anchor.get("strike") or "",
    }
    identity = _security_identity(row)
    if anchor.get("resolved_identity_confidence"):
        identity["confidence"] = str(anchor.get("resolved_identity_confidence"))
    return identity


def _anchor_events(parsed_anchors: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    events = []
    for anchor in parsed_anchors:
        events.append({
            "date": anchor["anchor_date"],
            "security_key": anchor["security_key"],
            "quantity": anchor["quantity"],
            "anchor_date": anchor["anchor_date"].isoformat(),
        })
    return events


def _build_history(
    events: Iterable[Mapping[str, Any]],
    anchor_events: Iterable[Mapping[str, Any]],
    anchors: Iterable[Mapping[str, Any]],
    *,
    as_of: date | None,
) -> list[dict[str, str]]:
    event_list = list(events)
    security_keys = sorted(
        {event["security_key"] for event in event_list}
        | {anchor["security_key"] for anchor in anchors}
    )
    dates = {
        event["activity_date"] for event in event_list if as_of is None or event["activity_date"] <= as_of
    } | {
        event["settle_date"] for event in event_list if as_of is None or event["settle_date"] <= as_of
    } | {
        anchor["date"] for anchor in anchor_events if as_of is None or anchor["date"] <= as_of
    }
    trade_by_date: dict[tuple[date, str], Decimal] = defaultdict(Decimal)
    settle_by_date: dict[tuple[date, str], Decimal] = defaultdict(Decimal)
    trade_long_by_date: dict[tuple[date, str], Decimal] = defaultdict(Decimal)
    trade_short_by_date: dict[tuple[date, str], Decimal] = defaultdict(Decimal)
    settle_long_by_date: dict[tuple[date, str], Decimal] = defaultdict(Decimal)
    settle_short_by_date: dict[tuple[date, str], Decimal] = defaultdict(Decimal)
    anchor_by_key = {
        anchor["security_key"]: anchor
        for anchor in anchors
        if as_of is None or anchor["anchor_date"] <= as_of
    }
    for event in event_list:
        if as_of is None or event["activity_date"] <= as_of:
            trade_by_date[(event["activity_date"], event["security_key"])] += event["signed_quantity"]
            _add_history_side_quantity(trade_long_by_date, trade_short_by_date, event, "activity_date")
        if as_of is None or event["settle_date"] <= as_of:
            settle_by_date[(event["settle_date"], event["security_key"])] += event["signed_quantity"]
            _add_history_side_quantity(settle_long_by_date, settle_short_by_date, event, "settle_date")

    trade_qty = {key: Decimal("0") for key in security_keys}
    settle_qty = {key: Decimal("0") for key in security_keys}
    trade_long_qty = {key: Decimal("0") for key in security_keys}
    trade_short_qty = {key: Decimal("0") for key in security_keys}
    settle_long_qty = {key: Decimal("0") for key in security_keys}
    settle_short_qty = {key: Decimal("0") for key in security_keys}
    rows: list[dict[str, str]] = []
    for current in sorted(dates):
        for key in security_keys:
            anchor = anchor_by_key.get(key)
            anchor_date = anchor["anchor_date"] if anchor else None
            if anchor and current == anchor_date:
                trade_qty[key] = anchor["quantity"]
                settle_qty[key] = anchor["quantity"]
                trade_long_qty[key], trade_short_qty[key] = _anchor_side_quantities(anchor)
                settle_long_qty[key], settle_short_qty[key] = _anchor_side_quantities(anchor)
                trade_qty[key] += trade_by_date[(current, key)]
                settle_qty[key] += settle_by_date[(current, key)]
                trade_long_qty[key] += trade_long_by_date[(current, key)]
                trade_short_qty[key] += trade_short_by_date[(current, key)]
                settle_long_qty[key] += settle_long_by_date[(current, key)]
                settle_short_qty[key] += settle_short_by_date[(current, key)]
            elif anchor and current < anchor_date:
                pass
            else:
                trade_qty[key] += trade_by_date[(current, key)]
                settle_qty[key] += settle_by_date[(current, key)]
                trade_long_qty[key] += trade_long_by_date[(current, key)]
                trade_short_qty[key] += trade_short_by_date[(current, key)]
                settle_long_qty[key] += settle_long_by_date[(current, key)]
                settle_short_qty[key] += settle_short_by_date[(current, key)]
            if (current, key) not in trade_by_date and (current, key) not in settle_by_date and not (
                anchor and current == anchor_date
            ):
                continue
            confidence = "partial/unanchored"
            if anchor and current == anchor_date:
                confidence = "verified"
            elif anchor and current > anchor_date:
                confidence = "derived"
            elif not anchor:
                confidence = "partial"
            rows.append({
                "date": current.isoformat(),
                "security_key": key,
                "trade_date_quantity": _quantity(trade_qty[key]),
                "settled_quantity": _quantity(settle_qty[key]),
                "trade_date_long_quantity": _quantity(trade_long_qty[key]),
                "trade_date_short_quantity": _quantity(trade_short_qty[key]),
                "settled_long_quantity": _quantity(settle_long_qty[key]),
                "settled_short_quantity": _quantity(settle_short_qty[key]),
                "anchor_date": anchor_date.isoformat() if anchor_date else "",
                "confidence": confidence,
                "review_status": "validated",
                "review_reasons": "",
            })
    return rows


def _add_history_side_quantity(
    long_by_date: dict[tuple[date, str], Decimal],
    short_by_date: dict[tuple[date, str], Decimal],
    event: Mapping[str, Any],
    date_field: str,
) -> None:
    if event["asset_type"] != "option":
        return
    key = (event[date_field], event["security_key"])
    side = str(event.get("position_side") or "")
    quantity = event["signed_quantity"]
    if side == "long":
        long_by_date[key] += quantity
    elif side == "short":
        short_by_date[key] -= quantity


def _anchor_side_quantities(anchor: Mapping[str, Any]) -> tuple[Decimal, Decimal]:
    if anchor["asset_type"] != "option":
        return Decimal("0"), Decimal("0")
    return max(anchor["quantity"], Decimal("0")), abs(min(anchor["quantity"], Decimal("0")))


def _positions_as_of(state: Mapping[str, Mapping[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for key in sorted(state):
        item = state[key]
        reasons = sorted(item["review_reasons"])
        rows.append({
            "security_key": key,
            "cusip": item["cusip"],
            "primary_observed_symbol": item["primary_observed_symbol"],
            "ticker_aliases": "|".join(sorted(item["ticker_aliases"])),
            "asset_type": item["asset_type"],
            "option_underlying": item["option_underlying"],
            "option_expiration": item["option_expiration"],
            "option_type": item["option_type"],
            "option_strike": item["option_strike"],
            "trade_date_quantity": _quantity(item["trade_date_quantity"]),
            "settled_quantity": _quantity(item["settled_quantity"]),
            "trade_date_long_quantity": _quantity(item["trade_date_long_quantity"]),
            "trade_date_short_quantity": _quantity(item["trade_date_short_quantity"]),
            "settled_long_quantity": _quantity(item["settled_long_quantity"]),
            "settled_short_quantity": _quantity(item["settled_short_quantity"]),
            "anchor_date": item["anchor_date"],
            "confidence": item["confidence"],
            "review_status": "review" if reasons else "validated",
            "review_reasons": "|".join(reasons),
        })
    return rows


def _build_summary(
    events: list[Mapping[str, Any]],
    positions: list[Mapping[str, str]],
    history: list[Mapping[str, str]],
    pending_settlement: list[Mapping[str, str]],
    review_issues: list[Mapping[str, str]],
    anchors: list[Mapping[str, Any]],
) -> dict[str, Any]:
    event_totals: dict[str, Decimal] = defaultdict(Decimal)
    anchored_event_totals: dict[str, Decimal] = defaultdict(Decimal)
    pending_event_keys = {_event_key(event) for event in pending_settlement}
    effective_anchors = {
        anchor["security_key"]: anchor
        for anchor in anchors
        if anchor["anchor_status"] == "applied"
    }
    for event in events:
        event_totals[event["security_key"]] += event["signed_quantity"]
        anchor = effective_anchors.get(event["security_key"])
        if anchor is not None and event["activity_date"] >= anchor["anchor_date"]:
            anchored_event_totals[event["security_key"]] += event["signed_quantity"]
    position_totals = {row["security_key"]: Decimal(row["trade_date_quantity"]) for row in positions}
    expected_totals = dict(event_totals)
    for key, anchor in effective_anchors.items():
        expected_totals[key] = anchor["quantity"] + anchored_event_totals[key]
    trade_side_expected, settled_side_expected, comparison_dates = _expected_option_side_totals(
        events,
        effective_anchors,
        pending_event_keys,
    )
    option_trade_sides_reconcile = _append_option_side_reconciliation_issues(
        review_issues,
        positions,
        trade_side_expected,
        comparison_dates,
        "trade_date",
    )
    option_settled_sides_reconcile = _append_option_side_reconciliation_issues(
        review_issues,
        positions,
        settled_side_expected,
        comparison_dates,
        "settled",
    )
    net_trade_reconciles = all(
        position_totals.get(key, Decimal("0")) == total
        for key, total in expected_totals.items()
    )
    return {
        "event_count": len(events),
        "position_count": len(positions),
        "history_count": len(history),
        "pending_settlement_count": len(pending_settlement),
        "review_count": len(review_issues),
        "anchor_count": len(anchors),
        "future_anchor_count": sum(anchor["anchor_status"] == "future_unapplied" for anchor in anchors),
        "trade_date_reconciles_to_events": net_trade_reconciles and option_trade_sides_reconcile,
        "net_trade_date_reconciles_to_events": net_trade_reconciles,
        "option_trade_date_sides_reconcile_to_events": option_trade_sides_reconcile,
        "option_settled_sides_reconcile_to_events": option_settled_sides_reconcile,
        "settled_quantities_visible": True,
    }


def _event_key(event: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(event.get("source_row_id", "")),
        str(event.get("security_key", "")),
        str(event.get("settle_date", "")),
    )


def _expected_option_side_totals(
    events: Iterable[Mapping[str, Any]],
    effective_anchors: Mapping[str, Mapping[str, Any]],
    pending_event_keys: set[tuple[str, str, str]],
) -> tuple[
    dict[str, tuple[Decimal, Decimal]],
    dict[str, tuple[Decimal, Decimal]],
    dict[str, str],
]:
    trade_totals: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal("0"), Decimal("0")])
    settled_totals: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal("0"), Decimal("0")])
    anchored_trade_totals: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal("0"), Decimal("0")])
    anchored_settled_totals: dict[str, list[Decimal]] = defaultdict(lambda: [Decimal("0"), Decimal("0")])
    comparison_dates: dict[str, str] = {}
    for event in events:
        if event["asset_type"] != "option":
            continue
        key = event["security_key"]
        _add_option_side_total(trade_totals[key], event)
        comparison_dates[key] = max(comparison_dates.get(key, ""), event["activity_date"].isoformat())
        anchor = effective_anchors.get(key)
        if anchor is not None and event["activity_date"] >= anchor["anchor_date"]:
            _add_option_side_total(anchored_trade_totals[key], event)
        if _event_key(event) not in pending_event_keys:
            _add_option_side_total(settled_totals[key], event)
            if anchor is not None and event["settle_date"] >= anchor["anchor_date"]:
                _add_option_side_total(anchored_settled_totals[key], event)

    for key, anchor in effective_anchors.items():
        if anchor["asset_type"] != "option":
            continue
        anchor_long, anchor_short = _anchor_side_quantities(anchor)
        trade_totals[key] = [
            anchor_long + anchored_trade_totals[key][0],
            anchor_short + anchored_trade_totals[key][1],
        ]
        settled_totals[key] = [
            anchor_long + anchored_settled_totals[key][0],
            anchor_short + anchored_settled_totals[key][1],
        ]
        comparison_dates[key] = max(comparison_dates.get(key, ""), anchor["anchor_date"].isoformat())

    return (
        {key: (totals[0], totals[1]) for key, totals in trade_totals.items()},
        {key: (totals[0], totals[1]) for key, totals in settled_totals.items()},
        comparison_dates,
    )


def _add_option_side_total(total: list[Decimal], event: Mapping[str, Any]) -> None:
    side = str(event.get("position_side") or "")
    quantity = event["signed_quantity"]
    if side == "long":
        total[0] += quantity
    elif side == "short":
        total[1] -= quantity


def _append_option_side_reconciliation_issues(
    review_issues: list[Mapping[str, str]],
    positions: Iterable[Mapping[str, str]],
    expected_totals: Mapping[str, tuple[Decimal, Decimal]],
    comparison_dates: Mapping[str, str],
    view: str,
) -> bool:
    reconciles = True
    for position in positions:
        if position["asset_type"] != "option":
            continue
        key = position["security_key"]
        expected_long, expected_short = expected_totals.get(key, (Decimal("0"), Decimal("0")))
        actual_long = Decimal(position[f"{view}_long_quantity"])
        actual_short = Decimal(position[f"{view}_short_quantity"])
        if actual_long == expected_long and actual_short == expected_short:
            continue
        reconciles = False
        review_issues.append({
            "security_key": key,
            "cusip": position["cusip"],
            "primary_symbol": position["primary_observed_symbol"],
            "asset_type": position["asset_type"],
            "option_underlying": position["option_underlying"],
            "option_expiration": position["option_expiration"],
            "option_type": position["option_type"],
            "option_strike": position["option_strike"],
            "expected_long_quantity": _quantity(expected_long),
            "actual_long_quantity": _quantity(actual_long),
            "expected_short_quantity": _quantity(expected_short),
            "actual_short_quantity": _quantity(actual_short),
            "anchor_date": position["anchor_date"],
            "comparison_date": comparison_dates.get(key, ""),
            "review_reason": f"option_side_quantity_reconciliation_failed:{view}",
        })
    return reconciles


def _has_anchor_or_quantity(
    state: Mapping[str, Mapping[str, Any]],
    security_key: str,
    quantity_name: str,
) -> bool:
    item = state.get(security_key)
    if not item:
        return False
    return bool(item.get("anchor_date")) or Decimal(str(item.get(f"{quantity_name}_quantity", "0"))) > 0


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
        value = Decimal(text)
    except InvalidOperation:
        return None, False
    return (value, True) if value.is_finite() else (None, False)


def _split_reasons(value: Any) -> list[str]:
    text = str(value or "").strip()
    return [item for item in text.split("|") if item]


def _format_event(event: Mapping[str, Any]) -> dict[str, str]:
    return {
        "source_row_id": str(event["source_row_id"]),
        "activity_date": event["activity_date"].isoformat(),
        "settle_date": event["settle_date"].isoformat(),
        "transaction_code": event["transaction_code"],
        "event_type": event["event_type"],
        "security_key": event["security_key"],
        "cusip": event["cusip"],
        "primary_symbol": event["primary_symbol"],
        "ticker_aliases": event["ticker_aliases"],
        "asset_type": event["asset_type"],
        "option_underlying": event["option_underlying"],
        "option_expiration": event["option_expiration"],
        "option_type": event["option_type"],
        "option_strike": event["option_strike"],
        "signed_quantity": _quantity(event["signed_quantity"]),
        "position_side": event["position_side"],
        "confidence": event["confidence"],
        "review_status": event["review_status"],
        "review_reason": event["review_reason"],
    }


def _pending_event(event: Mapping[str, Any]) -> dict[str, str]:
    return {
        "source_row_id": str(event["source_row_id"]),
        "activity_date": event["activity_date"].isoformat(),
        "settle_date": event["settle_date"].isoformat(),
        "transaction_code": event["transaction_code"],
        "event_type": event["event_type"],
        "security_key": event["security_key"],
        "signed_quantity": _quantity(event["signed_quantity"]),
        "position_side": event["position_side"],
    }


def _review_issue(
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
        "review_reason": reason,
    }
    if event:
        issue["security_key"] = str(event.get("security_key", ""))
        issue["cusip"] = str(event.get("cusip", ""))
        issue["primary_symbol"] = str(event.get("primary_symbol", ""))
        issue["event_quantity"] = _quantity(event["signed_quantity"])
        issue["position_side"] = str(event.get("position_side", ""))
        _add_close_review_details(issue, reason)
    return issue


def _add_close_review_details(issue: dict[str, str], reason: str) -> None:
    for token in reason.split(":"):
        if "=" not in token:
            continue
        key, value = token.split("=", 1)
        if key == "available":
            issue["available_quantity"] = value
        elif key == "event":
            issue["source_event_quantity"] = value
        elif key == "anchor_date":
            issue["anchor_date"] = value
        elif key == "anchor_quantity":
            issue["anchor_quantity"] = value
        elif key == "position_side":
            issue["position_side"] = value


def _quantity(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _write_csv(path: Path, fields: list[str], rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
