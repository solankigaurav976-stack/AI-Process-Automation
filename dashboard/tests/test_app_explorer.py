"""Phase 5e: Application Explorer on the real app and real data.

Clicks are simulated as in test_app_executive (the page's real callbacks run against
the live AppTest session state). Expected values come from the event log read
independently of the page (raw rows, stable-sorted by timestamp) and from the
approved core metrics.
"""

from __future__ import annotations

import io

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from core import config, definitions, loaders
from core import metrics as m
from tests.test_app_executive import _State, captions, figures
from tests.test_read_only import _fingerprint
from ui import aggregations as ag
from ui import components, selection
from ui.filters import FILTER_KEYS

pytestmark = [pytest.mark.integration, pytest.mark.ui]

APP = str(config.DASHBOARD_DIR / "app.py")
PAGE = "views/explorer.py"
CANCELLED = config.FINAL_STATUS_LABELS["A_Cancelled"]
PENDING = config.FINAL_STATUS_LABELS["A_Pending"]

#: One application per final status, an open one and the longest one (180 events).
APPLICATIONS = {
    "A_Pending": "Application_1000158214",
    "A_Cancelled": "Application_1000086665",
    "A_Denied": "Application_1000334415",
    "open": "Application_1061631975",
    "longest": "Application_1219772874",
}


@pytest.fixture(scope="module")
def fingerprint_before():
    return _fingerprint(config.PROTECTED_DIRS)


@pytest.fixture(scope="module")
def expected():
    raw = loaders.load_event_log()  # every column, file order, independent of the page
    ev = m.prepare_events(loaders.load_event_log(m.METRIC_EVENT_COLUMNS))
    return {"raw": raw, "summary": m.application_summary(ev, dataset_end=definitions.DATASET_END),
            "offers": m.offer_table(ev)}


@pytest.fixture
def at(fingerprint_before):
    app = AppTest.from_file(APP, default_timeout=600).run()
    app.switch_page(PAGE).run()
    assert not app.exception, [e.value for e in app.exception]
    return app


def ok(at):
    assert not at.exception, [e.value for e in at.exception]


def sel(at, name):
    key = selection.SELECTION_KEYS[name]
    return at.session_state[key] if key in at.session_state else None


def search(at, text):
    at.text_input(key="explorer_query").set_value(text).run()
    ok(at)


def raw_events(expected, case_id) -> pd.DataFrame:
    sub = expected["raw"][expected["raw"][config.COL_CASE_ID] == case_id]
    return sub.sort_values(config.COL_TIMESTAMP, kind="mergesort")  # ties keep the original order


def summary_row(expected, case_id) -> pd.Series:
    s = expected["summary"]
    return s[s["case_id"] == case_id].iloc[0]


def timeline_fig(at) -> dict:
    [fig] = [f for f in figures(at) if any(t.get("name", "").endswith(" events") for t in f["data"])]
    return fig


def event_table(at) -> pd.DataFrame:
    return at.dataframe[-1].value


def _table_click(at, monkeypatch, key, name, values, rows):
    state = _State(at)
    monkeypatch.setattr(selection, "_session", lambda: state)
    state[key] = {"selection": {"rows": rows, "columns": [], "cells": []}}
    selection.row_selection_callback(key, name, values)()
    del at.session_state[key]
    at.run()
    ok(at)


def click_event(at, monkeypatch, event_id):
    case_id = sel(at, "application")
    key = f"explorer_timeline::{case_id}::{sel(at, 'event') or ''}"
    valid = [c[0] for t in timeline_fig(at)["data"] if t.get("name", "").endswith(" events") for c in t["customdata"]]
    state = _State(at)
    monkeypatch.setattr(selection, "_session", lambda: state)
    state[key] = {"selection": {"points": [{"customdata": [event_id]}], "point_indices": [0], "box": [], "lasso": []}}
    selection.page_selection_callback(key, "event", valid)()
    del at.session_state[key]
    at.run()
    ok(at)


# =========================================================================== #
# X1 - selection
# =========================================================================== #
def test_initial_state_lists_filtered_applications_without_selection(at):
    assert len(at.dataframe) == 1 and at.dataframe[0].value.shape[0] == 31_509
    assert sel(at, "application") is None
    assert "No application selected" in captions(at)
    assert not figures(at)


