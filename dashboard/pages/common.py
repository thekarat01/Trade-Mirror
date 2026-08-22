from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable, Mapping

from dashboard.data_loader import DashboardData
from dashboard.formatters import format_currency, format_percent, format_quantity


def badge(st: Any, data: DashboardData) -> None:
    st.markdown(f"<div class='tm-badge'>{data.source_label}</div>", unsafe_allow_html=True)


def page_header(st: Any, data: DashboardData, title: str, caption: str) -> None:
    badge(st, data)
    st.title(title)
    st.caption(caption)


def unavailable(st: Any, message: str) -> None:
    st.info(message)


def money_delta(value: Decimal) -> str:
    if value > 0:
        return "gain"
    if value < 0:
        return "loss"
    return "flat"


def decimal_chart_rows(rows: Iterable[Mapping[str, Any]], fields: Iterable[str]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        converted = dict(row)
        for field in fields:
            value = converted.get(field)
            if isinstance(value, Decimal):
                converted[field] = float(value)
        result.append(converted)
    return result


def metric_card(st: Any, label: str, value: object, *, kind: str = "currency", help_text: str = "", emphasis: str = "") -> None:
    if kind == "currency":
        display = format_currency(value)
    elif kind == "percent":
        display = format_percent(value)
    elif kind == "quantity":
        display = format_quantity(value)
    else:
        display = str(value)
    class_name = "tm-metric tm-metric-review" if emphasis == "review" else "tm-metric"
    note = f"<div class='tm-metric-help'>{help_text}</div>" if help_text else ""
    st.markdown(
        f"<div class='{class_name}'><div class='tm-metric-label'>{label}</div><div class='tm-metric-value'>{display}</div>{note}</div>",
        unsafe_allow_html=True,
    )
