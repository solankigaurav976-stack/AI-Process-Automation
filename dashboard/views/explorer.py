"""Application Explorer: find an application and inspect every recorded event.

Interaction model
-----------------
* The application list follows the global filters (sidebar / chart clicks); this
  page never changes them.
* An application is selected by an exact ID search, by selecting a table row, or by
  a hand-off from another page (``selection.request_application``). The selection is
  the page selection ``sel_application``; it is cleared, with a notice, when the
  global filters no longer include it.
* Clicking an event in the timeline (or selecting an event-table row) selects that
  event (``sel_event``): it is ringed in the timeline and shown as the selected row
  of the event table (``selection_default``). Streamlit cannot scroll a table to a
  row, so the selected event is also summarised above the table.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from core import config
from ui import aggregations as ag
from ui import charts, selection, theme
from ui import components as ui
from ui import data, filters
from ui.formatting import fmt_int

state = filters.current_state()
mode = ui.theme_mode()
ss = st.session_state

ui.page_header(
    "Application Explorer",
    "Find an application and inspect every recorded event, in the original event order.",
)
ui.filter_caption(filters.describe(state))
ui.active_filter_chips(ss, data.options().months)

COLUMNS = {
    "case_id": "Application",
    "start_time": "First event (UTC)",
    "end_time": "Last event (UTC)",
    "duration_days": "Duration (days)",
    "final_status_label": "Final status",
    "days_to_final_status": "Days to final status",
    "total_events": "Events",
    "offers_created": "Offers created",
    "offer_cancellation_events": "O_Cancelled events",
    "has_application_cancellation": "A_Cancelled",
    "loan_goal": "Loan goal",
    "application_type": "Application type",
    "requested_amount": "Requested amount",
}
TIME_FORMAT = "D MMM YYYY, HH:mm:ss.SSS"

s = data.filtered_summary(state)
if s.empty:
    ui.empty_state()
    st.stop()

ids = s["case_id"].tolist()
message = selection.validate_selection(ss, "application", ids)
if message:
    st.info(message + " The global filters were not changed; widen them in the sidebar to view it again.",
            icon=":material/filter_alt_off:")
sel_app = selection.get_selection(ss, "application")

# ---------------------------------------------------------------------- #
# X1 - find and select an application
# ---------------------------------------------------------------------- #
ui.section("Find an application", "The list follows the global filters. Search by ID or select a row.")
query = st.text_input(
    "Find an application ID", placeholder="e.g. Application_1000086665", key="explorer_query",
    on_change=selection.search_callback("explorer_query", "application", ids),
    help="Typing part of an ID narrows the list; the full ID selects that application.",
).strip()
table = s[[c for c in COLUMNS if c in s.columns]]
if query:
    table = table[table["case_id"].str.contains(query, case=False, regex=False)]
st.caption(f"{fmt_int(len(table))} of {fmt_int(len(s))} applications shown")
if table.empty:
    ui.empty_state(f"No application ID contains '{query}'.")
    outside = ag.find_application(query, data.summary()["case_id"])
    if outside is not None:
        st.info(f"{outside} exists but is outside the current filters ({filters.describe(state)}). "
                "The filters are not changed automatically; widen them in the sidebar to view it.",
                icon=":material/filter_alt:")
else:
    display = table.rename(columns=COLUMNS)
    key = f"explorer_table::{query}::{sel_app or ''}"
    table_ids = table["case_id"].tolist()
    st.dataframe(
        ui.display_frame(display), hide_index=True, key=key,
        on_select=selection.row_selection_callback(key, "application", table_ids),
        selection_mode="single-row", selection_default=selection.row_default(table_ids, sel_app),
        column_config={
            "First event (UTC)": st.column_config.DatetimeColumn(format="D MMM YYYY, HH:mm"),
            "Last event (UTC)": st.column_config.DatetimeColumn(format="D MMM YYYY, HH:mm"),
            "Duration (days)": st.column_config.NumberColumn(format="%.1f"),
            "Days to final status": st.column_config.NumberColumn(format="%.1f"),
            "Requested amount": st.column_config.NumberColumn(format="%,.0f"),
        },
    )
    st.caption(":material/touch_app: Select a row to open that application below.")
    ui.download_csv(display, "applications.csv", key="dl_explorer")

if sel_app is None:
    selection.clear_selection(ss, "event")  # an event belongs to its application (5f fix)
    st.caption(":material/info: No application selected. Search for a full ID, select a row, "
               "or use “Open in Application Explorer” on the Cancellation Intelligence page.")
    ui.source_caption("Event log (data/processed/loan_application_events.parquet), recalculated", "all times UTC")
    st.stop()

# ---------------------------------------------------------------------- #
# Selected application
# ---------------------------------------------------------------------- #
row = s[s["case_id"] == sel_app].iloc[0]
detail = data.application_detail(sel_app)
timeline, lifelines, events = detail.timeline, detail.lifelines, detail.events

event_ids = timeline["event_id"].tolist()
if selection.get_selection(ss, "event") not in (None, *event_ids):
    selection.clear_selection(ss, "event")  # an event of a previously selected application
sel_event = selection.get_selection(ss, "event")

with st.container(border=True):
    head, clear = st.columns([5, 1], vertical_alignment="center")
    head.markdown(f"**Selected application: `{sel_app}`** · {row['final_status_label']} · "
                  f"{fmt_int(row['total_events'])} recorded events")
    clear.button("Clear", icon=":material/close:", key="chip_sel_application",
                 on_click=selection.clear_selection, args=(ss, "application"),
                 help="Clear the selected application (global filters are kept).")

# ---------------------------------------------------------------------- #
# X3a - application summary (core.metrics.application_summary)
# ---------------------------------------------------------------------- #
ui.section("Application summary", "From the application summary used on every page (first-to-last-event duration).")
for col, (title, frame) in zip(st.columns(3), ag.application_details(row).items()):
    with col:
        st.markdown(f"**{title}**")
        ui.table(frame)
if bool(row["is_open"]):
    ui.notice("censoring", extra="This application has no final status by 1 February 2017; its duration is censored.")
elif row["final_status"] == "A_Pending":
    ui.notice("a_pending")
if row["last_activity"] != row["final_status"]:
    ui.notice("last_activity")
if bool(row["has_offer_cancellation"]) and bool(row["has_application_cancellation"]):
    ui.notice("cancellation_types")

# ---------------------------------------------------------------------- #
# X2 - event timeline
# ---------------------------------------------------------------------- #
ui.section("Event timeline", "One marker per recorded event, in three event-stream lanes.")
key = f"explorer_timeline::{sel_app}::{sel_event or ''}"
ui.plotly(
    charts.application_timeline(timeline, lifelines, mode, is_open=bool(row["is_open"]), selected_event=sel_event),
    key=key,
    on_select=selection.page_selection_callback(key, "event", event_ids),
    config=theme.ZOOM_PLOTLY_CONFIG,
)
no_outcome = int(lifelines["offer_outcome_time"].isna().sum())
ui.interaction_caption(
    "application_timeline",
    extra=f"{fmt_int(len(timeline))} markers for {fmt_int(row['total_events'])} recorded events. "
          f"Offer lifelines ({fmt_int(len(lifelines))}) run from O_Create Offer to the offer's last outcome "
          "(O_Accepted, O_Refused or O_Cancelled)"
          + (f"; {fmt_int(no_outcome)} without a recorded outcome are dotted to the data end." if no_outcome else ".")
          + " Workflow events are points only: lifecycle stages cannot be paired reliably, so no task durations "
            "are shown. Resources are shown as recorded; roles are not inferred.",
)

# ---------------------------------------------------------------------- #
# X3b - complete event table
# ---------------------------------------------------------------------- #
ui.section("Events", "Every recorded event of this application, in the original event order, as recorded.")
if sel_event is not None:
    picked = timeline[timeline["event_id"] == sel_event].iloc[0]
    info, clear = st.columns([5, 1], vertical_alignment="center")
    lifecycle, resource = (v if isinstance(v, str) else "—" for v in (picked["lifecycle"], picked["resource"]))
    info.markdown(f"**Selected event #{picked['order']}:** {picked['activity']} · {lifecycle} · "
                  f"{pd.Timestamp(picked['time']).strftime('%d %b %Y, %H:%M:%S UTC')} · "
                  f"resource {resource} (as recorded) — selected in the table below.")
    clear.button("Clear", icon=":material/close:", key="chip_sel_event",
                 on_click=selection.clear_selection, args=(ss, "event"))
key = f"explorer_events::{sel_app}::{sel_event or ''}"
event_rows = events["Event ID"].tolist()
st.dataframe(
    ui.display_frame(events), hide_index=True, key=key,
    on_select=selection.row_selection_callback(key, "event", event_rows),
    selection_mode="single-row", selection_default=selection.row_default(event_rows, sel_event),
    column_config={
        "Time (UTC)": st.column_config.DatetimeColumn(format=TIME_FORMAT),
        "#": st.column_config.NumberColumn(width="small"),
    },
)
st.caption(":material/touch_app: Select a row to ring that event in the timeline. "
           "Offer attributes, Accepted and Selected are recorded on O_Create Offer events only.")
if (events.get("CreditScore", pd.Series(dtype=float)) == 0).any():
    ui.notice("credit_score")
ui.download_csv(events, f"{sel_app}_events.csv", label="Download this application's events (CSV)",
                key="dl_explorer_events")

ui.source_caption(
    "Event log (data/processed/loan_application_events.parquet), recalculated",
    f"final statuses: {', '.join(config.FINAL_STATUS_ACTIVITIES)}", "all times UTC",
)
