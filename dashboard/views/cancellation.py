"""Cancellation Intelligence: offer cancellation (O_Cancelled) and application
cancellation (A_Cancelled), always shown separately, with page-level drill-down.

Interaction model
-----------------
* Global filters (sidebar / Executive clicks) apply to everything on this page.
* Clicks on this page create PAGE selections only (``sel_*`` keys); they never
  change the global filters. The case table (C7) shows the applications behind the
  most recent selection; its source can also be chosen directly.
* Chart keys include the current selection so a cleared selection never lingers.
"""

from __future__ import annotations

import streamlit as st

from core import config, definitions
from core import metrics as m
from ui import aggregations as ag
from ui import charts, selection
from ui import components as ui
from ui import data, filters
from ui.formatting import fmt_int, fmt_pct, fmt_share

SOURCE_KEY = "cancel_case_source"
SOURCES = {
    "offer_status": "Offer cancellations (O_Cancelled) by final status",
    "sequence": "Application cancellations (A_Cancelled) by sequence",
    "pattern": "Offer-first follow-up patterns",
}
SELECTION_FOR_SOURCE = {"offer_status": "offer_status", "sequence": "sequence", "pattern": "pattern"}

state = filters.current_state()
mode = ui.theme_mode()
ss = st.session_state

ui.page_header(
    "Cancellation Intelligence",
    "Offer cancellations and application cancellations, measured separately.",
)
ui.filter_caption(filters.describe(state))
ui.active_filter_chips(ss, data.options().months)
ui.notice("cancellation_types")


summary = data.filtered_summary(state)
if summary.empty:
    ui.empty_state()
    st.stop()

c = data.cancellations(state)
detail = data.cancellation_detail(state)
followup, pattern_summary = detail.followup, detail.pattern_summary
app_table = detail.application_cancellations

# Drop page selections that the current global filters no longer contain.
offer_breakdown = c.offer.final_status_breakdown
valid_offer_status = offer_breakdown.loc[offer_breakdown["applications"] > 0, "label"].tolist()
valid_sequences = c.application.sequence_breakdown.loc[c.application.sequence_breakdown["applications"] > 0, "sequence"].tolist()
valid_patterns = pattern_summary["pattern"].tolist()
for name, valid in (("offer_status", valid_offer_status), ("sequence", valid_sequences), ("pattern", valid_patterns),
                    ("application", summary["case_id"].tolist())):
    message = selection.validate_selection(ss, name, valid)
    if message:
        st.info(message, icon=":material/filter_alt_off:")

sel_offer_status = selection.get_selection(ss, "offer_status")
sel_sequence = selection.get_selection(ss, "sequence")
sel_pattern = selection.get_selection(ss, "pattern")
sel_application = selection.get_selection(ss, "application")

# ---------------------------------------------------------------------- #
# C1 / C2 - offer cancellation and application cancellation, side by side
# ---------------------------------------------------------------------- #
offer_col, app_col = st.columns(2, gap="large")
with offer_col:
    ui.section("Offer cancellation · O_Cancelled", "One offer is withdrawn; the application can continue.")
    k1, k2 = st.columns(2)
    with k1:
        ui.kpi_card(ui.Kpi("offer_cancellation_events", c.offer.offer_cancellation_events))
    with k2:
        ui.kpi_card(ui.Kpi("applications_with_offer_cancellation", c.offer.applications_with_offer_cancellation,
                           f"{fmt_pct(c.offer.share_of_applications_percent)} of applications in the selection"))
    st.markdown("**Final status of applications with an offer cancellation**")
    key = f"cancel_offer_status::{sel_offer_status or ''}"
    ui.plotly(
        charts.status_bars(offer_breakdown, mode, selected=[sel_offer_status] if sel_offer_status else (),
                           action="list these applications below"),
        key=key,
        on_select=selection.then_set(selection.page_selection_callback(key, "offer_status", valid_offer_status), SOURCE_KEY, "offer_status"),
    )
    ui.interaction_caption("offer_cancellation_status",
                           extra="This does not change the global final-status filter.")

with app_col:
    ui.section("Application cancellation · A_Cancelled", "The whole application is cancelled.")
    k1, k2 = st.columns(2)
    with k1:
        ui.kpi_card(ui.Kpi("application_cancellation_events", c.application.application_cancellation_events))
    with k2:
        ui.kpi_card(ui.Kpi("final_status_cancelled", c.application.applications_final_status_cancelled,
                           f"{fmt_pct(c.application.share_of_applications_percent)} of applications in the selection"))
    st.markdown("**When the application was cancelled, relative to its first offer cancellation**")
    if c.application.applications_with_application_cancellation == 0:
        ui.empty_state("No application cancellations in the current selection.")
    else:
        key = f"cancel_sequence::{sel_sequence or ''}"
        ui.plotly(
            charts.sequence_bars(c.application.sequence_breakdown, mode, selected=sel_sequence),
            key=key,
            on_select=selection.then_set(selection.page_selection_callback(key, "sequence", valid_sequences), SOURCE_KEY, "sequence"),
        )
        ui.interaction_caption("cancellation_sequence")

