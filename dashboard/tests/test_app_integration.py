"""Phase 5f: cross-page integration, shared state, downloads and wording on the real app.

Clicks are simulated through the pages' real callbacks (see test_app_executive).
Downloads are captured by wrapping ``components.download_csv`` and building the CSV
bytes with the real deferred ``csv_payload`` - exactly what the button serves.
"""

from __future__ import annotations

import io
import re

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from core import config, definitions, loaders
from core import metrics as m
from tests.test_app_executive import _State, click, month_key, status_key
from tests.test_read_only import _fingerprint
from ui import components, selection
from ui.filters import FILTER_KEYS, STATUS_LABEL_ORDER

pytestmark = [pytest.mark.integration, pytest.mark.ui]

APP = str(config.DASHBOARD_DIR / "app.py")
PAGES = {
    "views/executive.py": "Executive Overview",
    "views/process.py": "Process Analysis",
    "views/cancellation.py": "Cancellation Intelligence",
    "views/explorer.py": "Application Explorer",
    "views/data_quality.py": "Data Quality & Reconciliation",
}
FILTERED_PAGES = [p for p in PAGES if p != "views/data_quality.py"]
PENDING, CANCELLED, DENIED = (config.FINAL_STATUS_LABELS[s] for s in config.FINAL_STATUS_ACTIVITIES)
OPEN = config.NO_FINAL_STATUS_LABEL
PAIR = ("A_Create Application", "A_Submitted")
APP_ID = "Application_1000086665"  # A_Cancelled, offer-first follow-up pattern population


@pytest.fixture(scope="module")
def fingerprint_before():
    return _fingerprint(config.PROTECTED_DIRS)


@pytest.fixture(scope="module")
def expected():
    ev = m.prepare_events(loaders.load_event_log(m.METRIC_EVENT_COLUMNS))
    return {"events": ev, "summary": m.application_summary(ev, dataset_end=definitions.DATASET_END),
            "followup": m.offer_first_cancellation_followup(ev)}


@pytest.fixture
def at(fingerprint_before):
    app = AppTest.from_file(APP, default_timeout=600).run()
    ok(app)
    return app


@pytest.fixture
def downloads(monkeypatch):
    """Every download offered during the test: {key: (file_name, DataFrame read back from the CSV bytes)}."""
    captured: dict[str, tuple[str, pd.DataFrame]] = {}
    original = components.download_csv

    def recording(df, file_name, *, label="Download CSV", key):
        captured[key] = (file_name, pd.read_csv(io.BytesIO(components.csv_payload(df)())))
        original(df, file_name, label=label, key=key)

    monkeypatch.setattr(components, "download_csv", recording)
    return captured


def ok(at):
    assert not at.exception, [e.value for e in at.exception]


def go(at, page):
    at.switch_page(page).run()
    ok(at)
    assert [t.value for t in at.title] == [PAGES[page]]


def sel(at, name):
    key = selection.SELECTION_KEYS[name]
    return at.session_state[key] if key in at.session_state else None


def filters_now(at) -> dict:
    return {k: at.session_state[v] for k, v in FILTER_KEYS.items() if v in at.session_state}


def filter_caption(at) -> str:
    return [c.value for c in at.caption if c.value.startswith(":material/filter_alt:")][0]


def page_text(at) -> str:
    parts = [x.value for kind in ("title", "header", "subheader", "markdown", "caption", "info", "warning", "error",
                                  "success") for x in at.get(kind)]
    parts += [b.label for b in at.button] + [str(x.label) for x in at.metric] + [str(x.value) for x in at.metric]
    return "\n".join(parts)


def chip(at, key):
    [button] = [b for b in at.button if b.key == key]
    button.click().run()
    ok(at)


