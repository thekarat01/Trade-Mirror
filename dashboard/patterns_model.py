from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Mapping

from dashboard.data_loader import (
    BEHAVIORAL_CSV_SCHEMAS,
    BEHAVIORAL_JSON_FILES,
    DashboardData,
    LoadedFile,
    behavioral_csv_rows,
    behavioral_json_data,
)
from dashboard.formatters import format_currency, format_percent, parse_decimal


ADEQUATE_CONFIDENCE = {"medium", "high"}
PROHIBITED_VIEW_TOKENS = (
    "instrument_",
    "security_key",
    "option_cusip",
    "structural_key",
    "equity:",
    "option:",
    "cusip",
    "description_raw",
    "raw_row_json",
)


@dataclass(frozen=True)
class PatternValidationError(ValueError):
    issues: tuple[str, ...]

    def __str__(self) -> str:
        return "Behavioral insights are unavailable or invalid."


def build_patterns_view_model(data: DashboardData) -> dict[str, Any]:
    _require_behavioral_outputs(data)
    summary = behavioral_json_data(data, "behavioral_summary.json")
    candidates = _candidate_items(data)
    ranked = behavioral_json_data(data, "ranked_insights.json")
    validation = behavioral_json_data(data, "insight_validation.json")
    _validate_behavioral_reconciliation(data, summary, candidates, ranked, validation)

    priority = _unique_cards([*ranked.get("what_hurt", []), *ranked.get("what_helped", [])])[:3]
    helped = _unique_cards(ranked.get("what_helped", []))[:3]
    hurt = _unique_cards(ranked.get("what_hurt", []))[:3]
    guardrails = _guardrail_rows(ranked.get("priority_guardrails", []), candidates)[:3]
    model = {
        "available": True,
        "title": "My Patterns",
        "caption": "Evidence-backed patterns from your trusted completed trades.",
        "disclaimer": "This page describes historical aggregate evidence only. It does not predict future performance or provide security guidance.",
        "date_range": _date_range(summary),
        "coverage": _coverage(summary),
        "overall_confidence": _overall_confidence(candidates),
        "priority_patterns": [_card(item) for item in priority],
        "what_helped": [_card(item) for item in helped],
        "what_hurt": [_card(item) for item in hurt],
        "guardrails": guardrails,
        "charts": _charts(data, summary),
        "reliability": _reliability(summary, validation),
        "empty_states": _empty_states(summary, helped, hurt),
    }
    _assert_safe_view_model(model)
    return model


def _require_behavioral_outputs(data: DashboardData) -> None:
    issues: list[str] = []
    csv_files = data.behavioral_csv_files or {}
    json_files = data.behavioral_json_files or {}
    for name in BEHAVIORAL_CSV_SCHEMAS:
        loaded = csv_files.get(name, LoadedFile(name, False))
        if not loaded.available:
            issues.append(f"{name}: unavailable")
    for name in BEHAVIORAL_JSON_FILES:
        loaded = json_files.get(name, LoadedFile(name, False))
        if not loaded.available:
            issues.append(f"{name}: unavailable")
    if issues:
        raise PatternValidationError(tuple(issues))


def _candidate_items(data: DashboardData) -> list[Mapping[str, Any]]:
    raw = behavioral_json_data(data, "insight_candidates.json").get("items", [])
    return [item for item in raw if isinstance(item, Mapping)]


