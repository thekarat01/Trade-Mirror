from __future__ import annotations

from typing import Any

from dashboard.data_loader import annual_realized_chart_rows, csv_rows, decimal_or_zero, known_basis_security_rows, overview_metrics
from dashboard.formatters import format_currency
from dashboard.pages.common import decimal_chart_rows, page_header


def render(st: Any, data: Any) -> None:
    page_header(st, data, "Realized P&L", "Known-basis analytical FIFO results. Unknown-basis and basis-transfer rows stay separate.")

    metrics = overview_metrics(data)
    columns = st.columns(3)
    columns[0].metric("Equity realized P&L", format_currency(metrics["equity_realized_pnl"]))
    columns[1].metric("Option realized P&L", format_currency(metrics["option_realized_pnl"]))
    columns[2].metric("Included net P&L", format_currency(metrics["included_net_realized_pnl"]))

    annual = annual_realized_chart_rows(data)
    if annual:
        st.subheader("Annual realized P&L")
        st.bar_chart(decimal_chart_rows(annual, ("Equity", "Options")), x="Year", y=["Equity", "Options"])
        st.caption("Positive bars are realized gains and negative bars are realized losses.")

    security_rows = known_basis_security_rows(data)
    known = [row for row in security_rows if row["pnl"] != 0]
    known.sort(key=lambda row: row["pnl"])
    left, right = st.columns(2)
    with left:
        st.subheader("Worst realized securities/contracts")
        st.dataframe(_display_ranked(known[:5]), hide_index=True, use_container_width=True)
    with right:
        st.subheader("Best realized securities/contracts")
        st.dataframe(_display_ranked(list(reversed(known[-5:]))), hide_index=True, use_container_width=True)

    years = ["All"] + sorted({row.get("closing_trade_date", "")[:4] for row in csv_rows(data, "realized_pnl/equity_lot_matches.csv") if row.get("closing_trade_date")})
    selected_year = st.selectbox("Year", years)
    asset_type = st.selectbox("Asset type", ["All", "Equity", "Option"])

    equity_rows = [
        row for row in csv_rows(data, "realized_pnl/equity_lot_matches.csv")
        if selected_year == "All" or row.get("closing_trade_date", "").startswith(selected_year)
    ]
    option_rows = [
        row for row in csv_rows(data, "option_realized_pnl/option_lot_matches.csv")
        if selected_year == "All" or row.get("closing_trade_date", "").startswith(selected_year)
    ]

    if asset_type in {"All", "Equity"}:
        st.subheader("Equity matches")
        st.dataframe(equity_rows, hide_index=True, use_container_width=True)
    if asset_type in {"All", "Option"}:
        side = st.selectbox("Option side", ["All", "long", "short"])
        option_type = st.selectbox("Call/put", ["All", "call", "put"])
        outcome = st.selectbox("Outcome", ["All"] + sorted({row.get("outcome", "") for row in option_rows if row.get("outcome")}))
        filtered_options = [
            row for row in option_rows
            if (side == "All" or row.get("position_side") == side)
            and (option_type == "All" or row.get("option_type") == option_type)
            and (outcome == "All" or row.get("outcome") == outcome)
        ]
        st.subheader("Option matches")
        st.dataframe(filtered_options, hide_index=True, use_container_width=True)

    transfers = list(csv_rows(data, "option_realized_pnl/option_basis_transfers.csv"))
    with st.expander("Basis-transfer option events"):
        if transfers:
            st.dataframe(transfers, hide_index=True, use_container_width=True)
        else:
            st.write("No basis-transfer option events are available.")
    st.caption("Short-option premium ratios are descriptive only and are not presented as ROI.")


def _display_ranked(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "asset_type": row["asset_type"],
            "name": row["name"],
            "known_basis_pnl": format_currency(row["pnl"]),
        }
        for row in rows
    ]
