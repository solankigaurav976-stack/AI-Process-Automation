"""Cached access to the approved data, metrics and reconciliation layers.

This is the only UI module that calls ``core.loaders``, ``core.metrics`` and
``core.reconciliation``. It performs no calculations of its own.

Caching and mutability
----------------------
* The 1.2M-row event log is held once with ``st.cache_resource`` and is never
  returned to pages. It is only passed to core functions, which are tested not to
  modify their input, or filtered into a new frame.
* Every result handed to a page comes from ``st.cache_data``, which returns a
  fresh copy on every call - a page or widget cannot change the cached original.
* Filtered results are cached per :class:`~ui.filters.FilterState` (hashable).
* Load failures raise :class:`~core.errors.DashboardError`; Streamlit does not
  cache exceptions, so the next rerun retries.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import streamlit as st

from core import definitions, loaders
from core import metrics as m
from core import reconciliation as rc
from ui.filters import FilterOptions, FilterState, apply_filters, filter_options

DATASET_END = definitions.DATASET_END


# --------------------------------------------------------------------------- #
# Shared source data (never returned to pages)
# --------------------------------------------------------------------------- #
@st.cache_resource(show_spinner="Loading the event log (1.2 million events)…")
def _shared_events() -> pd.DataFrame:
    """Prepared event log. Read-only by contract: never mutate, never return to UI code."""
    return m.prepare_events(loaders.load_event_log(m.METRIC_EVENT_COLUMNS))


def _events_for(state: FilterState) -> pd.DataFrame:
    """Events of the selected applications (a new frame when filtered)."""
    events = _shared_events()
    if state.is_default:
        return events
    ids = filtered_summary(state)["case_id"]
    return events[events[m.C].isin(ids)]


# --------------------------------------------------------------------------- #
# Application-level results
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner="Summarising applications…")
def summary() -> pd.DataFrame:
    return m.application_summary(_shared_events(), dataset_end=DATASET_END)


@st.cache_data(show_spinner=False)
def options() -> FilterOptions:
    return filter_options(summary())


@st.cache_data(show_spinner=False)
def filtered_summary(state: FilterState) -> pd.DataFrame:
    return apply_filters(summary(), state)


@st.cache_data(show_spinner=False)
def final_status_distribution(state: FilterState) -> pd.DataFrame:
    return m.final_status_distribution(filtered_summary(state))


@st.cache_data(show_spinner=False)
def durations(state: FilterState) -> dict[str, pd.DataFrame]:
    s = filtered_summary(state)
    return {
        "duration_days": m.duration_statistics(s, column="duration_days"),
        "days_to_final_status": m.duration_statistics(s, column="days_to_final_status", include_open=False),
    }


@st.cache_data(show_spinner=False)
def monthly(state: FilterState) -> pd.DataFrame:
    return m.monthly_trend(filtered_summary(state), dataset_end=DATASET_END)


@dataclass(frozen=True)
class DatasetOverview:
    events: int
    applications: int
    first_event: pd.Timestamp
    last_event: pd.Timestamp


@st.cache_data(show_spinner=False)
def overview() -> DatasetOverview:
    s = summary()
    return DatasetOverview(
        events=int(s["total_events"].sum()),
        applications=len(s),
        first_event=s["start_time"].min(),
        last_event=DATASET_END,
    )


# --------------------------------------------------------------------------- #
# Cancellations (O_Cancelled and A_Cancelled kept separate)
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class CancellationOverview:
    offer: m.OfferCancellationMetrics
    application: m.ApplicationCancellationMetrics
    offer_first_population: int


@st.cache_data(show_spinner="Calculating cancellation metrics…")
def cancellations(state: FilterState) -> CancellationOverview:
    events, s = _events_for(state), filtered_summary(state)
    return CancellationOverview(
        offer=m.offer_cancellation_metrics(events, s),
        application=m.application_cancellation_metrics(events, s),
        offer_first_population=len(m.offer_first_cancellation_followup(events)),
    )


# --------------------------------------------------------------------------- #
# Process analysis (Phase 5d)
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner="Calculating transitions…")
def transitions(state: FilterState, min_count: int = m.DEFAULT_MIN_TRANSITION_COUNT) -> pd.DataFrame:
    """core.metrics.transition_metrics for the selected applications."""
    return m.transition_metrics(_events_for(state), min_count=min_count)


@st.cache_data(show_spinner=False)
def activity_frequency(state: FilterState, completed_only: bool = False) -> pd.DataFrame:
    return m.activity_frequency(_events_for(state), lifecycle=["complete"] if completed_only else None)


@st.cache_data(show_spinner=False)
def lifecycle_breakdown(state: FilterState) -> pd.DataFrame:
    return m.activity_lifecycle_breakdown(_events_for(state))


@st.cache_data(show_spinner="Collecting individual waits…")
def transition_instances(state: FilterState, activity: str, next_activity: str) -> pd.DataFrame:
    """core.drilldown.transition_instances for one pair (next event found on the full log)."""
    from core import drilldown

    return drilldown.transition_instances(_events_for(state), activity, next_activity)


@st.cache_resource(show_spinner="Loading the remaining event attributes…")
def _shared_event_extras() -> pd.DataFrame:
    """Action, EventOrigin, Accepted, Selected aligned to the shared log (EventID-verified). Never returned."""
    from ui import aggregations as ag

    extras = loaders.load_event_log((m.EVENT_ID, *ag.EXTRA_EVENT_COLUMNS))
    return ag.align_event_extras(extras, _shared_events())


@dataclass(frozen=True)
class ApplicationDetail:
    """One application's events for the Application Explorer (Phase 5e). Independent of filters."""

    timeline: pd.DataFrame  # ag.timeline_frame: one row per recorded event, original order
    lifelines: pd.DataFrame  # ag.offer_lifelines over core.metrics.offer_table
    events: pd.DataFrame  # ag.event_table: every column as recorded