# =========================================================================== #
# Global filters: one source of truth across every page
# =========================================================================== #
def test_sidebar_filters_apply_identically_on_every_page(at, expected):
    at.multiselect(key=FILTER_KEYS["final_statuses"]).set_value([CANCELLED, DENIED]).run()
    at.select_slider(key=FILTER_KEYS["months"]).set_range("2016-03", "2016-08").run()
    at.multiselect(key=FILTER_KEYS["loan_goals"]).set_value(["Car"]).run()
    ok(at)
    before = filters_now(at)
    s = expected["summary"]
    population = s[s["final_status_label"].isin([CANCELLED, DENIED]) & s["start_month"].between("2016-03", "2016-08")
                   & (s["loan_goal"] == "Car")]
    captions = set()
    for page in [*FILTERED_PAGES, "views/data_quality.py", "views/executive.py"]:
        go(at, page)
        assert filters_now(at) == before, page  # navigation never resets a filter
        if page in FILTERED_PAGES:
            captions.add(filter_caption(at))
        if page == "views/explorer.py":
            assert at.dataframe[0].value.shape[0] == len(population)
            assert set(at.dataframe[0].value["Application"]) == set(population["case_id"])
        if page == "views/process.py":
            assert f"{len(population):,} applications in the selection" in page_text(at)
    assert len(captions) == 1  # every filtered page describes the same filter state


def test_data_quality_page_is_not_filtered(at):
    go(at, "views/data_quality.py")
    unfiltered = page_text(at)
    at.multiselect(key=FILTER_KEYS["final_statuses"]).set_value([OPEN]).run()
    ok(at)
    assert page_text(at) == unfiltered


def test_clear_individual_filters_and_reset_all(at):
    at.multiselect(key=FILTER_KEYS["final_statuses"]).set_value([PENDING]).run()
    at.select_slider(key=FILTER_KEYS["months"]).set_range("2016-02", "2016-04").run()
    at.multiselect(key=FILTER_KEYS["application_types"]).set_value(["New credit"]).run()
    at.session_state[selection.SELECTION_KEYS["pattern"]] = m.PATTERN_OTHER
    go(at, "views/process.py")
    chip(at, "chip_status")  # clear the status filter only
    assert at.session_state[FILTER_KEYS["final_statuses"]] == []
    assert tuple(at.session_state[FILTER_KEYS["months"]]) == ("2016-02", "2016-04")
    assert at.session_state[FILTER_KEYS["application_types"]] == ["New credit"]
    chip(at, "chip_month")  # clear the month range only
    months = at.select_slider(key=FILTER_KEYS["months"]).options
    assert tuple(at.session_state[FILTER_KEYS["months"]]) == ("2016-01", "2017-01") or len(months) > 1
    assert at.session_state[FILTER_KEYS["application_types"]] == ["New credit"]
    at.session_state[FILTER_KEYS["final_statuses"]] = [DENIED]
    at.run()
    [reset] = [b for b in at.button if b.label == "Reset filters"]
    reset.click().run()
    ok(at)
    state = at.session_state["filter_state"]
    assert state.is_default and filters_now(at)[ "final_statuses"] == [] and filters_now(at)["application_types"] == []
    assert sel(at, "pattern") == m.PATTERN_OTHER  # page selections are not filters


# =========================================================================== #
# Chart clicks write the global filters, seen by every page
# =========================================================================== #
def test_status_bar_click_filters_every_page(at, monkeypatch, expected):
    click(at, monkeypatch, status_key(), selection.status_filter_callback(status_key(), STATUS_LABEL_ORDER), CANCELLED)
    assert at.session_state[FILTER_KEYS["final_statuses"]] == [CANCELLED]
    go(at, "views/explorer.py")
    assert at.dataframe[0].value.shape[0] == 10_431
    assert set(at.dataframe[0].value["Final status"]) == {CANCELLED}
    go(at, "views/process.py")
    assert "10,431 applications in the selection" in page_text(at)
    go(at, "views/executive.py")
    click(at, monkeypatch, status_key((CANCELLED,)),
          selection.status_filter_callback(status_key((CANCELLED,)), STATUS_LABEL_ORDER), CANCELLED)
    assert at.session_state[FILTER_KEYS["final_statuses"]] == []  # clicking the selected bar clears it


