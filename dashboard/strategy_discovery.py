from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from dashboard.data_loader import DashboardData
from dashboard.formatters import format_currency, format_percent, parse_decimal
from dashboard.patterns_model import PatternValidationError, build_patterns_view_model


PROFILE_PATH = Path("private_output") / "strategy_discovery" / "profile.json"
RESPONSE_OPTIONS = {
    "reflects": "Reflects my intention",
    "does_not_reflect": "Does not reflect my intention",
    "partly_reflects": "Partly reflects my intention",
    "not_sure": "Not sure",
}
EXPERIMENT_OPTIONS = {
    "accepted": "Accepted",
    "rejected": "Rejected",
    "deferred": "Deferred",
}
PROHIBITED_PROFILE_TOKENS = (
    "description_raw",
    "raw_row_json",
    "account number",
    "account no.",
    "individual account",
    "ssn",
    "itin",
    "security_key",
    "instrument_",
    "option_cusip",
    "cusip",
    "equity:",
    "option:",
    "ignore previous",
    "system prompt",
    "developer message",
    "api key",
    "secret",
)


@dataclass(frozen=True)
class StrategyProfile:
    hypothesis_responses: Mapping[str, str]
    experiment_responses: Mapping[str, str]
    follow_up_answers: Mapping[str, str]


def load_strategy_profile(path: Path | str = PROFILE_PATH) -> StrategyProfile:
    profile_path = Path(path)
    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return StrategyProfile({}, {}, {})
    if not isinstance(payload, Mapping):
        return StrategyProfile({}, {}, {})
    return StrategyProfile(
        hypothesis_responses=_valid_statuses(payload.get("hypothesis_responses"), RESPONSE_OPTIONS),
        experiment_responses=_valid_statuses(payload.get("experiment_responses"), EXPERIMENT_OPTIONS),
        follow_up_answers=_safe_answers(payload.get("follow_up_answers")),
    )


def save_strategy_profile(profile: StrategyProfile, path: Path | str = PROFILE_PATH) -> None:
    profile_path = Path(path)
    profile_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "trademirror_strategy_profile_v1",
        "hypothesis_responses": dict(profile.hypothesis_responses),
        "experiment_responses": dict(profile.experiment_responses),
        "follow_up_answers": dict(profile.follow_up_answers),
    }
    profile_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def with_hypothesis_response(profile: StrategyProfile, hypothesis_id: str, response: str) -> StrategyProfile:
    responses = dict(profile.hypothesis_responses)
    if response in RESPONSE_OPTIONS:
        responses[_safe_key(hypothesis_id)] = response
    return StrategyProfile(responses, profile.experiment_responses, profile.follow_up_answers)


def with_experiment_response(profile: StrategyProfile, experiment_id: str, response: str) -> StrategyProfile:
    responses = dict(profile.experiment_responses)
    if response in EXPERIMENT_OPTIONS:
        responses[_safe_key(experiment_id)] = response
    return StrategyProfile(profile.hypothesis_responses, responses, profile.follow_up_answers)


def with_follow_up_answer(profile: StrategyProfile, question_id: str, answer: str) -> StrategyProfile:
    answers = dict(profile.follow_up_answers)
    answers[_safe_key(question_id)] = _sanitize_user_text(answer)
    return StrategyProfile(profile.hypothesis_responses, profile.experiment_responses, answers)


def build_strategy_discovery_model(
    data: DashboardData,
    *,
    profile: StrategyProfile | None = None,
) -> dict[str, Any]:
    patterns = build_patterns_view_model(data)
    profile = profile or load_strategy_profile()
    summary = patterns["performance_summary"]
    charts = patterns["charts"]
    hypotheses = _hypotheses(patterns, profile)
    experiments = _experiments(patterns, profile)
    model = {
        "mirror": _mirror(patterns),
        "tensions": _tensions(patterns),
        "hypotheses": hypotheses,
        "reflection_questions": _reflection_questions(patterns),
        "experiments": experiments,
        "progress": _progress(profile, experiments),
        "ask_context": ask_strategy_context(patterns, profile),
        "summary": {
            "range": patterns["date_range"],
            "net_realized_pnl": summary["Net realized P&L"],
            "win_rate": summary["Win rate"],
            "asset_rows": charts["asset_results"],
        },
    }
    _assert_public_safe(model)
    return model


