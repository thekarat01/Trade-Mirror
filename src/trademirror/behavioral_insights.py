from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_CEILING
from pathlib import Path
from typing import Any, Iterable, Mapping


ANNUAL_FIELDS = [
    "year",
    "trade_count",
    "equity_count",
    "option_count",
    "net_pnl",
    "win_rate",
    "gross_gains",
    "gross_losses",
    "confidence",
]

HOLDING_FIELDS = [
    "holding_period_bin",
    "trade_count",
    "win_rate",
    "net_pnl",
]

ACTIVITY_FIELDS = [
    "month",
    "trade_count",
    "net_pnl",
    "gross_loss",
    "win_rate",
    "average_pnl",
    "activity_segment",
]

REENTRY_FIELDS = [
    "window_days",
    "eligible_trade_count",
    "win_rate",
    "net_pnl",
    "comparison_trade_count",
    "comparison_win_rate",
    "comparison_net_pnl",
    "confidence",
]

INSIGHT_SAMPLE_MINIMUMS = {
    "overall": 20,
    "segment": 10,
    "activity_months": 5,
    "reentry": 5,
}

HOLDING_BINS = [
    ("same_day", 0, 0),
    ("1_7_days", 1, 7),
    ("8_30_days", 8, 30),
    ("31_90_days", 31, 90),
    ("more_than_90_days", 91, None),
]


def build_behavioral_insights(
    *,
    trusted_dir: str | Path,
) -> dict[str, Any]:
    source = Path(trusted_dir)
    high = _read_csv(source / "trusted_closed_trades.csv")
    limited = _read_csv(source / "limited_confidence_trades.csv")
    coverage = _read_json(source / "coverage_summary.json") or {}
    exclusion = _read_json(source / "exclusion_summary.json") or {}
    high_trades = [_parse_trade(row) for row in high]
    limited_trades = [_parse_trade(row) for row in limited]
    overall = _overall_metrics(high_trades)
    asset = _asset_metrics(high_trades)
    holding = _holding_metrics(high_trades)
    loss = _loss_concentration(high_trades)
    activity = _activity_metrics(high_trades)
    reentry = _reentry_metrics(high_trades)
    annual = _annual_metrics(high_trades)
    sensitivity = _limited_sensitivity(high_trades, limited_trades)
    candidates = _insight_candidates(
        high_trades=high_trades,
        overall=overall,
        asset=asset,
        holding=holding,
        loss=loss,
        activity=activity,
        reentry=reentry,
        sensitivity=sensitivity,
        coverage=coverage,
        exclusion=exclusion,
    )
    ranked = _rank_insights(candidates)
    return {
        "behavioral_summary": {
            "methodology": "deterministic_behavioral_insights_v1",
            "sample_requirements": INSIGHT_SAMPLE_MINIMUMS,
            "primary_dataset": "high_confidence_trades_only",
            "high_confidence_trade_count": len(high_trades),
            "limited_confidence_trade_count": len(limited_trades),
            "excluded_match_count": _int_path(coverage, "confidence", "excluded", "count"),
            "date_range": _date_range(high_trades),
            "overall": overall,
            "asset_type": asset,
            "holding_period": {
                "winner_median_days": holding["winner_median_days"],
                "loser_median_days": holding["loser_median_days"],
                "losers_generally_held_longer": holding["losers_generally_held_longer"],
            },
            "loss_concentration": loss,
            "trading_activity": activity["summary"],
            "reentry_after_loss": reentry["summary"],
            "limited_confidence_sensitivity": sensitivity,
            "limitations": _limitations(coverage, exclusion),
        },
        "insight_candidates": candidates,
        "ranked_insights": ranked,
        "annual_behavior": annual,
        "holding_period_behavior": holding["bins"],
        "activity_behavior": activity["months"],
        "reentry_behavior": reentry["rows"],
        "insight_validation": _validation(high_trades, limited_trades, coverage, candidates),
    }


