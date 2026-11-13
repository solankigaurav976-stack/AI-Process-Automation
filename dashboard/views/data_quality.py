"""Data Quality & Reconciliation.

Reconciliation results (PASS / FAIL / ERROR) are kept apart from informational
findings (INFO), which are context, never failures. Global: not affected by the
sidebar filters.
"""

from __future__ import annotations

import numbers

import pandas as pd
import streamlit as st

from core import config
from core import reconciliation as rc
from ui import components as ui
from ui import data
from ui.filters import FilterState
from ui.formatting import fmt_int, fmt_pct

ui.page_header(
    "Data Quality & Reconciliation",
    "Every existing report recalculated from the event log and compared value by value.",
)
st.caption(":material/public: Global view - sidebar filters do not apply to this page.")


def _display(value) -> str:
    """Readable text for mixed expected/calculated values."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, numbers.Integral):
        return f"{int(value):,}"
    if isinstance(value, numbers.Real):
        v = float(value)
        return f"{v:,.0f}" if v.is_integer() else f"{v:.6g}"
    return str(value)


def _checks_table(df: pd.DataFrame) -> pd.DataFrame:
    out = df[["check_id", "report", "check", "expected", "calculated", "difference",
              "max_abs_difference", "tolerance", "status", "note"]].copy()
    for col in ("expected", "calculated", "difference"):
        out[col] = out[col].map(_display)
    return out.rename(columns={
        "check_id": "ID", "report": "Report", "check": "Check", "expected": "Expected (CSV)",
        "calculated": "Calculated (event log)", "difference": "Difference",
        "max_abs_difference": "Max |difference|", "tolerance": "Tolerance", "status": "Result", "note": "Note",
    })


result = data.reconciliation()
_, report_errors = data.reports()
checks = result.checks
recon = checks[checks["status"].isin([rc.PASS, rc.FAIL, rc.ERROR])]
info = checks[checks["status"] == rc.INFO]
counts = result.status_counts()

# --------------------------------------------------------------------------- #
# Status banner and counts
# --------------------------------------------------------------------------- #
if result.passed:
    st.success(
        f"**All {fmt_int(len(recon))} reconciliation checks passed.** All seven reports match the "
        "event-log recalculation.", icon=":material/check_circle:",
    )
else:
    st.error(
        f"**{fmt_int(counts[rc.FAIL] + counts[rc.ERROR])} reconciliation check(s) did not pass.** "
        "See the failing rows below.", icon=":material/error:",
    )
for name, message in report_errors.items():
    st.error(f"**{config.REPORT_FILES[name]}** could not be loaded: {message}", icon=":material/description:")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Checks passed", fmt_int(counts[rc.PASS]), border=True)
c2.metric("Checks failed", fmt_int(counts[rc.FAIL]), border=True)
c3.metric("Reports not loaded", fmt_int(counts[rc.ERROR]), border=True)
c4.metric("Informational findings", fmt_int(counts[rc.INFO]), border=True,
          help="Context recorded during reconciliation. These are not checks and never count as failures.")

tab_recon, tab_info, tab_outcomes, tab_limits = st.tabs(
    ["Reconciliation results", "Informational findings", "Final status vs last activity", "Known limitations"]
)

# --------------------------------------------------------------------------- #
# Reconciliation results (PASS / FAIL / ERROR only)
# --------------------------------------------------------------------------- #
with tab_recon:
    st.caption(
        "Expected = value in the existing CSV; Calculated = value recalculated from the event log. "
        "Row-by-row checks show rows compared vs rows matching (difference 0 = all match). "
        "Floats stored in full precision use a 1e-6 tolerance (observed gaps ≤ 4e-15, from summation order); "
        "values the report rounded to 2 decimals must equal the recalculated value rounded to 2 decimals."
    )
    reports_available = sorted(recon["report"].unique())
    chosen = st.multiselect("Report", reports_available, placeholder="All reports", key="dq_report_filter")
    only_failures = st.toggle("Show only failed checks", key="dq_only_failures")
    view = recon if not chosen else recon[recon["report"].isin(chosen)]
    if only_failures:
        view = view[view["status"] != rc.PASS]
    if view.empty:
        ui.empty_state("No checks match this selection.")
    else:
        st.dataframe(
            _checks_table(view), hide_index=True,
            column_config={
                "Max |difference|": st.column_config.NumberColumn(format="%.2e"),
                "Tolerance": st.column_config.NumberColumn(format="%.0e"),
            },
        )
    ui.download_csv(_checks_table(recon), "reconciliation_checks.csv", label="Download reconciliation checks",
                    key="dl_dq_checks")
    for check_id, detail in result.details.items():
        status = checks.loc[checks["check_id"] == check_id, "status"].iloc[0]
        if status in (rc.FAIL, rc.ERROR):
            with st.expander(f"Mismatching rows for {check_id}"):
                st.dataframe(detail, hide_index=True)

# --------------------------------------------------------------------------- #
# Informational findings (never failures)
# --------------------------------------------------------------------------- #
with tab_info:
    st.info(
        "**These are findings, not failures.** They record context observed during reconciliation "
        "(for example how a category is composed) and are excluded from the pass/fail counts.",
        icon=":material/info:",
    )
    for row in info.itertuples():
        with st.container(border=True):
            st.markdown(f"**{row.check}**  \n{row.report} · {row.check_id}")
            st.markdown(_display(row.calculated))
            if row.note:
                st.caption(row.note)
            if row.check_id in result.details:
                with st.expander("Applications"):
                    st.dataframe(result.details[row.check_id], hide_index=True)
    ui.download_csv(_checks_table(info), "informational_findings.csv", label="Download findings",
                    key="dl_dq_info")

# --------------------------------------------------------------------------- #
# Final status vs last recorded activity
# --------------------------------------------------------------------------- #
with tab_outcomes:
    ui.notice("last_activity")
    reports, _ = data.reports()
    left, right = st.columns(2, gap="large")
    with left:
        st.markdown("**Final status** · recalculated from the event log")
        dist = data.final_status_distribution(FilterState())[["label", "applications", "percentage"]]  # global
        st.dataframe(dist.rename(columns={"label": "Final status", "applications": "Applications",
                                          "percentage": "Share (%)"}),
                     hide_index=True, column_config={"Share (%)": st.column_config.NumberColumn(format="%.1f")})
    with right:
        st.markdown("**Last recorded activity (not a final status)** · reports/application_outcomes.csv")
        if "application_outcomes" in reports:
            outcomes = reports["application_outcomes"].rename(columns={
                "final_activity": "Last recorded activity", "applications": "Applications", "percentage": "Share (%)"})
            st.dataframe(outcomes, hide_index=True,
                         column_config={"Share (%)": st.column_config.NumberColumn(format="%.2f")})
        else:
            st.warning("application_outcomes.csv could not be loaded.", icon=":material/warning:")

# --------------------------------------------------------------------------- #
# Known limitations
# --------------------------------------------------------------------------- #
with tab_limits:
    ui.notice("censoring")
    ui.notice("timer")
    cs = data.credit_scores().iloc[0]
    ui.notice(
        "credit_score",
        extra=f"{fmt_int(cs['score_zero_potentially_missing'])} of {fmt_int(cs['offers'])} offers "
              f"({fmt_pct(cs['zero_share_percent'])}) have CreditScore = 0.",
    )
    ui.notice("lifecycle")
    ui.notice("closure")
    st.markdown(
        "- **Offer creation is recorded twice.** O_Create Offer and O_Created occur together for every offer; "
        "only O_Create Offer is counted.\n"
        "- **Three reports had no source script.** cancellation_completion_delays.csv and the two offer-cancellation "
        "reports were matched exactly, but their definitions were reconstructed from the data.\n"
        "- **'Workflow activity only' is broader than its name.** Some of these applications also contain offer or "
        "application events (see Informational findings); the original category is kept unchanged."
    )