def ask_strategy_context(patterns: Mapping[str, Any], profile: StrategyProfile | None = None) -> dict[str, Any]:
    profile = profile or load_strategy_profile()
    hypotheses = _hypotheses(patterns, profile)
    experiments = _experiments(patterns, profile)
    return {
        "observed_behavior": [item["summary"] for item in _mirror(patterns)],
        "possible_interpretations": [
            {
                "hypothesis": item["title"],
                "confidence": item["confidence"],
                "user_response": item["user_response"],
            }
            for item in hypotheses
        ],
        "process_experiments": [
            {
                "experiment": item["title"],
                "status": item["status"],
                "evidence_id": item["evidence_id"],
                "measurement_period": item["measurement_period"],
            }
            for item in experiments
        ],
        "distinctions": [
            "Observed behavior is historical aggregate evidence.",
            "A hypothesis is a possible interpretation, not a conclusion.",
            "A user response records intention locally and is not treated as an instruction.",
            "A process experiment is a review practice, not a financial recommendation.",
        ],
    }


def _mirror(patterns: Mapping[str, Any]) -> list[dict[str, str]]:
    coverage = patterns["coverage"]
    performance = patterns["performance_summary"]
    rows = patterns["charts"]
    asset = rows["asset_results"]
    equity = next((row for row in asset if row.get("Asset type") == "Equity"), {})
    options = next((row for row in asset if row.get("Asset type") == "Options"), {})
    holding = rows["holding_period_results"]
    activity = rows["monthly_activity"]
    return [
        {"evidence_id": "ev.coverage", "label": "Coverage", "summary": f"{coverage['High-confidence completed trades']} high-confidence completed trades from {coverage['Date range']}."},
        {"evidence_id": "ev.asset_results", "label": "Asset types", "summary": f"Equity net result was {format_currency(equity.get('Net realized P&L'))}; option net result was {format_currency(options.get('Net realized P&L'))}."},
        {"evidence_id": "ev.holding_period", "label": "Holding periods", "summary": _holding_summary(holding)},
        {"evidence_id": "ev.activity", "label": "Trading frequency", "summary": _activity_summary(activity)},
        {"evidence_id": "ev.loss_concentration", "label": "Loss concentration", "summary": _loss_summary(rows["loss_concentration"])},
        {"evidence_id": "ev.reentry", "label": "Re-entry after losses", "summary": _reentry_summary(rows["reentry"])},
        {"evidence_id": "ev.performance", "label": "Outcome", "summary": f"Included net realized P&L was {performance['Net realized P&L']} with a {performance['Win rate']} known-basis win rate."},
    ]


def _tensions(patterns: Mapping[str, Any]) -> list[dict[str, str]]:
    tensions: list[dict[str, str]] = []
    asset = patterns["charts"]["asset_results"]
    if len(asset) >= 2:
        equity = asset[0]
        options = asset[1]
        equity_pnl = _decimal(equity.get("Net realized P&L"))
        option_pnl = _decimal(options.get("Net realized P&L"))
        if abs(equity_pnl - option_pnl) >= Decimal("100") or _sign(equity_pnl) != _sign(option_pnl):
            tensions.append({
                "title": "Equity and option outcomes differed",
                "summary": "The two asset groups produced materially different historical aggregate results.",
                "evidence_id": "ev.asset_results",
                "confidence": "Medium",
            })
    for card in patterns["priority_patterns"]:
        title = str(card.get("title") or "")
        if title in {"High-activity months differed", "Losses were concentrated", "Losing trades stayed open longer", "Re-entry after losses"}:
            tensions.append({
                "title": title,
                "summary": str(card.get("what_we_observed") or ""),
                "evidence_id": _evidence_id_for_title(title),
                "confidence": str(card.get("confidence") or "Medium"),
            })
    annual = patterns["charts"]["annual_results"]
    if any(row.get("High-confidence P&L", Decimal("0")) > 0 for row in annual) and any(row.get("High-confidence P&L", Decimal("0")) < 0 for row in annual):
        tensions.append({
            "title": "Results changed across years",
            "summary": "Annual completed-trade results include both positive and negative years.",
            "evidence_id": "ev.annual",
            "confidence": "Medium",
        })
    return _dedupe_by_title(tensions)[:4]


