from __future__ import annotations

from collections import Counter
from typing import Any

from dashboard.data_loader import attention_display_rows, review_display_rows, technical_review_rows
from dashboard.pages.common import page_header


def render(st: Any, data: Any) -> None:
    page_header(st, data, "Data Quality", "Review items, confidence levels and methodology boundaries for the synthetic demo data.")

    reviews = review_display_rows(data)
    if reviews:
        by_category = Counter(row["Category"] for row in reviews)
        by_severity = Counter(row["Severity"] for row in reviews)
        left, right = st.columns(2)
        with left:
            st.subheader("Review counts by category")
            st.dataframe([{"Category": key, "Count": value} for key, value in sorted(by_category.items())], hide_index=True, use_container_width=True)
        with right:
            st.subheader("Review counts by severity")
            st.dataframe([{"Severity": key, "Count": value} for key, value in sorted(by_severity.items())], hide_index=True, use_container_width=True)
        st.subheader("Review detail")
        st.dataframe(reviews, hide_index=True, use_container_width=True)
        with st.expander("Technical details"):
            st.dataframe(technical_review_rows(data), hide_index=True, use_container_width=True)
    else:
        st.info("No review items are available in the selected sanitized outputs.")

    st.subheader("Attention summary")
    st.dataframe(attention_display_rows(data), hide_index=True, use_container_width=True)

    st.subheader("Confidence labels")
    st.write({
        "deterministic_cusip": "Security identity was supported by a CUSIP.",
        "lower_structural_only": "Option identity used underlying, expiration, call/put and strike without CUSIP.",
        "partial": "A balance or position is based on incomplete history.",
        "verified": "A supplied anchor was applied at its effective date.",
        "derived": "A value was rolled forward from a verified anchor using ledger events.",
    })

    st.subheader("Methodologies")
    st.write([
        "Settlement-date cash ledger",
        "Anchored position ledger with trade-date and settled views",
        "Analytical FIFO equity realized P&L",
        "Analytical side-aware option FIFO realized P&L",
    ])

    st.subheader("Known limitations")
    st.write([
        "Sprint 4A uses synthetic demo data and does not read private brokerage files.",
        "No market prices, unrealized returns or allocation percentages are shown.",
        "Tax treatment, wash sales, strategy grouping and spread grouping are deferred.",
        "Generalized PII detection remains a later hardening sprint.",
    ])

    st.info("Privacy notice: this dashboard defaults to synthetic demo data and does not display raw descriptions or raw source-row JSON.")
