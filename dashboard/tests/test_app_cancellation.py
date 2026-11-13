"""Phase 5c: Cancellation Intelligence interactions on the real app and real data.

Clicks are simulated as in test_app_executive: the page's real callbacks
(ui.selection factories + then_set) run against the live AppTest session state,
then the app reruns. Expected values come from the approved core metrics and the
existing reports (read-only).
"""

from __future__ import annotations

import re

import pytest
from streamlit.testing.v1 import AppTest

from core import config, definitions, loaders
from core import metrics as m
from tests.test_app_executive import _State, captions, figures, kpi
from tests.test_read_only import _fingerprint
from ui import selection
from ui.filters import FILTER_KEYS

pytestmark = [pytest.mark.integration, pytest.mark.ui]

APP = str(config.DASHBOARD_DIR / "app.py")
PAGE = "views/cancellation.py"
SOURCE_KEY = "cancel_case_source"
PENDING, CANCELLED, DENIED = (config.FINAL_STATUS_LABELS[s] for s in config.FINAL_STATUS_ACTIVITIES)


@pytest.fixture(scope="module")
def fingerprint_before():
    return _fingerprint(config.PROTECTED_DIRS)


@pytest.fixture(scope="module")
def reports():
    return {
        "summary": loaders.load_report("offer_cancellation_pattern_summary").set_index("pattern"),
        "followup": loaders.load_report("offer_cancellation_followup_patterns"),
    }


@pytest.fixture(scope="module")
def expected():
    ev = m.prepare_events(loaders.load_event_log(m.METRIC_EVENT_COLUMNS))
    s = m.application_summary(ev, dataset_end=definitions.DATASET_END)
    table = m.application_cancellation_table(ev)
    return {"summary": s, "table": table, "followup": m.offer_first_cancellation_followup(ev),
            "offer": m.offer_cancellation_metrics(ev, s)}


@pytest.fixture
def at(fingerprint_before):
    app = AppTest.from_file(APP, default_timeout=600).run()
    app.switch_page(PAGE).run()
    assert not app.exception, [e.value for e in app.exception]
    return app


def click(at, monkeypatch, chart_key, callback, *customdata):
    state = _State(at)
    monkeypatch.setattr(selection, "_session", lambda: state)
    state[chart_key] = {"selection": {"points": [{"customdata": list(customdata)}], "point_indices": [0],
                                      "box": [], "lasso": []}}
    callback()
    del at.session_state[chart_key]
    at.run()
    assert not at.exception, [e.value for e in at.exception]


def sel(at, name):
    key = selection.SELECTION_KEYS[name]
    return at.session_state[key] if key in at.session_state else None


def click_offer_status(at, mp, label, valid):
    key = f"cancel_offer_status::{sel(at, 'offer_status') or ''}"
    click(at, mp, key, selection.then_set(selection.page_selection_callback(key, "offer_status", valid), SOURCE_KEY,
                                          "offer_status"), label)


def click_sequence(at, mp, seq, valid):
    key = f"cancel_sequence::{sel(at, 'sequence') or ''}"
    click(at, mp, key, selection.then_set(selection.page_selection_callback(key, "sequence", valid), SOURCE_KEY,
                                          "sequence"), seq)


def click_pattern(at, mp, pattern, valid=m.PATTERN_PRECEDENCE):
    key = f"cancel_patterns::{sel(at, 'pattern') or ''}"
    click(at, mp, key, selection.then_set(selection.page_selection_callback(key, "pattern", list(valid)), SOURCE_KEY,
                                          "pattern"), pattern)


def click_gap_point(at, mp, case_id, valid):
    key = f"cancel_gap::{sel(at, 'pattern') or ''}::{sel(at, 'application') or ''}"
    click(at, mp, key, selection.then_set(selection.page_selection_callback(key, "application", list(valid)),
                                          SOURCE_KEY, "pattern"), case_id)


# ---- figure lookup by content ------------------------------------------------ #
def fig_where(at, predicate):
    found = [f for f in figures(at) if predicate(f)]
    assert len(found) == 1, f"expected one matching figure, found {len(found)}"
    return found[0]


def offer_status_fig(at):
    return fig_where(at, lambda f: f["data"][0].get("type") == "bar" and PENDING in (f["data"][0].get("y") or [])
                     and "list these applications" in f["data"][0].get("hovertemplate", ""))


def sequence_fig(at):
    return fig_where(at, lambda f: m.SEQ_OFFER_FIRST in (f["data"][0].get("y") or []))


