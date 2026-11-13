"""Selection and cross-filter logic.

Two kinds of selection:

* **Global filters** (final status, start month) have ONE source of truth: the
  sidebar widgets' session-state keys (``ui.filters.FILTER_KEYS``). A chart click
  writes to those same keys inside an ``on_select`` callback, which Streamlit runs
  before the script (and therefore before the sidebar widgets are drawn), so the
  sidebar always shows exactly what is applied on every page.
* **Page selections** (pattern, sequence, transition pair, activity, application,
  offer-cancellation status) live under ``sel_*`` keys and are cleared when the
  current data no longer contains the selected item.

All functions take the session-state mapping as a parameter so they are pure and
unit-testable; the ``*_callback`` factories bind them to ``st.session_state``.

Streamlit returns click selections for point-based Plotly traces (bar, scatter).
Heatmap and Sankey traces return no selection, so clickable matrices are drawn
with square scatter markers and the Sankey is hover-only.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, MutableMapping, Sequence
from typing import Any

from ui.filters import FILTER_KEYS

SELECTION_KEYS = {
    "pattern": "sel_pattern",
    "sequence": "sel_sequence",
    "transition": "sel_transition",
    "activity": "sel_activity",
    "application": "sel_application",
    "offer_status": "sel_offer_status",
    "event": "sel_event",  # Application Explorer: event highlighted in the timeline and table
}


# --------------------------------------------------------------------------- #
# Parsing Streamlit selection payloads
# --------------------------------------------------------------------------- #
def _get(obj: Any, key: str, default=None):
    if obj is None:
        return default
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def selected_points(event: Any) -> list[Mapping]:
    """Points of a Plotly selection event (empty list for None / malformed payloads)."""
    points = _get(_get(event, "selection"), "points", [])
    if not isinstance(points, Sequence) or isinstance(points, (str, bytes)):
        return []
    return [p for p in points if isinstance(p, Mapping)]


def point_customdata(point: Mapping) -> tuple:
    data = point.get("customdata")
    if data is None:
        return ()
    if isinstance(data, (list, tuple)):
        return tuple(data)
    return (data,)


def first_value(event: Any, position: int = 0, valid: Iterable | None = None):
    """``customdata[position]`` of the first selected point, or None.

    Returns None when nothing is selected, the point has no such customdata, or the
    value is not in ``valid`` (stale or foreign selections are ignored).
    """
    allowed = None if valid is None else set(valid)
    for point in selected_points(event):
        data = point_customdata(point)
        if len(data) > position:
            value = data[position]
            if allowed is None or value in allowed:
                return value
    return None


def selected_rows(event: Any) -> list[int]:
    """Row positions of a ``st.dataframe`` selection event."""
    rows = _get(_get(event, "selection"), "rows", [])
    if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes)):
        return []
    return [int(r) for r in rows if isinstance(r, (int,)) and not isinstance(r, bool)]


# --------------------------------------------------------------------------- #
# Global filters (single source of truth: the sidebar widget keys)
# --------------------------------------------------------------------------- #
def toggle_status_filter(state: MutableMapping, label: str | None, valid_labels: Iterable[str]) -> None:
    """Make ``label`` the only final-status filter; clicking it again clears the filter."""
    if label is None or label not in set(valid_labels):
        return
    key = FILTER_KEYS["final_statuses"]
    current = list(state.get(key, []) or [])
    state[key] = [] if current == [label] else [label]


def toggle_month_filter(state: MutableMapping, month: str | None, months: Sequence[str]) -> None:
    """Restrict the start-month range to ``month``; clicking it again restores the full range."""
    if month is None or month not in set(months) or not months:
        return
    key = FILTER_KEYS["months"]
    full = (months[0], months[-1])
    current = tuple(state.get(key, full) or full)
    state[key] = full if current == (month, month) else (month, month)


def clear_status_filter(state: MutableMapping) -> None:
    state[FILTER_KEYS["final_statuses"]] = []


def clear_month_filter(state: MutableMapping, months: Sequence[str]) -> None:
    if months:
        state[FILTER_KEYS["months"]] = (months[0], months[-1])


# --------------------------------------------------------------------------- #
# Page selections
# --------------------------------------------------------------------------- #
def get_selection(state: Mapping, name: str):
    return state.get(SELECTION_KEYS[name])


def set_selection(state: MutableMapping, name: str, value) -> None:
    state[SELECTION_KEYS[name]] = value


def toggle_selection(state: MutableMapping, name: str, value) -> None:
    """Select ``value``; selecting the current value again clears it."""
    if value is None:
        return
    key = SELECTION_KEYS[name]
    state[key] = None if state.get(key) == value else value


def clear_selection(state: MutableMapping, name: str) -> None:
    state[SELECTION_KEYS[name]] = None


def validate_selection(state: MutableMapping, name: str, valid_values: Iterable) -> str | None:
    """Clear a selection that is no longer present in the data; return a notice if cleared."""
    value = get_selection(state, name)
    if value is None or value in set(valid_values):
        return None
    clear_selection(state, name)
    return f"The previous selection ({value}) is not in the current filtered data and was cleared."


def request_application(state: MutableMapping, case_id: str | None) -> None:
    """Hand an application over to the Application Explorer."""
    if case_id:
        set_selection(state, "application", case_id)


def prepare_handoff(state: MutableMapping, case_id: str | None, query_key: str = "explorer_query") -> None:
    """``on_click`` callback for an "Open in Application Explorer" button (then call ``st.switch_page``).

    ``st.switch_page`` starts the next run without the browser's widget states, so
    widget-managed values (the sidebar filters) would fall back to their defaults.
    Re-storing them here - in a callback, before any widget is created - keeps the
    global filters across the hand-off. The Explorer search box shows the application.
    """
    for key in FILTER_KEYS.values():
        if key in state:
            state[key] = state[key]
    request_application(state, case_id)
    if case_id:
        state[query_key] = case_id


# --------------------------------------------------------------------------- #
# Callback factories (bind the pure functions to st.session_state)
# --------------------------------------------------------------------------- #
def _session() -> MutableMapping:
    import streamlit as st

    return st.session_state


def status_filter_callback(chart_key: str, valid_labels: Sequence[str], position: int = 0) -> Callable[[], None]:
    def callback() -> None:
        state = _session()
        toggle_status_filter(state, first_value(state.get(chart_key), position, valid_labels), valid_labels)

    return callback


def month_filter_callback(chart_key: str, months: Sequence[str], position: int = 0) -> Callable[[], None]:
    def callback() -> None:
        state = _session()
        toggle_month_filter(state, first_value(state.get(chart_key), position, months), months)

    return callback


def then_set(callback: Callable[[], None], key: str, value) -> Callable[[], None]:
    """Run ``callback`` and then set ``state[key] = value`` (e.g. which table to show)."""

    def run() -> None:
        callback()
        _session()[key] = value

    return run


def page_selection_callback(
    chart_key: str, name: str, valid_values: Sequence | None = None, position: int = 0, *, pair: bool = False
) -> Callable[[], None]:
    """Toggle a page selection from a chart click. ``pair=True`` stores customdata[0:2] as a tuple."""

    def callback() -> None:
        state = _session()
        event = state.get(chart_key)
        if pair:
            points = selected_points(event)
            value = tuple(point_customdata(points[0])[:2]) if points else None
            if value is not None and (len(value) != 2 or (valid_values is not None and value not in set(valid_values))):
                value = None
        else:
            value = first_value(event, position, valid_values)
        toggle_selection(state, name, value)

    return callback


def row_selection_callback(table_key: str, name: str, values: Sequence) -> Callable[[], None]:
    """Select ``values[row]`` from a ``st.dataframe`` row selection (positions refer to ``values``).

    Deselecting the row clears the selection; out-of-range rows are ignored. Pages
    redraw the table under a key that embeds the selection, with the selected row
    passed as ``selection_default`` (see :func:`row_default`).
    """

    def callback() -> None:
        state = _session()
        rows = selected_rows(state.get(table_key))
        if not rows:
            clear_selection(state, name)
            return
        for row in rows:
            if 0 <= row < len(values) and values[row] is not None:
                set_selection(state, name, values[row])
                return

    return callback


def row_default(values: Sequence, selected) -> dict | None:
    """``selection_default`` for ``st.dataframe`` showing ``selected`` as the selected row."""
    if selected is None:
        return None
    for i, value in enumerate(values):
        if value == selected:
            return {"selection": {"rows": [i], "columns": []}}
    return None


def search_callback(query_key: str, name: str, valid_ids: Sequence[str]) -> Callable[[], None]:
    """Select the application whose ID equals the search text (case-insensitive)."""

    def callback() -> None:
        from ui.aggregations import find_application

        state = _session()
        match = find_application(str(state.get(query_key) or ""), valid_ids)
        if match is not None:
            set_selection(state, name, match)

    return callback
