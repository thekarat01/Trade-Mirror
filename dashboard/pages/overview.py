from __future__ import annotations

from typing import Any

from dashboard.data_loader import annual_realized_chart_rows, attention_display_rows, overview_metrics
from dashboard.formatters import format_currency, format_date, format_percent
from dashboard.pages.common import decimal_chart_rows, metric_card, page_header, safe_chart, safe_dataframe, safe_structured_write


def render(st: Any, data: Any) -> None:
    page_header(st, data, "TradeMirror", "Understand what helped or hurt your investing using evidence from your own history.")

    metrics = overview_metrics(data)
    st.markdown(
        f"**As of:** {format_date(metrics['as_of'])}  |  "
        "**Methods:** settlement-date cash, anchored positions, analytical FIFO P&L"
    )
    st.markdown(
        "<div class='tm-note'>Analytical results may differ from tax documents. "
        "Unknown-basis and basis-transfer records remain visible but excluded from included P&L.</div>",
        unsafe_allow_html=True,
    )

    columns = st.columns(6)
    with columns[0]:
        metric_card(st, "Included net P&L", metrics["included_net_realized_pnl"], help_text="Known-basis equity and option realized P&L.")
    with columns[1]:
        metric_card(st, "Equity P&L", metrics["equity_realized_pnl"])
    with columns[2]:
        metric_card(st, "Option P&L", metrics["option_realized_pnl"])
    with columns[3]:
        metric_card(st, "Known-basis win rate", metrics["known_basis_win_rate"], kind="percent")
    with columns[4]:
        metric_card(st, "Open positions", metrics["open_positions"], kind="plain")
    with columns[5]:
        metric_card(st, "Review queue", f"Needs review: {metrics['review_count']}", kind="plain", emphasis="review")

    st.subheader("What needs attention")
    safe_dataframe(st, attention_display_rows(data))
    st.caption("Use the Data Quality page for the detailed review queue.")

    annual = annual_realized_chart_rows(data)
    st.subheader("Annual realized P&L")
    if annual:
        chart_rows = decimal_chart_rows(annual, ("Equity", "Options"))
        safe_chart(
            st,
            lambda: st.bar_chart(chart_rows, x="Year", y=["Equity", "Options"]),
            fallback_rows=chart_rows,
        )
        st.caption("Bars above zero are realized gains; bars below zero are realized losses. Equity and Options values come directly from the annual summary JSON.")
    else:
        st.info("Annual realized-P&L data is unavailable.")

    left, right = st.columns([1, 1])
    with left:
        st.subheader("Reading the chart")
        safe_structured_write(st, [
            "The zero line separates gains from losses.",
            "Each year shows Equity and Options as separate labeled series.",
            "Unknown-basis and basis-transfer records remain outside included P&L.",
        ])
    with right:
        st.subheader("Included totals")
        safe_structured_write(
            st,
            {
                "Equity realized P&L": format_currency(metrics["equity_realized_pnl"]),
                "Option realized P&L": format_currency(metrics["option_realized_pnl"]),
                "Known-basis win rate": format_percent(metrics["known_basis_win_rate"]),
            }
        )