def pattern_fig(at):
    return fig_where(at, lambda f: f["data"][0].get("type") == "bar" and m.PATTERN_WORKFLOW in (f["data"][0].get("y") or []))


def expiry_fig(at):
    return fig_where(at, lambda f: f["data"][0].get("name", "").startswith("Likely automatic expiry"))


def gap_fig(at):
    return fig_where(at, lambda f: any(t.get("type") == "box" for t in f["data"]))


def case_table(at):
    return at.dataframe[-1].value


# =========================================================================== #
# Structure, separation, warnings
# =========================================================================== #
def test_page_structure_and_separation(at):
    charts = at.get("plotly_chart")
    assert len(charts) == 5
    assert [len(c.proto.selection_mode) > 0 for c in charts] == [True, True, False, True, True]  # C3 hover only
    heads = [h.value for h in at.subheader]
    assert heads[0] == "Offer cancellation · O_Cancelled" and heads[1] == "Application cancellation · A_Cancelled"
    infos = " ".join(i.value for i in at.info)
    assert "O_Cancelled ≠ A_Cancelled" in infos and "Not customer recovery" in infos
    assert "likely, but not confirmed, a system account" in infos  # timer notice
    text = captions(at)
    assert "likely, but not confirmed, a system account" in text  # C3 definition caption
    assert "This does not change the global final-status filter." in text


# =========================================================================== #
# C1 - offer cancellation status
# =========================================================================== #
def test_c1_totals_and_click(at, monkeypatch, expected):
    bar = offer_status_fig(at)["data"][0]
    shown = dict(zip(bar["y"], bar["x"]))
    breakdown = expected["offer"].final_status_breakdown.set_index("label")["applications"]
    assert shown == {k: int(v) for k, v in breakdown.items()}
    assert shown == {PENDING: 5050, CANCELLED: 10431, DENIED: 195, config.NO_FINAL_STATUS_LABEL: 6}

    valid = [k for k, v in shown.items() if v > 0]
    click_offer_status(at, monkeypatch, PENDING, valid)
    assert sel(at, "offer_status") == PENDING and at.session_state[SOURCE_KEY] == "offer_status"
    table = case_table(at)
    assert len(table) == 5050 and set(table["Final status"]) == {PENDING}
    assert (table["O_Cancelled events"] >= 1).all()
    assert at.session_state[FILTER_KEYS["final_statuses"]] in ([], None)  # global filter untouched
    assert kpi(at, "Applications with an offer cancellation") == "15,682"

    click_offer_status(at, monkeypatch, PENDING, valid)  # click again clears
    assert sel(at, "offer_status") is None and len(case_table(at)) == 15_682


# =========================================================================== #
# C2 - cancellation sequence
# =========================================================================== #
def test_c2_totals_and_click(at, monkeypatch, reports):
    bar = sequence_fig(at)["data"][0]
    assert dict(zip(bar["y"], bar["x"])) == {m.SEQ_OFFER_FIRST: 576, m.SEQ_SAME_TIME: 0,
                                             m.SEQ_APPLICATION_FIRST: 9855, m.SEQ_NO_OFFER_CANCEL: 0}
    valid = [m.SEQ_OFFER_FIRST, m.SEQ_APPLICATION_FIRST]
    click_sequence(at, monkeypatch, m.SEQ_SAME_TIME, valid)  # zero-size category is not selectable
    assert sel(at, "sequence") is None
    click_sequence(at, monkeypatch, m.SEQ_OFFER_FIRST, valid)
    table = case_table(at)
    assert len(table) == 576 and set(table["Sequence"]) == {m.SEQ_OFFER_FIRST}
    assert set(table["Application"]) == set(reports["followup"]["case_id"])  # same 576 as the report
    click_sequence(at, monkeypatch, m.SEQ_APPLICATION_FIRST, valid)  # switch
    assert len(case_table(at)) == 9855


# =========================================================================== #
# C3 - likely automatic expiry
# =========================================================================== #
def test_c3_expiry_histogram(at, expected):
    fig = expiry_fig(at)
    likely, other = fig["data"]
    assert likely["name"] == "Likely automatic expiry (exploratory)"
    assert len(likely["x"]) == 7932
    t = expected["table"]
    flag = t["likely_automatic_expiry"].fillna(False).astype(bool)
    assert len(other["x"]) == int((~flag & t["days_since_last_offer_sent"].notna()).sum())
    assert min(likely["x"]) >= 30.0
    assert any("30 days" in a["text"] for a in fig["layout"]["annotations"])
    assert kpi(at, "Likely automatic expiry cancellations") == "7,932"
    assert "Exploratory indicator" in captions(at)