def test_monthly_click_filters_every_page(at, monkeypatch, expected):
    s = expected["summary"]
    months = sorted(s["start_month"].unique())
    key = month_key()
    click(at, monkeypatch, key, selection.month_filter_callback(key, months), "2016-06")
    assert tuple(at.session_state[FILTER_KEYS["months"]]) == ("2016-06", "2016-06")
    go(at, "views/explorer.py")
    assert at.dataframe[0].value.shape[0] == int((s["start_month"] == "2016-06").sum())
    go(at, "views/cancellation.py")
    assert "Started Jun 2016" in filter_caption(at)


# =========================================================================== #
# Page selections: independent, preserved across navigation, validated
# =========================================================================== #
def _set_all_page_selections(at):
    values = {"transition": PAIR, "activity": "A_Submitted", "pattern": m.PATTERN_OTHER,
              "application": APP_ID, "sequence": m.SEQ_OFFER_FIRST}
    for name, value in values.items():
        at.session_state[selection.SELECTION_KEYS[name]] = value
    return values


def test_page_selections_survive_navigation_round_trip(at):
    values = _set_all_page_selections(at)
    for page in [*PAGES, "views/executive.py"]:
        go(at, page)
        for name, value in values.items():
            assert sel(at, name) == value, (page, name)
    assert filters_now(at)["final_statuses"] == []


@pytest.mark.parametrize("page,chip_key,cleared", [
    ("views/process.py", "chip_sel_transition", "transition"),
    ("views/process.py", "chip_sel_activity", "activity"),
    ("views/cancellation.py", "chip_sel_pattern", "pattern"),
    ("views/cancellation.py", "chip_sel_sequence", "sequence"),
    ("views/explorer.py", "chip_sel_application", "application"),
])
def test_clearing_one_selection_keeps_the_others(at, page, chip_key, cleared):
    values = _set_all_page_selections(at)
    at.session_state[FILTER_KEYS["application_types"]] = ["New credit"]
    go(at, page)
    chip(at, chip_key)
    assert sel(at, cleared) is None
    for name, value in values.items():
        if name != cleared:
            assert sel(at, name) == value, name
    assert at.session_state[FILTER_KEYS["application_types"]] == ["New credit"]


def test_filter_change_clears_only_selections_it_excludes(at):
    _set_all_page_selections(at)
    at.session_state[FILTER_KEYS["final_statuses"]] = [PENDING]  # APP_ID is A_Cancelled
    for page in ("views/explorer.py", "views/cancellation.py", "views/process.py"):
        go(at, page)
    assert sel(at, "application") is None  # excluded by the filter: cleared with a notice
    assert sel(at, "transition") == PAIR and sel(at, "activity") == "A_Submitted"  # still present


@pytest.mark.parametrize("page", list(PAGES))
def test_invalid_selections_are_handled_safely(at, page):
    garbage = {"transition": ("No_such", "Activity"), "activity": "No_such_activity", "pattern": "No pattern",
               "sequence": "No sequence", "offer_status": "No status", "application": "Application_0",
               "event": "Workitem_0"}
    for name, value in garbage.items():
        at.session_state[selection.SELECTION_KEYS[name]] = value
    go(at, page)
    go(at, page)  # and on the rerun
    used = {"views/process.py": ("transition", "activity"),
            "views/cancellation.py": ("pattern", "sequence", "offer_status", "application"),
            "views/explorer.py": ("application", "event")}.get(page, ())
    for name in used:
        assert sel(at, name) is None, name


def test_empty_filter_result_on_every_page(at):
    at.session_state[FILTER_KEYS["final_statuses"]] = [OPEN]
    at.session_state[FILTER_KEYS["months"]] = ("2016-01", "2016-09")  # open applications all start Oct-Dec 2016
    at.run()
    for page in PAGES:
        go(at, page)
        if page in FILTERED_PAGES:
            assert any("No applications match the current filters" in i.value for i in at.info), page


