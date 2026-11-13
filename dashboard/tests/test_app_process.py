"""Phase 5d: Process Analysis interactions on the real app and real data.

Clicks are simulated as in test_app_executive (real page callbacks against the
live AppTest session state). Expected values come from the approved core metrics
and drill-down functions.
"""

from __future__ import annotations

import json
import re

import numpy as np
import pytest
from streamlit.testing.v1 import AppTest

from core import config, definitions, drilldown, loaders
from core import metrics as m
from tests.test_app_executive import _State, captions, figures, kpi
from tests.test_read_only import _fingerprint
from ui import aggregations as ag
from ui import selection, theme
from ui.filters import FILTER_KEYS, FilterState, apply_filters
from ui.formatting import fmt_hours

pytestmark = [pytest.mark.integration, pytest.mark.ui]

APP = str(config.DASHBOARD_DIR / "app.py")
PAGE = "views/process.py"
TIMER_PAIR = ("W_Call after offers", "A_Cancelled")
PLAIN_PAIR = ("A_Create Application", "A_Submitted")
CANCELLED = config.FINAL_STATUS_LABELS["A_Cancelled"]


@pytest.fixture(scope="module")
def fingerprint_before():
    return _fingerprint(config.PROTECTED_DIRS)


@pytest.fixture(scope="module")
def expected():
    ev = m.prepare_events(loaders.load_event_log(m.METRIC_EVENT_COLUMNS))
    s = m.application_summary(ev, dataset_end=definitions.DATASET_END)
    return {"events": ev, "summary": s, "all_pairs": m.transition_metrics(ev, min_count=1),
            "frequent": m.transition_metrics(ev), "freq_all": m.activity_frequency(ev),
            "freq_completed": m.activity_frequency(ev, lifecycle=["complete"])}


@pytest.fixture
def at(fingerprint_before):
    app = AppTest.from_file(APP, default_timeout=600).run()
    app.switch_page(PAGE).run()
    assert not app.exception, [e.value for e in app.exception]
    return app


def sel(at, name):
    key = selection.SELECTION_KEYS[name]
    return at.session_state[key] if key in at.session_state else None


def _click(at, monkeypatch, chart_key, callback, customdata):
    state = _State(at)
    monkeypatch.setattr(selection, "_session", lambda: state)
    state[chart_key] = {"selection": {"points": [{"customdata": list(customdata)}], "point_indices": [0],
                                      "box": [], "lasso": []}}
    callback()
    del at.session_state[chart_key]
    at.run()
    assert not at.exception, [e.value for e in at.exception]


def _pair_text(at):
    pair = sel(at, "transition")
    return "->".join(pair) if pair else ""


def click_matrix(at, mp, pair, valid, measure="median_wait_hours"):
    key = f"proc_matrix::{_pair_text(at)}::{sel(at, 'activity') or ''}::{measure}"
    _click(at, mp, key, selection.page_selection_callback(key, "transition", valid, pair=True), [*pair, 0])


def click_ranked(at, mp, pair, valid):
    key = f"proc_ranked::{_pair_text(at)}"
    _click(at, mp, key, selection.page_selection_callback(key, "transition", valid, pair=True), [*pair, 0])


def click_activity(at, mp, activity, valid, basis="All lifecycle stages"):
    key = f"proc_freq::{sel(at, 'activity') or ''}::{basis}"
    _click(at, mp, key, selection.page_selection_callback(key, "activity", valid), [activity, 0])


def pairs_of(df):
    return list(zip(df["activity"], df["next_activity"]))


# ---- figures by content ------------------------------------------------------ #
def fig_where(at, predicate):
    found = [f for f in figures(at) if predicate(f)]
    assert len(found) == 1, f"expected one matching figure, found {len(found)}"
    return found[0]


def sankey_fig(at):
    return fig_where(at, lambda f: f["data"][0].get("type") == "sankey")


def matrix_fig(at):
    return fig_where(at, lambda f: f["data"][0].get("marker", {}).get("symbol") == "square")


