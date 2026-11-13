"""Unit tests for ui.charts (figure structure, encodings, interactions, wording)."""

from __future__ import annotations

import inspect
import re

import numpy as np
import pandas as pd
import pytest

from core import config, definitions
from core import metrics as m
from ui import aggregations as ag
from ui import charts as ch
from ui import theme
from ui.filters import STATUS_LABEL_ORDER

MODES = ("light", "dark")
PENDING, CANCELLED, DENIED = (config.FINAL_STATUS_LABELS[s] for s in config.FINAL_STATUS_ACTIVITIES)


# --------------------------------------------------------------------------- #
# Fixtures (shapes match the core outputs)
# --------------------------------------------------------------------------- #
@pytest.fixture
def dist():
    return pd.DataFrame({"final_status": ["A_Pending", "A_Cancelled", "A_Denied", None],
                         "label": list(STATUS_LABEL_ORDER), "applications": [17228, 10431, 3752, 98],
                         "percentage": [54.68, 33.10, 11.91, 0.31]})


@pytest.fixture
def trend():
    months = ["2016-09", "2016-10", "2016-11", "2016-12"]
    return pd.DataFrame({
        "start_month": months, "applications": [100, 90, 80, 70], "A_Pending": [50, 45, 40, 30],
        "A_Cancelled": [30, 30, 25, 20], "A_Denied": [20, 14, 14, 10], "no_final_status": [0, 1, 1, 10],
        "median_duration_days": [21.0, 18.9, 18.8, 17.2], "p90_duration_days": [34.8, 34.7, 34.6, 32.6],
        "days_observed_after_month_end": [123.6, 92.6, 62.6, 31.6],
        "has_open_applications": [False, True, True, True], "short_observation_window": [False, False, False, True],
        "censoring_warning": [False, True, True, True],
    })


@pytest.fixture
def transitions():
    return pd.DataFrame({
        "activity": ["W_Call after offers", "A_Create Application", "O_Sent (mail and online)"],
        "next_activity": ["A_Cancelled", "W_Handle leads", "O_Returned"],
        "transition_count": [8539, 200, 150], "average_wait_hours": [555.1, 0.2, 60.0],
        "median_wait_hours": [640.9, 0.1, 66.1], "p90_wait_hours": [648.5, 0.4, 140.9],
        "crosses_event_streams": [True, True, False], "target_system_share": [0.86, 0.0, 0.0],
        "target_batch_window_share": [0.87, 0.1, 0.1],
        "likely_timer_driven": pd.array([True, False, False], dtype="boolean"),
        "interpretation": ["x", "y", "z"],
    })


@pytest.fixture
def followup():
    return pd.DataFrame({
        "case_id": [f"App_{i}" for i in range(10)],
        "gap_days": [0.0, 0.1, 30.5, 30.7, 31.0, 5.0, 30.6, 0.0, 29.0, 30.2],
        "events_between": [0, 1, 10, 12, 3, 8, 5, 1, 9, 2],
        "pattern": [m.PATTERN_NO_ACTIVITY, m.PATTERN_OTHER, m.PATTERN_REPLACEMENT_OFFER, m.PATTERN_REPLACEMENT_OFFER,
                    m.PATTERN_WORKFLOW, m.PATTERN_OFFER_SENT, m.PATTERN_WORKFLOW, m.PATTERN_NO_ACTIVITY,
                    m.PATTERN_OFFER_SENT, m.PATTERN_REPLACEMENT_OFFER],
        "new_offer": False, "offer_sent": False, "workflow_activity": False, "returned_offer": False,
    })


def customdata(trace, col=0):
    return [row[col] for row in trace.customdata]


def no_secondary_axis(fig):
    layout = fig.to_plotly_json()["layout"]
    assert not any(re.fullmatch(r"yaxis\d+", k) for k in layout), "dual y-axis is not allowed"