def test_partial_search_narrows_and_exact_search_selects(at):
    search(at, "Application_100")
    assert sel(at, "application") is None and len(at.dataframe[0].value) > 1
    search(at, APPLICATIONS["A_Denied"].lower())  # case-insensitive exact match
    assert sel(at, "application") == APPLICATIONS["A_Denied"]
    assert at.dataframe[0].value["Application"].tolist() == [APPLICATIONS["A_Denied"]]
    assert at.dataframe[0].proto.selection_default  # the selected application is the selected row
    banner = [x.value for x in at.markdown if x.value.startswith("**Selected application:")]
    assert banner and f"`{APPLICATIONS['A_Denied']}`" in banner[0]


def test_row_selection_selects_application(at, monkeypatch):
    table = at.dataframe[0].value
    target = table["Application"].iloc[7]
    _table_click(at, monkeypatch, "explorer_table::::", "application", table["Application"].tolist(), [7])
    assert sel(at, "application") == target
    [chip] = [b for b in at.button if b.key == "chip_sel_application"]
    chip.click().run()
    ok(at)
    assert sel(at, "application") is None and not figures(at)


def test_handoff_from_cancellation_preserves_global_filters(at):
    """Filters set through the real sidebar widgets survive the st.switch_page hand-off (5c fix)."""
    at.multiselect(key=FILTER_KEYS["final_statuses"]).set_value([CANCELLED]).run()
    at.select_slider(key=FILTER_KEYS["months"]).set_range("2016-01", "2016-09").run()
    at.switch_page("views/cancellation.py").run()
    ok(at)
    target = APPLICATIONS["A_Cancelled"]
    at.session_state[selection.SELECTION_KEYS["application"]] = target  # as a gap-point or row click does
    at.run()
    [open_btn] = [b for b in at.button if b.key == "open_in_explorer"]
    assert not open_btn.disabled and target in open_btn.label
    open_btn.click().run()
    ok(at)
    assert [t.value for t in at.title] == ["Application Explorer"]
    assert sel(at, "application") == target
    assert at.session_state[FILTER_KEYS["final_statuses"]] == [CANCELLED]  # filters untouched
    assert tuple(at.session_state[FILTER_KEYS["months"]]) == ("2016-01", "2016-09")
    assert at.session_state["explorer_query"] == target
    assert at.dataframe[0].value["Application"].tolist() == [target]
    assert len(event_table(at)) == 22 and timeline_fig(at)
    at.run()  # and they stay on the next interaction
    ok(at)
    assert at.session_state[FILTER_KEYS["final_statuses"]] == [CANCELLED]
    assert tuple(at.session_state[FILTER_KEYS["months"]]) == ("2016-01", "2016-09")


def test_selection_outside_new_filters_is_cleared_with_notice(at):
    search(at, APPLICATIONS["A_Pending"])
    at.session_state[FILTER_KEYS["final_statuses"]] = [CANCELLED]
    at.run()
    ok(at)
    assert sel(at, "application") is None
    assert any("was cleared" in i.value and "global filters were not changed" in i.value for i in at.info)
    assert at.session_state[FILTER_KEYS["final_statuses"]] == [CANCELLED]


def test_search_for_application_outside_filters_explains(at):
    at.session_state[FILTER_KEYS["final_statuses"]] = [CANCELLED]
    at.run()
    search(at, APPLICATIONS["A_Pending"])
    assert sel(at, "application") is None
    assert any("exists but is outside the current filters" in i.value for i in at.info)