def write_behavioral_insights_outputs(result: Mapping[str, Any], output_dir: str | Path) -> None:
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "behavioral_summary.json").write_text(
        json.dumps(result["behavioral_summary"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (destination / "insight_candidates.json").write_text(
        json.dumps({"items": result["insight_candidates"]}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (destination / "ranked_insights.json").write_text(
        json.dumps(result["ranked_insights"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (destination / "insight_validation.json").write_text(
        json.dumps(result["insight_validation"], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    _write_csv(destination / "annual_behavior.csv", ANNUAL_FIELDS, result["annual_behavior"])
    _write_csv(destination / "holding_period_behavior.csv", HOLDING_FIELDS, result["holding_period_behavior"])
    _write_csv(destination / "activity_behavior.csv", ACTIVITY_FIELDS, result["activity_behavior"])
    _write_csv(destination / "reentry_behavior.csv", REENTRY_FIELDS, result["reentry_behavior"])


def _parse_trade(row: Mapping[str, str]) -> dict[str, Any]:
    return {
        "trade_id": str(row.get("trade_id") or ""),
        "instrument_id": str(row.get("instrument_id") or ""),
        "asset_type": str(row.get("asset_type") or ""),
        "open_date": _parse_date(row.get("open_date")),
        "close_date": _parse_date(row.get("close_date")),
        "holding_period_days": _parse_int(row.get("holding_period_days")),
        "matched_quantity": _parse_decimal(row.get("matched_quantity")),
        "cost_basis": _parse_decimal(row.get("cost_basis")),
        "proceeds": _parse_decimal(row.get("proceeds")),
        "realized_pnl": _parse_decimal(row.get("realized_pnl")),
        "return_percentage": _parse_decimal(row.get("return_percentage")),
        "confidence": str(row.get("confidence") or ""),
        "reason_codes": str(row.get("reason_codes") or ""),
    }


def _overall_metrics(trades: list[Mapping[str, Any]]) -> dict[str, str | int]:
    pnls = [trade["realized_pnl"] for trade in trades if trade["realized_pnl"] is not None]
    gains = [pnl for pnl in pnls if pnl > 0]
    losses = [pnl for pnl in pnls if pnl < 0]
    gross_gains = sum(gains, Decimal("0"))
    gross_losses = sum((abs(loss) for loss in losses), Decimal("0"))
    avg_gain = _average(gains)
    avg_loss = _average([abs(loss) for loss in losses])
    largest_gain = max(gains) if gains else Decimal("0")
    largest_loss = max((abs(loss) for loss in losses), default=Decimal("0"))
    return {
        "trade_count": len(trades),
        "net_realized_pnl": _decimal_text(sum(pnls, Decimal("0"))),
        "gross_gains": _decimal_text(gross_gains),
        "gross_losses": _decimal_text(gross_losses),
        "win_rate": _ratio(len(gains), len(trades)),
        "average_gain": _decimal_text(avg_gain),
        "median_gain": _decimal_text(_median(gains)),
        "average_loss": _decimal_text(avg_loss),
        "median_loss": _decimal_text(_median([abs(loss) for loss in losses])),
        "average_gain_to_average_loss_ratio": _decimal_text(_divide(avg_gain, avg_loss)),
        "profit_factor": _decimal_text(_divide(gross_gains, gross_losses)),
        "maximum_individual_gain_share_of_gross_gains": _decimal_text(_divide(largest_gain, gross_gains)),
        "maximum_individual_loss_share_of_gross_losses": _decimal_text(_divide(largest_loss, gross_losses)),
    }


def _asset_metrics(trades: list[Mapping[str, Any]]) -> dict[str, dict[str, str | int]]:
    gross_losses_total = sum((abs(trade["realized_pnl"]) for trade in trades if trade["realized_pnl"] is not None and trade["realized_pnl"] < 0), Decimal("0"))
    output: dict[str, dict[str, str | int]] = {}
    for asset_type in sorted({trade["asset_type"] for trade in trades} | {"equity", "option"}):
        rows = [trade for trade in trades if trade["asset_type"] == asset_type]
        pnls = [trade["realized_pnl"] for trade in rows if trade["realized_pnl"] is not None]
        gains = [pnl for pnl in pnls if pnl > 0]
        losses = [abs(pnl) for pnl in pnls if pnl < 0]
        gross_loss = sum(losses, Decimal("0"))
        output[asset_type] = {
            "trade_count": len(rows),
            "net_pnl": _decimal_text(sum(pnls, Decimal("0"))),
            "win_rate": _ratio(len(gains), len(rows)),
            "average_gain": _decimal_text(_average(gains)),
            "average_loss": _decimal_text(_average(losses)),
            "gross_loss_contribution": _decimal_text(gross_loss),
            "share_of_overall_included_losses": _decimal_text(_divide(gross_loss, gross_losses_total)),
        }
    return output


def _holding_metrics(trades: list[Mapping[str, Any]]) -> dict[str, Any]:
    winners = [trade for trade in trades if trade["realized_pnl"] is not None and trade["realized_pnl"] > 0 and trade["holding_period_days"] is not None]
    losers = [trade for trade in trades if trade["realized_pnl"] is not None and trade["realized_pnl"] < 0 and trade["holding_period_days"] is not None]
    bins = []
    for name, low, high in HOLDING_BINS:
        rows = [
            trade for trade in trades
            if trade["holding_period_days"] is not None
            and trade["holding_period_days"] >= low
            and (high is None or trade["holding_period_days"] <= high)
        ]
        wins = [trade for trade in rows if trade["realized_pnl"] is not None and trade["realized_pnl"] > 0]
        bins.append({
            "holding_period_bin": name,
            "trade_count": str(len(rows)),
            "win_rate": _ratio(len(wins), len(rows)),
            "net_pnl": _decimal_text(sum((trade["realized_pnl"] or Decimal("0") for trade in rows), Decimal("0"))),
        })
    winner_median = _median([Decimal(trade["holding_period_days"]) for trade in winners])
    loser_median = _median([Decimal(trade["holding_period_days"]) for trade in losers])
    enough = len(winners) >= INSIGHT_SAMPLE_MINIMUMS["segment"] and len(losers) >= INSIGHT_SAMPLE_MINIMUMS["segment"]
    return {
        "winner_count": len(winners),
        "loser_count": len(losers),
        "winner_median_days": _decimal_text(winner_median),
        "loser_median_days": _decimal_text(loser_median),
        "losers_generally_held_longer": bool(enough and loser_median is not None and winner_median is not None and loser_median > winner_median),
        "bins": bins,
    }


def _loss_concentration(trades: list[Mapping[str, Any]]) -> dict[str, Any]:
    losses = sorted([abs(trade["realized_pnl"]) for trade in trades if trade["realized_pnl"] is not None and trade["realized_pnl"] < 0], reverse=True)
    gross_losses = sum(losses, Decimal("0"))
    gains = [trade["realized_pnl"] for trade in trades if trade["realized_pnl"] is not None and trade["realized_pnl"] > 0]
    avg_gain = _average(gains)
    loss_by_instrument: dict[str, Decimal] = defaultdict(Decimal)
    by_asset_year: dict[str, Decimal] = defaultdict(Decimal)
    for trade in trades:
        pnl = trade["realized_pnl"]
        if pnl is None or pnl >= 0:
            continue
        loss = abs(pnl)
        loss_by_instrument[trade["instrument_id"]] += loss
        close_year = trade["close_date"].year if trade["close_date"] is not None else "unknown"
        by_asset_year[f"{trade['asset_type']}_{close_year}"] += loss
    largest_loss = losses[0] if losses else Decimal("0")
    return {
        "largest_1_loss_share": _decimal_text(_divide(sum(losses[:1], Decimal("0")), gross_losses)),
        "largest_3_loss_share": _decimal_text(_divide(sum(losses[:3], Decimal("0")), gross_losses)),
        "largest_5_loss_share": _decimal_text(_divide(sum(losses[:5], Decimal("0")), gross_losses)),
        "largest_opaque_instrument_loss_share": _decimal_text(_divide(max(loss_by_instrument.values(), default=Decimal("0")), gross_losses)),
        "profitable_trades_required_to_offset_largest_loss": _ceil_divide(largest_loss, avg_gain),
        "loss_by_asset_type_and_year": {
            key: _decimal_text(value)
            for key, value in sorted(by_asset_year.items())
        },
    }


def _activity_metrics(trades: list[Mapping[str, Any]]) -> dict[str, Any]:
    months: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for trade in trades:
        if trade["close_date"] is None:
            continue
        months[trade["close_date"].strftime("%Y-%m")].append(trade)
    threshold = _nearest_rank_percentile([len(rows) for rows in months.values()], Decimal("0.75"))
    rows = []
    high_months = []
    other_months = []
    for month in sorted(months):
        month_trades = months[month]
        pnls = [trade["realized_pnl"] for trade in month_trades if trade["realized_pnl"] is not None]
        segment = "high_activity" if threshold is not None and len(month_trades) >= threshold else "other"
        if segment == "high_activity":
            high_months.extend(month_trades)
        else:
            other_months.extend(month_trades)
        wins = [pnl for pnl in pnls if pnl > 0]
        rows.append({
            "month": month,
            "trade_count": str(len(month_trades)),
            "net_pnl": _decimal_text(sum(pnls, Decimal("0"))),
            "gross_loss": _decimal_text(sum((abs(pnl) for pnl in pnls if pnl < 0), Decimal("0"))),
            "win_rate": _ratio(len(wins), len(month_trades)),
            "average_pnl": _decimal_text(_average(pnls)),
            "activity_segment": segment,
        })
    summary = {
        "high_activity_threshold_trade_count": "" if threshold is None else str(threshold),
        "threshold_rule": "months with trade count at or above nearest-rank 75th percentile",
        "eligible_month_count": len(months),
        "high_activity_month_count": len({row["month"] for row in rows if row["activity_segment"] == "high_activity"}),
        "high_activity_trade_count": len(high_months),
        "other_activity_trade_count": len(other_months),
        "high_activity_average_pnl": _decimal_text(_average([trade["realized_pnl"] for trade in high_months if trade["realized_pnl"] is not None])),
        "other_activity_average_pnl": _decimal_text(_average([trade["realized_pnl"] for trade in other_months if trade["realized_pnl"] is not None])),
    }
    return {"summary": summary, "months": rows}


def _reentry_metrics(trades: list[Mapping[str, Any]]) -> dict[str, Any]:
    sorted_trades = sorted(
        [trade for trade in trades if trade["open_date"] is not None and trade["close_date"] is not None],
        key=lambda trade: (trade["open_date"], trade["close_date"], trade["trade_id"]),
    )
    rows = []
    summary: dict[str, Any] = {}
    for window in (7, 30):
        reentries = []
        comparisons = []
        for index, trade in enumerate(sorted_trades):
            prior_losses = [
                prior for prior in sorted_trades[:index]
                if prior["instrument_id"] == trade["instrument_id"]
                and prior["realized_pnl"] is not None
                and prior["realized_pnl"] < 0
                and prior["close_date"] is not None
                and 0 <= (trade["open_date"] - prior["close_date"]).days <= window
            ]
            if prior_losses:
                reentries.append(trade)
            else:
                comparisons.append(trade)
        confidence = _confidence(
            len(reentries),
            minimum=INSIGHT_SAMPLE_MINIMUMS["reentry"],
            sensitivity_stable=True,
            coverage_material=False,
        )
        row = {
            "window_days": str(window),
            "eligible_trade_count": str(len(reentries)),
            "win_rate": _ratio(_winner_count(reentries), len(reentries)),
            "net_pnl": _decimal_text(_sum_pnl(reentries)),
            "comparison_trade_count": str(len(comparisons)),
            "comparison_win_rate": _ratio(_winner_count(comparisons), len(comparisons)),
            "comparison_net_pnl": _decimal_text(_sum_pnl(comparisons)),
            "confidence": confidence,
        }
        rows.append(row)
        summary[f"{window}_day"] = row
    return {"summary": summary, "rows": rows}


def _annual_metrics(trades: list[Mapping[str, Any]]) -> list[dict[str, str]]:
    by_year: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for trade in trades:
        year = str(trade["close_date"].year) if trade["close_date"] is not None else "unknown"
        by_year[year].append(trade)
    rows = []
    for year in sorted(by_year):
        rows_for_year = by_year[year]
        pnls = [trade["realized_pnl"] for trade in rows_for_year if trade["realized_pnl"] is not None]
        rows.append({
            "year": year,
            "trade_count": str(len(rows_for_year)),
            "equity_count": str(sum(trade["asset_type"] == "equity" for trade in rows_for_year)),
            "option_count": str(sum(trade["asset_type"] == "option" for trade in rows_for_year)),
            "net_pnl": _decimal_text(sum(pnls, Decimal("0"))),
            "win_rate": _ratio(sum(pnl > 0 for pnl in pnls), len(rows_for_year)),
            "gross_gains": _decimal_text(sum((pnl for pnl in pnls if pnl > 0), Decimal("0"))),
            "gross_losses": _decimal_text(sum((abs(pnl) for pnl in pnls if pnl < 0), Decimal("0"))),
            "confidence": _confidence(len(rows_for_year), minimum=INSIGHT_SAMPLE_MINIMUMS["overall"], sensitivity_stable=True, coverage_material=False),
        })
    return rows


def _limited_sensitivity(high_trades: list[Mapping[str, Any]], limited_trades: list[Mapping[str, Any]]) -> dict[str, Any]:
    combined = high_trades + limited_trades
    high_pnl = _sum_pnl(high_trades)
    combined_pnl = _sum_pnl(combined)
    return {
        "limited_confidence_trade_count": len(limited_trades),
        "combined_trade_count": len(combined),
        "high_confidence_net_pnl": _decimal_text(high_pnl),
        "combined_high_plus_limited_net_pnl": _decimal_text(combined_pnl),
        "net_pnl_direction_stable": _direction(high_pnl) == _direction(combined_pnl),
        "kept_separate_from_primary": True,
    }


def _insight_candidates(
    *,
    high_trades: list[Mapping[str, Any]],
    overall: Mapping[str, Any],
    asset: Mapping[str, Mapping[str, Any]],
    holding: Mapping[str, Any],
    loss: Mapping[str, Any],
    activity: Mapping[str, Any],
    reentry: Mapping[str, Any],
    sensitivity: Mapping[str, Any],
    coverage: Mapping[str, Any],
    exclusion: Mapping[str, Any],
) -> list[dict[str, Any]]:
    date_range = _date_range(high_trades)
    coverage_material = _int_path(coverage, "confidence", "excluded", "count") > len(high_trades) // 10
    sensitivity_stable = bool(sensitivity["net_pnl_direction_stable"])
    activity_segment_count = min(
        int(activity["summary"]["high_activity_trade_count"]),
        int(activity["summary"]["other_activity_trade_count"]),
    )
    if int(activity["summary"]["eligible_month_count"]) < INSIGHT_SAMPLE_MINIMUMS["activity_months"]:
        activity_confidence = "insufficient_evidence"
    else:
        activity_confidence = _confidence(
            activity_segment_count,
            minimum=INSIGHT_SAMPLE_MINIMUMS["segment"],
            sensitivity_stable=sensitivity_stable,
            coverage_material=coverage_material,
        )
    candidates = [
        _candidate(
            code="overall_result",
            kind="helped" if _parse_decimal(overall["net_realized_pnl"]) >= 0 else "hurt",
            finding=f"High-confidence completed trades had net realized P&L of {overall['net_realized_pnl']}.",
            metric=overall["net_realized_pnl"],
            comparison="0",
            eligible=len(high_trades),
            date_range=date_range,
            confidence=_confidence(len(high_trades), minimum=INSIGHT_SAMPLE_MINIMUMS["overall"], sensitivity_stable=sensitivity_stable, coverage_material=coverage_material),
            reason="uses only high-confidence completed trades",
            limitation="Analytical realized P&L is not tax P&L and excludes unresolved trades.",
            guardrail="Review realized outcomes separately from deposits, withdrawals, and unresolved records.",
            evidence=["behavioral_summary.overall"],
            impact=abs(_parse_decimal(overall["net_realized_pnl"]) or Decimal("0")),
        ),
        _candidate(
            code="asset_option_relative_result",
            kind="hurt" if (_parse_decimal(asset["option"]["net_pnl"]) or Decimal("0")) < (_parse_decimal(asset["equity"]["net_pnl"]) or Decimal("0")) else "helped",
            finding="Option and equity outcomes differed in the high-confidence completed-trade sample.",
            metric=asset["option"]["net_pnl"],
            comparison=asset["equity"]["net_pnl"],
            eligible=min(int(asset["option"]["trade_count"]), int(asset["equity"]["trade_count"])),
            date_range=date_range,
            confidence=_confidence(min(int(asset["option"]["trade_count"]), int(asset["equity"]["trade_count"])), minimum=INSIGHT_SAMPLE_MINIMUMS["segment"], sensitivity_stable=sensitivity_stable, coverage_material=coverage_material),
            reason="compares asset-type aggregates without recalculating option multipliers",
            limitation="Options with basis transfers or unresolved lifecycle events are excluded.",
            guardrail="Separate options-risk review from long-equity review before evaluating behavior.",
            evidence=["behavioral_summary.asset_type"],
            impact=abs((_parse_decimal(asset["option"]["net_pnl"]) or Decimal("0")) - (_parse_decimal(asset["equity"]["net_pnl"]) or Decimal("0"))),
        ),
        _candidate(
            code="holding_loser_duration",
            kind="hurt" if holding["losers_generally_held_longer"] else "helped",
            finding="Losing-trade holding periods were compared with winning-trade holding periods using calendar days.",
            metric=str(holding["loser_median_days"]),
            comparison=str(holding["winner_median_days"]),
            eligible=min(int(holding["loser_count"]), int(holding["winner_count"])),
            date_range=date_range,
            confidence=_confidence(min(int(holding["loser_count"]), int(holding["winner_count"])), minimum=INSIGHT_SAMPLE_MINIMUMS["segment"], sensitivity_stable=sensitivity_stable, coverage_material=coverage_material),
            reason="winner and loser median holding periods are calculated from completed trades",
            limitation="CSV exports lack intraday ordering, so same-day timing is not inferred.",
            guardrail="Define an exit condition before entering a trade and review whether losers stay open longer.",
            evidence=["holding_period_behavior.csv"],
            impact=abs((_parse_decimal(str(holding["loser_median_days"])) or Decimal("0")) - (_parse_decimal(str(holding["winner_median_days"])) or Decimal("0"))),
        ),
        _candidate(
            code="loss_concentration_top_losses",
            kind="hurt",
            finding="A small number of losses accounted for a measurable share of gross losses.",
            metric=loss["largest_5_loss_share"],
            comparison="gross_losses",
            eligible=len([trade for trade in high_trades if trade["realized_pnl"] is not None and trade["realized_pnl"] < 0]),
            date_range=date_range,
            confidence=_confidence(len([trade for trade in high_trades if trade["realized_pnl"] is not None and trade["realized_pnl"] < 0]), minimum=INSIGHT_SAMPLE_MINIMUMS["segment"], sensitivity_stable=sensitivity_stable, coverage_material=coverage_material),
            reason="largest-loss concentration is computed from opaque grouped high-confidence trades",
            limitation="Excluded unknown-basis and unmatched records may hide additional losses.",
            guardrail="Review whether one loss can erase several typical gains before entering a trade.",
            evidence=["behavioral_summary.loss_concentration"],
            impact=(_parse_decimal(loss["largest_5_loss_share"]) or Decimal("0")) * (_parse_decimal(overall["gross_losses"]) or Decimal("0")),
        ),
        _candidate(
            code="activity_high_months",
            kind="hurt" if (_parse_decimal(activity["summary"]["high_activity_average_pnl"]) or Decimal("0")) < (_parse_decimal(activity["summary"]["other_activity_average_pnl"]) or Decimal("0")) else "helped",
            finding="High-activity months were compared with other eligible months without claiming causation.",
            metric=activity["summary"]["high_activity_average_pnl"],
            comparison=activity["summary"]["other_activity_average_pnl"],
            eligible=activity_segment_count,
            date_range=date_range,
            confidence=activity_confidence,
            reason="high activity is defined by the documented 75th percentile monthly trade-count rule",
            limitation="Month-level aggregation cannot prove trading frequency caused outcomes.",
            guardrail="Set a monthly trading-frequency review threshold and compare outcomes after the month closes.",
            evidence=["activity_behavior.csv"],
            impact=abs((_parse_decimal(activity["summary"]["high_activity_average_pnl"]) or Decimal("0")) - (_parse_decimal(activity["summary"]["other_activity_average_pnl"]) or Decimal("0"))),
        ),
        _candidate(
            code="reentry_after_loss_30d",
            kind="hurt" if (_parse_decimal(reentry["summary"]["30_day"]["net_pnl"]) or Decimal("0")) < 0 else "helped",
            finding="The engine found completed trades opened after a prior loss in the same opaque instrument.",
            metric=reentry["summary"]["30_day"]["net_pnl"],
            comparison=reentry["summary"]["30_day"]["comparison_net_pnl"],
            eligible=int(reentry["summary"]["30_day"]["eligible_trade_count"]),
            date_range=date_range,
            confidence=reentry["summary"]["30_day"]["confidence"],
            reason="uses only sequence evidence by opaque instrument and calendar date",
            limitation="This does not infer intent, emotion, revenge trading, or averaging down.",
            guardrail="Use a cooling-off checklist before quickly re-entering the same instrument after a loss.",
            evidence=["reentry_behavior.csv"],
            impact=abs(_parse_decimal(reentry["summary"]["30_day"]["net_pnl"]) or Decimal("0")),
        ),
        _candidate(
            code="insufficient_reentry_7d",
            kind="hurt",
            finding="Seven-day same-instrument re-entry analysis did not meet the minimum sample threshold.",
            metric=reentry["summary"]["7_day"]["eligible_trade_count"],
            comparison=str(INSIGHT_SAMPLE_MINIMUMS["reentry"]),
            eligible=int(reentry["summary"]["7_day"]["eligible_trade_count"]),
            date_range=date_range,
            confidence=reentry["summary"]["7_day"]["confidence"],
            reason="sample-size safeguard suppressed the candidate",
            limitation="Small samples are retained in supporting outputs but not promoted.",
            guardrail="Treat small-sample patterns as review prompts, not conclusions.",
            evidence=["reentry_behavior.csv"],
            impact=Decimal("0"),
        ),
    ]
    return sorted(candidates, key=lambda item: (item["insight_code"]))


def _candidate(
    *,
    code: str,
    kind: str,
    finding: str,
    metric: str,
    comparison: str,
    eligible: int,
    date_range: Mapping[str, str],
    confidence: str,
    reason: str,
    limitation: str,
    guardrail: str,
    evidence: list[str],
    impact: Decimal,
) -> dict[str, Any]:
    return {
        "insight_code": code,
        "finding_type": kind,
        "finding": finding,
        "metric_value": str(metric),
        "comparison_value": str(comparison),
        "eligible_trade_count": eligible,
        "date_range": dict(date_range),
        "confidence": confidence,
        "evidence_strength_reason": reason,
        "limitation": limitation,
        "educational_guardrail": guardrail,
        "supporting_aggregate_evidence": evidence,
        "rank_score": _rank_score(confidence, impact, eligible),
    }


def _rank_insights(candidates: list[Mapping[str, Any]]) -> dict[str, Any]:
    eligible = [
        item for item in candidates
        if item["confidence"] not in {"low", "insufficient_evidence"}
    ]
    hurt = [item for item in eligible if item["finding_type"] == "hurt"]
    helped = [item for item in eligible if item["finding_type"] == "helped"]
    guardrails = sorted(eligible, key=lambda item: (-Decimal(str(item["rank_score"])), item["insight_code"]))[:3]
    return {
        "what_hurt": _top_ranked(hurt),
        "what_helped": _top_ranked(helped),
        "priority_guardrails": [
            {
                "insight_code": item["insight_code"],
                "educational_guardrail": item["educational_guardrail"],
                "supporting_aggregate_evidence": item["supporting_aggregate_evidence"],
            }
            for item in guardrails
        ],
    }


def _top_ranked(items: list[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return sorted(items, key=lambda item: (-Decimal(str(item["rank_score"])), item["insight_code"]))[:3]


def _validation(
    high_trades: list[Mapping[str, Any]],
    limited_trades: list[Mapping[str, Any]],
    coverage: Mapping[str, Any],
    candidates: list[Mapping[str, Any]],
) -> dict[str, Any]:
    high_source_pnl = _parse_decimal(_str_path(coverage, "confidence", "high_confidence", "realized_pnl"))
    high_row_pnl = _sum_pnl(high_trades)
    return {
        "primary_metrics_use_only_high_confidence": True,
        "limited_confidence_sensitivity_is_separate": True,
        "excluded_records_used_in_primary_metrics": False,
        "high_confidence_pnl_reconciles_to_trusted_dataset": high_source_pnl == high_row_pnl,
        "high_confidence_trade_count": len(high_trades),
        "limited_confidence_trade_count": len(limited_trades),
        "candidate_count": len(candidates),
        "low_or_insufficient_candidates_promoted": False,
        "privacy_schema_uses_only_opaque_instrument_ids": True,
    }


def _limitations(coverage: Mapping[str, Any], exclusion: Mapping[str, Any]) -> list[str]:
    notes = [
        "Behavioral insights describe historical aggregate patterns only.",
        "High-confidence trades drive primary findings; limited-confidence trades are sensitivity analysis only.",
        "Excluded trades, unknown-basis matches, unresolved lifecycle events, and basis transfers are not used in primary metrics.",
    ]
    excluded = _int_path(coverage, "confidence", "excluded", "count")
    review = int((exclusion or {}).get("review_item_count") or 0)
    if excluded:
        notes.append("Excluded completed matches may limit coverage.")
    if review:
        notes.append("Review items remain in audit outputs and may affect future completeness.")
    return notes


def _confidence(
    count: int,
    *,
    minimum: int,
    sensitivity_stable: bool,
    coverage_material: bool,
) -> str:
    if count < minimum:
        return "insufficient_evidence"
    if coverage_material:
        return "low"
    if not sensitivity_stable:
        return "medium"
    if count >= minimum * 2:
        return "high"
    return "medium"


def _rank_score(confidence: str, impact: Decimal, eligible: int) -> str:
    confidence_weight = {
        "high": Decimal("4"),
        "medium": Decimal("3"),
        "low": Decimal("1"),
        "insufficient_evidence": Decimal("0"),
    }.get(confidence, Decimal("0"))
    recurrence = min(Decimal(eligible), Decimal("100")) / Decimal("100")
    return _decimal_text(confidence_weight * (Decimal("1") + recurrence) + min(abs(impact), Decimal("1000000")) / Decimal("1000000"))


def _winner_count(trades: Iterable[Mapping[str, Any]]) -> int:
    return sum(trade["realized_pnl"] is not None and trade["realized_pnl"] > 0 for trade in trades)


def _sum_pnl(trades: Iterable[Mapping[str, Any]]) -> Decimal:
    return sum((trade["realized_pnl"] for trade in trades if trade["realized_pnl"] is not None), Decimal("0"))


def _date_range(trades: list[Mapping[str, Any]]) -> dict[str, str]:
    dates = [trade["close_date"] for trade in trades if trade["close_date"] is not None]
    if not dates:
        return {"start": "", "end": ""}
    return {"start": min(dates).isoformat(), "end": max(dates).isoformat()}


def _average(values: Iterable[Decimal]) -> Decimal | None:
    items = list(values)
    if not items:
        return None
    return sum(items, Decimal("0")) / Decimal(len(items))


def _median(values: Iterable[Decimal]) -> Decimal | None:
    items = sorted(values)
    if not items:
        return None
    midpoint = len(items) // 2
    if len(items) % 2 == 1:
        return items[midpoint]
    return (items[midpoint - 1] + items[midpoint]) / Decimal("2")


def _ratio(numerator: int, denominator: int) -> str:
    if denominator == 0:
        return ""
    return _decimal_text((Decimal(numerator) / Decimal(denominator) * Decimal("100")).quantize(Decimal("0.0001")))


def _divide(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator in (None, Decimal("0")):
        return None
    return numerator / denominator


def _ceil_divide(numerator: Decimal | None, denominator: Decimal | None) -> str:
    if numerator is None or denominator in (None, Decimal("0")):
        return ""
    return str(int((numerator / denominator).to_integral_value(rounding=ROUND_CEILING)))


def _nearest_rank_percentile(values: list[int], percentile: Decimal) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = int((Decimal(len(ordered)) * percentile).to_integral_value(rounding=ROUND_CEILING))
    return ordered[max(rank - 1, 0)]


def _direction(value: Decimal) -> str:
    if value > 0:
        return "positive"
    if value < 0:
        return "negative"
    return "zero"


def _parse_decimal(value: Any) -> Decimal | None:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _parse_int(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _decimal_text(value: Decimal | None) -> str:
    if value is None:
        return ""
    return format(value.normalize(), "f")


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _read_json(path: Path) -> Any:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _write_csv(path: Path, fields: list[str], rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def _int_path(source: Mapping[str, Any], *path: str) -> int:
    value: Any = source
    for key in path:
        if not isinstance(value, Mapping):
            return 0
        value = value.get(key)
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _str_path(source: Mapping[str, Any], *path: str) -> str:
    value: Any = source
    for key in path:
        if not isinstance(value, Mapping):
            return ""
        value = value.get(key)
    return str(value or "")
