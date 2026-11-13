"""Executive Overview: final status, monthly trends and processing time, with global
cross-filtering.

Cross-filter rules
------------------
* Clicking a status bar or a month writes to the SAME session-state keys as the
  sidebar filters (single source of truth), so every page updates.
* Each chart ignores its own filter (the status chart shows all statuses under the
  other filters; the monthly charts show all months) so the selection can be
  switched or cleared by clicking again. KPI cards always apply every filter.
* Chart keys include the current filter value, so a filter changed elsewhere
  (sidebar, clear button) never leaves a stale selection on the chart.
"""

from __future__ import annotations

from dataclasses import replace

import streamlit as st

from core import config
from ui import charts, selection
from ui import components as ui
from ui import data, filters
from ui.formatting import fmt_int, fmt_month, fmt_share

state = filters.current_state()
s = data.filtered_summary(state)
options = data.options()
mode = ui.theme_mode()

ui.page_header(
    "Executive Overview",
    "Final status and processing time of loan applications, recalculated from the event log "
    "and reconciled against the existing reports.",
)
ui.filter_caption(filters.describe(state))
ui.active_filter_chips(st.session_state, options.months)

if s.empty:
    ui.empty_state()
else:
    state_all_statuses = replace(state, final_statuses=())  # status chart ignores its own filter
    state_all_months = replace(state, start_month_from=None, start_month_to=None)  # monthly charts ignore theirs

    dist = data.final_status_distribution(state)
    counts = dict(zip(dist["final_status"].fillna(""), dist["applications"]))
    total = len(s)
    open_n = int(s["is_open"].sum())

    trend_all = data.monthly(state_all_months)
    flagged = trend_all.loc[trend_all["censoring_warning"].astype(bool), "start_month"].tolist()
    flagged_text = ", ".join(fmt_month(month) for month in flagged)
    selected_trend = data.monthly(state)
    if selected_trend["censoring_warning"].astype(bool).any():
        ui.notice("censoring", extra="Start months in this selection affected: " + ", ".join(
            fmt_month(month) for month in selected_trend.loc[selected_trend["censoring_warning"].astype(bool), "start_month"]
        ) + ".")

    # ------------------------------------------------------------------ #
    # Final status
    # ------------------------------------------------------------------ #
    ui.section("Final application status",
               "Each application's final status event (A_Pending, A_Cancelled, A_Denied) - not its last recorded activity.")

    def share(status: str) -> str:
        return f"{fmt_share(counts[status], total)} of applications"

    ui.kpi_row(
        [
            ui.Kpi("total_applications", total),
            ui.Kpi("final_status_pending", counts[config.APPLICATION_PENDING], share(config.APPLICATION_PENDING)),
            ui.Kpi("final_status_cancelled", counts[config.APPLICATION_CANCELLED], share(config.APPLICATION_CANCELLED)),
            ui.Kpi("final_status_denied", counts[config.APPLICATION_DENIED], share(config.APPLICATION_DENIED)),
            ui.Kpi("no_final_status", open_n, f"{fmt_share(open_n, total)} · still open at data end"),
        ]
    )

    chart_col, side_col = st.columns([3, 2], gap="large")
    with chart_col:
        status_dist = data.final_status_distribution(state_all_statuses)
        status_key = "exec_status_chart::" + "|".join(state.final_statuses)
        ui.plotly(
            charts.status_bars(status_dist, mode, selected=state.final_statuses),
            key=status_key,
            on_select=selection.status_filter_callback(status_key, filters.STATUS_LABEL_ORDER),
        )
        ui.interaction_caption("status_bars", extra="The chart shows every final status for the other active "
                                                    "filters; the selected status is highlighted.")
    with side_col:
        ui.notice("a_pending")
        table = status_dist[["label", "applications", "percentage"]].rename(
            columns={"label": "Final status", "applications": "Applications", "percentage": "Share (%)"}
        )
        with st.expander("Table view", icon=":material/table:"):
            st.dataframe(table, hide_index=True,
                         column_config={"Share (%)": st.column_config.NumberColumn(format="%.1f")})
            ui.download_csv(table, "final_status_distribution.csv", key="dl_exec_status")

    # ------------------------------------------------------------------ #
    # Monthly volume by final status
    # ------------------------------------------------------------------ #
    ui.section("Applications by start month",
               "Stacked by final status. Every start month is shown for the other active filters; "
               "selected months are highlighted.")
    view = st.segmented_control("Show", ["Counts", "100% share"], default="Counts", key="exec_monthly_view",
                                label_visibility="collapsed") or "Counts"
    normalised = view == "100% share"
    selected_range = (state.start_month_from, state.start_month_to) if state.start_month_from else None
    month_key = "exec_monthly_chart::" + (f"{selected_range[0]}..{selected_range[1]}" if selected_range else "all")
    ui.plotly(
        charts.monthly_status_bars(trend_all, mode, normalised=normalised, selected_range=selected_range),
        key=month_key,
        on_select=selection.month_filter_callback(month_key, options.months),
    )
    ui.interaction_caption("monthly_status")
    if flagged:
        st.caption(
            f":material/event_busy: **Incomplete months: {flagged_text}** (faded bars, shaded band). The data ends on "
            "1 February 2017, so applications from these months may still be open; their final-status mix and "
            "durations are not yet complete."
        )
    monthly_table = trend_all.rename(columns={
        "start_month": "Start month", "applications": "Applications",
        config.APPLICATION_PENDING: config.FINAL_STATUS_LABELS[config.APPLICATION_PENDING],
        config.APPLICATION_CANCELLED: config.FINAL_STATUS_LABELS[config.APPLICATION_CANCELLED],
        config.APPLICATION_DENIED: config.FINAL_STATUS_LABELS[config.APPLICATION_DENIED],
        "no_final_status": config.NO_FINAL_STATUS_LABEL,
        "median_duration_days": "Median duration (days)", "p90_duration_days": "90th percentile duration (days)",
        "days_observed_after_month_end": "Days observed after month end", "censoring_warning": "Incomplete month",
    }).drop(columns=["has_open_applications", "short_observation_window"])
    with st.expander("Table view", icon=":material/table:"):
        st.dataframe(monthly_table, hide_index=True, column_config={
            "Median duration (days)": st.column_config.NumberColumn(format="%.1f"),
            "90th percentile duration (days)": st.column_config.NumberColumn(format="%.1f"),
            "Days observed after month end": st.column_config.NumberColumn(format="%.0f"),
        })
        ui.download_csv(monthly_table, "applications_by_start_month.csv", key="dl_exec_monthly")

    # ------------------------------------------------------------------ #
    # Processing time
    # ------------------------------------------------------------------ #
    ui.section("Processing time",
               "Headline duration runs from the first to the last recorded event, matching application_performance.csv.")
    dur = data.durations(state)
    d_all, d_final = dur["duration_days"].iloc[0], dur["days_to_final_status"].iloc[0]
    open_note = (
        f"Includes {fmt_int(d_all['open_applications'])} open applications, cut off at data end"
        if d_all["open_applications"] else "No open applications in this selection"
    )
    ui.kpi_row(
        [
            ui.Kpi("median_duration_days", d_all["median"], open_note),
            ui.Kpi("p90_duration_days", d_all["p90"], open_note),
            ui.Kpi("median_days_to_final_status", d_final["median"],
                   f"Separate metric · {fmt_int(d_final['count'])} applications with a final status"),
        ]
    )
    ui.plotly(charts.monthly_duration_lines(trend_all, mode), key="exec_duration_chart")
    ui.interaction_caption("monthly_duration", extra=ui.LEGEND_NOTE)
    if flagged:
        st.caption(
            f":material/event_busy: Dashed lines and hollow markers mark incomplete months ({flagged_text}). "
            "Their durations look shorter because applications still open on 1 February 2017 are cut off at "
            "their last recorded event; they are not evidence of faster processing."
        )

ui.source_caption("Event log (data/processed/loan_application_events.parquet), recalculated", "all times UTC")
