"""Application filters: a hashable filter state, the pure selection logic, and the
sidebar controls.

Filters only *select applications* from the application summary; they never
compute metrics. The global dataset end date is unaffected by filtering.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from core import config
from ui.formatting import fmt_month

#: Fixed display order for final statuses (never re-ordered by counts).
STATUS_LABEL_ORDER: tuple[str, ...] = (
    *(config.FINAL_STATUS_LABELS[s] for s in config.FINAL_STATUS_ACTIVITIES),
    config.NO_FINAL_STATUS_LABEL,
)

#: Session-state keys of the sidebar filter widgets. These widgets are the single
#: source of truth for global filters; chart clicks write to the same keys (see
#: ui.selection) in callbacks that run before the sidebar is drawn.
FILTER_KEYS = {
    "months": "flt_months",
    "loan_goals": "flt_loan_goals",
    "application_types": "flt_application_types",
    "final_statuses": "flt_final_statuses",
}
_KEYS = FILTER_KEYS


@dataclass(frozen=True)
class FilterOptions:
    months: tuple[str, ...]
    loan_goals: tuple[str, ...]
    application_types: tuple[str, ...]
    final_statuses: tuple[str, ...] = STATUS_LABEL_ORDER


@dataclass(frozen=True)
class FilterState:
    """Empty tuples / None mean "no restriction"."""

    start_month_from: str | None = None
    start_month_to: str | None = None
    loan_goals: tuple[str, ...] = field(default_factory=tuple)
    application_types: tuple[str, ...] = field(default_factory=tuple)
    final_statuses: tuple[str, ...] = field(default_factory=tuple)  # final_status_label values

    @property
    def is_default(self) -> bool:
        return self == FilterState()


def filter_options(summary: pd.DataFrame) -> FilterOptions:
    def values(col):
        return tuple(sorted(summary[col].dropna().astype(str).unique())) if col in summary else ()

    return FilterOptions(
        months=values("start_month"),
        loan_goals=values("loan_goal"),
        application_types=values("application_type"),
    )


def apply_filters(summary: pd.DataFrame, state: FilterState) -> pd.DataFrame:
    """Return the applications matching ``state`` (a new frame; input untouched).

    ``attrs`` (including ``dataset_end``) are preserved.
    """
    mask = pd.Series(True, index=summary.index)
    lo, hi = state.start_month_from, state.start_month_to
    if lo and hi and lo > hi:
        lo, hi = hi, lo
    if lo:
        mask &= summary["start_month"] >= lo
    if hi:
        mask &= summary["start_month"] <= hi
    if state.loan_goals:
        mask &= summary["loan_goal"].isin(state.loan_goals)
    if state.application_types:
        mask &= summary["application_type"].isin(state.application_types)
    if state.final_statuses:
        mask &= summary["final_status_label"].isin(state.final_statuses)
    out = summary[mask].copy()
    out.attrs = dict(summary.attrs)
    return out


def describe(state: FilterState) -> str:
    """Human-readable summary of the active filters."""
    if state.is_default:
        return "All applications (no filters applied)"
    parts = []
    if state.start_month_from or state.start_month_to:
        if state.start_month_from == state.start_month_to:
            parts.append(f"Started {fmt_month(state.start_month_from)}")
        else:
            parts.append(f"Started {fmt_month(state.start_month_from)} – {fmt_month(state.start_month_to)}")
    if state.loan_goals:
        parts.append(f"Loan goal: {', '.join(state.loan_goals)}")
    if state.application_types:
        parts.append(f"Application type: {', '.join(state.application_types)}")
    if state.final_statuses:
        parts.append(f"Final status: {', '.join(state.final_statuses)}")
    return " · ".join(parts)


# --------------------------------------------------------------------------- #
# Streamlit controls
# --------------------------------------------------------------------------- #
def _reset(options: FilterOptions) -> None:
    import streamlit as st

    st.session_state[_KEYS["months"]] = (options.months[0], options.months[-1]) if options.months else None
    for key in ("loan_goals", "application_types", "final_statuses"):
        st.session_state[_KEYS[key]] = []


def sidebar_filters(options: FilterOptions) -> FilterState:
    """Render the filter controls in the sidebar and return the current state."""
    import streamlit as st

    st.sidebar.markdown("**Filters**")
    st.sidebar.caption("Applied to every view calculated from the event log.")
    months = options.months
    month_range = None
    if len(months) > 1:
        # value= sets range mode and the first-run default; when a chart click has
        # written this key via session state, the session-state value takes precedence.
        month_range = st.sidebar.select_slider(
            "Application start month",
            options=months,
            value=(months[0], months[-1]),
            format_func=fmt_month,
            key=_KEYS["months"],
        )
    goals = st.sidebar.multiselect("Loan goal", options.loan_goals, key=_KEYS["loan_goals"], placeholder="All")
    types = st.sidebar.multiselect(
        "Application type", options.application_types, key=_KEYS["application_types"], placeholder="All"
    )
    statuses = st.sidebar.multiselect(
        "Final status", options.final_statuses, key=_KEYS["final_statuses"], placeholder="All",
        help="The application's final status (A_Pending, A_Cancelled, A_Denied), not its last recorded activity.",
    )
    st.sidebar.button("Reset filters", on_click=_reset, args=(options,), width="stretch")

    lo = hi = None
    if month_range and (month_range[0] != months[0] or month_range[1] != months[-1]):
        lo, hi = month_range
    return FilterState(
        start_month_from=lo,
        start_month_to=hi,
        loan_goals=tuple(goals),
        application_types=tuple(types),
        final_statuses=tuple(s for s in STATUS_LABEL_ORDER if s in statuses),
    )


def current_state() -> FilterState:
    """Filter state for the current run (set by app.py before the page runs)."""
    import streamlit as st

    return st.session_state.get("filter_state", FilterState())