def _validate_behavioral_reconciliation(
    data: DashboardData,
    summary: Mapping[str, Any],
    candidates: list[Mapping[str, Any]],
    ranked: Mapping[str, Any],
    validation: Mapping[str, Any],
) -> None:
    issues: list[str] = []
    high_count = int(parse_decimal(summary.get("high_confidence_trade_count")) or Decimal("0"))
    if int(parse_decimal(validation.get("high_confidence_trade_count")) or Decimal("-1")) != high_count:
        issues.append("insight_validation.json: high-confidence count mismatch")
    if int(parse_decimal(validation.get("candidate_count")) or Decimal("-1")) != len(candidates):
        issues.append("insight_validation.json: candidate count mismatch")
    annual_total = sum((parse_decimal(row.get("net_pnl")) or Decimal("0") for row in behavioral_csv_rows(data, "annual_behavior.csv")), Decimal("0"))
    overall_pnl = parse_decimal(_path(summary, "overall", "net_realized_pnl")) or Decimal("0")
    if not _decimal_close(annual_total, overall_pnl):
        issues.append("annual_behavior.csv: net P&L does not reconcile to behavioral summary")
    candidate_by_code = {str(item.get("insight_code")): item for item in candidates}
    for section in ("what_helped", "what_hurt"):
        items = ranked.get(section, [])
        if not isinstance(items, list):
            issues.append(f"ranked_insights.json: {section} is invalid")
            continue
        for item in items:
            if not isinstance(item, Mapping):
                issues.append(f"ranked_insights.json: {section} contains invalid item")
                continue
            code = str(item.get("insight_code") or "")
            if code not in candidate_by_code:
                issues.append("ranked_insights.json: ranked insight references missing candidate")
            if str(item.get("confidence") or "") not in ADEQUATE_CONFIDENCE:
                issues.append("ranked_insights.json: ranked insight has inadequate confidence")
            for evidence in item.get("supporting_aggregate_evidence", []):
                if not _evidence_available(data, str(evidence)):
                    issues.append("ranked_insights.json: ranked insight references unavailable evidence")
    if issues:
        raise PatternValidationError(tuple(sorted(set(issues))))


def _evidence_available(data: DashboardData, evidence: str) -> bool:
    if evidence.startswith("behavioral_summary."):
        return bool(behavioral_json_data(data, "behavioral_summary.json"))
    csv_files = data.behavioral_csv_files or {}
    loaded = csv_files.get(evidence)
    return bool(loaded and loaded.available)


def _coverage(summary: Mapping[str, Any]) -> dict[str, str]:
    high = parse_decimal(summary.get("high_confidence_trade_count")) or Decimal("0")
    limited = parse_decimal(summary.get("limited_confidence_trade_count")) or Decimal("0")
    excluded = parse_decimal(summary.get("excluded_match_count")) or Decimal("0")
    total = high + limited + excluded
    return {
        "High-confidence completed trades": str(int(high)),
        "Limited-confidence trades": str(int(limited)),
        "Excluded matches": str(int(excluded)),
        "High-confidence coverage": format_percent((high / total * Decimal("100")) if total else None),
        "Date range": _date_range(summary),
    }


def _date_range(summary: Mapping[str, Any]) -> str:
    date_range = summary.get("date_range", {})
    if not isinstance(date_range, Mapping):
        return "Unavailable"
    start = str(date_range.get("start") or "").strip()
    end = str(date_range.get("end") or "").strip()
    if start and end:
        return f"{start} to {end}"
    return "Unavailable"


def _overall_confidence(candidates: list[Mapping[str, Any]]) -> str:
    weights = {"high": 3, "medium": 2, "low": 1, "insufficient_evidence": 0}
    primary = [
        weights.get(str(item.get("confidence")), 0)
        for item in candidates
        if str(item.get("confidence") or "") in ADEQUATE_CONFIDENCE
    ]
    eligible = primary or [weights.get(str(item.get("confidence")), 0) for item in candidates]
    if not eligible:
        return "Unavailable"
    value = min(eligible)
    for label, weight in weights.items():
        if weight == value:
            return _confidence_label(label)
    return "Unavailable"