def ranked_fig(at):
    return fig_where(at, lambda f: len(f["data"]) == 2 and f["data"][1].get("name") == "90th percentile")


def hist_fig(at):
    return fig_where(at, lambda f: "log-spaced" in str(f["layout"].get("xaxis", {}).get("title", "")))


def freq_fig(at):
    return fig_where(at, lambda f: any(t.get("name") == "Workflow events" for t in f["data"]))


def sankey_flows(fig):
    s = fig["data"][0]
    labels = s["node"]["label"]
    inflow, outflow = {}, {}
    for src, tgt, v in zip(s["link"]["source"], s["link"]["target"], s["link"]["value"]):
        outflow[labels[src]] = outflow.get(labels[src], 0) + v
        inflow[labels[tgt]] = inflow.get(labels[tgt], 0) + v
    return inflow, outflow


# =========================================================================== #
# Structure
# =========================================================================== #
def test_structure_and_hover_only_sankey(at):
    charts = at.get("plotly_chart")
    assert len(charts) == 4  # Sankey, matrix, ranked, activity frequency (drill-down appears on selection)
    assert [len(c.proto.selection_mode) > 0 for c in charts] == [False, True, True, True]
    text = captions(at)
    assert "Sankey diagrams cannot be clicked in Streamlit" in text
    infos = " ".join(i.value for i in at.info)
    assert "Exploratory: likely timer-driven steps" in infos and "Mixed event streams" in infos
    assert "How activities are counted" in infos


# =========================================================================== #
# P1 - Sankey reconciliation
# =========================================================================== #
def test_sankey_reconciles_to_all_applications(at, expected):
    inflow, outflow = sankey_flows(sankey_fig(at))
    assert outflow[ag.NODE_ALL] == 31_509
    for node in (ag.NODE_OFFER_SENT, ag.NODE_OFFER_RETURNED, ag.NODE_OFFER_NOT_RETURNED, ag.NODE_NO_OFFER_SENT):
        assert inflow[node] == outflow[node]  # conservation: nobody created or lost
    dist = m.final_status_distribution(expected["summary"]).set_index("label")["applications"]
    assert {k: inflow[k] for k in dist.index} == {k: int(v) for k, v in dist.items()}
    assert kpi(at, "Applications with at least one offer sent") == "31,362"
    assert kpi(at, "Applications with no offer sent") == "147"
    assert kpi(at, "Applications with at least one offer returned") == "21,768"
    assert kpi(at, "Applications with an offer sent but none returned") == "9,594"
    table = [d.value for d in at.dataframe if "Offer milestone" in d.value.columns][0]
    assert table["Applications"].sum() == 31_509
    assert "31,509 applications in the selection; every one follows exactly one path." in captions(at)


@pytest.mark.parametrize(
    "setup,state",
    [
        (lambda at: at.select_slider(key=FILTER_KEYS["months"]).set_range("2016-03", "2016-05"),
         FilterState("2016-03", "2016-05")),
        (lambda at: at.multiselect(key=FILTER_KEYS["final_statuses"]).set_value([CANCELLED]),
         FilterState(final_statuses=(CANCELLED,))),
        (lambda at: at.multiselect(key=FILTER_KEYS["loan_goals"]).set_value(["Car"]), FilterState(loan_goals=("Car",))),
    ],
)
def test_sankey_reconciles_to_filtered_population(at, expected, setup, state):
    setup(at).run()
    filtered = apply_filters(expected["summary"], state)
    inflow, outflow = sankey_flows(sankey_fig(at))
    assert outflow[ag.NODE_ALL] == len(filtered)
    assert sum(inflow.get(lbl, 0) for lbl in ag.SANKEY_NODES[5:]) == len(filtered)  # each application once


