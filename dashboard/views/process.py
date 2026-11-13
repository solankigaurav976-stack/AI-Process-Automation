"""Process Analysis: milestone flow, transitions, waiting times and activity volume.

Interaction model
-----------------
* Global filters (sidebar / Executive clicks) apply to everything on this page.
* Clicking a transition (matrix cell or ranked bar) selects a PAGE transition pair
  for the drill-down; clicking an activity highlights its row/column in the matrix.
  These never change the global filters.
* The Sankey is hover-only (Streamlit returns no clicks for Sankey traces).
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import drilldown
from core import metrics as m
from ui import aggregations as ag
from ui import charts, selection
from ui import components as ui
from ui import data, filters
from ui.formatting import fmt_int

state = filters.current_state()
mode = ui.theme_mode()
ss = st.session_state

ui.page_header(
    "Process Analysis",
    "How applications move through offer milestones, and where time passes between recorded events.",
)
ui.filter_caption(filters.describe(state))
ui.active_filter_chips(ss, data.options().months)

summary = data.filtered_summary(state)
if summary.empty:
    ui.empty_state()
    st.stop()

all_pairs = data.transitions(state, 1)
frequent_pairs = data.transitions(state)  # >= 100 occurrences, as in process_bottlenecks.csv
freq_all = data.activity_frequency(state, False)
valid_pairs = list(zip(all_pairs["activity"], all_pairs["next_activity"]))
for name, valid in (("transition", valid_pairs), ("activity", freq_all["activity"].tolist())):
    message = selection.validate_selection(ss, name, valid)
    if message:
        st.info(message, icon=":material/filter_alt_off:")
sel_pair = selection.get_selection(ss, "transition")
sel_pair = tuple(sel_pair) if sel_pair else None
sel_activity = selection.get_selection(ss, "activity")

# ---------------------------------------------------------------------- #
# P1 - milestone Sankey (hover only)
# ---------------------------------------------------------------------- #
ui.section("Offer milestones to final status",
           "Each application is counted once, on its furthest offer milestone, then by final status.")
sankey = ag.milestone_sankey(summary)
links = sankey.links.set_index(["source_label", "target_label"])["value"]
ui.kpi_row([
    ui.Kpi("milestone_offer_sent", int(links.get((ag.NODE_ALL, ag.NODE_OFFER_SENT), 0))),
    ui.Kpi("milestone_no_offer_sent", int(links.get((ag.NODE_ALL, ag.NODE_NO_OFFER_SENT), 0))),
    ui.Kpi("milestone_offer_returned", int(links.get((ag.NODE_OFFER_SENT, ag.NODE_OFFER_RETURNED), 0))),
    ui.Kpi("milestone_offer_not_returned", int(links.get((ag.NODE_OFFER_SENT, ag.NODE_OFFER_NOT_RETURNED), 0))),
])
ui.plotly(charts.milestone_sankey(sankey, mode), key="proc_sankey")
ui.interaction_caption("milestone_sankey")
st.caption(f"{fmt_int(sankey.total)} applications in the selection; every one follows exactly one path.")
paths = ag.milestone_path_counts(summary).rename(columns={
    "offer_milestone": "Offer milestone", "final_status_label": "Final status", "applications": "Applications"})
with st.expander("Table view", icon=":material/table:"):
    ui.table(paths)
    ui.download_csv(paths, "offer_milestones_by_final_status.csv", key="dl_proc_milestones")

# ---------------------------------------------------------------------- #
# P2 - transition matrix
# ---------------------------------------------------------------------- #
ui.section("Transitions between recorded events",
           "Every activity pair that directly follows within an application. Rows: activity; columns: next activity.")
ui.notice("timer")
ui.notice("interleaving")
measure = st.segmented_control("Colour cells by", ["Median wait", "Transition count"], default="Median wait",
                               key="proc_matrix_measure") or "Median wait"
value = "median_wait_hours" if measure == "Median wait" else "transition_count"
order = ag.activity_order(freq_all, extra=sorted(set(all_pairs["activity"]) | set(all_pairs["next_activity"])))
cells = ag.transition_matrix(all_pairs, order)
pair_text = "->".join(sel_pair) if sel_pair else ""
key = f"proc_matrix::{pair_text}::{sel_activity or ''}::{value}"
ui.plotly(
    charts.transition_matrix(cells, order, mode, value=value, selected_pair=sel_pair, highlight_activity=sel_activity),
    key=key,
    on_select=selection.page_selection_callback(key, "transition", valid_pairs, pair=True),
)
n_timer = int(all_pairs["likely_timer_driven"].fillna(False).sum())
ui.interaction_caption(
    "transition_matrix",
    extra=f"{fmt_int(len(all_pairs))} pairs; ⏱ outlines mark {n_timer} likely timer-driven pairs (exploratory). "
          f"{fmt_int(int(all_pairs['crosses_event_streams'].sum()))} pairs cross event streams (shown in hover). "
          "Colour uses a log scale.",
)

# ---------------------------------------------------------------------- #
# P3 - longest median waits
# ---------------------------------------------------------------------- #
ui.section("Longest median waits", "Pairs occurring at least 100 times, as in process_bottlenecks.csv.")
if frequent_pairs.empty:
    ui.empty_state("No transition occurs at least 100 times in the current selection.")
else:
    top = ag.top_waits(frequent_pairs, 15)
    key = f"proc_ranked::{pair_text}"
    ui.plotly(
        charts.ranked_waits(top, mode, selected_pair=sel_pair),
        key=key,
        on_select=selection.page_selection_callback(key, "transition", list(zip(top["activity"], top["next_activity"])),
                                                    pair=True),
    )
    ui.interaction_caption(
        "ranked_waits",
        extra="Grey bars marked ⏱ are likely timer-driven (exploratory) - not staff waiting times. "
              "Tick marks show the 90th percentile.",
    )

# ---------------------------------------------------------------------- #
# P4 - drill-down for the selected pair
# ---------------------------------------------------------------------- #
ui.section("Waiting-time drill-down")
if sel_pair is None:
    st.caption(":material/touch_app: Click a matrix cell or a ranked bar to see every individual wait for that pair.")
else:
    a, b = sel_pair
    pair_row = all_pairs[(all_pairs["activity"] == a) & (all_pairs["next_activity"] == b)].iloc[0]
    instances = data.transition_instances(state, a, b)
    stats = drilldown.instance_statistics(instances)
    head_col, clear_col = st.columns([5, 1], vertical_alignment="center")
    head_col.markdown(f"**{a} → {b}**")
    clear_col.button("Clear", icon=":material/close:", key="chip_sel_transition",
                     on_click=selection.clear_selection, args=(ss, "transition"))
    flag = pair_row["likely_timer_driven"]
    timer = bool(flag) if not pd.isna(flag) else False  # NA (no resource data) is not flagged
    labels = [
        "⏱ Likely timer-driven (exploratory) - not a staff waiting time" if timer else "Wait between recorded events",
        "Crosses event streams (interleaving, not necessarily a hand-off)" if bool(pair_row["crosses_event_streams"])
        else "Same event stream",
    ]
    st.caption(" · ".join(labels))
    ui.kpi_row([
        ui.Kpi("transition_count", stats["transition_count"]),
        ui.Kpi("median_wait_hours", stats["median_wait_hours"]),
        ui.Kpi("p90_wait_hours", stats["p90_wait_hours"]),
    ])
    ui.plotly(charts.wait_histogram(ag.wait_histogram(instances["wait_hours"]), mode, timer_driven=timer),
              key=f"proc_hist::{a}->{b}")
    ui.interaction_caption("wait_histogram", extra=ag.describe_wait(stats) + ".")
    occurrences = instances.rename(columns={
        "case_id": "Application", "event_time": "Event (UTC)", "next_event_time": "Next event (UTC)",
        "wait_hours": "Wait (hours)", "next_resource": "Resource of next event (as recorded)",
    }).drop(columns=["activity", "next_activity"])
    with st.expander(f"All {fmt_int(len(occurrences))} occurrences", icon=":material/table:"):
        ui.table(occurrences, column_config={
            "Event (UTC)": st.column_config.DatetimeColumn(format="D MMM YYYY, HH:mm"),
            "Next event (UTC)": st.column_config.DatetimeColumn(format="D MMM YYYY, HH:mm"),
            "Wait (hours)": st.column_config.NumberColumn(format="%.2f"),
        })
        ui.download_csv(occurrences, "transition_occurrences.csv", key="dl_proc_occurrences")

# ---------------------------------------------------------------------- #
# P5 - activity frequency
# ---------------------------------------------------------------------- #
ui.section("Activity frequency")
ui.notice("lifecycle")
basis = st.segmented_control("Count", ["All lifecycle stages", "Completed only"], default="All lifecycle stages",
                             key="proc_freq_basis") or "All lifecycle stages"
completed_only = basis == "Completed only"
freq = data.activity_frequency(state, completed_only)
freq_key = "activity_events_completed" if completed_only else "activity_events_all"
st.caption(f"**{ui.kpi_label(freq_key)}.** {ui.kpi_help(freq_key).split(chr(10))[0]}")
key = f"proc_freq::{sel_activity or ''}::{basis}"
ui.plotly(
    charts.activity_frequency_bars(freq, mode, selected_activity=sel_activity),
    key=key,
    on_select=selection.page_selection_callback(key, "activity", freq["activity"].tolist()),
)
ui.interaction_caption("activity_frequency", extra=ui.LEGEND_NOTE)
if sel_activity:
    st.button(f"Activity: {sel_activity}", icon=":material/close:", key="chip_sel_activity",
              on_click=selection.clear_selection, args=(ss, "activity"))
with st.expander("Table view", icon=":material/table:"):
    table = freq.rename(columns={"activity": "Activity", "total_events": "Events", "applications": "Applications"})
    ui.table(table)
    ui.download_csv(table, "activity_frequency.csv", key="dl_proc_freq")
    st.markdown("**Events by lifecycle stage** (all stages)")
    ui.table(data.lifecycle_breakdown(state))

ui.source_caption("Event log (data/processed/loan_application_events.parquet), recalculated", "all times UTC")