# --------------------------------------------------------------------------- #
# Interaction registry
# --------------------------------------------------------------------------- #
def test_every_chart_builder_documents_its_interaction():
    builders = {name for name, fn in inspect.getmembers(ch, inspect.isfunction)
                if fn.__module__ == ch.__name__ and not name.startswith("_")}
    documented = set(ch.INTERACTIONS)
    expected_map = {"status_bars", "monthly_status_bars", "monthly_duration_lines", "milestone_sankey", "transition_matrix",
                    "ranked_waits", "wait_histogram", "activity_frequency_bars", "sequence_bars", "expiry_histogram",
                    "pattern_bars", "gap_strip", "pattern_composition_bars", "application_timeline"}
    assert expected_map <= builders
    assert len(documented) >= len(expected_map)


@pytest.mark.parametrize("name", ["milestone_sankey", "wait_histogram", "monthly_duration", "expiry_histogram", "pattern_composition"])
def test_hover_only_charts_say_so(name):
    assert ch.INTERACTIONS[name].kind == ch.HOVER


def test_sankey_note_explains_click_is_not_supported():
    assert "cannot be clicked" in ch.INTERACTIONS["milestone_sankey"].note


# --------------------------------------------------------------------------- #
# Executive
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("mode", MODES)
def test_status_bars(dist, mode):
    fig = ch.status_bars(dist, mode)
    bar = fig.data[0]
    assert bar.type == "bar" and list(bar.y) == list(STATUS_LABEL_ORDER)[::-1]
    assert customdata(bar) == list(STATUS_LABEL_ORDER)[::-1]
    assert list(bar.marker.color) == [theme.status_color(s, mode) for s in (None, "A_Denied", "A_Cancelled", "A_Pending")]
    assert bar.text[-1] == "17,228  (54.7%)"
    assert fig.layout.clickmode == "event+select" and bar.unselected.marker.opacity == 1
    no_secondary_axis(fig)


def test_status_bars_highlight_selected_and_colour_follows_entity(dist):
    fig = ch.status_bars(dist, selected=[CANCELLED])
    op = dict(zip(fig.data[0].y, fig.data[0].marker.opacity))
    assert op[CANCELLED] == 1 and op[PENDING] == theme.DIMMED_OPACITY
    partial = ch.status_bars(dist.iloc[[1]])
    assert partial.data[0].marker.color[0] == theme.status_color("A_Cancelled")


@pytest.mark.parametrize("mode", MODES)
def test_monthly_status_bars_mark_censored_months(trend, mode):
    fig = ch.monthly_status_bars(trend, mode)
    assert [t.name for t in fig.data] == list(STATUS_LABEL_ORDER)
    assert fig.layout.barmode == "stack"
    first = fig.data[0]
    assert list(first.marker.opacity) == [1.0, theme.CENSORED_OPACITY, theme.CENSORED_OPACITY, theme.CENSORED_OPACITY]
    assert customdata(first) == trend["start_month"].tolist()
    assert any("data ends 1 Feb 2017" in a.text for a in fig.layout.annotations)
    assert len(fig.layout.shapes) == 1 and fig.layout.shapes[0].x0 == 0.5  # band starts at first censored month
    assert "Incomplete: data ends 1 Feb 2017" in first.customdata[3][5]
    assert list(fig.layout.xaxis.ticktext) == ["Sep 2016", "Oct 2016", "Nov 2016", "Dec 2016"]
    no_secondary_axis(fig)


def test_monthly_status_bars_normalised_and_selected(trend):
    fig = ch.monthly_status_bars(trend, normalised=True, selected_month="2016-09")
    assert fig.layout.barnorm == "percent" and "Share" in fig.layout.yaxis.title.text
    op = list(fig.data[0].marker.opacity)
    assert op[0] == 1.0 and op[1] == pytest.approx(theme.CENSORED_OPACITY * theme.DIMMED_OPACITY)


def test_monthly_status_bars_without_censoring(trend):
    fig = ch.monthly_status_bars(trend.assign(censoring_warning=False))
    assert not fig.layout.shapes and not fig.layout.annotations