# =========================================================================== #
# P2 - matrix
# =========================================================================== #
def test_matrix_covers_every_pair_and_marks_timers(at, expected):
    fig = matrix_fig(at)
    cells = fig["data"][0]
    shown = [tuple(c[:2]) for c in cells["customdata"]]
    assert sorted(shown) == sorted(pairs_of(expected["all_pairs"])) and len(shown) == 178
    counts = {tuple(c[:2]): int(c[2]) for c in cells["customdata"]}
    assert counts == dict(zip(pairs_of(expected["all_pairs"]), expected["all_pairs"]["transition_count"].astype(int)))
    [timer] = [t for t in fig["data"] if t.get("name") == "Likely timer-driven (exploratory)"]
    flagged = expected["all_pairs"][expected["all_pairs"]["likely_timer_driven"].fillna(False)]
    assert len(timer["x"]) == len(flagged) == 3
    timer_text = {tuple(c[:2]): c[5] for c in cells["customdata"]}
    for pair in pairs_of(flagged):
        assert timer_text[pair] == "Likely timer-driven (exploratory) - not a staff wait"
    assert timer_text[PLAIN_PAIR] == ""
    stream_text = {tuple(c[:2]): c[6] for c in cells["customdata"]}
    crossing = expected["all_pairs"][expected["all_pairs"]["crosses_event_streams"]]
    assert sum(1 for t in stream_text.values() if t.startswith("Crosses")) == len(crossing) == 109
    assert "3 likely timer-driven pairs (exploratory)" in captions(at)


def test_matrix_measure_toggle(at, expected):
    assert matrix_fig(at)["data"][0]["marker"]["colorbar"]["title"]["text"] == "hours"
    at.segmented_control(key="proc_matrix_measure").set_value("Transition count").run()
    cells = matrix_fig(at)["data"][0]
    assert cells["marker"]["colorbar"]["title"]["text"] == "transitions"
    expected_colour = np.log10(np.clip(ag.transition_matrix(
        expected["all_pairs"], ag.activity_order(expected["freq_all"], extra=sorted(
            set(expected["all_pairs"]["activity"]) | set(expected["all_pairs"]["next_activity"]))))["transition_count"]
        .to_numpy(dtype=float), 1e-4, None))
    assert np.allclose(cells["marker"]["color"], expected_colour)


# =========================================================================== #
# P2 / P4 - selection and drill-down
# =========================================================================== #
def _assert_drilldown(at, events, pair, all_pairs):
    row = all_pairs[(all_pairs["activity"] == pair[0]) & (all_pairs["next_activity"] == pair[1])].iloc[0]
    assert kpi(at, "Transition count") == f"{int(row['transition_count']):,}"
    assert kpi(at, "Median wait between events") == fmt_hours(row["median_wait_hours"])
    assert kpi(at, "90th percentile wait between events") == fmt_hours(row["p90_wait_hours"])
    instances = drilldown.transition_instances(events, *pair)
    stats = drilldown.instance_statistics(instances)
    assert stats["transition_count"] == int(row["transition_count"])
    assert stats["median_wait_hours"] == row["median_wait_hours"] and stats["p90_wait_hours"] == row["p90_wait_hours"]
    hist = hist_fig(at)["data"][0]
    assert sum(hist["y"]) == int(row["transition_count"])  # every wait in exactly one band
    occ = [d.value for d in at.dataframe if "Wait (hours)" in d.value.columns][0]
    assert len(occ) == int(row["transition_count"])


def test_matrix_click_opens_drilldown_and_click_again_clears(at, monkeypatch, expected):
    valid = pairs_of(expected["all_pairs"])
    click_matrix(at, monkeypatch, TIMER_PAIR, valid)
    assert tuple(sel(at, "transition")) == TIMER_PAIR
    _assert_drilldown(at, expected["events"], TIMER_PAIR, expected["all_pairs"])
    text = captions(at)
    assert "⏱ Likely timer-driven (exploratory) - not a staff waiting time" in text
    assert "Crosses event streams (interleaving, not necessarily a hand-off)" in text
    ring = [t for t in matrix_fig(at)["data"] if t.get("marker", {}).get("size") == 28]
    assert len(ring) == 1  # selected cell ringed
    assert hist_fig(at)["data"][0]["marker"]["color"] == theme.neutral("light", soft=True)  # timer histogram greyed
    click_matrix(at, monkeypatch, TIMER_PAIR, valid)
    assert sel(at, "transition") is None
    assert len(at.get("plotly_chart")) == 4