# =========================================================================== #
# C4 / C6 - patterns and composition
# =========================================================================== #
def test_c4_pattern_order_and_counts(at, reports):
    bar = pattern_fig(at)["data"][0]
    assert list(bar["y"]) == list(m.PATTERN_PRECEDENCE)[::-1]  # original order, first on top
    shown = dict(zip(bar["y"], bar["x"]))
    assert shown == {p: int(reports["summary"].loc[p, "applications"]) for p in m.PATTERN_PRECEDENCE}
    assert sum(shown.values()) == 576


@pytest.mark.parametrize("pattern", m.PATTERN_PRECEDENCE)
def test_each_pattern_click_matches_the_report(at, monkeypatch, reports, pattern):
    click_pattern(at, monkeypatch, pattern)
    table = case_table(at)
    row = reports["summary"].loc[pattern]
    assert len(table) == int(row["applications"]) and set(table["Pattern"]) == {pattern}
    assert set(table["Final status"]) == {"A_Cancelled"}
    expected_ids = set(reports["followup"].loc[reports["followup"]["pattern"] == pattern, "case_id"])
    assert set(table["Application"]) == expected_ids
    comp = fig_where(at, lambda f: f["data"][0].get("y") and "Workflow activity (W_)" in f["data"][0]["y"])["data"][0]
    got = dict(zip(comp["y"], comp["x"]))
    assert got == {
        "New offer created (O_Create Offer)": int(row["applications_with_new_offers"]),
        "Offer sent (O_Sent)": int(row["applications_with_offers_sent"]),
        "Workflow activity (W_)": int(row["applications_with_workflow_activity"]),
        "Offer returned (O_Returned)": int(row["applications_with_returned_offers"]),
    }
    assert m.PATTERN_DESCRIPTIONS[pattern] in captions(at)
    assert any("Not customer recovery" in i.value for i in at.info)


def test_workflow_only_finding_is_preserved(at, monkeypatch):
    click_pattern(at, monkeypatch, m.PATTERN_WORKFLOW)
    info = " ".join(i.value for i in at.info)
    assert "**16 of 102** 'Workflow activity only' applications also contain non-workflow events" in info
    assert "offer events: 16" in info and "application events: 9" in info
    assert "original category is kept unchanged" in info
    detail = [d.value for d in at.dataframe if "Non-workflow activities in between" in d.value.columns]
    assert len(detail) == 1 and len(detail[0]) == 16


def test_all_576_remain_correctly_classified(at, monkeypatch, reports):
    seen = {}
    for pattern in m.PATTERN_PRECEDENCE:
        click_pattern(at, monkeypatch, pattern)
        for case in case_table(at)["Application"]:
            assert case not in seen, f"{case} in two patterns"
            seen[case] = pattern
    assert len(seen) == 576
    assert seen == dict(zip(reports["followup"]["case_id"], reports["followup"]["pattern"]))


# =========================================================================== #
# C5 / C7 - gap points, case table, Explorer handoff
# =========================================================================== #
def test_c5_points_and_explorer_handoff(at, monkeypatch, reports):
    points = [t for t in gap_fig(at)["data"] if t.get("type") == "scatter"][0]
    ids = [c[0] for c in points["customdata"]]
    assert sorted(ids) == sorted(reports["followup"]["case_id"]) and len(ids) == 576
    target = reports["followup"].sort_values("gap_days").iloc[-1]["case_id"]
    click_gap_point(at, monkeypatch, target, ids)
    assert sel(at, "application") == target and at.session_state[SOURCE_KEY] == "pattern"
    rings = [t for t in gap_fig(at)["data"] if t.get("marker", {}).get("symbol") == "circle-open"]
    assert len(rings) == 1
    [open_btn] = [b for b in at.button if b.key == "open_in_explorer"]
    assert not open_btn.disabled and target in open_btn.label
    open_btn.click().run()
    assert not at.exception
    assert [t.value for t in at.title] == ["Application Explorer"]
    explorer = at.dataframe[0].value
    assert explorer["Application"].tolist() == [target]


def test_open_button_disabled_without_selection(at):
    [open_btn] = [b for b in at.button if b.key == "open_in_explorer"]
    assert open_btn.disabled


def test_case_table_source_switch_and_download(at):
    at.segmented_control(key=SOURCE_KEY).set_value("sequence").run()
    assert at.session_state[SOURCE_KEY] == "sequence" and len(case_table(at)) == 10_431
    at.segmented_control(key=SOURCE_KEY).set_value("offer_status").run()
    assert len(case_table(at)) == 15_682
    assert len(at.get("download_button")) >= 1