@pytest.mark.parametrize("mode", MODES)
def test_monthly_duration_lines(trend, mode):
    fig = ch.monthly_duration_lines(trend, mode)
    names = [t.name for t in fig.data if t.showlegend is not False]
    assert names == [definitions.METRICS["median_duration_days"].name, definitions.METRICS["p90_duration_days"].name]
    dashed = [t for t in fig.data if t.line.dash == "dash"]
    assert len(dashed) == 2  # one incomplete segment per series
    solid = fig.data[0]
    assert np.isnan(solid.y[1]) and not np.isnan(solid.y[0])  # censored months not drawn solid
    assert dashed[0].y[0] == trend["median_duration_days"].iloc[0]  # dashed segment joins last complete month
    no_secondary_axis(fig)


# --------------------------------------------------------------------------- #
# Process
# --------------------------------------------------------------------------- #
def _sankey_summary():
    return pd.DataFrame({"case_id": list("abcd"), "offers_sent": [1, 1, 0, 2], "offers_returned": [1, 0, 0, 1],
                         "final_status_label": [PENDING, CANCELLED, CANCELLED, None or config.NO_FINAL_STATUS_LABEL]})


@pytest.mark.parametrize("mode", MODES)
def test_milestone_sankey(mode):
    data = ag.milestone_sankey(_sankey_summary())
    fig = ch.milestone_sankey(data, mode)
    s = fig.data[0]
    assert s.type == "sankey" and list(s.node.label) == list(ag.SANKEY_NODES)
    colors = dict(zip(s.node.label, s.node.color))
    assert colors[PENDING] == theme.status_color("A_Pending", mode)
    assert colors[ag.NODE_ALL] == theme.neutral(mode)
    assert sum(v for src, v in zip(s.link.source, s.link.value) if src == 0) == 4
    assert "applications" in s.link.hovertemplate
    assert fig.layout.clickmode is None  # not selectable


@pytest.mark.parametrize("mode", MODES)
def test_transition_matrix(transitions, mode):
    order = ["A_Cancelled", "A_Create Application", "O_Sent (mail and online)", "O_Returned", "W_Call after offers", "W_Handle leads"]
    cells = ag.transition_matrix(transitions, order)
    fig = ch.transition_matrix(cells, order, mode, selected_pair=("W_Call after offers", "A_Cancelled"))
    main = fig.data[0]
    assert main.type == "scatter" and main.marker.symbol == "square"  # clickable, unlike go.Heatmap
    assert [tuple(r[:2]) for r in main.customdata] == list(zip(cells["activity"], cells["next_activity"]))
    timer = [t for t in fig.data if t.name == "Likely timer-driven (exploratory)"]
    assert len(timer) == 1 and list(timer[0].text) == ["⏱"]
    assert "not a staff wait" in main.customdata[0][5]
    assert len(fig.data) == 3  # cells, timer overlay, selection ring
    assert list(fig.layout.xaxis.ticktext) == order
    no_secondary_axis(fig)


def test_transition_matrix_value_modes_and_highlight(transitions):
    order = sorted(set(transitions["activity"]) | set(transitions["next_activity"]))
    cells = ag.transition_matrix(transitions, order)
    count = ch.transition_matrix(cells, order, value="transition_count", highlight_activity="A_Cancelled")
    op = list(count.data[0].marker.opacity)
    assert op.count(1.0) == 1 and theme.DIMMED_OPACITY in op
    with pytest.raises(ValueError):
        ch.transition_matrix(cells, order, value="mean")
    no_timer = ch.transition_matrix(ag.transition_matrix(transitions.iloc[1:], order), order)
    assert len(no_timer.data) == 1 and not no_timer.layout.showlegend


