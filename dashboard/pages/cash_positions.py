from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from dashboard.data_loader import csv_rows, decimal_field, decimal_or_zero, json_data
from dashboard.formatters import format_currency
from dashboard.pages.common import decimal_chart_rows, page_header, safe_chart, safe_dataframe


def render(st: Any, data: Any) -> None:
    page_header(st, data, "Cash & Positions", "Settlement-date cash and trade-date versus settled position views.")

    cash_summary = json_data(data, "cash_ledger/cash_ledger_summary.json")
    as_of = dashboard_as_of(cash_summary)
    daily = filter_cash_history_rows(list(csv_rows(data, "cash_ledger/cash_ledger_daily.csv")), as_of)
    if daily:
        st.subheader("Settled cash history")
        balance_chart = cash_balance_chart_rows(daily)
        movement_chart = cash_movement_chart_rows(daily)
        if any(row["Settled cash balance"] is not None for row in balance_chart):
            safe_chart(
                st,
                lambda: st.altair_chart(cash_balance_chart(balance_chart, as_of=as_of), use_container_width=True),
                fallback_rows=balance_chart,
            )
            if any(row["Settled cash balance"] is None for row in balance_chart):
                st.caption("Gaps in cash-balance history represent unavailable balances.")
        else:
            st.info("Cash-balance history is unavailable for this data.")
        st.subheader("Daily cash movement")
        if movement_chart:
            safe_chart(
                st,
                lambda: st.altair_chart(cash_movement_chart(movement_chart, as_of=as_of), use_container_width=True),
                fallback_rows=movement_chart,
            )
        else:
            st.info("Daily cash movement is unavailable for this data.")
    else:
        st.info("Settled cash history is unavailable.")

    cash_events = list(csv_rows(data, "cash_ledger/cash_ledger_events.csv"))
    metrics = cash_summary_metrics(cash_events)

    columns = st.columns(4)
    columns[0].metric("External inflows", format_currency(metrics["External inflows"]))
    columns[1].metric("External outflows", format_currency(metrics["External outflows"]))
    columns[2].metric("Trading cash flow", format_currency(metrics["Trading cash flow"]))
    columns[3].metric("Income", format_currency(metrics["Income"]))
    st.caption(f"Balance confidence: {cash_summary.get('balance_confidence', 'partial')}")

    positions = list(csv_rows(data, "position_ledger/positions_as_of.csv"))
    asset_filter = st.selectbox("Asset type", ["All", "equity", "option"], index=0)
    security_filter = st.text_input("Security contains", "")
    filtered = [
        row for row in positions
        if (asset_filter == "All" or row.get("asset_type") == asset_filter)
        and (not security_filter or security_filter.lower() in row.get("security_key", "").lower())
    ]
    equities = [row for row in filtered if row.get("asset_type") == "equity"]
    options = [row for row in filtered if row.get("asset_type") == "option"]

    st.subheader("Current equity positions")
    if equities:
        safe_dataframe(st, equities)
    else:
        st.info("No equity positions are available for the selected filters.")

    st.subheader("Current option positions")
    if options:
        safe_dataframe(st, options)
    else:
        st.info("No option positions are available for the selected filters.")

    pending = list(csv_rows(data, "position_ledger/pending_position_settlement.csv"))
    with st.expander("Pending settlement"):
        if pending:
            safe_dataframe(st, pending)
        else:
            st.write("No pending position settlement rows are available.")


def cash_history_chart_rows(daily: list[dict[str, str]]) -> list[dict[str, Any]]:
    rows = cash_balance_chart_rows(daily)
    movements = cash_movement_chart_rows(daily)
    return [
        {
            "date": balance["Date"],
            "closing_cash": balance["Settled cash balance"],
            "net_cash_movement": movement["Daily cash movement"],
        }
        for balance, movement in zip(rows, movements)
    ]


def filter_cash_history_rows(daily: list[dict[str, str]], as_of: date | None) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for row in daily:
        row_date = _optional_chart_date(row.get("date", ""))
        if row_date is None:
            continue
        if as_of is None or row_date <= as_of:
            rows.append(row)
    return rows


def dashboard_as_of(summary: Any) -> date | None:
    value = summary.get("as_of") if hasattr(summary, "get") else None
    if value in (None, ""):
        return None
    return _chart_date(str(value))


def cash_balance_chart_rows(daily: list[dict[str, str]], as_of: date | None = None) -> list[dict[str, Any]]:
    bounded = filter_cash_history_rows(daily, as_of)
    return [
        {
            "Date": _chart_date(row["date"]),
            "Settled cash balance": decimal_field(row, "closing_cash"),
        }
        for row in bounded
    ]