# =========================================================================== #
# Clearing, empty selections, combined filters
# =========================================================================== #
def test_page_selection_chips_clear_selections(at, monkeypatch):
    click_pattern(at, monkeypatch, m.PATTERN_OTHER)
    click_sequence(at, monkeypatch, m.SEQ_OFFER_FIRST, [m.SEQ_OFFER_FIRST, m.SEQ_APPLICATION_FIRST])
    labels = [b.label for b in at.button]
    assert f"Pattern: {m.PATTERN_OTHER}" in labels and f"Sequence: {m.SEQ_OFFER_FIRST}" in labels
    [chip] = [b for b in at.button if b.key == "chip_sel_pattern"]
    chip.click().run()
    assert sel(at, "pattern") is None and sel(at, "sequence") == m.SEQ_OFFER_FIRST
    [chip] = [b for b in at.button if b.key == "chip_sel_sequence"]
    chip.click().run()
    assert sel(at, "sequence") is None
    assert not any(b.key and b.key.startswith("chip_sel_") for b in at.button)


def test_empty_offer_first_population_under_global_filter(at):
    at.multiselect(key=FILTER_KEYS["final_statuses"]).set_value([PENDING]).run()
    assert not at.exception
    infos = " ".join(i.value for i in at.info)
    assert "No offer-first cancellations in the current selection." in infos
    assert "No application cancellations in the current selection." in infos
    bar = offer_status_fig(at)["data"][0]
    assert dict(zip(bar["y"], bar["x"]))[PENDING] == 5050 and sum(bar["x"]) == 5050
    assert "No applications in this selection." in infos  # pattern case table is empty


def test_combined_global_month_and_pattern(at, monkeypatch, expected, reports):
    at.select_slider(key=FILTER_KEYS["months"]).set_range("2016-03", "2016-03").run()
    march = set(expected["summary"].loc[expected["summary"]["start_month"] == "2016-03", "case_id"])
    rep = reports["followup"][reports["followup"]["case_id"].isin(march)]
    bar = pattern_fig(at)["data"][0]
    assert dict(zip(bar["y"], bar["x"])) == rep["pattern"].value_counts().to_dict()
    pattern = rep["pattern"].value_counts().index[0]
    click_pattern(at, monkeypatch, pattern, rep["pattern"].unique())
    assert set(case_table(at)["Application"]) == set(rep.loc[rep["pattern"] == pattern, "case_id"])


def test_stale_page_selection_cleared_when_global_filter_removes_it(at, monkeypatch):
    click_pattern(at, monkeypatch, m.PATTERN_OTHER)
    at.multiselect(key=FILTER_KEYS["final_statuses"]).set_value([PENDING]).run()
    assert sel(at, "pattern") is None
    assert any("not in the current filtered data and was cleared" in i.value for i in at.info)


def test_page_clicks_do_not_change_global_filters(at, monkeypatch):
    click_pattern(at, monkeypatch, m.PATTERN_REPLACEMENT_OFFER)
    click_offer_status(at, monkeypatch, CANCELLED, [PENDING, CANCELLED, DENIED, config.NO_FINAL_STATUS_LABEL])
    assert at.session_state[FILTER_KEYS["final_statuses"]] in ([], None)
    at.switch_page("views/executive.py").run()
    assert kpi(at, "Total applications") == "31,509"


# =========================================================================== #
# Wording and protection
# =========================================================================== #
def test_cancellation_wording(at, monkeypatch):
    click_pattern(at, monkeypatch, m.PATTERN_REPLACEMENT_OFFER)
    import json

    text = "\n".join([captions(at), *(x.value for x in at.markdown), *(x.value for x in at.info),
                      *(x.value for x in at.warning), *(h.value for h in at.subheader), json.dumps(figures(at))])
    assert "Approved" not in text and "Funded" not in text
    assert "the system account" not in text.lower()
    for match in re.finditer(r"recover\w*", text, flags=re.IGNORECASE):
        assert "not" in text[max(0, match.start() - 40) : match.start()].lower(), text[match.start() - 60 : match.end()]
    for match in re.finditer(r"system account", text):
        assert "likely" in text[max(0, match.start() - 40) : match.start()].lower()


def test_protected_files_unchanged(fingerprint_before):
    after = _fingerprint(config.PROTECTED_DIRS)
    assert after.keys() == fingerprint_before.keys()
    assert [k for k in after if after[k] != fingerprint_before[k]] == []


def test_offer_first_kpi_states_all_ended_cancelled(at):
    assert "Final status of all of them: A_Cancelled 576" in captions(at)