def test_ranked_waits(transitions):
    fig = ch.ranked_waits(ag.top_waits(transitions), selected_pair=("A_Create Application", "W_Handle leads"))
    bar, p90 = fig.data
    assert bar.y[-1] == "W_Call after offers → A_Cancelled"  # longest on top
    assert bar.marker.color[-1] == theme.neutral("light", soft=True)  # timer-driven bar greyed
    assert bar.text[-1].startswith("⏱ likely timer-driven")
    assert p90.name == "90th percentile" and p90.hoverinfo == "skip"
    assert tuple(bar.customdata[0][:2]) == ("A_Create Application", "W_Handle leads")
    assert list(bar.marker.opacity).count(1.0) == 1
    no_secondary_axis(fig)


def test_wait_histogram_chart():
    bins = ag.wait_histogram(pd.Series([0.5, 1.0, 600.0, 640.0, 650.0]))
    fig = ch.wait_histogram(bins, timer_driven=True)
    assert list(fig.data[0].y) == bins["count"].tolist()
    assert fig.data[0].marker.color == theme.neutral("light", soft=True)
    assert "log-spaced" in fig.layout.xaxis.title.text


def test_activity_frequency_bars():
    freq = pd.DataFrame({"activity": ["W_Validate application", "O_Create Offer", "A_Create Application"],
                         "total_events": [209496, 42995, 31509], "applications": [21870, 31509, 31509]})
    fig = ch.activity_frequency_bars(freq, selected_activity="O_Create Offer")
    assert [t.name for t in fig.data] == ["Application events", "Offer events", "Workflow events"]
    assert fig.data[1].marker.color == theme.stream_color("Offer")
    assert list(fig.data[1].marker.opacity) == [1.0] and list(fig.data[0].marker.opacity) == [theme.DIMMED_OPACITY]
    assert customdata(fig.data[2]) == ["W_Validate application"]


# --------------------------------------------------------------------------- #
# Cancellation
# --------------------------------------------------------------------------- #
def test_sequence_bars():
    bd = pd.DataFrame({"sequence": [m.SEQ_OFFER_FIRST, m.SEQ_SAME_TIME, m.SEQ_APPLICATION_FIRST, m.SEQ_NO_OFFER_CANCEL],
                       "applications": [576, 0, 9855, 0], "percentage": [5.5, 0.0, 94.5, 0.0]})
    fig = ch.sequence_bars(bd, selected=m.SEQ_OFFER_FIRST)
    bar = fig.data[0]
    assert bar.y[-1] == m.SEQ_OFFER_FIRST and customdata(bar)[-1] == m.SEQ_OFFER_FIRST
    assert bar.marker.opacity[-1] == 1 and bar.marker.opacity[0] == theme.DIMMED_OPACITY


def test_expiry_histogram_is_exploratory():
    tbl = pd.DataFrame({"days_since_last_offer_sent": [30.5, 30.7, 5.0, np.nan, 12.0],
                        "likely_automatic_expiry": pd.array([True, True, False, False, pd.NA], dtype="boolean")})
    fig = ch.expiry_histogram(tbl)
    names = [t.name for t in fig.data]
    assert names == ["Likely automatic expiry (exploratory)", "Other application cancellations"]
    assert len(fig.data[0].x) == 2 and len(fig.data[1].x) == 2  # missing days excluded
    assert any("30 days" in a.text for a in fig.layout.annotations)
    assert ch.INTERACTIONS["expiry_histogram"].kind == ch.HOVER


def test_pattern_bars_order_and_selection():
    ps = pd.DataFrame({"pattern": sorted(m.PATTERN_PRECEDENCE), "applications": [126, 85, 33, 230, 102],
                       "share_percent": [21.88, 14.76, 5.73, 39.93, 17.71]})
    fig = ch.pattern_bars(ps, selected=m.PATTERN_WORKFLOW)
    bar = fig.data[0]
    assert list(bar.y) == list(m.PATTERN_PRECEDENCE)[::-1]  # report precedence, first on top
    assert customdata(bar) == list(bar.y)
    assert dict(zip(bar.y, bar.marker.opacity))[m.PATTERN_WORKFLOW] == 1
    assert "not recovery" in dict(zip(bar.y, customdata(bar, 1)))[m.PATTERN_REPLACEMENT_OFFER]