# =========================================================================== #
# X2 / X3 - per application: counts, order, lanes, lifelines, final status
# =========================================================================== #
@pytest.mark.parametrize("kind", list(APPLICATIONS))
def test_application_timeline_and_events(at, expected, kind):
    case_id = APPLICATIONS[kind]
    search(at, case_id)
    row, raw = summary_row(expected, case_id), raw_events(expected, case_id)
    if kind in ("A_Pending", "A_Cancelled", "A_Denied"):
        assert row["final_status"] == kind
    if kind == "open":
        assert bool(row["is_open"])

    # counts: timeline markers = event-table rows = total_events = raw rows
    fig = timeline_fig(at)
    lanes = {t["name"]: t for t in fig["data"] if t.get("name", "").endswith(" events")}
    markers = sum(len(t["x"]) for t in lanes.values())
    table = event_table(at)
    assert markers == len(table) == row["total_events"] == len(raw)

    # original event order (timestamp, ties in file order) and the recorded values
    assert table["Event ID"].tolist() == raw[config.COL_EVENT_ID].tolist()
    assert table["#"].tolist() == list(range(1, len(raw) + 1))
    assert table["Time (UTC)"].is_monotonic_increasing
    assert table["Activity"].tolist() == raw[config.COL_ACTIVITY].tolist()
    assert table["Resource (as recorded)"].tolist() == raw[config.COL_RESOURCE].tolist()
    assert table["Lifecycle stage"].tolist() == raw[config.COL_LIFECYCLE].tolist()
    assert table["Action"].tolist() == raw["Action"].tolist()
    assert table["Event origin"].tolist() == raw["EventOrigin"].tolist()
    recorded_offer = raw[config.COL_OFFER_ID]
    assert table["OfferID (as recorded)"].isna().tolist() == recorded_offer.isna().tolist()

    # lane classification by activity prefix
    prefix = {"Application events": "A_", "Offer events": "O_", "Workflow events": "W_"}
    for name, trace in lanes.items():
        assert all(c[1].startswith(prefix[name]) for c in trace["customdata"]), name
    assert sorted(c[0] for t in lanes.values() for c in t["customdata"]) == sorted(raw[config.COL_EVENT_ID])

    # final status marker / dataset end
    annotations = [a["text"] for a in fig["layout"].get("annotations", [])]
    if bool(row["is_open"]):
        assert any("Data ends 1 Feb 2017" in a for a in annotations)
        assert not any(a.startswith("Final status") for a in annotations)
        assert not table["Final-status event"].any()
        assert any("Data ends on 1 February 2017" in w.value for w in at.warning)
    else:
        label = config.FINAL_STATUS_LABELS[row["final_status"]]
        assert f"Final status: {label}" in annotations
        flagged = table[table["Final-status event"]]
        assert len(flagged) == 1 and flagged["Activity"].iloc[0] == row["final_status"]
        assert flagged["Time (UTC)"].iloc[0] == row["final_status_time"]
        assert not any("Data ends 1 Feb 2017" in a for a in annotations)

    # offer lifelines from the approved offer table
    offers = expected["offers"][expected["offers"]["case_id"] == case_id]
    lines = [t for t in fig["data"] if t.get("mode") == "lines"]
    assert len(lines) == len(offers) == row["offers_created"]
    for offer in offers.itertuples():
        [line] = [t for t in lines if f"<b>{offer.offer_id}</b>" in t["hovertemplate"]]
        assert pd.Timestamp(line["x"][0]) == offer.created_time
        if pd.isna(offer.offer_outcome_time):
            assert line["line"]["dash"] == "dot" and pd.Timestamp(line["x"][1]) == definitions.DATASET_END
            assert "No outcome recorded" in line["hovertemplate"]
        else:
            assert line["line"]["dash"] == "solid" and pd.Timestamp(line["x"][1]) == offer.offer_outcome_time
            assert offer.offer_outcome in line["hovertemplate"]

    # summary tables come from the approved application summary
    tables = {d.value.iloc[0, 0]: d.value for d in at.dataframe[1:4]}
    fields = {k: v for frame in tables.values() for k, v in zip(frame["Field"], frame["Value"])}
    assert fields["Application"] == case_id and fields["Final status"] == row["final_status_label"]
    assert fields["Recorded events"] == f"{row['total_events']:,}"
    assert fields["Offers created"] == f"{row['offers_created']:,}"


def test_open_application_has_offers_without_outcome(at, expected):
    case_id = APPLICATIONS["open"]
    offers = expected["offers"][expected["offers"]["case_id"] == case_id]
    assert offers["offer_outcome_time"].isna().any()  # the case the dotted lifeline is for
    search(at, case_id)
    assert "without a recorded outcome are dotted to the data end" in captions(at)