def _unique_cards(items: list[Any]) -> list[Mapping[str, Any]]:
    seen: set[str] = set()
    output: list[Mapping[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        if str(item.get("confidence") or "") not in ADEQUATE_CONFIDENCE:
            continue
        code = str(item.get("insight_code") or "")
        if not code or code in seen:
            continue
        seen.add(code)
        output.append(item)
    return output


def _card(item: Mapping[str, Any]) -> dict[str, Any]:
    code = str(item.get("insight_code") or "")
    return {
        "title": _title_for(code),
        "finding_type": _plain_type(str(item.get("finding_type") or "")),
        "what_we_observed": str(item.get("finding") or ""),
        "supporting_metric": _metric_for(code, item.get("metric_value")),
        "comparison": _comparison_for(code, item.get("comparison_value")),
        "eligible_trade_count": str(item.get("eligible_trade_count") or "0"),
        "confidence": _confidence_label(str(item.get("confidence") or "")),
        "why_it_matters": _why_it_matters(code),
        "guardrail": str(item.get("educational_guardrail") or ""),
        "limitation": str(item.get("limitation") or ""),
        "supporting_evidence": [_evidence_label(str(value)) for value in item.get("supporting_aggregate_evidence", [])],
    }


def _guardrail_rows(items: list[Any], candidates: list[Mapping[str, Any]]) -> list[dict[str, str]]:
    candidate_by_code = {str(item.get("insight_code")): item for item in candidates}
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping):
            continue
        code = str(item.get("insight_code") or "")
        candidate = candidate_by_code.get(code, {})
        guardrail = str(item.get("educational_guardrail") or candidate.get("educational_guardrail") or "")
        if not guardrail or guardrail in seen:
            continue
        seen.add(guardrail)
        rows.append({
            "Pattern": _title_for(code),
            "Process guardrail": guardrail,
            "Evidence": ", ".join(_evidence_label(str(value)) for value in item.get("supporting_aggregate_evidence", [])),
        })
    return rows


def _charts(data: DashboardData, summary: Mapping[str, Any]) -> dict[str, list[dict[str, Any]]]:
    return {
        "asset_results": _asset_results(summary),
        "holding_period_results": _holding_rows(data),
        "annual_results": _annual_rows(data),
        "monthly_activity": _activity_rows(data),
        "loss_concentration": _loss_concentration_rows(summary),
        "reentry": _reentry_rows(data),
    }


def _asset_results(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    asset = summary.get("asset_type", {})
    if not isinstance(asset, Mapping):
        return []
    rows = []
    for key, label in (("equity", "Equity"), ("option", "Options")):
        values = asset.get(key, {})
        if isinstance(values, Mapping):
            rows.append({
                "Asset type": label,
                "Net realized P&L": parse_decimal(values.get("net_pnl")) or Decimal("0"),
                "Trade count": int(parse_decimal(values.get("trade_count")) or Decimal("0")),
                "Win rate": parse_decimal(values.get("win_rate")) or Decimal("0"),
            })
    return rows


def _holding_rows(data: DashboardData) -> list[dict[str, Any]]:
    return [
        {
            "Holding period": _holding_label(row.get("holding_period_bin", "")),
            "Trade count": int(parse_decimal(row.get("trade_count")) or Decimal("0")),
            "Net P&L": parse_decimal(row.get("net_pnl")) or Decimal("0"),
            "Win rate": parse_decimal(row.get("win_rate")),
        }
        for row in behavioral_csv_rows(data, "holding_period_behavior.csv")
    ]


def _annual_rows(data: DashboardData) -> list[dict[str, Any]]:
    return [
        {
            "Year": str(row.get("year") or ""),
            "High-confidence P&L": parse_decimal(row.get("net_pnl")) or Decimal("0"),
            "Trade count": int(parse_decimal(row.get("trade_count")) or Decimal("0")),
            "Confidence": _confidence_label(str(row.get("confidence") or "")),
        }
        for row in behavioral_csv_rows(data, "annual_behavior.csv")
    ]


def _activity_rows(data: DashboardData) -> list[dict[str, Any]]:
    return [
        {
            "Month": str(row.get("month") or ""),
            "Average P&L": parse_decimal(row.get("average_pnl")),
            "Net P&L": parse_decimal(row.get("net_pnl")) or Decimal("0"),
            "Trade count": int(parse_decimal(row.get("trade_count")) or Decimal("0")),
            "Win rate": parse_decimal(row.get("win_rate")),
            "Activity segment": "High activity" if row.get("activity_segment") == "high_activity" else "Other months",
        }
        for row in behavioral_csv_rows(data, "activity_behavior.csv")
    ]


def _loss_concentration_rows(summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    loss = summary.get("loss_concentration", {})
    if not isinstance(loss, Mapping):
        return []
    return [
        {"Group": "Largest loss", "Share of gross losses": _share(loss.get("largest_1_loss_share"))},
        {"Group": "Largest three losses", "Share of gross losses": _share(loss.get("largest_3_loss_share"))},
        {"Group": "Largest five losses", "Share of gross losses": _share(loss.get("largest_5_loss_share"))},
    ]


def _reentry_rows(data: DashboardData) -> list[dict[str, Any]]:
    rows = []
    for row in behavioral_csv_rows(data, "reentry_behavior.csv"):
        if row.get("confidence") not in ADEQUATE_CONFIDENCE:
            continue
        rows.append({
            "Window": f"{row.get('window_days')} days",
            "Trades after prior loss": int(parse_decimal(row.get("eligible_trade_count")) or Decimal("0")),
            "Net P&L": parse_decimal(row.get("net_pnl")) or Decimal("0"),
            "Comparison P&L": parse_decimal(row.get("comparison_net_pnl")) or Decimal("0"),
            "Confidence": _confidence_label(str(row.get("confidence") or "")),
        })
    return rows


def _reliability(summary: Mapping[str, Any], validation: Mapping[str, Any]) -> dict[str, Any]:
    sensitivity = summary.get("limited_confidence_sensitivity", {})
    if not isinstance(sensitivity, Mapping):
        sensitivity = {}
    sample = summary.get("sample_requirements", {})
    if not isinstance(sample, Mapping):
        sample = {}
    return {
        "coverage": _coverage(summary),
        "sample_rules": {
            "Overall patterns": str(sample.get("overall", "Unavailable")),
            "Compared segments": str(sample.get("segment", "Unavailable")),
            "Activity months": str(sample.get("activity_months", "Unavailable")),
            "Re-entry samples": str(sample.get("reentry", "Unavailable")),
        },
        "confidence_definitions": {
            "High": "Large enough sample, stable sensitivity check and no material excluded-record pressure.",
            "Medium": "Adequate evidence with a smaller sample or sensitivity caveat.",
            "Low": "Material excluded-record pressure limits reliability.",
            "Insufficient evidence": "Minimum sample rules were not met.",
        },
        "sensitivity": {
            "Direction stable": "Yes" if sensitivity.get("net_pnl_direction_stable") else "No or unavailable",
            "Limited-confidence trades kept separate": "Yes" if sensitivity.get("kept_separate_from_primary") else "No",
        },
        "validation": {
            "High-confidence P&L reconciles": "Yes" if validation.get("high_confidence_pnl_reconciles_to_trusted_dataset") else "No",
            "Excluded records used in primary metrics": "No" if not validation.get("excluded_records_used_in_primary_metrics") else "Yes",
        },
        "limitations": [str(item) for item in summary.get("limitations", []) if item],
    }


def _empty_states(summary: Mapping[str, Any], helped: list[Mapping[str, Any]], hurt: list[Mapping[str, Any]]) -> list[str]:
    states: list[str] = []
    if int(parse_decimal(summary.get("high_confidence_trade_count")) or Decimal("0")) == 0:
        states.append("No high-confidence completed trades are available yet.")
    if not helped:
        states.append("No positive pattern has enough evidence to show as a primary finding.")
    if not hurt:
        states.append("No negative pattern has enough evidence to show as a primary finding.")
    reentry = summary.get("reentry_after_loss", {})
    if isinstance(reentry, Mapping):
        rows = [value for value in reentry.values() if isinstance(value, Mapping)]
        if rows and all(row.get("confidence") not in ADEQUATE_CONFIDENCE for row in rows):
            states.append("No re-entry-after-loss pattern has enough evidence for a primary finding.")
    if not isinstance(summary.get("limited_confidence_sensitivity"), Mapping):
        states.append("Limited-confidence sensitivity data is unavailable.")
    return states


def _assert_safe_view_model(model: Mapping[str, Any]) -> None:
    rendered = str(model).casefold()
    for token in PROHIBITED_VIEW_TOKENS:
        if token in rendered:
            raise PatternValidationError(("Behavioral view contains prohibited identifier context.",))


def _title_for(code: str) -> str:
    return {
        "overall_result": "Overall completed-trade result",
        "asset_option_relative_result": "Equity and option outcomes differed",
        "holding_loser_duration": "Losing trades stayed open longer",
        "loss_concentration_top_losses": "Losses were concentrated",
        "activity_high_months": "High-activity months differed",
        "reentry_after_loss_30d": "Re-entry after losses",
        "insufficient_reentry_7d": "Short-window re-entry evidence was limited",
    }.get(code, "Behavioral pattern")


def _metric_for(code: str, value: object) -> str:
    if code in {"overall_result", "asset_option_relative_result", "activity_high_months", "reentry_after_loss_30d"}:
        return format_currency(value)
    if code == "loss_concentration_top_losses":
        return format_percent(_share(value))
    if code == "holding_loser_duration":
        return f"{value} days"
    return str(value or "Unavailable")


def _comparison_for(code: str, value: object) -> str:
    if code in {"asset_option_relative_result", "activity_high_months", "reentry_after_loss_30d"}:
        return format_currency(value)
    if code == "holding_loser_duration":
        return f"{value} days"
    return str(value or "Unavailable")


def _plain_type(value: str) -> str:
    if value == "helped":
        return "Helped"
    if value == "hurt":
        return "Hurt"
    return "Pattern"


def _confidence_label(value: str) -> str:
    return {
        "high": "High",
        "medium": "Medium",
        "low": "Low",
        "insufficient_evidence": "Insufficient evidence",
    }.get(value, "Unavailable")


def _why_it_matters(code: str) -> str:
    return {
        "overall_result": "It sets the baseline for reviewing completed trades without mixing in deposits, withdrawals or unresolved records.",
        "asset_option_relative_result": "Different product types can have different evidence quality and risk profiles.",
        "holding_loser_duration": "Holding periods can reveal whether losing trades stayed open materially longer than winning trades.",
        "loss_concentration_top_losses": "Concentrated losses can dominate many smaller gains.",
        "activity_high_months": "Activity bands help compare busier months with quieter months without claiming causation.",
        "reentry_after_loss_30d": "Rapid re-entry after a loss is worth reviewing as a process pattern, not as proof of intent.",
    }.get(code, "It provides a historical aggregate checkpoint for your review process.")


def _evidence_label(value: str) -> str:
    return {
        "behavioral_summary.overall": "Overall behavioral summary",
        "behavioral_summary.asset_type": "Asset-type behavioral summary",
        "behavioral_summary.loss_concentration": "Loss-concentration summary",
        "holding_period_behavior.csv": "Holding-period behavior",
        "activity_behavior.csv": "Monthly activity behavior",
        "reentry_behavior.csv": "Re-entry behavior",
    }.get(value, "Aggregate behavioral output")


def _holding_label(value: object) -> str:
    return {
        "same_day": "Same day",
        "1_7_days": "1-7 days",
        "8_30_days": "8-30 days",
        "31_90_days": "31-90 days",
        "more_than_90_days": "More than 90 days",
    }.get(str(value), str(value or "Unavailable"))


def _share(value: object) -> Decimal | None:
    amount = parse_decimal(value)
    if amount is None:
        return None
    return amount * Decimal("100")


def _decimal_close(left: Decimal, right: Decimal) -> bool:
    return abs(left - right) <= Decimal("0.000001")


def _path(data: Mapping[str, Any], *parts: str) -> object:
    current: object = data
    for part in parts:
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current
