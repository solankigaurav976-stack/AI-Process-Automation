"""Loan Application Process Intelligence Dashboard - entry point.

Run from inside dashboard/:

    ../.venv/bin/streamlit run app.py

The dashboard only reads data/processed/ and reports/; it never writes files.
"""

from __future__ import annotations

import streamlit as st

st.set_page_config(
    page_title="Loan Process Intelligence",
    page_icon=":material/account_balance:",
    layout="wide",
    initial_sidebar_state="expanded",
)

from core.errors import DashboardError  # noqa: E402
from ui import components, data, filters, theme  # noqa: E402
from ui.formatting import fmt_date, fmt_int  # noqa: E402

PAGES = {
    "Overview": [
        st.Page("views/executive.py", title="Executive Overview", icon=":material/dashboard:",
                url_path="executive", default=True),
    ],
    "Analysis": [
        st.Page("views/process.py", title="Process Analysis", icon=":material/account_tree:", url_path="process"),
        st.Page("views/cancellation.py", title="Cancellation Intelligence", icon=":material/cancel:",
                url_path="cancellation"),
    ],
    "Detail": [
        st.Page("views/explorer.py", title="Application Explorer", icon=":material/search:", url_path="explorer"),
    ],
    "Assurance": [
        st.Page("views/data_quality.py", title="Data Quality & Reconciliation", icon=":material/verified:",
                url_path="data-quality"),
    ],
}


def main() -> None:
    st.markdown(theme.PAGE_CSS, unsafe_allow_html=True)
    navigation = st.navigation(PAGES)

    st.sidebar.markdown("### Loan Process Intelligence")
    st.sidebar.caption("BPI Challenge 2017 loan application event log")
    components.dataset_attribution()  # before loading data, so it is shown even if loading fails

    try:
        options = data.options()
        overview = data.overview()
    except DashboardError as error:
        components.error_panel(error)
        st.stop()

    st.session_state["filter_state"] = filters.sidebar_filters(options)

    st.sidebar.divider()
    st.sidebar.caption(
        f"**Data:** {fmt_int(overview.applications)} applications · {fmt_int(overview.events)} events  \n"
        f"**Period:** {fmt_date(overview.first_event)} – {fmt_date(overview.last_event)}  \n"
        "**All times UTC.** Applications open on the last date are right-censored."
    )
    navigation.run()


main()
