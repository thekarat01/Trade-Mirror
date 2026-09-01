from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from uuid import uuid4


STORAGE_PATH = Path("private_output") / "pre_trade_checkins" / "checkins.json"
SCHEMA = "trademirror_pre_trade_checkins_v1"
TARGET_COMPLETED_CHECKINS = 20
TARGET_PERIOD_DAYS = 90
ASSET_TYPES = ("stock", "ETF", "option", "other")
TRADE_PURPOSES = ("investing", "speculation", "hedge", "income", "unsure")
STATUSES = ("draft", "completed", "canceled", "reviewed")
ENTRY_TIMING_OPTIONS = ("before_entry", "after_entry", "unspecified")
THESIS_STATUSES = ("intact", "invalidated", "uncertain")
CURRENT_STATUSES = ("held", "reduced", "exited", "expired", "other")
OUTCOMES = ("gain", "loss", "approximately breakeven", "still open")
PLAN_FOLLOWED_OPTIONS = ("followed", "changed", "ignored")
CHECKIN_FIELDS = (
    "instrument",
    "asset_type",
    "trade_purpose",
    "entry_timing",
    "entry_rationale",
    "intended_holding_period",
    "profit_exit_condition",
    "loss_invalidation_condition",
    "review_date",
    "personal_note",
)
REVIEW_FIELDS = (
    "thesis_status",
    "current_status",
    "outcome",
    "manual_outcome",
    "review_trigger_occurred",
    "plan_adherence",
    "plan_change_reason",
    "decision_review_date",
    "review_notes",
    "option_underlying",
    "option_call_put",
    "option_strike",
    "option_expiration",
    "option_premium_paid",
    "option_quantity",
)
REVIEW_REQUIRED_FIELDS = (
    "thesis_status",
    "current_status",
    "outcome",
    "review_trigger_occurred",
    "plan_adherence",
    "decision_review_date",
)
REQUIRED_FIELDS = (
    "asset_type",
    "trade_purpose",
    "entry_rationale",
    "loss_invalidation_condition",
    "review_date",
)
PROHIBITED_STORAGE_TOKENS = (
    "description_raw",
    "raw_row_json",
    "account number",
    "account no.",
    "ssn",
    "itin",
    "api key",
    "secret",
)


@dataclass(frozen=True)
class CheckInValidationIssue:
    field: str
    reason: str


