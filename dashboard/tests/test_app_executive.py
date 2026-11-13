"""Phase 5b: Executive Overview interactions on the real app and real data.

AppTest cannot click a Plotly chart, so the tests invoke the page's real
``on_select`` callbacks (ui.selection factories) against the live AppTest session
state, exactly as Streamlit does after a click, then rerun the app. Chart contents
are read from the rendered figure JSON.
"""

from __future__ import annotations

import base64
import json

import numpy as np
import pytest
from streamlit.testing.v1 import AppTest

from core import config, definitions, loaders
from core import metrics as m
from tests.test_read_only import _fingerprint
from ui import selection, theme
from ui.filters import FILTER_KEYS, STATUS_LABEL_ORDER

pytestmark = [pytest.mark.integration, pytest.mark.ui]

APP = str(config.DASHBOARD_DIR / "app.py")
PENDING, CANCELLED, DENIED = (config.FINAL_STATUS_LABELS[s] for s in config.FINAL_STATUS_ACTIVITIES)
CENSORED = {"2016-10", "2016-11", "2016-12"}


class _State:
    """Adapter giving AppTest's session state the mapping interface ui.selection uses."""

    def __init__(self, at):
        self.at = at

    def get(self, key, default=None):
        return self.at.session_state[key] if key in self.at.session_state else default

    def __getitem__(self, key):
        return self.at.session_state[key]

    def __setitem__(self, key, value):
        self.at.session_state[key] = value

    def __contains__(self, key):
        return key in self.at.session_state


def click(at, monkeypatch, chart_key, callback, *customdata):
    """Simulate a Plotly point click: store the selection payload, run the callback, rerun."""
    state = _State(at)
    monkeypatch.setattr(selection, "_session", lambda: state)
    state[chart_key] = {"selection": {"points": [{"customdata": list(customdata)}], "point_indices": [0],
                                      "box": [], "lasso": []}}
    callback()
    del at.session_state[chart_key]  # the old chart key is not rendered again
    at.run()
    assert not at.exception, [e.value for e in at.exception]


@pytest.fixture(scope="module")
def fingerprint_before():
    return _fingerprint(config.PROTECTED_DIRS)


@pytest.fixture(scope="module")
def expected():
    ev = m.prepare_events(loaders.load_event_log(m.METRIC_EVENT_COLUMNS))
    s = m.application_summary(ev, dataset_end=definitions.DATASET_END)
    return {"summary": s, "dist": m.final_status_distribution(s),
            "trend": m.monthly_trend(s, dataset_end=definitions.DATASET_END)}


@pytest.fixture
def at(fingerprint_before):
    app = AppTest.from_file(APP, default_timeout=600).run()
    assert not app.exception, [e.value for e in app.exception]
    return app


