"""Unit tests for ui.selection (payload parsing, global filters, page selections, callbacks)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from core import config
from ui import selection as sel
from ui.filters import FILTER_KEYS, STATUS_LABEL_ORDER

PENDING = config.FINAL_STATUS_LABELS["A_Pending"]
CANCELLED = config.FINAL_STATUS_LABELS["A_Cancelled"]
MONTHS = ("2016-01", "2016-02", "2016-03")


def payload(*customdata, rows=None):
    points = [{"customdata": c, "point_index": i, "curve_number": 0} for i, c in enumerate(customdata)]
    return {"selection": {"points": points, "point_indices": list(range(len(points))), "box": [], "lasso": [],
                          "rows": rows or []}}


# --------------------------------------------------------------------------- #
# Parsing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", [None, {}, {"selection": None}, {"selection": {}}, {"selection": {"points": "x"}},
                                 {"selection": {"points": [1, "a", None]}}, "garbage", 42])
def test_malformed_payloads_yield_nothing(bad):
    assert sel.selected_points(bad) == []
    assert sel.first_value(bad) is None
    assert sel.selected_rows(bad) == []


def test_first_value_and_validation():
    assert sel.first_value(payload([PENDING, "A_Pending"])) == PENDING
    assert sel.first_value(payload([PENDING, "A_Pending"]), position=1) == "A_Pending"
    assert sel.first_value(payload([PENDING]), position=3) is None
    assert sel.first_value(payload(["stale label"]), valid=STATUS_LABEL_ORDER) is None
    assert sel.first_value(payload(["stale"], [CANCELLED]), valid=STATUS_LABEL_ORDER) == CANCELLED  # first valid point
    assert sel.first_value(payload("2016-02")) == "2016-02"  # scalar customdata
    assert sel.first_value({"selection": {"points": [{"x": 1}]}}) is None  # no customdata


def test_attribute_style_payload():
    event = SimpleNamespace(selection=SimpleNamespace(points=[{"customdata": ["2016-03"]}], rows=[2, 5]))
    assert sel.first_value(event) == "2016-03"
    assert sel.selected_rows(event) == [2, 5]


def test_selected_rows_ignores_non_integers():
    assert sel.selected_rows(payload(rows=[1, "2", True, 3])) == [1, 3]


# --------------------------------------------------------------------------- #
# Global filters (single source of truth = sidebar widget keys)
# --------------------------------------------------------------------------- #
def test_status_filter_toggle_writes_sidebar_key():
    state = {}
    sel.toggle_status_filter(state, PENDING, STATUS_LABEL_ORDER)
    assert state[FILTER_KEYS["final_statuses"]] == [PENDING]
    sel.toggle_status_filter(state, CANCELLED, STATUS_LABEL_ORDER)
    assert state[FILTER_KEYS["final_statuses"]] == [CANCELLED]  # replaces, does not accumulate
    sel.toggle_status_filter(state, CANCELLED, STATUS_LABEL_ORDER)
    assert state[FILTER_KEYS["final_statuses"]] == []  # same click again clears
    sel.toggle_status_filter(state, "Approved", STATUS_LABEL_ORDER)  # invalid labels ignored
    sel.toggle_status_filter(state, None, STATUS_LABEL_ORDER)
    assert state[FILTER_KEYS["final_statuses"]] == []


def test_status_click_replaces_a_multi_status_sidebar_selection():
    state = {FILTER_KEYS["final_statuses"]: [PENDING, CANCELLED]}
    sel.toggle_status_filter(state, PENDING, STATUS_LABEL_ORDER)
    assert state[FILTER_KEYS["final_statuses"]] == [PENDING]


def test_month_filter_toggle():
    state = {}
    sel.toggle_month_filter(state, "2016-02", MONTHS)
    assert state[FILTER_KEYS["months"]] == ("2016-02", "2016-02")
    sel.toggle_month_filter(state, "2016-02", MONTHS)
    assert state[FILTER_KEYS["months"]] == ("2016-01", "2016-03")
    sel.toggle_month_filter(state, "2017-05", MONTHS)  # unknown month ignored
    assert state[FILTER_KEYS["months"]] == ("2016-01", "2016-03")


def test_clear_global_filters():
    state = {FILTER_KEYS["final_statuses"]: [PENDING], FILTER_KEYS["months"]: ("2016-02", "2016-02")}
    sel.clear_status_filter(state)
    sel.clear_month_filter(state, MONTHS)
    assert state == {FILTER_KEYS["final_statuses"]: [], FILTER_KEYS["months"]: ("2016-01", "2016-03")}


def test_global_and_page_selections_do_not_interfere():
    state = {}
    sel.toggle_status_filter(state, PENDING, STATUS_LABEL_ORDER)
    sel.toggle_selection(state, "pattern", "Workflow activity only")
    sel.toggle_month_filter(state, "2016-01", MONTHS)
    assert sel.get_selection(state, "pattern") == "Workflow activity only"
    assert state[FILTER_KEYS["final_statuses"]] == [PENDING]
    assert set(state) == {FILTER_KEYS["final_statuses"], FILTER_KEYS["months"], sel.SELECTION_KEYS["pattern"]}


# --------------------------------------------------------------------------- #
# Page selections
# --------------------------------------------------------------------------- #
def test_page_selection_toggle_and_clear():
    state = {}
    sel.toggle_selection(state, "sequence", "O_Cancelled before A_Cancelled")
    assert sel.get_selection(state, "sequence") == "O_Cancelled before A_Cancelled"
    sel.toggle_selection(state, "sequence", "O_Cancelled before A_Cancelled")
    assert sel.get_selection(state, "sequence") is None
    sel.toggle_selection(state, "sequence", None)  # empty click does nothing
    assert sel.get_selection(state, "sequence") is None
    sel.set_selection(state, "transition", ("a", "b"))
    sel.clear_selection(state, "transition")
    assert sel.get_selection(state, "transition") is None


def test_stale_selection_is_cleared_with_notice():
    state = {sel.SELECTION_KEYS["application"]: "Application_1"}
    assert sel.validate_selection(state, "application", ["Application_1", "Application_2"]) is None
    notice = sel.validate_selection(state, "application", ["Application_2"])
    assert "not in the current filtered data" in notice and sel.get_selection(state, "application") is None
    assert sel.validate_selection(state, "application", []) is None  # nothing selected: no notice


def test_request_application():
    state = {}
    sel.request_application(state, "")
    assert sel.get_selection(state, "application") is None
    sel.request_application(state, "Application_42")
    assert sel.get_selection(state, "application") == "Application_42"


def test_unknown_selection_name_raises():
    with pytest.raises(KeyError):
        sel.get_selection({}, "nope")


# --------------------------------------------------------------------------- #
# Callbacks (bound to session state)
# --------------------------------------------------------------------------- #
@pytest.fixture
def session(monkeypatch):
    state = {}
    monkeypatch.setattr(sel, "_session", lambda: state)
    return state


def test_status_callback(session):
    cb = sel.status_filter_callback("exec_status_chart", STATUS_LABEL_ORDER)
    session["exec_status_chart"] = payload([CANCELLED, "A_Cancelled"])
    cb()
    assert session[FILTER_KEYS["final_statuses"]] == [CANCELLED]
    session["exec_status_chart"] = payload()  # deselect event: filter stays (cleared via chip/sidebar)
    cb()
    assert session[FILTER_KEYS["final_statuses"]] == [CANCELLED]


def test_month_callback(session):
    cb = sel.month_filter_callback("exec_monthly_chart", MONTHS)
    session["exec_monthly_chart"] = payload(["2016-03", "Mar 2016"])
    cb()
    assert session[FILTER_KEYS["months"]] == ("2016-03", "2016-03")


def test_page_selection_callbacks(session):
    cb = sel.page_selection_callback("pattern_chart", "pattern", valid_values=["Workflow activity only"])
    session["pattern_chart"] = payload(["Workflow activity only", "desc"])
    cb()
    assert sel.get_selection(session, "pattern") == "Workflow activity only"
    session["pattern_chart"] = payload(["Unknown pattern"])
    cb()  # invalid value ignored, selection kept
    assert sel.get_selection(session, "pattern") == "Workflow activity only"

    pair_cb = sel.page_selection_callback("matrix", "transition", valid_values=[("W_Call after offers", "A_Cancelled")], pair=True)
    session["matrix"] = payload(["W_Call after offers", "A_Cancelled", 8539])
    pair_cb()
    assert sel.get_selection(session, "transition") == ("W_Call after offers", "A_Cancelled")
    session["matrix"] = payload(["X", "Y", 1])
    pair_cb()  # pair not in valid set: ignored
    assert sel.get_selection(session, "transition") == ("W_Call after offers", "A_Cancelled")
    session["matrix"] = payload(["W_Call after offers", "A_Cancelled", 8539])
    pair_cb()  # same pair again: toggles off
    assert sel.get_selection(session, "transition") is None


def test_then_set_runs_callback_then_sets_key(session):
    calls = []
    cb = sel.then_set(lambda: calls.append(dict(session)), "cancel_case_source", "sequence")
    cb()
    assert calls == [{}] and session["cancel_case_source"] == "sequence"


def test_event_selection_key_is_registered():
    assert sel.SELECTION_KEYS["event"] == "sel_event"
    assert len(set(sel.SELECTION_KEYS.values())) == len(sel.SELECTION_KEYS)


def test_row_selection_callback(session):
    cb = sel.row_selection_callback("table", "application", ["A1", "A2", "A3"])
    session["table"] = payload(rows=[1])
    cb()
    assert sel.get_selection(session, "application") == "A2"
    session["table"] = payload(rows=[9])
    cb()  # out of range: ignored
    assert sel.get_selection(session, "application") == "A2"
    session["table"] = payload()
    cb()  # row deselected: cleared
    assert sel.get_selection(session, "application") is None


def test_row_default():
    assert sel.row_default(["A1", "A2"], "A2") == {"selection": {"rows": [1], "columns": []}}
    assert sel.row_default(["A1", "A2"], None) is None and sel.row_default(["A1"], "zz") is None


def test_search_callback_selects_exact_match_only(session):
    cb = sel.search_callback("q", "application", ["Application_1", "Application_12"])
    session["q"] = "Application_"
    cb()
    assert sel.get_selection(session, "application") is None
    session["q"] = " application_12 "
    cb()
    assert sel.get_selection(session, "application") == "Application_12"
    session["q"] = ""
    cb()  # clearing the search keeps the selection
    assert sel.get_selection(session, "application") == "Application_12"


def test_prepare_handoff_restores_filters_and_selects(session):
    session[FILTER_KEYS["final_statuses"]] = [CANCELLED]
    session[FILTER_KEYS["months"]] = ("2016-02", "2016-03")
    sel.prepare_handoff(session, "Application_1")
    assert session[FILTER_KEYS["final_statuses"]] == [CANCELLED]
    assert session[FILTER_KEYS["months"]] == ("2016-02", "2016-03")
    assert sel.get_selection(session, "application") == "Application_1"
    assert session["explorer_query"] == "Application_1"
    sel.prepare_handoff(session, None)  # nothing selected: nothing changes
    assert sel.get_selection(session, "application") == "Application_1"