# ---------------------------------------------------------------------- #
# C3 - likely automatic expiry (exploratory)
# ---------------------------------------------------------------------- #
ui.section("Likely automatic expiry · exploratory",
           "Days from the last offer sent to A_Cancelled. The indicator is exploratory, not a confirmed rule.")
if app_table.empty:
    ui.empty_state("No application cancellations in the current selection.")
else:
    exp_col, chart_col = st.columns([1, 3], gap="large")
    with exp_col:
        ui.kpi_card(ui.Kpi(
            "likely_automatic_expiry_cancellations", c.application.likely_automatic_expiry,
            "Exploratory indicator · " + fmt_share(c.application.likely_automatic_expiry,
                                                   c.application.applications_with_application_cancellation)
            + " of application cancellations",
        ))
    with chart_col:
        ui.plotly(charts.expiry_histogram(app_table, mode), key="cancel_expiry_chart")
        no_offer = int(app_table["days_since_last_offer_sent"].isna().sum())
        ui.interaction_caption("expiry_histogram", extra=ui.LEGEND_NOTE)
        st.caption(
            f"**How the indicator is defined:** {definitions.METRICS['likely_automatic_expiry_cancellations'].definition} "
            f"{fmt_int(no_offer)} cancelled applications had no offer sent before A_Cancelled and are not shown."
        )
    ui.notice("timer")

# ---------------------------------------------------------------------- #
# C4 / C5 / C6 - offer-first follow-up patterns
# ---------------------------------------------------------------------- #
ui.section("Offer-first cancellations",
           "Applications whose first O_Cancelled happened before their A_Cancelled, grouped by what was recorded in between.")
ui.notice("not_recovery")
if followup.empty:
    ui.empty_state("No offer-first cancellations in the current selection.")
else:
    ui.kpi_card(ui.Kpi("offer_first_cancellation_population", len(followup),
                       "Final status of all of them: " + ", ".join(
                           f"{k} {fmt_int(v)}" for k, v in ag.final_status_counts(followup).items())))
    pat_col, comp_col = st.columns([3, 2], gap="large")
    with pat_col:
        st.markdown("**Five follow-up patterns** (original report order)")
        key = f"cancel_patterns::{sel_pattern or ''}"
        ui.plotly(
            charts.pattern_bars(pattern_summary, mode, selected=sel_pattern),
            key=key,
            on_select=selection.then_set(selection.page_selection_callback(key, "pattern", valid_patterns), SOURCE_KEY, "pattern"),
        )
        ui.interaction_caption("pattern_bars")
    with comp_col:
        st.markdown("**Activity recorded between the two cancellations**")
        if sel_pattern is None:
            st.caption(":material/touch_app: Select a pattern to see which activities its applications contain.")
            for p in m.PATTERN_PRECEDENCE:
                if p in valid_patterns:
                    st.caption(f"**{p}.** {m.PATTERN_DESCRIPTIONS[p]}")
        else:
            st.caption(f"**{sel_pattern}.** {m.PATTERN_DESCRIPTIONS[sel_pattern]}")
            ui.plotly(charts.pattern_composition_bars(ag.pattern_composition(pattern_summary, sel_pattern), mode),
                      key="cancel_composition")
            ui.interaction_caption("pattern_composition")
            if sel_pattern == m.PATTERN_WORKFLOW:
                comp = detail.workflow_composition
                n_wf = int((followup["pattern"] == m.PATTERN_WORKFLOW).sum())
                st.info(
                    f"**{fmt_int(len(comp))} of {fmt_int(n_wf)}** 'Workflow activity only' applications also contain "
                    f"non-workflow events (offer events: {fmt_int(int(comp['has_offer_events'].sum()))}, application "
                    f"events: {fmt_int(int(comp['has_application_events'].sum()))}). The original category is kept "
                    "unchanged; the label overstates 'only'.",
                    icon=":material/info:",
                )
                with st.expander("Those applications"):
                    ui.table(comp.rename(columns={"case_id": "Application",
                                                  "non_workflow_activities": "Non-workflow activities in between",
                                                  "has_offer_events": "Offer events", "has_application_events": "Application events"}))

    st.markdown("**Gap from first O_Cancelled to A_Cancelled, per application**")
    key = f"cancel_gap::{sel_pattern or ''}::{sel_application or ''}"
    ui.plotly(
        charts.gap_strip(followup, mode, selected_pattern=sel_pattern, selected_application=sel_application),
        key=key,
        on_select=selection.then_set(
            selection.page_selection_callback(key, "application", followup["case_id"].tolist()), SOURCE_KEY, "pattern"),
    )
    ui.interaction_caption("gap_strip", extra="Boxes summarise each pattern; each point is one application.")

# ---------------------------------------------------------------------- #
# C7 - applications in the current selection
# ---------------------------------------------------------------------- #
ui.section("Applications in the current selection")
if SOURCE_KEY not in ss:  # initialise only when absent (never overwrite a stored widget value)
    ss[SOURCE_KEY] = "pattern"