def test_plain_pair_drilldown_labels(at, monkeypatch, expected):
    click_matrix(at, monkeypatch, PLAIN_PAIR, pairs_of(expected["all_pairs"]))
    _assert_drilldown(at, expected["events"], PLAIN_PAIR, expected["all_pairs"])
    assert "Wait between recorded events · Same event stream" in captions(at)


def test_invalid_matrix_click_is_ignored(at, monkeypatch, expected):
    click_matrix(at, monkeypatch, ("A_Nonexistent", "A_Other"), pairs_of(expected["all_pairs"]))
    assert sel(at, "transition") is None


def test_drilldown_clear_button(at, monkeypatch, expected):
    click_matrix(at, monkeypatch, PLAIN_PAIR, pairs_of(expected["all_pairs"]))
    [btn] = [b for b in at.button if b.key == "chip_sel_transition"]
    btn.click().run()
    assert sel(at, "transition") is None


# =========================================================================== #
# P3 - ranked waits
# =========================================================================== #
def test_ranked_waits_match_metrics_and_click(at, monkeypatch, expected):
    fig = ranked_fig(at)
    bar = fig["data"][0]
    top = ag.top_waits(expected["frequent"], 15)
    assert [tuple(c[:2]) for c in bar["customdata"]] == pairs_of(top)[::-1]
    assert np.allclose(bar["x"], top["median_wait_hours"].to_numpy()[::-1])
    timer_bars = [t for t in bar["text"] if t.startswith("⏱ likely timer-driven")]
    assert len(timer_bars) == int(top["likely_timer_driven"].fillna(False).sum()) == 2
    assert "not staff waiting times" in captions(at)
    click_ranked(at, monkeypatch, TIMER_PAIR, pairs_of(top))
    assert tuple(sel(at, "transition")) == TIMER_PAIR
    _assert_drilldown(at, expected["events"], TIMER_PAIR, expected["all_pairs"])
    # the same selection is reflected in the matrix and the ranked chart
    assert len([t for t in matrix_fig(at)["data"] if t.get("marker", {}).get("size") == 28]) == 1
    op = ranked_fig(at)["data"][0]["marker"]["opacity"]
    assert op.count(1.0) == 1


# =========================================================================== #
# P5 - activity frequency
# =========================================================================== #
def _freq_values(fig):
    out = {}
    for trace in fig["data"]:
        out.update(dict(zip(trace["y"], trace["x"])))
    return out


def test_activity_frequency_lifecycle_toggle(at, expected):
    assert _freq_values(freq_fig(at)) == dict(zip(expected["freq_all"]["activity"], expected["freq_all"]["total_events"]))
    assert _freq_values(freq_fig(at))["W_Validate application"] == 209_496
    assert "Activity events (all lifecycle stages)" in captions(at)
    at.segmented_control(key="proc_freq_basis").set_value("Completed only").run()
    shown = _freq_values(freq_fig(at))
    assert shown == dict(zip(expected["freq_completed"]["activity"], expected["freq_completed"]["total_events"]))
    assert shown["W_Validate application"] < 209_496
    assert "Activity events (completed only)" in captions(at)


def test_activity_click_highlights_matrix_and_clears(at, monkeypatch, expected):
    click_activity(at, monkeypatch, "A_Cancelled", expected["freq_all"]["activity"].tolist())
    assert sel(at, "activity") == "A_Cancelled"
    cells = matrix_fig(at)["data"][0]
    lit = [tuple(c[:2]) for c, o in zip(cells["customdata"], cells["marker"]["opacity"]) if o == 1.0]
    assert lit and all("A_Cancelled" in pair for pair in lit)
    expected_lit = [p for p in pairs_of(expected["all_pairs"]) if "A_Cancelled" in p]
    assert sorted(lit) == sorted(expected_lit)
    [chip] = [b for b in at.button if b.key == "chip_sel_activity"]
    chip.click().run()
    assert sel(at, "activity") is None


