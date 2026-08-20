from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable, Mapping


CASH_EVENT_FIELDS = [
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
]

DAILY_LEDGER_FIELDS = [
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
]

PENDING_SETTLEMENT_FIELDS = [
    "source_row_id",
    "activity_date",
    "settle_date",
    "transaction_code",
    "event_type",
    "signed_amount",
    "cash_category",
]


def build_cash_ledger(
    records: Iterable[Mapping[str, Any]],
    *,
    as_of: date | None = None,
    opening_cash: Decimal | None = None,
    opening_date: date | None = None,
) -> dict[str, Any]:
    if (opening_cash is None) != (opening_date is None):
        raise ValueError("opening_cash and opening_date must be provided together")

    events: list[dict[str, Any]] = []
    pending_settlement: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []

    for record in records:
        parsed = _cash_event_from_record(record)
        if parsed["exclude_from_totals"]:
            review.append(_review_issue(record, parsed["review_reason"]))
            continue

        if as_of and _is_pending_trade(parsed, as_of):
            pending_settlement.append(_pending_event(parsed))
            continue

        if as_of and parsed["settle_date"] > as_of:
            continue

        events.append(parsed)

    anchor_status, anchor_applied = _anchor_state(
        opening_cash=opening_cash,
        opening_date=opening_date,
        as_of=as_of,
    )
    daily = _build_daily_ledger(
        events,
        opening_cash=opening_cash if anchor_applied else None,
        opening_date=opening_date if anchor_applied else None,
        as_of=as_of,
    )
    summary = _build_summary(
        events,
        daily,
        pending_settlement,
        review,
        as_of=as_of,
        opening_cash=opening_cash,
        opening_date=opening_date,
        anchor_status=anchor_status,
        anchor_applied=anchor_applied,
    )
    return {
        "events": [_format_event(event) for event in events],
        "daily": daily,
        "pending_settlement": pending_settlement,
        "summary": summary,
        "review": {
            "review_count": len(review),
            "issues": review,
        },
    }