source = st.segmented_control("Show applications from", list(SOURCES), format_func=SOURCES.get, key=SOURCE_KEY,
                              label_visibility="visible") or "pattern"

if source == "offer_status":
    cases = ag.offer_cancellation_cases(summary, sel_offer_status)
    scope = f"final status {sel_offer_status}" if sel_offer_status else "all final statuses"
    table = cases[["case_id", "final_status_label", "offer_cancellation_events", "offers_created", "start_time",
                   "duration_days"]].rename(columns={
        "case_id": "Application", "final_status_label": "Final status", "offer_cancellation_events": "O_Cancelled events",
        "offers_created": "Offers created", "start_time": "First event (UTC)", "duration_days": "Duration (days)"})
    title = f"Applications with an offer cancellation · {scope}"
elif source == "sequence":
    cases = ag.cases_for_sequence(app_table, sel_sequence)
    scope = sel_sequence or "all sequences"
    table = cases[["case_id", "cancellation_sequence", "application_cancellation_time", "first_offer_cancellation_time",
                   "days_since_last_offer_sent", "likely_automatic_expiry", "cancelled_by"]].rename(columns={
        "case_id": "Application", "cancellation_sequence": "Sequence", "application_cancellation_time": "A_Cancelled (UTC)",
        "first_offer_cancellation_time": "First O_Cancelled (UTC)",
        "days_since_last_offer_sent": "Days from last offer sent", "likely_automatic_expiry": "Likely automatic expiry (exploratory)",
        "cancelled_by": "A_Cancelled resource (as recorded)"})
    title = f"Applications with A_Cancelled · {scope}"
else:
    cases = ag.cases_for_pattern(followup, sel_pattern)
    scope = sel_pattern or "all five patterns"
    table = cases[["case_id", "pattern", "gap_days", "events_between", "new_offer", "offer_sent", "workflow_activity",
                   "returned_offer", "final_status"]].rename(columns={
        "case_id": "Application", "pattern": "Pattern", "gap_days": "Gap (days)", "events_between": "Events in between",
        "new_offer": "New offer created", "offer_sent": "Offer sent", "workflow_activity": "Workflow activity",
        "returned_offer": "Offer returned", "final_status": "Final status"})
    title = f"Offer-first cancellations · {scope}"

# Page-selection chips (clear buttons)
active = [(n, v) for n, v in (("offer_status", sel_offer_status), ("sequence", sel_sequence),
                              ("pattern", sel_pattern), ("application", sel_application)) if v]
if active:
    chip_cols = st.columns(len(active) + 1, vertical_alignment="center")
    chip_cols[0].caption("Page selections (not global filters):")
    labels = {"offer_status": "Status", "sequence": "Sequence", "pattern": "Pattern", "application": "Application"}
    for col, (name, value) in zip(chip_cols[1:], active):
        col.button(f"{labels[name]}: {value}", icon=":material/close:", key=f"chip_sel_{name}",
                   on_click=selection.clear_selection, args=(ss, name))

st.markdown(f"**{title}** · {fmt_int(len(table))} applications")
if table.empty:
    ui.empty_state("No applications in this selection.")
else:
    # The key embeds the selection and the selected row is passed as selection_default, so
    # a lingering table selection can never override a newer selection or a Clear (5f fix).
    table_key = f"cancel_case_table::{source}::{scope}::{sel_application or ''}"
    table_ids = table["Application"].tolist()
    st.dataframe(
        ui.display_frame(table), hide_index=True, key=table_key,
        on_select=selection.row_selection_callback(table_key, "application", table_ids),
        selection_mode="single-row", selection_default=selection.row_default(table_ids, sel_application),
        column_config={
            "Gap (days)": st.column_config.NumberColumn(format="%.1f"),
            "Duration (days)": st.column_config.NumberColumn(format="%.1f"),
            "Days from last offer sent": st.column_config.NumberColumn(format="%.1f"),
            "First event (UTC)": st.column_config.DatetimeColumn(format="D MMM YYYY, HH:mm"),
            "A_Cancelled (UTC)": st.column_config.DatetimeColumn(format="D MMM YYYY, HH:mm"),
            "First O_Cancelled (UTC)": st.column_config.DatetimeColumn(format="D MMM YYYY, HH:mm"),
        },
    )
    st.caption(":material/touch_app: Select a row (or click a point in the gap chart) to choose an application.")

    dl_col, open_col = st.columns([1, 2], vertical_alignment="center")
    with dl_col:
        ui.download_csv(table, f"cancellation_cases_{source}.csv", key="dl_cancel_cases")
    with open_col:
        if st.button(f"Open {sel_application or 'application'} in Application Explorer", icon=":material/open_in_new:",
                     key="open_in_explorer", disabled=sel_application is None, type="primary",
                     on_click=selection.prepare_handoff, args=(ss, sel_application)):  # keeps global filters
            st.switch_page("views/explorer.py")

ui.source_caption("Event log (data/processed/loan_application_events.parquet), recalculated")