# =========================================================================== #
# Event selection: timeline click <-> event table row
# =========================================================================== #
def test_timeline_click_selects_table_row_and_toggles(at, monkeypatch, expected):
    case_id = APPLICATIONS["A_Denied"]
    search(at, case_id)
    target = raw_events(expected, case_id)[config.COL_EVENT_ID].iloc[10]
    click_event(at, monkeypatch, target)
    assert sel(at, "event") == target
    rings = [t for t in timeline_fig(at)["data"] if t.get("name") == "Selected event"]
    assert len(rings) == 1
    assert '"rows": [10]' in at.dataframe[-1].proto.selection_default
    assert any(x.value.startswith("**Selected event #11:**") for x in at.markdown)
    click_event(at, monkeypatch, target)  # same event again clears
    assert sel(at, "event") is None and not at.dataframe[-1].proto.selection_default


def test_event_table_row_rings_timeline_and_clear(at, monkeypatch):
    case_id = APPLICATIONS["A_Pending"]
    search(at, case_id)
    ids = event_table(at)["Event ID"].tolist()
    _table_click(at, monkeypatch, f"explorer_events::{case_id}::", "event", ids, [3])
    assert sel(at, "event") == ids[3]
    [ring] = [t for t in timeline_fig(at)["data"] if t.get("name") == "Selected event"]
    [chip] = [b for b in at.button if b.key == "chip_sel_event"]
    chip.click().run()
    ok(at)
    assert sel(at, "event") is None


def test_invalid_timeline_click_is_ignored(at, monkeypatch):
    search(at, APPLICATIONS["A_Pending"])
    click_event(at, monkeypatch, "Workitem_not_in_this_application")
    assert sel(at, "event") is None


def test_changing_application_drops_event_selection(at, monkeypatch):
    search(at, APPLICATIONS["A_Pending"])
    click_event(at, monkeypatch, event_table(at)["Event ID"].iloc[0])
    assert sel(at, "event") is not None
    search(at, APPLICATIONS["A_Denied"])
    assert sel(at, "event") is None and sel(at, "application") == APPLICATIONS["A_Denied"]


def test_other_page_selections_are_preserved(at):
    at.session_state[selection.SELECTION_KEYS["transition"]] = ("A_Create Application", "A_Submitted")
    at.session_state[selection.SELECTION_KEYS["pattern"]] = m.PATTERN_OTHER
    search(at, APPLICATIONS["A_Pending"])
    assert sel(at, "transition") == ("A_Create Application", "A_Submitted")
    assert sel(at, "pattern") == m.PATTERN_OTHER


# =========================================================================== #
# Downloads, wording, protected files
# =========================================================================== #
def test_event_download(at, expected):
    case_id = APPLICATIONS["open"]
    search(at, case_id)
    [btn] = [b for b in at.get("download_button") if b.proto.id and "dl_explorer_events" in b.proto.id]
    assert not btn.proto.disabled
    assert btn.proto.label == "Download this application's events (CSV)"
    # The button streams components.csv_payload(event table); rebuild it from the same functions.
    from ui import data

    payload = components.csv_payload(data.application_detail(case_id).events)
    back = pd.read_csv(io.BytesIO(payload()))
    raw = raw_events(expected, case_id)
    assert back["Event ID"].tolist() == raw[config.COL_EVENT_ID].tolist()
    assert back["Activity"].tolist() == raw[config.COL_ACTIVITY].tolist()
    assert len(back) == summary_row(expected, case_id)["total_events"]
    assert list(back.columns) == list(event_table(at).columns)


def test_wording_and_no_task_durations(at):
    search(at, APPLICATIONS["longest"])
    text = captions(at) + " ".join(x.value for x in at.markdown) + " ".join(x.value for x in at.info)
    text = text.replace(definitions.A_PENDING_NOTE, "")  # the approved note itself says "not labelled 'Approved'"
    assert "no task durations are shown" in text and "roles are not inferred" in text
    for word in ("Approved", "Funded"):
        assert word not in text
    assert "the system account" not in text.lower()
    columns = event_table(at).columns
    assert "Resource (as recorded)" in columns and not any("duration" in c.lower() for c in columns)


def test_protected_files_unchanged(at, fingerprint_before):
    search(at, APPLICATIONS["longest"])
    assert _fingerprint(config.PROTECTED_DIRS) == fingerprint_before