def cash_balance_chart(rows: list[dict[str, Any]], as_of: date | None = None) -> Any:
    import altair as alt

    chart_rows = temporal_chart_rows(rows, ("Settled cash balance",))
    x_scale = cash_chart_x_scale(alt, rows, as_of)
    return (
        alt.Chart(alt.Data(values=chart_rows))
        .mark_line(point=True)
        .encode(
            x=alt.X("Date:T", title="Date", scale=x_scale) if x_scale else alt.X("Date:T", title="Date"),
            y=alt.Y(
                "Settled cash balance:Q",
                title="Settled cash balance",
                axis=alt.Axis(format="$,.0f"),
                scale=alt.Scale(zero=False, domain=cash_balance_y_domain(rows)),
            ),
            tooltip=[
                alt.Tooltip("Date:T", title="Date"),
                alt.Tooltip("Settled cash balance:Q", title="Settled cash balance", format="$,.2f"),
            ],
        )
    )


def cash_balance_y_domain(rows: list[dict[str, Any]]) -> list[float] | None:
    values = [row["Settled cash balance"] for row in rows if row["Settled cash balance"] is not None]
    if not values:
        return None
    low = min(values)
    high = max(values)
    spread = high - low
    padding = max(spread * Decimal("0.08"), Decimal("1"))
    return [float(low - padding), float(high + padding)]


def cash_movement_chart_rows(daily: list[dict[str, str]], as_of: date | None = None) -> list[dict[str, Any]]:
    bounded = filter_cash_history_rows(daily, as_of)
    return [
        {
            "Date": _chart_date(row["date"]),
            "Daily cash movement": decimal_or_zero(row.get("net_cash_movement")),
        }
        for row in bounded
    ]


def cash_movement_chart(rows: list[dict[str, Any]], as_of: date | None = None) -> Any:
    import altair as alt

    chart_rows = temporal_chart_rows(rows, ("Daily cash movement",))
    x_scale = cash_chart_x_scale(alt, rows, as_of)
    return (
        alt.Chart(alt.Data(values=chart_rows))
        .mark_bar(size=3)
        .encode(
            x=alt.X("Date:T", title="Date", scale=x_scale) if x_scale else alt.X("Date:T", title="Date"),
            y=alt.Y(
                "Daily cash movement:Q",
                title="Daily cash movement",
                axis=alt.Axis(format="$,.0f"),
            ),
            tooltip=[
                alt.Tooltip("Date:T", title="Date"),
                alt.Tooltip("Daily cash movement:Q", title="Daily cash movement", format="$,.2f"),
            ],
        )
    )


def cash_chart_x_scale(alt: Any, rows: list[dict[str, Any]], as_of: date | None) -> Any:
    domain = cash_chart_x_domain(rows, as_of)
    return alt.Scale(domain=domain, nice=False) if domain else None


def cash_chart_x_domain(rows: list[dict[str, Any]], as_of: date | None) -> list[str] | None:
    dates = [row["Date"] for row in rows]
    if not dates or as_of is None:
        return None
    return [min(dates).isoformat(), as_of.isoformat()]


def temporal_chart_rows(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    chart_rows = decimal_chart_rows(rows, fields)
    for row in chart_rows:
        value = row.get("Date")
        if isinstance(value, date):
            row["Date"] = value.isoformat()
    return chart_rows


def _chart_date(value: str) -> date:
    return date.fromisoformat(value)


def _optional_chart_date(value: str) -> date | None:
    try:
        return _chart_date(value)
    except (TypeError, ValueError):
        return None


def cash_summary_metrics(cash_events: list[dict[str, str]]) -> dict[str, Decimal]:
    return {
        "External inflows": sum((decimal_or_zero(row.get("signed_amount")) for row in cash_events if row.get("cash_category") == "External contribution"), Decimal("0")),
        "External outflows": sum((decimal_or_zero(row.get("signed_amount")) for row in cash_events if row.get("cash_category") == "External withdrawal"), Decimal("0")),
        "Trading cash flow": sum((decimal_or_zero(row.get("signed_amount")) for row in cash_events if row.get("cash_category") in {"Equity trade", "Option trade"}), Decimal("0")),
        "Income": sum((decimal_or_zero(row.get("signed_amount")) for row in cash_events if row.get("cash_category") == "Dividend or interest income"), Decimal("0")),
    }