def test_gap_strip(followup):
    fig = ch.gap_strip(followup, selected_pattern=m.PATTERN_WORKFLOW, selected_application="App_4")
    points = [t for t in fig.data if t.type == "scatter" and t.name == "Applications"][0]
    assert customdata(points) == followup["case_id"].tolist()
    op = np.array(points.marker.opacity)
    assert (op[(followup["pattern"] == m.PATTERN_WORKFLOW).to_numpy()] == 0.85).all()
    boxes = [t for t in fig.data if t.type == "box"]
    assert len(boxes) == 5 and all(b.hoverinfo == "skip" for b in boxes)
    again = ch.gap_strip(followup)
    assert list(again.data[-1].y) == list(points.y)  # deterministic jitter


def test_pattern_composition_bars():
    comp = pd.DataFrame({"component": [c for _, c in ag.COMPOSITION_COMPONENTS], "applications": [0, 0, 102, 9],
                         "pattern_applications": 102})
    fig = ch.pattern_composition_bars(comp)
    assert fig.data[0].text[0] == "9 of 102" and fig.layout.xaxis.range[1] == pytest.approx(107.1)


# --------------------------------------------------------------------------- #
# Timeline
# --------------------------------------------------------------------------- #
def _timeline():
    t0 = pd.Timestamp("2016-08-03 15:57", tz="UTC")
    rows = [("A_Create Application", "Application", None), ("W_Handle leads", "Workflow", None),
            ("O_Create Offer", "Offer", "O1"), ("O_Sent (mail and online)", "Offer", "O1"), ("A_Pending", "Application", None)]
    return pd.DataFrame({
        "order": range(1, 6), "time": [t0 + pd.Timedelta(hours=h) for h in (0, 1, 2, 3, 50)],
        "activity": [r[0] for r in rows], "stream": [r[1] for r in rows], "lifecycle": "complete",
        "resource": "User_1", "offer_id": [None, None, None, "O1", None], "event_id": [f"E{i}" for i in range(5)],
        "offer": [r[2] for r in rows], "is_final_status": [False, False, False, False, True],
    })


def _lifelines(outcome_time=True):
    t0 = pd.Timestamp("2016-08-03 17:57", tz="UTC")
    return pd.DataFrame({"offer_id": ["O1"], "created_time": [t0],
                         "offer_outcome_time": [t0 + pd.Timedelta(hours=40) if outcome_time else pd.NaT],
                         "offer_outcome": ["O_Accepted" if outcome_time else "No outcome recorded"],
                         "sent_channel": ["mail and online"], "was_returned": [True]})


@pytest.mark.parametrize("mode", MODES)
def test_application_timeline(mode):
    fig = ch.application_timeline(_timeline(), _lifelines(), mode, selected_event="E2")
    lanes = {t.name: t for t in fig.data if t.name and t.name.endswith("events")}
    assert set(lanes) == {"Application events", "Offer events", "Workflow events"}
    assert lanes["Offer events"].marker.symbol == "diamond"
    assert lanes["Offer events"].marker.color == theme.stream_color("Offer", mode)
    assert customdata(lanes["Application events"]) == ["E0", "E4"]
    assert sum(len(t.x) for t in lanes.values()) == 5  # one marker per event
    assert "Resource (as recorded)" in lanes["Workflow events"].hovertemplate
    assert any("Final status: A_Pending" in a.text for a in fig.layout.annotations)
    assert not any("Data ends" in a.text for a in fig.layout.annotations)  # closed application
    assert list(fig.layout.yaxis.ticktext) == ["Application", "Offer", "Workflow"]
    no_secondary_axis(fig)


def test_application_timeline_open_application():
    tl = _timeline().assign(is_final_status=False)
    fig = ch.application_timeline(tl, _lifelines(outcome_time=False), is_open=True)
    assert any("Data ends 1 Feb 2017" in a.text for a in fig.layout.annotations)
    lifeline = [t for t in fig.data if t.mode == "lines"][0]
    assert lifeline.line.dash == "dot" and pd.Timestamp(lifeline.x[1]) == definitions.DATASET_END
    assert "No outcome recorded by the data end" in lifeline.hovertemplate
    assert "not an outcome date" in lifeline.hovertemplate