# =========================================================================== #
# Global filters and other pages' selections
# =========================================================================== #
def test_drilldown_under_global_filter(at, monkeypatch, expected):
    at.select_slider(key=FILTER_KEYS["months"]).set_range("2016-06", "2016-06").run()
    june = apply_filters(expected["summary"], FilterState("2016-06", "2016-06"))
    events = expected["events"][expected["events"][m.C].isin(june["case_id"])]
    pairs = m.transition_metrics(events, min_count=1)
    click_matrix(at, monkeypatch, TIMER_PAIR, pairs_of(pairs))
    _assert_drilldown(at, events, TIMER_PAIR, pairs)


def test_stale_pair_cleared_when_filter_removes_it(at, monkeypatch, expected):
    rare = expected["all_pairs"].sort_values("transition_count").iloc[0]
    pair = (rare["activity"], rare["next_activity"])
    cases = drilldown.transition_instances(expected["events"], *pair)["case_id"]
    months = set(expected["summary"].set_index("case_id").loc[cases, "start_month"])
    other = next(mm for mm in sorted(set(expected["summary"]["start_month"])) if mm not in months)
    click_matrix(at, monkeypatch, pair, pairs_of(expected["all_pairs"]))
    assert tuple(sel(at, "transition")) == pair
    at.select_slider(key=FILTER_KEYS["months"]).set_range(other, other).run()
    still_valid = pair in pairs_of(m.transition_metrics(expected["events"][expected["events"][m.C].isin(
        apply_filters(expected["summary"], FilterState(other, other))["case_id"])], min_count=1))
    assert not still_valid  # precondition: the rarest pair does not occur in the other month
    assert sel(at, "transition") is None
    assert any("not in the current filtered data and was cleared" in i.value for i in at.info)


def test_selections_on_other_pages_are_preserved(at, monkeypatch, expected):
    at.session_state[selection.SELECTION_KEYS["pattern"]] = m.PATTERN_WORKFLOW
    at.run()
    click_matrix(at, monkeypatch, TIMER_PAIR, pairs_of(expected["all_pairs"]))
    assert sel(at, "pattern") == m.PATTERN_WORKFLOW
    at.switch_page("views/cancellation.py").run()
    assert not at.exception and sel(at, "pattern") == m.PATTERN_WORKFLOW
    at.switch_page(PAGE).run()
    assert tuple(sel(at, "transition")) == TIMER_PAIR
    assert at.session_state[FILTER_KEYS["final_statuses"]] in ([], None)


def test_downloads_present(at, monkeypatch, expected):
    click_matrix(at, monkeypatch, PLAIN_PAIR, pairs_of(expected["all_pairs"]))
    assert len(at.get("download_button")) >= 3  # milestones, occurrences, activity frequency


# =========================================================================== #
# Wording and protection
# =========================================================================== #
def test_process_wording(at, monkeypatch, expected):
    click_matrix(at, monkeypatch, TIMER_PAIR, pairs_of(expected["all_pairs"]))
    text = "\n".join([captions(at), *(x.value for x in at.markdown), *(x.value for x in at.info),
                      *(x.value for x in at.warning), json.dumps(figures(at))])
    assert "Approved" not in text and "Funded" not in text
    assert "the system account" not in text.lower()
    for match in re.finditer(r"timer-driven", text):
        assert "likely" in text[max(0, match.start() - 25) : match.start()].lower()
    for match in re.finditer(r"system account", text):
        assert "likely" in text[max(0, match.start() - 40) : match.start()].lower()


def test_protected_files_unchanged(fingerprint_before):
    after = _fingerprint(config.PROTECTED_DIRS)
    assert after.keys() == fingerprint_before.keys()
    assert [k for k in after if after[k] != fingerprint_before[k]] == []