# =========================================================================== #
# Cancellation case table (5f regression) and the Explorer hand-off
# =========================================================================== #
def test_case_table_selection_can_be_cleared_and_replaced(at):
    """Regression (5f): a lingering table row selection used to undo Clear and override newer selections."""
    go(at, "views/cancellation.py")
    [table] = [d for d in at.dataframe if d.key and d.key.startswith("cancel_case_table")]
    ids = table.value["Application"].tolist()
    at.session_state[table.key] = {"selection": {"rows": [2], "columns": [], "cells": []}}  # stays, as in a browser
    at.run()
    if sel(at, "application") is None:  # selection made through the row callback
        at.session_state[selection.SELECTION_KEYS["application"]] = ids[2]
    at.run()
    ok(at)
    assert sel(at, "application") == ids[2]
    chip(at, "chip_sel_application")
    at.run()
    at.run()
    assert sel(at, "application") is None  # Clear sticks
    at.session_state[selection.SELECTION_KEYS["application"]] = ids[5]  # e.g. a gap-chart click
    at.run()
    at.run()
    assert sel(at, "application") == ids[5]  # not overridden by the old row
    [table] = [d for d in at.dataframe if d.key and d.key.startswith("cancel_case_table")]
    assert '"rows": [5]' in table.proto.selection_default  # the current selection is the selected row


def test_cancellation_to_explorer_handoff_end_to_end(at, monkeypatch, expected):
    at.multiselect(key=FILTER_KEYS["final_statuses"]).set_value([CANCELLED]).run()
    go(at, "views/cancellation.py")
    at.session_state[selection.SELECTION_KEYS["pattern"]] = m.PATTERN_OTHER
    at.run()
    target = expected["followup"].query("pattern == @m.PATTERN_OTHER")["case_id"].iloc[0]
    at.session_state[selection.SELECTION_KEYS["application"]] = target
    at.run()
    [open_btn] = [b for b in at.button if b.key == "open_in_explorer"]
    open_btn.click().run()
    ok(at)
    assert [t.value for t in at.title] == ["Application Explorer"] and sel(at, "application") == target
    assert at.session_state[FILTER_KEYS["final_statuses"]] == [CANCELLED]
    assert sel(at, "pattern") == m.PATTERN_OTHER  # the source page's selection is kept
    go(at, "views/cancellation.py")  # back again: same application highlighted, nothing contradictory
    assert sel(at, "application") == target
    assert any(b.key == "chip_sel_application" and target in b.label for b in at.button)


def test_clearing_the_application_drops_its_event(at):
    """Regression: an event selection used to outlive its application."""
    at.session_state[selection.SELECTION_KEYS["application"]] = APP_ID
    go(at, "views/explorer.py")
    event = at.dataframe[-1].value["Event ID"].iloc[3]
    at.session_state[selection.SELECTION_KEYS["event"]] = event
    at.run()
    assert sel(at, "event") == event
    chip(at, "chip_sel_application")
    assert sel(at, "application") is None and sel(at, "event") is None
    at.session_state[selection.SELECTION_KEYS["application"]] = APP_ID
    at.run()
    assert not at.dataframe[-1].proto.selection_default  # no old event re-appears


def test_explorer_and_event_selection_survive_navigation(at):
    at.session_state[selection.SELECTION_KEYS["application"]] = APP_ID
    go(at, "views/explorer.py")
    event = at.dataframe[-1].value["Event ID"].iloc[5]
    at.session_state[selection.SELECTION_KEYS["event"]] = event
    at.run()
    go(at, "views/process.py")
    go(at, "views/explorer.py")
    assert sel(at, "application") == APP_ID and sel(at, "event") == event
    assert '"rows": [5]' in at.dataframe[-1].proto.selection_default