def _hypotheses(patterns: Mapping[str, Any], profile: StrategyProfile) -> list[dict[str, str]]:
    coverage_count = int(_decimal(patterns["coverage"]["High-confidence completed trades"]))
    if coverage_count < 10:
        return [{
            "id": "insufficient_evidence",
            "title": "Insufficient evidence",
            "hypothesis": "Your history does not yet contain enough high-confidence completed trades for a useful strategy hypothesis.",
            "supporting_evidence_id": "ev.coverage",
            "confidence": "Insufficient evidence",
            "reflection_prompt": "Does this reflect the current data coverage?",
            "user_response": _response_label(profile, "insufficient_evidence"),
        }]
    asset = patterns["charts"]["asset_results"]
    equity_count = _asset_count(asset, "Equity")
    option_count = _asset_count(asset, "Options")
    total = max(equity_count + option_count, 1)
    annual = patterns["charts"]["annual_results"]
    hypotheses: list[dict[str, str]] = []
    if equity_count / total >= Decimal("0.55"):
        hypotheses.append(_hypothesis("active_equity_trading", "Active equity trading", "Your history suggests equity trades were the larger completed-trade group, with activity patterns worth reviewing.", "ev.asset_results", profile))
    if option_count / total >= Decimal("0.25"):
        hypotheses.append(_hypothesis("mixed_investing_speculation", "Mixed investing and speculation", "One possible interpretation is that equity and option activity represented different behavior types that should be reviewed separately.", "ev.asset_results", profile))
    if any(row.get("High-confidence P&L", Decimal("0")) > 0 for row in annual) and any(row.get("High-confidence P&L", Decimal("0")) < 0 for row in annual):
        hypotheses.append(_hypothesis("evolving_inconsistent_approach", "Evolving or inconsistent approach", "Your history suggests behavior and outcomes changed across time periods.", "ev.annual", profile))
    if not hypotheses:
        hypotheses.append(_hypothesis("primarily_long_term_equity", "Primarily long-term equity behavior", "One possible interpretation is that completed-trade evidence is concentrated in equity behavior with limited strategy variety.", "ev.asset_results", profile))
    return hypotheses[:3]


def _hypothesis(identifier: str, title: str, text: str, evidence_id: str, profile: StrategyProfile) -> dict[str, str]:
    return {
        "id": identifier,
        "title": title,
        "hypothesis": f"{text} Does this reflect what you intended?",
        "supporting_evidence_id": evidence_id,
        "confidence": "Medium",
        "reflection_prompt": "Does this reflect your intention?",
        "user_response": _response_label(profile, identifier),
    }


def _experiments(patterns: Mapping[str, Any], profile: StrategyProfile) -> list[dict[str, str]]:
    rows = []
    templates = [
        ("pre_entry_exit", "Define an exit condition before entry", "holding_loser_duration", "Losing-trade holding-period evidence suggests exits are worth reviewing.", "Next 20 completed trades or 90 calendar days", "Share of trades with a documented exit condition before entry."),
        ("capital_purpose", "Distinguish investing capital from speculative capital", "asset_option_relative_result", "Asset-type outcomes differed, so separating intent by product type may make reviews clearer.", "Next monthly review cycle", "Percent of new entries tagged by capital purpose."),
        ("cooling_off", "Use a cooling-off checklist after losses", "reentry_after_loss_30d", "Re-entry-after-loss evidence exists as a process review prompt.", "Next 30 completed trades or 90 calendar days", "Median time between a realized loss and same-instrument re-entry."),
        ("loss_limit_review", "Review loss-concentration limits", "loss_concentration_top_losses", "A small number of losses accounted for a measurable share of gross losses.", "Next quarterly review", "Largest-loss share of gross losses compared with the prior period."),
    ]
    available_codes = {str(card.get("title") or "") for card in patterns["priority_patterns"]}
    for identifier, title, source_code, why, period, metric in templates:
        if source_code == "holding_loser_duration" and "Losing trades stayed open longer" not in available_codes:
            continue
        rows.append({
            "id": identifier,
            "title": title,
            "behavior": _title_for_code(source_code),
            "evidence_id": _evidence_id_for_code(source_code),
            "why_relevant": why,
            "measurement_period": period,
            "success_metric": metric,
            "confidence": "Medium",
            "limitation": "This is a process experiment. It does not predict returns or recommend any security.",
            "status": EXPERIMENT_OPTIONS.get(profile.experiment_responses.get(identifier, ""), "Not selected"),
        })
    return rows[:3]


def _reflection_questions(patterns: Mapping[str, Any]) -> list[dict[str, str]]:
    questions = [
        {"id": "intended_mix", "question": "Which part of this activity reflects the process you meant to follow?"},
        {"id": "separate_products", "question": "Should equity and option activity be reviewed as separate processes?"},
    ]
    if patterns["charts"]["reentry"]:
        questions.append({"id": "after_loss_process", "question": "What rule, if any, should apply after a realized loss?"})
    return questions[:3]