def write_cash_ledger_outputs(result: Mapping[str, Any], output_dir: str | Path) -> None:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    _write_csv(destination / "cash_ledger_events.csv", CASH_EVENT_FIELDS, result["events"])
    _write_csv(destination / "cash_ledger_daily.csv", DAILY_LEDGER_FIELDS, result["daily"])
    _write_csv(
        destination / "pending_settlement.csv",
        PENDING_SETTLEMENT_FIELDS,
        result["pending_settlement"],
    )
    (destination / "cash_ledger_summary.json").write_text(
        json.dumps(result["summary"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (destination / "cash_ledger_review.json").write_text(
        json.dumps(result["review"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _cash_event_from_record(record: Mapping[str, Any]) -> dict[str, Any]:
    amount, amount_valid = _parse_decimal(record.get("amount"))
    activity_date, activity_valid = _parse_date(record.get("activity_date"))
    settle_date, settle_valid = _parse_date(record.get("settle_date"))
    reasons = list(_split_reasons(record.get("review_reasons")))
    if str(record.get("review_status") or "") == "review" and not reasons:
        reasons.append("source_record_in_review")
    if not amount_valid:
        reasons.append("invalid_or_missing_amount")
    if not activity_valid:
        reasons.append("invalid_or_missing_activity_date")
    if not settle_valid:
        reasons.append("invalid_or_missing_settle_date")
    exclude_from_totals = not (amount_valid and activity_valid and settle_valid)

    category = _classify_cash_category(record, amount)
    if category == "Other/review":
        reasons.append("cash_category_review")

    review_reason = "|".join(sorted(set(reasons)))
    review_status = "review" if review_reason else "validated"
    confidence = "review" if review_status == "review" else "deterministic"
    return {
        "source_row_id": record.get("source_row_id", ""),
        "activity_date": activity_date,
        "settle_date": settle_date,
        "transaction_code": str(record.get("transaction_code_raw") or ""),
        "event_type": str(record.get("event_type") or ""),
        "signed_amount": amount,
        "cash_category": category,
        "external_cash_flow": _to_bool(record.get("external_cash_flow")),
        "internal_transfer": _to_bool(record.get("internal_transfer")),
        "confidence": confidence,
        "review_status": review_status,
        "review_reason": review_reason,
        "exclude_from_totals": exclude_from_totals,
    }


def _classify_cash_category(record: Mapping[str, Any], amount: Decimal | None) -> str:
    family = str(record.get("transaction_family") or "")
    event_type = str(record.get("event_type") or "")
    asset_type = str(record.get("asset_type") or "")
    external = _to_bool(record.get("external_cash_flow"))
    internal = _to_bool(record.get("internal_transfer"))

    if amount is not None and amount == 0:
        return "Other/review"
    if external and amount is not None and amount > 0:
        return "External contribution"
    if external and amount is not None and amount < 0:
        return "External withdrawal"
    if internal or family == "internal_transfer":
        return "Internal Robinhood transfer"
    if family == "trade" or (asset_type == "equity" and event_type in {"buy", "sell"}):
        return "Equity trade"
    if family in {"option_trade", "option_lifecycle"} or asset_type == "option":
        return "Option trade"
    if family == "income":
        return "Dividend or interest income"
    if family == "fee":
        return "Fee"
    if family == "financing":
        return "Margin/financing cost"
    if family == "corporate_action" and amount is not None:
        return "Corporate-action cash"
    return "Other/review"


def _build_daily_ledger(
    events: Iterable[Mapping[str, Any]],
    *,
    opening_cash: Decimal | None,
    opening_date: date | None,
    as_of: date | None,
) -> list[dict[str, str]]:
    events_by_date: dict[date, list[Mapping[str, Any]]] = defaultdict(list)
    for event in events:
        events_by_date[event["settle_date"]].append(event)

    if not events_by_date and opening_date is None:
        return []

    first_date = min(events_by_date) if events_by_date else opening_date
    last_date = max(events_by_date) if events_by_date else opening_date
    if opening_date is not None:
        first_date = min(first_date, opening_date)
        last_date = max(last_date, opening_date)
    if as_of is not None:
        last_date = min(last_date, as_of)
    if first_date > last_date:
        return []

    balance = Decimal("0")
    cumulative_change = Decimal("0")
    verified_balance_started = False
    rows: list[dict[str, str]] = []
    current = first_date
    while current <= last_date:
        buckets = _daily_buckets(events_by_date.get(current, []))
        net = sum(buckets.values(), Decimal("0"))
        cumulative_change += net
        if opening_cash is None:
            opening = balance
            closing = opening + net
            balance = closing
            opening_text = _money(opening)
            closing_text = _money(closing)
            confidence = "partial"
            position_type = "cumulative_change_from_zero"
        elif current < opening_date:
            opening_text = ""
            closing_text = ""
            confidence = "partial/unanchored"
            position_type = "pre_anchor_cumulative_change"
        elif current == opening_date:
            opening = opening_cash
            closing = opening + net
            balance = closing
            verified_balance_started = True
            opening_text = _money(opening)
            closing_text = _money(closing)
            confidence = "verified"
            position_type = "verified_account_balance"
        else:
            opening = balance
            closing = opening + net
            balance = closing
            opening_text = _money(opening)
            closing_text = _money(closing)
            confidence = "derived" if verified_balance_started else "partial/unanchored"
            position_type = (
                "verified_account_balance" if verified_balance_started
                else "pre_anchor_cumulative_change"
            )
        rows.append({
            "date": current.isoformat(),
            "opening_cash": opening_text,
            "external_inflows": _money(buckets["external_inflows"]),
            "external_outflows": _money(buckets["external_outflows"]),
            "trading_cash_flow": _money(buckets["trading_cash_flow"]),
            "income": _money(buckets["income"]),
            "fees": _money(buckets["fees"]),
            "financing_costs": _money(buckets["financing_costs"]),
            "internal_transfers": _money(buckets["internal_transfers"]),
            "other_cash_flow": _money(buckets["other_cash_flow"]),
            "net_cash_movement": _money(net),
            "closing_cash": closing_text,
            "balance_confidence": confidence,
            "cash_position_type": position_type,
        })
        current += timedelta(days=1)
    return rows


def _daily_buckets(events: Iterable[Mapping[str, Any]]) -> dict[str, Decimal]:
    buckets = {
        "external_inflows": Decimal("0"),
        "external_outflows": Decimal("0"),
        "trading_cash_flow": Decimal("0"),
        "income": Decimal("0"),
        "fees": Decimal("0"),
        "financing_costs": Decimal("0"),
        "internal_transfers": Decimal("0"),
        "other_cash_flow": Decimal("0"),
    }
    for event in events:
        amount = event["signed_amount"]
        category = event["cash_category"]
        if category == "External contribution":
            buckets["external_inflows"] += amount
        elif category == "External withdrawal":
            buckets["external_outflows"] += amount
        elif category in {"Equity trade", "Option trade"}:
            buckets["trading_cash_flow"] += amount
        elif category == "Dividend or interest income":
            buckets["income"] += amount
        elif category == "Fee":
            buckets["fees"] += amount
        elif category == "Margin/financing cost":
            buckets["financing_costs"] += amount
        elif category == "Internal Robinhood transfer":
            buckets["internal_transfers"] += amount
        else:
            buckets["other_cash_flow"] += amount
    return buckets


def _build_summary(
    events: list[Mapping[str, Any]],
    daily: list[Mapping[str, str]],
    pending_settlement: list[Mapping[str, str]],
    review: list[Mapping[str, Any]],
    *,
    as_of: date | None,
    opening_cash: Decimal | None,
    opening_date: date | None,
    anchor_status: str,
    anchor_applied: bool,
) -> dict[str, Any]:
    event_total = sum((event["signed_amount"] for event in events), Decimal("0"))
    daily_total = sum((Decimal(row["net_cash_movement"]) for row in daily), Decimal("0"))
    return {
        "as_of": as_of.isoformat() if as_of else "",
        "opening_cash": _money(opening_cash) if opening_cash is not None else "",
        "opening_date": opening_date.isoformat() if opening_date else "",
        "anchor_date": opening_date.isoformat() if opening_date else "",
        "anchor_confidence": "verified" if opening_cash is not None else "none",
        "anchor_status": anchor_status,
        "anchor_applied": anchor_applied,
        "balance_confidence": "verified_then_derived" if anchor_applied else "partial",
        "event_count": len(events),
        "pending_settlement_count": len(pending_settlement),
        "review_count": len(review),
        "event_net_cash_movement": _money(event_total),
        "daily_net_cash_movement": _money(daily_total),
        "daily_reconciles_to_events": daily_total == event_total,
        "ending_cash": daily[-1]["closing_cash"] if daily else "",
    }


def _anchor_state(
    *,
    opening_cash: Decimal | None,
    opening_date: date | None,
    as_of: date | None,
) -> tuple[str, bool]:
    if opening_cash is None:
        return "not_provided", False
    if as_of is not None and opening_date > as_of:
        return "future_unapplied", False
    return "applied", True


def _is_pending_trade(event: Mapping[str, Any], as_of: date) -> bool:
    return (
        event["cash_category"] in {"Equity trade", "Option trade"}
        and event["activity_date"] <= as_of
        and event["settle_date"] > as_of
    )


def _pending_event(event: Mapping[str, Any]) -> dict[str, str]:
    return {
        "source_row_id": str(event["source_row_id"]),
        "activity_date": event["activity_date"].isoformat(),
        "settle_date": event["settle_date"].isoformat(),
        "transaction_code": event["transaction_code"],
        "event_type": event["event_type"],
        "signed_amount": _money(event["signed_amount"]),
        "cash_category": event["cash_category"],
    }


def _format_event(event: Mapping[str, Any]) -> dict[str, str]:
    return {
        "source_row_id": str(event["source_row_id"]),
        "activity_date": event["activity_date"].isoformat(),
        "settle_date": event["settle_date"].isoformat(),
        "transaction_code": event["transaction_code"],
        "event_type": event["event_type"],
        "signed_amount": _money(event["signed_amount"]),
        "cash_category": event["cash_category"],
        "external_cash_flow": str(event["external_cash_flow"]).lower(),
        "internal_transfer": str(event["internal_transfer"]).lower(),
        "confidence": event["confidence"],
        "review_status": event["review_status"],
        "review_reason": event["review_reason"],
    }


def _review_issue(record: Mapping[str, Any], reason: str) -> dict[str, str]:
    return {
        "source_row_id": str(record.get("source_row_id", "")),
        "activity_date": str(record.get("activity_date", "")),
        "settle_date": str(record.get("settle_date", "")),
        "transaction_code": str(record.get("transaction_code_raw", "")),
        "event_type": str(record.get("event_type", "")),
        "review_reason": reason,
    }


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
        amount = Decimal(text)
    except InvalidOperation:
        return None, False
    return (amount, True) if amount.is_finite() else (None, False)


def _split_reasons(value: Any) -> list[str]:
    text = str(value or "").strip()
    return [item for item in text.split("|") if item]


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() == "true"


def _money(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.01")), "f")


def _write_csv(path: Path, fields: list[str], rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