def _decode(obj):
    """Decode Plotly typed arrays ({"dtype", "bdata", "shape"}) in figure JSON to lists."""
    if isinstance(obj, dict):
        if "bdata" in obj and "dtype" in obj:
            arr = np.frombuffer(base64.b64decode(obj["bdata"]), dtype=np.dtype(obj["dtype"]))
            if "shape" in obj:
                arr = arr.reshape([int(s) for s in str(obj["shape"]).split(",")])
            return arr.tolist()
        return {k: _decode(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_decode(v) for v in obj]
    return obj


def figures(at) -> list[dict]:
    return [_decode(json.loads(c.proto.spec)) for c in at.get("plotly_chart")]


def figure(at, index: int) -> dict:
    return figures(at)[index]


def kpi(at, label: str) -> str:
    return {x.label: x.value for x in at.metric}[label]


def captions(at) -> str:
    return "\n".join(c.value for c in at.caption)


def status_key(statuses=()) -> str:
    return "exec_status_chart::" + "|".join(statuses)


def month_key(rng=None) -> str:
    return "exec_monthly_chart::" + (f"{rng[0]}..{rng[1]}" if rng else "all")


# --------------------------------------------------------------------------- #
# Structure and totals
# --------------------------------------------------------------------------- #
def test_three_charts_two_clickable(at):
    charts = at.get("plotly_chart")
    assert len(charts) == 3
    assert [len(c.proto.selection_mode) > 0 for c in charts] == [True, True, False]


def test_status_chart_totals_match_metrics(at, expected):
    bar = figure(at, 0)["data"][0]
    shown = dict(zip(bar["y"], bar["x"]))
    dist = expected["dist"].set_index("label")["applications"]
    assert shown == {k: int(v) for k, v in dist.items()}
    assert sum(bar["x"]) == len(expected["summary"]) == 31_509
    assert [c[0] for c in bar["customdata"]] == list(STATUS_LABEL_ORDER)[::-1]


def test_monthly_chart_totals_match_metrics(at, expected):
    fig = figure(at, 1)
    trend = expected["trend"]
    assert [t["name"] for t in fig["data"]] == list(STATUS_LABEL_ORDER)
    per_month = np.sum([t["y"] for t in fig["data"]], axis=0)
    assert per_month.tolist() == trend["applications"].tolist()
    assert per_month.sum() == 31_509
    for trace, col in zip(fig["data"], [*config.FINAL_STATUS_ACTIVITIES, "no_final_status"]):
        assert trace["y"] == trend[col].tolist()


def test_censored_months_are_visually_distinguished(at, expected):
    fig = figure(at, 1)
    months = expected["trend"]["start_month"].tolist()
    opacity = dict(zip(months, fig["data"][0]["marker"]["opacity"]))
    assert {mm for mm, o in opacity.items() if o == theme.CENSORED_OPACITY} == CENSORED
    assert all(o == 1.0 for mm, o in opacity.items() if mm not in CENSORED)
    assert any("data ends 1 Feb 2017" in a["text"] for a in fig["layout"]["annotations"])
    band = fig["layout"]["shapes"][0]
    assert (band["x0"], band["x1"]) == (months.index("2016-10") - 0.5, months.index("2016-12") + 0.5)
    text = captions(at)
    assert "Incomplete months: Oct 2016, Nov 2016, Dec 2016" in text
    duration = figure(at, 2)
    assert sum(1 for t in duration["data"] if t.get("line", {}).get("dash") == "dash") == 2
    assert "Dashed lines and hollow markers mark incomplete months (Oct 2016, Nov 2016, Dec 2016)" in text
    assert "not evidence of faster processing" in text
    assert any("Data ends on 1 February 2017" in w.value for w in at.warning)


def test_interaction_and_legend_notes(at):
    text = captions(at)
    assert "Click a bar to filter every page by that final status; click it again to clear." in text
    assert "Click a month to filter every page to that start month" in text
    assert text.count("Clicking a legend entry only hides that series") == 2  # monthly volume + duration


def test_counts_and_share_toggle(at):
    assert figure(at, 1)["layout"].get("barnorm") is None
    at.segmented_control(key="exec_monthly_view").set_value("100% share").run()
    fig = figure(at, 1)
    assert fig["layout"]["barnorm"] == "percent"
    assert np.sum([t["y"] for t in fig["data"]]) == 31_509  # same counts, normalised by Plotly


# --------------------------------------------------------------------------- #
# Status selection
# --------------------------------------------------------------------------- #
def test_status_click_sets_global_filter_and_click_again_clears(at, monkeypatch):
    click(at, monkeypatch, status_key(), selection.status_filter_callback(status_key(), STATUS_LABEL_ORDER),
          CANCELLED, "A_Cancelled")
    assert at.session_state[FILTER_KEYS["final_statuses"]] == [CANCELLED]
    assert at.multiselect(key=FILTER_KEYS["final_statuses"]).value == [CANCELLED]  # sidebar shows it
    assert kpi(at, "Total applications") == "10,431"
    bar = figure(at, 0)["data"][0]
    assert sum(bar["x"]) == 31_509  # status chart ignores its own filter
    opacity = dict(zip(bar["y"], bar["marker"]["opacity"]))
    assert opacity[CANCELLED] == 1 and opacity[PENDING] == theme.DIMMED_OPACITY
    assert any(b.label == f"Final status: {CANCELLED}" for b in at.button)

    key = status_key([CANCELLED])
    click(at, monkeypatch, key, selection.status_filter_callback(key, STATUS_LABEL_ORDER), CANCELLED, "A_Cancelled")
    assert at.session_state[FILTER_KEYS["final_statuses"]] == []
    assert kpi(at, "Total applications") == "31,509"
    assert not any(b.label.startswith("Final status:") for b in at.button)


def test_status_click_switches_selection(at, monkeypatch):
    click(at, monkeypatch, status_key(), selection.status_filter_callback(status_key(), STATUS_LABEL_ORDER), PENDING)
    key = status_key([PENDING])
    click(at, monkeypatch, key, selection.status_filter_callback(key, STATUS_LABEL_ORDER), DENIED)
    assert at.session_state[FILTER_KEYS["final_statuses"]] == [DENIED]
    assert kpi(at, "Total applications") == "3,752"


def test_status_click_replaces_a_multi_status_sidebar_filter(at, monkeypatch):
    at.multiselect(key=FILTER_KEYS["final_statuses"]).set_value([PENDING, CANCELLED]).run()
    assert kpi(at, "Total applications") == f"{17_228 + 10_431:,}"
    key = status_key([PENDING, CANCELLED])
    click(at, monkeypatch, key, selection.status_filter_callback(key, STATUS_LABEL_ORDER), PENDING)
    assert at.session_state[FILTER_KEYS["final_statuses"]] == [PENDING]
    assert kpi(at, "Total applications") == "17,228"


def test_invalid_status_click_is_ignored(at, monkeypatch):
    click(at, monkeypatch, status_key(), selection.status_filter_callback(status_key(), STATUS_LABEL_ORDER), "Approved")
    assert at.session_state[FILTER_KEYS["final_statuses"]] in ([], None)
    assert kpi(at, "Total applications") == "31,509"


# --------------------------------------------------------------------------- #
# Month selection
# --------------------------------------------------------------------------- #
def test_month_click_sets_global_filter_and_click_again_clears(at, monkeypatch, expected):
    months = tuple(expected["trend"]["start_month"])
    may = int(expected["trend"].set_index("start_month").loc["2016-05", "applications"])
    click(at, monkeypatch, month_key(), selection.month_filter_callback(month_key(), months), "2016-05", "May 2016")
    assert tuple(at.session_state[FILTER_KEYS["months"]]) == ("2016-05", "2016-05")
    assert tuple(at.select_slider(key=FILTER_KEYS["months"]).value) == ("2016-05", "2016-05")  # sidebar shows it
    assert kpi(at, "Total applications") == f"{may:,}"
    fig = figure(at, 1)
    assert len(fig["data"][0]["y"]) == len(months)  # monthly chart still shows every month
    opacity = dict(zip(months, fig["data"][0]["marker"]["opacity"]))
    assert opacity["2016-05"] == 1.0 and opacity["2016-04"] == theme.DIMMED_OPACITY
    assert any(b.label == "Start month: May 2016" for b in at.button)
    assert not any("Data ends on 1 February 2017" in w.value for w in at.warning)  # May is fully observed

    key = month_key(("2016-05", "2016-05"))
    click(at, monkeypatch, key, selection.month_filter_callback(key, months), "2016-05")
    assert tuple(at.session_state[FILTER_KEYS["months"]]) == (months[0], months[-1])
    assert kpi(at, "Total applications") == "31,509"


def test_censored_month_click_shows_censoring_warning(at, monkeypatch, expected):
    months = tuple(expected["trend"]["start_month"])
    click(at, monkeypatch, month_key(), selection.month_filter_callback(month_key(), months), "2016-12")
    warnings = " ".join(w.value for w in at.warning)
    assert "Data ends on 1 February 2017" in warnings and "Dec 2016" in warnings
    assert kpi(at, "No final status recorded") == "86"


# --------------------------------------------------------------------------- #
# Combining and clearing filters
# --------------------------------------------------------------------------- #
def test_status_and_month_combine_and_clear_independently(at, monkeypatch, expected):
    months = tuple(expected["trend"]["start_month"])
    click(at, monkeypatch, status_key(), selection.status_filter_callback(status_key(), STATUS_LABEL_ORDER), CANCELLED)
    key = month_key()
    click(at, monkeypatch, key, selection.month_filter_callback(key, months), "2016-03")
    march_cancelled = int(expected["trend"].set_index("start_month").loc["2016-03", "A_Cancelled"])
    assert kpi(at, "Total applications") == f"{march_cancelled:,}"
    # Monthly chart honours the status filter but not its own month filter
    fig = figure(at, 1)
    assert np.sum([t["y"] for t in fig["data"]]) == 10_431
    # Status chart honours the month filter but not its own status filter
    bar = figure(at, 0)["data"][0]
    assert sum(bar["x"]) == int(expected["trend"].set_index("start_month").loc["2016-03", "applications"])

    [chip] = [b for b in at.button if b.label.startswith("Final status:")]
    chip.click().run()
    assert at.session_state[FILTER_KEYS["final_statuses"]] == []
    assert tuple(at.session_state[FILTER_KEYS["months"]]) == ("2016-03", "2016-03")  # month kept
    [chip] = [b for b in at.button if b.label.startswith("Start month:")]
    chip.click().run()
    assert tuple(at.session_state[FILTER_KEYS["months"]]) == (months[0], months[-1])
    assert kpi(at, "Total applications") == "31,509"
    assert not any(b.label.startswith(("Final status:", "Start month:")) for b in at.button)


def test_sidebar_change_after_chart_click_is_respected(at, monkeypatch):
    click(at, monkeypatch, status_key(), selection.status_filter_callback(status_key(), STATUS_LABEL_ORDER), DENIED)
    at.multiselect(key=FILTER_KEYS["final_statuses"]).set_value([PENDING]).run()
    assert kpi(at, "Total applications") == "17,228"
    bar = figure(at, 0)["data"][0]
    assert dict(zip(bar["y"], bar["marker"]["opacity"]))[PENDING] == 1  # highlight follows the sidebar


def test_chart_filter_carries_to_other_pages(at, monkeypatch):
    click(at, monkeypatch, status_key(), selection.status_filter_callback(status_key(), STATUS_LABEL_ORDER), CANCELLED)
    at.switch_page("views/cancellation.py").run()
    assert not at.exception
    assert {x.label: x.value for x in at.metric}["Application cancellations (A_Cancelled events)"] == "10,431"
    at.switch_page("views/explorer.py").run()
    assert at.dataframe[0].value.shape[0] == 10_431


def test_reset_button_clears_chart_set_filters(at, monkeypatch, expected):
    months = tuple(expected["trend"]["start_month"])
    click(at, monkeypatch, status_key(), selection.status_filter_callback(status_key(), STATUS_LABEL_ORDER), PENDING)
    key = month_key()
    click(at, monkeypatch, key, selection.month_filter_callback(key, months), "2016-07")
    [reset] = [b for b in at.button if b.label == "Reset filters"]
    reset.click().run()
    assert kpi(at, "Total applications") == "31,509"


# --------------------------------------------------------------------------- #
# Wording and protection
# --------------------------------------------------------------------------- #
def test_executive_wording(at):
    text = "\n".join([captions(at), *(x.value for x in at.markdown), *(x.value for x in at.info),
                      *(x.value for x in at.warning), json.dumps(figures(at))])
    for word in ("Approved", "Funded"):
        idx = text.find(word)
        while idx != -1:
            assert "not labelled" in text[max(0, idx - 60) : idx], word
            idx = text.find(word, idx + 1)
    assert "A_Pending (offer accepted)" in text


def test_protected_files_unchanged(fingerprint_before):
    after = _fingerprint(config.PROTECTED_DIRS)
    assert after.keys() == fingerprint_before.keys()
    assert [k for k in after if after[k] != fingerprint_before[k]] == []
