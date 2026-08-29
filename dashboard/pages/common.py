from __future__ import annotations

from decimal import Decimal
from html import escape
from typing import Any, Iterable, Mapping

from dashboard.data_loader import DashboardData
from dashboard.formatters import format_currency, format_percent, format_quantity


def badge(st: Any, data: DashboardData) -> None:
    st.markdown(f"<div class='tm-badge'>{data.source_label}</div>", unsafe_allow_html=True)


def page_header(st: Any, data: DashboardData, title: str, caption: str) -> None:
    badge(st, data)
    if _is_demo_data(data):
        st.markdown(
            "<div class='tm-note'><strong>Illustrative demo results.</strong> "
            "This public demo uses synthetic data and does not accept brokerage credentials or personal transaction files. "
            "The numbers show how TradeMirror works and are not a real portfolio.</div>",
            unsafe_allow_html=True,
        )
    st.title(title)
    st.caption(caption)


def _is_demo_data(data: DashboardData) -> bool:
    return str(getattr(data, "source_label", "")).strip().casefold() == "demo data"


def unavailable(st: Any, message: str) -> None:
    st.info(message)


def safe_dataframe(st: Any, rows: Iterable[Mapping[str, Any]], *, empty_message: str = "No rows are available.") -> None:
    materialized = [dict(row) for row in rows]
    if not materialized:
        st.info(empty_message)
        return
    try:
        st.dataframe(materialized, hide_index=True, use_container_width=True)
    except Exception as exc:
        if not _is_dataframe_dependency_failure(exc):
            raise
        st.caption("Table rendered in compatibility mode because the native dataframe renderer is unavailable.")
        st.markdown(_html_table(materialized), unsafe_allow_html=True)


def safe_chart(
    st: Any,
    render_chart: Any,
    *,
    fallback_rows: Iterable[Mapping[str, Any]] = (),
    unavailable_message: str = "This chart is unavailable in the current Windows display environment.",
) -> None:
    try:
        render_chart()
    except Exception as exc:
        if not _is_dataframe_dependency_failure(exc):
            raise
        st.warning(unavailable_message)
        rows = list(fallback_rows)
        if rows:
            st.caption("Chart data is shown in compatibility mode.")
            safe_dataframe(st, rows)


def safe_structured_write(st: Any, value: Any) -> None:
    if isinstance(value, Mapping):
        st.markdown(_html_key_value_list(value), unsafe_allow_html=True)
        return
    if _is_row_sequence(value):
        safe_dataframe(st, value)
        return
    if isinstance(value, (list, tuple)):
        items = "".join(f"<li>{escape(str(item))}</li>" for item in value)
        st.markdown(f"<ul>{items}</ul>", unsafe_allow_html=True)
        return
    st.markdown(escape(str(value)))


def _is_dataframe_dependency_failure(exc: Exception) -> bool:
    text = f"{type(exc).__name__}: {exc}".casefold()
    return (
        "pyarrow" in text
        or "dll load failed" in text
        or "application control policy" in text
        or "while importing lib" in text
    )


def _is_row_sequence(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and all(isinstance(item, Mapping) for item in value)


def _html_key_value_list(value: Mapping[str, Any]) -> str:
    items = []
    for key, item in value.items():
        if isinstance(item, Mapping):
            display = _html_key_value_list(item)
        elif isinstance(item, (list, tuple)):
            display = "<ul>" + "".join(f"<li>{escape(str(part))}</li>" for part in item) + "</ul>"
        else:
            display = escape(str(item))
        items.append(f"<li><strong>{escape(str(key))}:</strong> {display}</li>")
    return f"<ul>{''.join(items)}</ul>"


def _html_table(rows: list[Mapping[str, Any]]) -> str:
    columns = list(rows[0].keys())
    header = "".join(f"<th>{escape(str(column))}</th>" for column in columns)
    body_rows = []
    for row in rows:
        cells = "".join(f"<td>{escape(str(row.get(column, '')))}</td>" for column in columns)
        body_rows.append(f"<tr>{cells}</tr>")
    return (
        "<div class='tm-table-wrap'><table class='tm-fallback-table'>"
        f"<thead><tr>{header}</tr></thead>"
        f"<tbody>{''.join(body_rows)}</tbody>"
        "</table></div>"
    )


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