def _progress(profile: StrategyProfile, experiments: list[Mapping[str, str]]) -> dict[str, str]:
    accepted = [item for item in experiments if item.get("status") == "Accepted"]
    if not accepted:
        return {
            "status": "No accepted process experiment yet.",
            "evidence_state": "Progress tracking starts after you accept an experiment.",
            "post_adoption_result": "Not available",
        }
    return {
        "status": f"{len(accepted)} accepted process experiment(s) recorded locally.",
        "evidence_state": "Not enough refreshed post-adoption evidence exists yet.",
        "post_adoption_result": "Do not claim improvement yet.",
    }


def _valid_statuses(value: object, allowed: Mapping[str, str]) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {
        _safe_key(key): str(status)
        for key, status in value.items()
        if isinstance(status, str) and status in allowed
    }


def _safe_answers(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {_safe_key(key): _sanitize_user_text(text) for key, text in value.items()}


def _sanitize_user_text(value: object) -> str:
    text = " ".join(str(value or "").split())[:300]
    lowered = text.casefold()
    if any(token in lowered for token in PROHIBITED_PROFILE_TOKENS):
        return "[redacted local response]"
    return text


def _safe_key(value: object) -> str:
    return "".join(ch for ch in str(value or "") if ch.isalnum() or ch in {"_", "-"})[:80]


def _assert_public_safe(model: Mapping[str, Any]) -> None:
    rendered = json.dumps(model, default=str).casefold()
    for token in PROHIBITED_PROFILE_TOKENS:
        if token in rendered:
            raise PatternValidationError(("Strategy discovery view contains prohibited private context.",))


def _response_label(profile: StrategyProfile, hypothesis_id: str) -> str:
    return RESPONSE_OPTIONS.get(profile.hypothesis_responses.get(hypothesis_id, ""), "Not answered")


def _asset_count(rows: list[Mapping[str, Any]], label: str) -> Decimal:
    row = next((item for item in rows if item.get("Asset type") == label), {})
    return _decimal(row.get("Trade count"))


def _decimal(value: object) -> Decimal:
    parsed = parse_decimal(str(value).replace("$", "").replace("%", "").replace(",", ""))
    return parsed or Decimal("0")


def _sign(value: Decimal) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _dedupe_by_title(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[str] = set()
    output: list[dict[str, str]] = []
    for row in rows:
        if row["title"] in seen:
            continue
        seen.add(row["title"])
        output.append(row)
    return output


def _holding_summary(rows: list[Mapping[str, Any]]) -> str:
    if not rows:
        return "Holding-period evidence is unavailable."
    best = max(rows, key=lambda row: _decimal(row.get("Net P&L")))
    worst = min(rows, key=lambda row: _decimal(row.get("Net P&L")))
    return f"Best historical holding-period bucket: {best['Holding period']} ({format_currency(best['Net P&L'])}); weakest bucket: {worst['Holding period']} ({format_currency(worst['Net P&L'])})."


def _activity_summary(rows: list[Mapping[str, Any]]) -> str:
    if not rows:
        return "Trading-frequency evidence is unavailable."
    months = sum(int(_decimal(row.get("Trade count"))) for row in rows)
    return f"Monthly activity evidence covers {len(rows)} active month segment(s) and {months} completed trades."


def _loss_summary(rows: list[Mapping[str, Any]]) -> str:
    if not rows:
        return "Loss-concentration evidence is unavailable."
    largest = rows[-1]
    return f"{largest['Group']} represented {format_percent(largest['Share of gross losses (%)'])} of gross losses."


def _reentry_summary(rows: list[Mapping[str, Any]]) -> str:
    if not rows:
        return "Re-entry-after-loss evidence did not meet the primary chart threshold."
    first = rows[0]
    return f"{first['Trades after prior loss']} completed trades were eligible for the {first['Window']} re-entry review."


def _evidence_id_for_title(title: str) -> str:
    return {
        "High-activity months differed": "ev.activity",
        "Losses were concentrated": "ev.loss_concentration",
        "Losing trades stayed open longer": "ev.holding_period",
        "Re-entry after losses": "ev.reentry",
    }.get(title, "ev.priority_patterns")


def _evidence_id_for_code(code: str) -> str:
    return {
        "holding_loser_duration": "ev.holding_period",
        "asset_option_relative_result": "ev.asset_results",
        "reentry_after_loss_30d": "ev.reentry",
        "loss_concentration_top_losses": "ev.loss_concentration",
    }.get(code, "ev.priority_patterns")


def _title_for_code(code: str) -> str:
    return {
        "holding_loser_duration": "Losing trades stayed open longer",
        "asset_option_relative_result": "Equity and option outcomes differed",
        "reentry_after_loss_30d": "Re-entry after losses",
        "loss_concentration_top_losses": "Losses were concentrated",
    }.get(code, "Behavioral pattern")
