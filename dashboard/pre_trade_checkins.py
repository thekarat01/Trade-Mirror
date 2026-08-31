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
CHECKIN_FIELDS = (
    "instrument",
    "asset_type",
    "trade_purpose",
    "entry_rationale",
    "intended_holding_period",
    "profit_exit_condition",
    "loss_invalidation_condition",
    "review_date",
    "personal_note",
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
    return [row for row in rows if isinstance(row, dict)]


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
    if cleaned.get("review_date") and not _valid_date(cleaned["review_date"]):
        issues.append(CheckInValidationIssue("review_date", "Use a valid YYYY-MM-DD review date."))
    return tuple(issues)


def checkin_summary(checkins: Iterable[Mapping[str, Any]], *, today: date | None = None) -> dict[str, Any]:
    rows = list(checkins)
    completed = [row for row in rows if row.get("status") == "completed"]
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
        "exit_condition_checkins": exit_count,
        "exit_condition_percent": pct,
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
        {"Measure": "With exit condition before entry", "Value": str(summary.get("exit_condition_checkins", 0))},
        {"Measure": "Exit-condition rate", "Value": pct_text},
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


def _public_record(row: Mapping[str, Any]) -> dict[str, Any]:
    allowed = {"id", "created_at", "updated_at", "status", *CHECKIN_FIELDS}
    return {key: _text(value) for key, value in row.items() if key in allowed}


def _clean_values(values: Mapping[str, Any]) -> dict[str, str]:
    cleaned = {field: _sanitize_text(values.get(field)) for field in CHECKIN_FIELDS}
    return cleaned


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
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


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