@st.cache_data(show_spinner="Loading the application's events…", max_entries=64)
def application_detail(case_id: str) -> ApplicationDetail:
    from ui import aggregations as ag

    events = _shared_events()
    case_events = ag.events_for_application(events, case_id)
    return ApplicationDetail(
        timeline=ag.timeline_frame(case_events),
        lifelines=ag.offer_lifelines(m.offer_table(case_events), case_id),
        events=ag.event_table(events, _shared_event_extras(), case_id),
    )


@dataclass(frozen=True)
class CancellationDetail:
    """Per-application cancellation tables for the selected applications (Phase 5c)."""

    application_cancellations: pd.DataFrame  # m.application_cancellation_table
    followup: pd.DataFrame  # m.offer_first_cancellation_followup
    pattern_summary: pd.DataFrame  # m.followup_pattern_summary
    workflow_composition: pd.DataFrame  # rc.workflow_only_composition (finding, category unchanged)


@st.cache_data(show_spinner="Preparing cancellation detail…")
def cancellation_detail(state: FilterState) -> CancellationDetail:
    events = _events_for(state)
    followup = m.offer_first_cancellation_followup(events)
    return CancellationDetail(
        application_cancellations=m.application_cancellation_table(events),
        followup=followup,
        pattern_summary=m.followup_pattern_summary(followup),
        workflow_composition=rc.workflow_only_composition(events, followup),
    )


# --------------------------------------------------------------------------- #
# Reports and reconciliation (global; not affected by filters)
# --------------------------------------------------------------------------- #
@st.cache_data(show_spinner="Loading existing reports…")
def reports() -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    """Existing CSV reports and, separately, plain-language load errors."""
    bundle = loaders.load_all_reports()
    return dict(bundle.reports), {k: e.user_message for k, e in bundle.errors.items()}


@st.cache_data(show_spinner="Reconciling reports against the event log…")
def reconciliation() -> rc.ReconciliationResult:
    bundle = loaders.load_all_reports()
    return rc.reconcile_all(_shared_events(), bundle.reports, bundle.errors, dataset_end=DATASET_END)


@st.cache_data(show_spinner=False)
def credit_scores() -> pd.DataFrame:
    return m.credit_score_summary(m.offer_table(_shared_events()))


def clear_caches() -> None:
    """Drop every cached result (used by tests and after a data refresh)."""
    st.cache_data.clear()
    st.cache_resource.clear()
