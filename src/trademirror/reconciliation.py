from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Iterable, Mapping, Any


@dataclass(frozen=True)
class CashAnchor:
    label: str
    start_date: date
    end_date: date
    opening_cash: Decimal
    closing_cash: Decimal


@dataclass(frozen=True)
class ReconciliationAdjustment:
    label: str
    amount: Decimal
    reason: str


def reconcile_cash(
    records: Iterable[Mapping[str, Any]],
    anchor: CashAnchor,
    adjustments: Iterable[ReconciliationAdjustment] = (),
) -> dict[str, str | bool | int | list[str]]:
    imported_net = Decimal("0")
    settled_rows = 0
    invalid_row_count = 0
    review_reasons: set[str] = set()
    review_issues: list[dict[str, str | int]] = []
    for record in records:
        settle_text = str(record.get("settle_date") or "")
        amount_text = str(record.get("amount") or "")
        if not settle_text or not amount_text:
            continue
        source_row_id = record.get("source_row_id")
        try:
            settle_date = date.fromisoformat(settle_text)
        except ValueError:
            invalid_row_count += 1
            review_reasons.add("invalid_settle_date")
            issue: dict[str, str | int] = {"reason": "invalid_settle_date"}
            if source_row_id not in (None, ""):
                issue["source_row_id"] = source_row_id
            review_issues.append(issue)
            continue
        try:
            amount = Decimal(amount_text)
        except InvalidOperation:
            invalid_row_count += 1
            review_reasons.add("invalid_amount")
            issue = {"reason": "invalid_amount"}
            if source_row_id not in (None, ""):
                issue["source_row_id"] = source_row_id
            review_issues.append(issue)
            continue
        if not amount.is_finite():
            invalid_row_count += 1
            review_reasons.add("invalid_amount")
            issue = {"reason": "invalid_amount"}
            if source_row_id not in (None, ""):
                issue["source_row_id"] = source_row_id
            review_issues.append(issue)
            continue
        if anchor.start_date <= settle_date <= anchor.end_date:
            imported_net += amount
            settled_rows += 1

    adjustments = list(adjustments)
    adjustment_net = sum((item.amount for item in adjustments), Decimal("0"))
    calculated_closing = anchor.opening_cash + imported_net + adjustment_net
    difference = calculated_closing - anchor.closing_cash
    passed = difference == 0 and invalid_row_count == 0
    return {
        "label": anchor.label,
        "settled_rows": settled_rows,
        "invalid_row_count": invalid_row_count,
        "review_reasons": sorted(review_reasons),
        "review_issues": review_issues,
        "imported_net_cash": format(imported_net, ".2f"),
        "adjustment_net_cash": format(adjustment_net, ".2f"),
        "calculated_closing_cash": format(calculated_closing, ".2f"),
        "expected_closing_cash": format(anchor.closing_cash, ".2f"),
        "difference": format(difference, ".2f"),
        "passed": passed,
        "adjustment_count": len(adjustments),
    }