def test_timeline_offer_events_sit_on_their_lifeline():
    tl = _timeline()
    tl.loc[4, ["activity", "stream", "offer", "offer_id"]] = ["O_Created", "Offer", "O2", "O2"]
    lifelines = pd.concat([_lifelines(), _lifelines().assign(offer_id="O2")], ignore_index=True)
    fig = ch.application_timeline(tl.assign(is_final_status=False), lifelines)
    lines = [t for t in fig.data if t.mode == "lines"]
    assert len(lines) == 2 and lines[0].y[0] != lines[1].y[0]
    assert [t.showlegend for t in lines] == [True, False] and lines[0].name.startswith("Offer lifeline")
    offers = [t for t in fig.data if t.name == "Offer events"][0]
    y_by_offer = {line.hovertemplate.split("</b>")[0][3:]: line.y[0] for line in lines}
    assert list(offers.y) == [y_by_offer["O1"], y_by_offer["O1"], y_by_offer["O2"]]
    assert "Event ID" in offers.hovertemplate and "Click to highlight" in offers.hovertemplate


def test_timeline_final_status_uses_approved_label_and_selection_ring():
    fig = ch.application_timeline(_timeline(), _lifelines(), selected_event="E3")
    assert any(a.text == "Final status: A_Pending (offer accepted)" for a in fig.layout.annotations)
    ring = [t for t in fig.data if t.name == "Selected event"]
    offers = [t for t in fig.data if t.name == "Offer events"][0]
    assert len(ring) == 1 and list(ring[0].x) == [offers.x[1]] and list(ring[0].y) == [offers.y[1]]
    assert fig.layout.yaxis.fixedrange is True and fig.layout.dragmode == "zoom"


# --------------------------------------------------------------------------- #
# Wording in every figure
# --------------------------------------------------------------------------- #
def _all_figure_text(fig) -> str:
    return str(fig.to_plotly_json())


def test_figures_respect_terminology(dist, trend, transitions, followup):
    order = sorted(set(transitions["activity"]) | set(transitions["next_activity"]))
    figs = [
        ch.status_bars(dist), ch.monthly_status_bars(trend), ch.monthly_duration_lines(trend),
        ch.milestone_sankey(ag.milestone_sankey(_sankey_summary())),
        ch.transition_matrix(ag.transition_matrix(transitions, order), order), ch.ranked_waits(transitions),
        ch.pattern_bars(pd.DataFrame({"pattern": list(m.PATTERN_PRECEDENCE), "applications": [1] * 5, "share_percent": [20.0] * 5})),
        ch.gap_strip(followup), ch.application_timeline(_timeline(), _lifelines()),
    ]
    text = " ".join(_all_figure_text(f) for f in figs)
    assert "Approved" not in text and "Funded" not in text
    assert "the system account" not in text.lower()
    for match in re.finditer(r"recover\w*", text, flags=re.IGNORECASE):
        assert "not" in text[max(0, match.start() - 40) : match.start()].lower()
    for match in re.finditer(r"timer-driven", text):
        assert "likely" in text[max(0, match.start() - 20) : match.start()].lower()


def test_monthly_status_bars_selected_range(trend):
    fig = ch.monthly_status_bars(trend, selected_range=("2016-11", "2016-09"))  # reversed bounds accepted
    op = list(fig.data[0].marker.opacity)
    c, d = theme.CENSORED_OPACITY, theme.DIMMED_OPACITY
    assert op == [1.0, c, c, pytest.approx(c * d)]  # Sep-Nov selected, Dec dimmed and still censored
    same = ch.monthly_status_bars(trend, selected_month="2016-10")
    assert list(same.data[0].marker.opacity) == list(ch.monthly_status_bars(trend, selected_range=("2016-10", "2016-10")).data[0].marker.opacity)