# =========================================================================== #
# Downloads: rows, columns, filters and selections
# =========================================================================== #
def test_downloads_respect_filters_and_selections(at, downloads, expected):
    s, followup = expected["summary"], expected["followup"]
    at.multiselect(key=FILTER_KEYS["final_statuses"]).set_value([CANCELLED]).run()
    at.select_slider(key=FILTER_KEYS["months"]).set_range("2016-01", "2016-06").run()
    population = s[(s["final_status_label"] == CANCELLED) & s["start_month"].between("2016-01", "2016-06")]

    go(at, "views/executive.py")
    _, dist = downloads["dl_exec_status"]
    in_months = s[s["start_month"].between("2016-01", "2016-06")]
    # the status chart (and its download) ignores its own status filter by design, so it shows every status
    assert dist["Applications"].sum() == len(in_months)
    assert dict(zip(dist.iloc[:, 0], dist["Applications"]))[CANCELLED] == len(population)

    go(at, "views/explorer.py")
    name, apps = downloads["dl_explorer"]
    assert name == "applications.csv" and len(apps) == len(population)
    assert apps["Application"].is_unique and set(apps["Application"]) == set(population["case_id"])
    assert list(apps.columns) == list(at.dataframe[0].value.columns)

    at.session_state[selection.SELECTION_KEYS["pattern"]] = m.PATTERN_REPLACEMENT_OFFER
    go(at, "views/cancellation.py")
    _, cases = downloads["dl_cancel_cases"]
    in_scope = followup[(followup["pattern"] == m.PATTERN_REPLACEMENT_OFFER)
                        & followup["case_id"].isin(population["case_id"])]
    assert len(cases) == len(in_scope) and cases["Application"].is_unique
    assert set(cases["Application"]) == set(in_scope["case_id"])

    at.session_state[selection.SELECTION_KEYS["transition"]] = PAIR
    go(at, "views/process.py")
    _, occurrences = downloads["dl_proc_occurrences"]
    ev = expected["events"]
    expected_pairs = m.transition_metrics(ev[ev[m.C].isin(population["case_id"])], min_count=1)
    row = expected_pairs[(expected_pairs["activity"] == PAIR[0]) & (expected_pairs["next_activity"] == PAIR[1])]
    assert len(occurrences) == int(row["transition_count"].iloc[0])
    assert set(occurrences["Application"]) <= set(population["case_id"])

    at.session_state[selection.SELECTION_KEYS["application"]] = population["case_id"].iloc[0]
    go(at, "views/explorer.py")
    _, events = downloads["dl_explorer_events"]
    assert len(events) == population["total_events"].iloc[0] and events["Event ID"].is_unique
    assert events["#"].tolist() == list(range(1, len(events) + 1))


def test_reconciliation_download_keeps_expected_results(at, downloads):
    go(at, "views/data_quality.py")
    name, checks = downloads["dl_dq_checks"]
    assert name == "reconciliation_checks.csv"
    assert len(checks) == 85 and set(checks["Result"]) == {"PASS"} and checks["ID"].is_unique
    name, findings = downloads["dl_dq_info"]
    assert name == "informational_findings.csv" and len(findings) == 7 and set(findings["Result"]) == {"INFO"}


# =========================================================================== #
# Analytical wording on every page, with selections active
# =========================================================================== #
def test_wording_on_every_page_with_selections(at):
    _set_all_page_selections(at)
    approved_notes = (definitions.A_PENDING_NOTE,)
    for page in PAGES:
        go(at, page)
        text = page_text(at)
        for note in approved_notes:
            text = text.replace(note, "")
        assert not re.search(r"\b(Approved|Funded)\b", text), page
        assert "the system account" not in text.lower(), page
        for match in re.finditer(r"User_1\b", text):
            window = text[match.start(): match.start() + 80]
            assert "likely" in window or "as recorded" in text, page
        for match in re.finditer(r"timer-driven", text):
            assert "likely" in text[max(0, match.start() - 30): match.start()].lower(), page
        for match in re.finditer(r"recover\w*", text, flags=re.IGNORECASE):
            assert "not" in text[max(0, match.start() - 40): match.start()].lower(), page
        assert "task duration" not in text.lower().replace("no task durations", ""), page
        if page == "views/executive.py":
            assert "A_Pending (offer accepted)" in text and OPEN in text
            assert any("Data ends on 1 February 2017" in w.value for w in at.warning)
        if page == "views/cancellation.py":
            assert "O_Cancelled ≠ A_Cancelled" in text
        if page == "views/process.py":
            assert "Exploratory: likely timer-driven steps" in text
        if page == "views/data_quality.py":
            assert "Last recorded activity is not a final status" in text


def test_protected_files_unchanged(at, fingerprint_before):
    for page in PAGES:
        go(at, page)
    assert _fingerprint(config.PROTECTED_DIRS) == fingerprint_before