def load_checkins(path: Path | str = STORAGE_PATH) -> list[dict[str, Any]]:
    storage_path = Path(path)
    try:
        payload = json.loads(storage_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(payload, Mapping) or payload.get("schema") != SCHEMA:
        return []
    rows = payload.get("checkins")
    if not isinstance(rows, list):
        return []
    return [_public_record(row) for row in rows if isinstance(row, dict)]


def save_checkins(checkins: Iterable[Mapping[str, Any]], path: Path | str = STORAGE_PATH) -> None:
    storage_path = Path(path)
    storage_path.parent.mkdir(parents=True, exist_ok=True)
    safe_rows = [_public_record(row) for row in checkins]
    storage_path.write_text(
        json.dumps({"schema": SCHEMA, "checkins": safe_rows}, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def create_checkin(
    values: Mapping[str, Any],
    *,
    status: str = "completed",
    path: Path | str = STORAGE_PATH,
    id_factory: Callable[[], str] | None = None,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    issues = validate_checkin(values, status=status)
    if issues:
        raise ValueError("Pre-trade check-in is missing required fields.")
    timestamp = _timestamp(now)
    checkin = {
        "id": (id_factory or _new_id)(),
        "created_at": timestamp,
        "updated_at": timestamp,
        "status": status if status in STATUSES else "draft",
        **_clean_values(values),
    }
    existing = load_checkins(path)
    save_checkins([*existing, checkin], path)
    return checkin


def update_checkin(
    checkin_id: str,
    updates: Mapping[str, Any],
    *,
    path: Path | str = STORAGE_PATH,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    checkins = load_checkins(path)
    for index, row in enumerate(checkins):
        if row.get("id") != checkin_id:
            continue
        updated = {
            **row,
            **_clean_values(updates),
            "updated_at": _timestamp(now),
        }
        if str(updates.get("status") or "") in STATUSES:
            updated["status"] = str(updates["status"])
        checkins[index] = updated
        save_checkins(checkins, path)
        return updated
    raise KeyError("Pre-trade check-in was not found.")


def complete_review(
    checkin_id: str,
    values: Mapping[str, Any],
    *,
    path: Path | str = STORAGE_PATH,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    issues = validate_review(values)
    if issues:
        raise ValueError("Decision review is missing required fields.")
    checkins = load_checkins(path)
    for index, row in enumerate(checkins):
        if row.get("id") != checkin_id:
            continue
        timestamp = _timestamp(now)
        review = {
            "status": "completed",
            "created_at": _nested(row.get("decision_review")).get("created_at") or timestamp,
            "updated_at": timestamp,
            **_clean_review_values(values),
        }
        updated = {
            **row,
            "status": "reviewed",
            "updated_at": timestamp,
            "decision_review": review,
        }
        checkins[index] = updated
        save_checkins(checkins, path)
        return updated
    raise KeyError("Pre-trade check-in was not found.")


def reopen_review(
    checkin_id: str,
    *,
    path: Path | str = STORAGE_PATH,
    now: Callable[[], datetime] | None = None,
) -> dict[str, Any]:
    checkins = load_checkins(path)
    for index, row in enumerate(checkins):
        if row.get("id") != checkin_id:
            continue
        review = {
            **_nested(row.get("decision_review")),
            "status": "reopened",
            "updated_at": _timestamp(now),
        }
        updated = {
            **row,
            "status": "completed",
            "updated_at": review["updated_at"],
            "decision_review": review,
        }
        checkins[index] = updated
        save_checkins(checkins, path)
        return updated
    raise KeyError("Pre-trade check-in was not found.")


def validate_checkin(values: Mapping[str, Any], *, status: str = "completed") -> tuple[CheckInValidationIssue, ...]:
    issues: list[CheckInValidationIssue] = []
    cleaned = _clean_values(values)
    if status not in STATUSES:
        issues.append(CheckInValidationIssue("status", "Choose draft, completed, canceled or reviewed."))
    if status == "completed":
        for field in REQUIRED_FIELDS:
            if not cleaned.get(field):
                issues.append(CheckInValidationIssue(field, "Required before completing a check-in."))
    if cleaned.get("asset_type") and cleaned["asset_type"] not in ASSET_TYPES:
        issues.append(CheckInValidationIssue("asset_type", "Choose a supported asset type."))
    if cleaned.get("trade_purpose") and cleaned["trade_purpose"] not in TRADE_PURPOSES:
        issues.append(CheckInValidationIssue("trade_purpose", "Choose a supported trade purpose."))
    if cleaned.get("entry_timing") and cleaned["entry_timing"] not in ENTRY_TIMING_OPTIONS:
        issues.append(CheckInValidationIssue("entry_timing", "Choose before entry, after entry or unspecified."))
    if cleaned.get("review_date") and not _valid_date(cleaned["review_date"]):
        issues.append(CheckInValidationIssue("review_date", "Use a valid YYYY-MM-DD review date."))
    return tuple(issues)


def validate_review(values: Mapping[str, Any]) -> tuple[CheckInValidationIssue, ...]:
    issues: list[CheckInValidationIssue] = []
    cleaned = _clean_review_values(values)
    for field in REVIEW_REQUIRED_FIELDS:
        if not cleaned.get(field):
            issues.append(CheckInValidationIssue(field, "Required before completing a decision review."))
    if cleaned.get("thesis_status") and cleaned["thesis_status"] not in THESIS_STATUSES:
        issues.append(CheckInValidationIssue("thesis_status", "Choose intact, invalidated or uncertain."))
    if cleaned.get("current_status") and cleaned["current_status"] not in CURRENT_STATUSES:
        issues.append(CheckInValidationIssue("current_status", "Choose held, reduced, exited, expired or other."))
    if cleaned.get("outcome") and cleaned["outcome"] not in OUTCOMES:
        issues.append(CheckInValidationIssue("outcome", "Choose gain, loss, approximately breakeven or still open."))
    if cleaned.get("plan_adherence") and cleaned["plan_adherence"] not in PLAN_FOLLOWED_OPTIONS:
        issues.append(CheckInValidationIssue("plan_adherence", "Choose followed, changed or ignored."))
    for field in ("decision_review_date", "option_expiration"):
        if cleaned.get(field) and not _valid_date(cleaned[field]):
            issues.append(CheckInValidationIssue(field, "Use a valid YYYY-MM-DD date."))
    return tuple(issues)


def checkin_summary(checkins: Iterable[Mapping[str, Any]], *, today: date | None = None) -> dict[str, Any]:
    rows = list(checkins)
    completed = [row for row in rows if row.get("status") in {"completed", "reviewed"}]
    reviewed = [row for row in rows if _review_completed(row)]
    pre_entry = [row for row in completed if row.get("entry_timing") in {"", "before_entry"}]
    on_time = [row for row in reviewed if _review_on_time(row)]
    followed = [row for row in reviewed if _nested(row.get("decision_review")).get("plan_adherence") == "followed"]
    changed = [row for row in reviewed if _nested(row.get("decision_review")).get("plan_adherence") == "changed"]
    ignored = [row for row in reviewed if _nested(row.get("decision_review")).get("plan_adherence") == "ignored"]
    with_exit = [
        row for row in completed
        if _text(row.get("loss_invalidation_condition")) and _text(row.get("review_date"))
    ]
    completed_count = len(completed)
    exit_count = len(with_exit)
    remaining = max(TARGET_COMPLETED_CHECKINS - completed_count, 0)
    pct = None if completed_count == 0 else round((exit_count / completed_count) * 100)
    status = "Not started"
    if completed_count:
        status = "Ready for review" if completed_count >= TARGET_COMPLETED_CHECKINS or _elapsed_days(completed, today) >= TARGET_PERIOD_DAYS else "In progress"
    return {
        "completed_checkins": completed_count,
        "pre_entry_checkins": len(pre_entry),
        "exit_condition_checkins": exit_count,
        "exit_condition_percent": pct,
        "reviews_completed": len(reviewed),
        "reviews_on_time": len(on_time),
        "plans_followed": len(followed),
        "plans_changed": len(changed),
        "plans_ignored": len(ignored),
        "decision_quality_evidence": "Insufficient evidence" if len(reviewed) < 5 else "Ready for review",
        "remaining_to_target": remaining,
        "target_completed_checkins": TARGET_COMPLETED_CHECKINS,
        "target_period_days": TARGET_PERIOD_DAYS,
        "status": status,
    }


def checkin_progress_rows(summary: Mapping[str, Any]) -> list[dict[str, str]]:
    pct = summary.get("exit_condition_percent")
    pct_text = "Not available" if pct is None else f"{pct}%"
    return [
        {"Measure": "Completed pre-trade check-ins", "Value": str(summary.get("completed_checkins", 0))},
        {"Measure": "Completed before entry", "Value": str(summary.get("pre_entry_checkins", 0))},
        {"Measure": "With exit condition before entry", "Value": str(summary.get("exit_condition_checkins", 0))},
        {"Measure": "Exit-condition rate", "Value": pct_text},
        {"Measure": "Reviews completed", "Value": str(summary.get("reviews_completed", 0))},
        {"Measure": "Reviews completed on time", "Value": str(summary.get("reviews_on_time", 0))},
        {"Measure": "Plans followed", "Value": str(summary.get("plans_followed", 0))},
        {"Measure": "Plans changed", "Value": str(summary.get("plans_changed", 0))},
        {"Measure": "Plans ignored", "Value": str(summary.get("plans_ignored", 0))},
        {"Measure": "Decision-quality evidence", "Value": str(summary.get("decision_quality_evidence", "Insufficient evidence"))},
        {"Measure": "Remaining to target", "Value": str(summary.get("remaining_to_target", 0))},
        {"Measure": "Target", "Value": "20 completed trades or 90 days"},
        {"Measure": "Status", "Value": str(summary.get("status", "Not started"))},
    ]


def summary_for_confirmation(values: Mapping[str, Any]) -> list[dict[str, str]]:
    cleaned = _clean_values(values)
    return [
        {"Prompt": "What you intend to do", "Response": _summary_value(cleaned.get("instrument"), cleaned.get("asset_type"))},
        {"Prompt": "Why you intend to do it", "Response": cleaned.get("entry_rationale") or "Not provided"},
        {"Prompt": "What would invalidate it", "Response": cleaned.get("loss_invalidation_condition") or "Not provided"},
        {"Prompt": "When you will reassess it", "Response": cleaned.get("review_date") or "Not provided"},
    ]


def decisions_to_review_rows(checkins: Iterable[Mapping[str, Any]], *, today: date | None = None) -> list[dict[str, str]]:
    current = today or datetime.now(timezone.utc).date()
    rows = []
    for row in checkins:
        if row.get("status") not in {"completed", "reviewed"}:
            continue
        rows.append({
            "Review date": _text(row.get("review_date")) or "Not available",
            "Reminder": review_reminder(row, today=current),
            "Asset type": _text(row.get("asset_type")) or "Not provided",
            "Identifier": _text(row.get("instrument")) or "Not provided",
            "Timing": entry_timing_label(row),
            "Review status": "Completed" if _review_completed(row) else "Not reviewed",
        })
    return sorted(rows, key=lambda item: item["Review date"])


def review_reminder(checkin: Mapping[str, Any], *, today: date | None = None) -> str:
    if _review_completed(checkin):
        return "completed"
    review_date = _parse_date(_text(checkin.get("review_date")))
    if review_date is None:
        return "unavailable"
    current = today or datetime.now(timezone.utc).date()
    delta = (review_date - current).days
    if delta < 0:
        return f"overdue by {abs(delta)} day(s)"
    if delta == 0:
        return "due today"
    return f"upcoming in {delta} day(s)"


def entry_timing_label(checkin: Mapping[str, Any]) -> str:
    value = _text(checkin.get("entry_timing")) or "before_entry"
    return {
        "before_entry": "Completed before entry",
        "after_entry": "Completed after entry",
        "unspecified": "Not specified",
    }.get(value, "Not specified")


def review_summary_for_confirmation(values: Mapping[str, Any]) -> list[dict[str, str]]:
    cleaned = _clean_review_values(values)
    return [
        {"Prompt": "Thesis status", "Response": cleaned.get("thesis_status") or "Not provided"},
        {"Prompt": "Current decision status", "Response": cleaned.get("current_status") or "Not provided"},
        {"Prompt": "Outcome", "Response": cleaned.get("outcome") or "Not provided"},
        {"Prompt": "Original trigger occurred", "Response": cleaned.get("review_trigger_occurred") or "Not provided"},
        {"Prompt": "Plan adherence", "Response": cleaned.get("plan_adherence") or "Not provided"},
        {"Prompt": "Review date", "Response": cleaned.get("decision_review_date") or "Not provided"},
    ]


def demo_session_checkins(session_state: dict[str, Any]) -> list[dict[str, Any]]:
    rows = session_state.get("pre_trade_checkins_demo")
    return rows if isinstance(rows, list) else []


def add_demo_session_checkin(session_state: dict[str, Any], values: Mapping[str, Any], *, status: str = "completed") -> dict[str, Any]:
    issues = validate_checkin(values, status=status)
    if issues:
        raise ValueError("Pre-trade check-in is missing required fields.")
    timestamp = _timestamp()
    row = {
        "id": f"demo-{len(demo_session_checkins(session_state)) + 1}",
        "created_at": timestamp,
        "updated_at": timestamp,
        "status": status,
        **_clean_values(values),
    }
    session_state["pre_trade_checkins_demo"] = [*demo_session_checkins(session_state), row]
    return row


def update_demo_session_review(session_state: dict[str, Any], checkin_id: str, values: Mapping[str, Any]) -> dict[str, Any]:
    issues = validate_review(values)
    if issues:
        raise ValueError("Decision review is missing required fields.")
    rows = demo_session_checkins(session_state)
    for index, row in enumerate(rows):
        if row.get("id") != checkin_id:
            continue
        updated = {
            **row,
            "status": "reviewed",
            "updated_at": _timestamp(),
            "decision_review": {
                "status": "completed",
                "created_at": _timestamp(),
                "updated_at": _timestamp(),
                **_clean_review_values(values),
            },
        }
        session_state["pre_trade_checkins_demo"] = [*rows[:index], updated, *rows[index + 1:]]
        return updated
    raise KeyError("Pre-trade check-in was not found.")


def _public_record(row: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"id", "created_at", "updated_at", "status", *CHECKIN_FIELDS}
    cleaned = {key: _text(value) for key, value in row.items() if key in allowed}
    review = row.get("decision_review")
    if isinstance(review, Mapping):
        cleaned["decision_review"] = _public_review(review)
    return cleaned


def _clean_values(values: Mapping[str, Any]) -> dict[str, str]:
    cleaned = {field: _sanitize_text(values.get(field)) for field in CHECKIN_FIELDS}
    if not cleaned.get("entry_timing"):
        cleaned["entry_timing"] = "before_entry"
    return cleaned


def _public_review(row: Mapping[str, Any]) -> dict[str, str]:
    allowed = {"status", "created_at", "updated_at", *REVIEW_FIELDS}
    return {key: _text(value) for key, value in row.items() if key in allowed}


def _clean_review_values(values: Mapping[str, Any]) -> dict[str, str]:
    return {field: _sanitize_text(values.get(field)) for field in REVIEW_FIELDS}


def _sanitize_text(value: Any) -> str:
    text = " ".join(str(value or "").split())[:500]
    lowered = text.casefold()
    if any(token in lowered for token in PROHIBITED_STORAGE_TOKENS):
        return "[redacted local response]"
    return text


def _summary_value(instrument: Any, asset_type: Any) -> str:
    parts = [part for part in (_text(asset_type), _text(instrument)) if part]
    return " / ".join(parts) if parts else "Not provided"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _valid_date(value: str) -> bool:
    return _parse_date(value) is not None


def _parse_date(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _nested(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _review_completed(row: Mapping[str, Any]) -> bool:
    return _nested(row.get("decision_review")).get("status") == "completed"


def _review_on_time(row: Mapping[str, Any]) -> bool:
    review = _nested(row.get("decision_review"))
    review_date = _parse_date(_text(row.get("review_date")))
    completed_date = _parse_date(_text(review.get("decision_review_date") or review.get("updated_at"))[:10])
    if review_date is None or completed_date is None:
        return False
    return completed_date <= review_date


def _elapsed_days(completed: list[Mapping[str, Any]], today: date | None) -> int:
    dates = []
    for row in completed:
        value = _text(row.get("created_at"))[:10]
        if _valid_date(value):
            dates.append(date.fromisoformat(value))
    if not dates:
        return 0
    current = today or datetime.now(timezone.utc).date()
    return max((current - min(dates)).days, 0)


def _timestamp(now: Callable[[], datetime] | None = None) -> str:
    current = (now or (lambda: datetime.now(timezone.utc)))()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _new_id() -> str:
    return f"checkin_{uuid4().hex}"
