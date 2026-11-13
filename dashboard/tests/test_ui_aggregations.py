"""Unit and integration tests for ui.aggregations."""

from __future__ import annotations

import pandas as pd
import pytest

from core import config, definitions, loaders
from core import metrics as m
from ui import aggregations as ag
from ui.filters import STATUS_LABEL_ORDER, FilterState, apply_filters
from tests.test_metrics import ev, offer

PENDING, CANCELLED, DENIED = (config.FINAL_STATUS_LABELS[s] for s in config.FINAL_STATUS_ACTIVITIES)
OPEN = config.NO_FINAL_STATUS_LABEL


def _summary(rows):
    return pd.DataFrame(rows, columns=["case_id", "offers_sent", "offers_returned", "final_status_label"])


@pytest.fixture
def summary():
    return _summary([
        ("a", 1, 1, PENDING), ("b", 2, 1, PENDING), ("c", 1, 0, CANCELLED), ("d", 0, 0, CANCELLED),
        ("e", 1, 0, OPEN), ("f", 3, 2, DENIED), ("g", 0, 0, DENIED),
    ])


# --------------------------------------------------------------------------- #
# Milestones: each application exactly once
# --------------------------------------------------------------------------- #
def test_milestone_paths_one_row_per_application(summary):
    paths = ag.milestone_paths(summary)
    assert len(paths) == len(summary) and paths["case_id"].is_unique
    got = dict(zip(paths["case_id"], paths["offer_milestone"]))
    assert got == {"a": ag.NODE_OFFER_RETURNED, "b": ag.NODE_OFFER_RETURNED, "c": ag.NODE_OFFER_NOT_RETURNED,
                   "d": ag.NODE_NO_OFFER_SENT, "e": ag.NODE_OFFER_NOT_RETURNED, "f": ag.NODE_OFFER_RETURNED,
                   "g": ag.NODE_NO_OFFER_SENT}


def test_milestone_path_counts_partition_the_population(summary):
    counts = ag.milestone_path_counts(summary)
    assert counts["applications"].sum() == len(summary)
    assert list(counts["offer_milestone"].drop_duplicates()) == list(ag.OFFER_MILESTONES)


def _flows(sk):
    inflow = sk.links.groupby("target_label")["value"].sum()
    outflow = sk.links.groupby("source_label")["value"].sum()
    return inflow, outflow


def test_sankey_conserves_applications(summary):
    sk = ag.milestone_sankey(summary)
    inflow, outflow = _flows(sk)
    assert sk.total == len(summary) == outflow[ag.NODE_ALL]
    for node in (ag.NODE_OFFER_SENT, ag.NODE_OFFER_RETURNED, ag.NODE_OFFER_NOT_RETURNED, ag.NODE_NO_OFFER_SENT):
        assert inflow[node] == outflow[node], node  # nothing created or lost in the middle
    final = inflow.reindex(STATUS_LABEL_ORDER, fill_value=0)
    assert final.to_dict() == {PENDING: 2, CANCELLED: 2, DENIED: 2, OPEN: 1}
    assert final.sum() == len(summary)


def test_sankey_indices_and_shares(summary):
    sk = ag.milestone_sankey(summary)
    assert sk.nodes == ag.SANKEY_NODES
    assert all(sk.nodes[i] == lbl for i, lbl in zip(sk.links["source"], sk.links["source_label"]))
    assert all(sk.nodes[i] == lbl for i, lbl in zip(sk.links["target"], sk.links["target_label"]))
    shares = sk.links.groupby("source_label")["share_of_source"].sum()
    assert (shares.round(9) == 100).all()
    assert (sk.links["value"] > 0).all()  # zero links omitted


def test_every_milestone_node_has_a_registered_definition():
    for node, key in ag.MILESTONE_DEFINITION_KEYS.items():
        assert key in definitions.METRICS, node


def test_milestones_empty_and_missing_columns(summary):
    sk = ag.milestone_sankey(summary.iloc[0:0])
    assert sk.total == 0 and sk.links.empty
    with pytest.raises(KeyError):
        ag.milestone_paths(summary.drop(columns=["offers_returned"]))


# --------------------------------------------------------------------------- #
# Activities, matrix, top waits
# --------------------------------------------------------------------------- #
def test_activity_order_by_stream_then_volume():
    freq = pd.DataFrame({"activity": ["W_B", "O_A", "A_B", "A_A", "W_A", "X_Odd"], "total_events": [50, 10, 5, 9, 70, 1]})
    assert ag.activity_order(freq, extra=["O_New"]) == ["A_A", "A_B", "O_A", "O_New", "W_A", "W_B", "X_Odd"]
    assert ag.stream_of("O_Cancelled") == "Offer" and ag.stream_of("W_x") == "Workflow" and ag.stream_of("Z") == "Other"


def test_transition_matrix_positions():
    tr = pd.DataFrame({"activity": ["A_A", "O_A", "Q"], "next_activity": ["O_A", "A_A", "A_A"], "median_wait_hours": [1.0, 2.0, 3.0]})
    cells = ag.transition_matrix(tr, ["A_A", "O_A"])
    assert len(cells) == 2  # pair with unknown activity dropped
    assert cells[["row", "col"]].values.tolist() == [[0, 1], [1, 0]]


def test_top_waits_sorted_with_ties_by_name():
    tr = pd.DataFrame({"activity": ["b", "a", "c"], "next_activity": ["x", "x", "x"], "median_wait_hours": [5.0, 5.0, 9.0]})
    assert ag.top_waits(tr, 2)["activity"].tolist() == ["c", "a"]


# --------------------------------------------------------------------------- #
# Wait histogram
# --------------------------------------------------------------------------- #
def test_wait_histogram_counts_sum_to_population():
    waits = pd.Series([0.0001, 0.0002, 0.5, 1.2, 3.0, 30.0, 640.9, 641.0, float("nan")])
    hist = ag.wait_histogram(waits)
    assert hist["count"].sum() == 8  # NaN excluded, every other wait counted once
    assert hist["bin"].iloc[0].startswith("<")  # sub-second waits collected in the first band
    assert (hist["upper_hours"] > hist["lower_hours"]).all()
    assert hist["lower_hours"].is_monotonic_increasing


def test_wait_histogram_edge_cases():
    assert ag.wait_histogram(pd.Series([], dtype=float)).empty
    single = ag.wait_histogram(pd.Series([5.0]))
    assert single["count"].tolist() == [1]
    assert "5.6 h" in single["bin"].iloc[0] or "h" in single["bin"].iloc[0]


def test_describe_wait():
    text = ag.describe_wait({"transition_count": 8539, "median_wait_hours": 640.93, "p90_wait_hours": 648.46})
    assert text == "8,539 occurrences · median 640.9 h · 90th percentile 648.5 h"


# --------------------------------------------------------------------------- #
# Cancellation selections
# --------------------------------------------------------------------------- #
def test_case_selections():
    fu = pd.DataFrame({"case_id": ["a", "b", "c"], "pattern": [m.PATTERN_WORKFLOW, m.PATTERN_OTHER, m.PATTERN_WORKFLOW]})
    assert ag.cases_for_pattern(fu, m.PATTERN_WORKFLOW)["case_id"].tolist() == ["a", "c"]
    assert len(ag.cases_for_pattern(fu, None)) == 3
    tbl = pd.DataFrame({"case_id": ["a", "b"], "cancellation_sequence": [m.SEQ_OFFER_FIRST, m.SEQ_APPLICATION_FIRST]})
    assert ag.cases_for_sequence(tbl, m.SEQ_OFFER_FIRST)["case_id"].tolist() == ["a"]
    s = pd.DataFrame({"case_id": ["a", "b", "c"], "has_offer_cancellation": [True, False, True],
                      "final_status_label": [PENDING, PENDING, CANCELLED]})
    assert ag.offer_cancellation_cases(s)["case_id"].tolist() == ["a", "c"]
    assert ag.offer_cancellation_cases(s, PENDING)["case_id"].tolist() == ["a"]


def test_pattern_composition_uses_existing_summary_columns():
    ps = pd.DataFrame({"pattern": [m.PATTERN_WORKFLOW], "applications": [102], "applications_with_new_offers": [0],
                       "applications_with_offers_sent": [0], "applications_with_workflow_activity": [102],
                       "applications_with_returned_offers": [9]})
    comp = ag.pattern_composition(ps, m.PATTERN_WORKFLOW)
    assert comp["applications"].tolist() == [0, 0, 102, 9] and set(comp["pattern_applications"]) == {102}
    assert ag.pattern_composition(ps, "missing").empty


# --------------------------------------------------------------------------- #
# Timeline
# --------------------------------------------------------------------------- #
@pytest.fixture
def case_log():
    return ev(
        ("a", "A_Create Application", 0),
        ("a", "W_Handle leads", 1, {m.L: "schedule"}),
        offer("a", m.OFFER_CREATE, 2, "O1"),
        offer("a", m.OFFER_SENT_MAIL_ONLINE, 3, "O1"),
        ("a", "A_Denied", 4),
        ("a", "A_Denied", 5),  # repeated final status: only the last is flagged
        ("b", "A_Create Application", 0),
    )


def test_timeline_frame(case_log):
    events = ag.events_for_application(case_log.sample(frac=1, random_state=2), "a")
    tl = ag.timeline_frame(events)
    assert len(tl) == 6 and tl["order"].tolist() == [1, 2, 3, 4, 5, 6]
    assert tl["time"].is_monotonic_increasing
    assert tl["stream"].tolist() == ["Application", "Workflow", "Offer", "Offer", "Application", "Application"]
    assert tl["is_final_status"].tolist() == [False] * 5 + [True]
    assert tl.loc[1, "lifecycle"] == "schedule" and tl.loc[3, "offer_id"] == "O1"
    assert tl.loc[2, "event_id"] == "O1"  # O_Create Offer's EventID is the offer id
    assert tl["offer"].tolist()[2:4] == ["O1", "O1"]  # creation event belongs to the offer it creates
    assert pd.isna(tl.loc[2, "offer_id"]) and tl["offer"].isna().sum() == 4  # OfferID kept as recorded
    assert list(tl.columns) == ag.TIMELINE_COLUMNS


def test_timeline_unknown_application(case_log):
    assert ag.timeline_frame(ag.events_for_application(case_log, "zzz")).empty


def test_offer_lifelines(case_log):
    offers = m.offer_table(case_log)
    ll = ag.offer_lifelines(offers, "a")
    assert ll["offer_id"].tolist() == ["O1"] and pd.isna(ll["offer_outcome_time"].iloc[0])
    assert ll["offer_outcome"].iloc[0] == "No outcome recorded"
    assert ag.offer_lifelines(offers, "b").empty


# --------------------------------------------------------------------------- #
# Explorer: event table, extras alignment, search, summary details (Phase 5e)
# --------------------------------------------------------------------------- #
def _extras(events):
    return pd.DataFrame({
        m.EVENT_ID: events[m.EVENT_ID], "Action": [f"act{i}" for i in range(len(events))],
        "EventOrigin": "Origin", "Accepted": pd.array([None] * len(events), dtype="boolean"),
        "Selected": pd.array([None] * len(events), dtype="boolean"),
    })


def test_event_table_keeps_order_and_records(case_log):
    shuffled = case_log.sample(frac=1, random_state=3)
    prepared = m.prepare_events(shuffled)
    extras = ag.align_event_extras(_extras(shuffled.sort_index()), prepared)
    table = ag.event_table(prepared, extras, "a")
    assert table["#"].tolist() == [1, 2, 3, 4, 5, 6]
    assert table["Activity"].tolist() == ["A_Create Application", "W_Handle leads", m.OFFER_CREATE,
                                          m.OFFER_SENT_MAIL_ONLINE, "A_Denied", "A_Denied"]
    assert table["Event ID"].tolist() == ["E0", "E1", "O1", "E3", "E4", "E5"]
    assert table["Action"].tolist() == ["act0", "act1", "act2", "act3", "act4", "act5"]  # joined to the right event
    assert table["Final-status event"].tolist() == [False] * 5 + [True]
    assert table["Resource (as recorded)"].eq("User_10").all()
    assert table["Offer"].tolist()[2:4] == ["O1", "O1"]
    for col in ("Time (UTC)", "Event stream", "Lifecycle stage", "OfferID (as recorded)", "Event origin",
                "Accepted", "Selected"):
        assert col in table
    assert ag.event_table(prepared, extras, "zzz").empty
    assert "Action" not in ag.event_table(prepared, None, "a")


def test_align_event_extras_rejects_misaligned_rows(case_log):
    from core.errors import DashboardError

    prepared = m.prepare_events(case_log)
    bad = _extras(case_log).iloc[::-1].reset_index(drop=True)
    with pytest.raises(DashboardError):
        ag.align_event_extras(bad, prepared)
    with pytest.raises(DashboardError):
        ag.align_event_extras(_extras(case_log).iloc[:-1], prepared)
    ok = ag.align_event_extras(_extras(case_log), prepared)
    assert m.EVENT_ID not in ok and isinstance(ok["Action"].dtype, pd.CategoricalDtype)


def test_find_application():
    ids = ["Application_1", "Application_12"]
    assert ag.find_application("application_1", ids) == "Application_1"
    assert ag.find_application("  Application_12 ", ids) == "Application_12"
    assert ag.find_application("Application_", ids) is None  # partial text only narrows the list
    assert ag.find_application("", ids) is None and ag.find_application(None, ids) is None


def test_application_details_for_closed_and_open(case_log):
    s = m.application_summary(case_log, dataset_end=definitions.DATASET_END)
    closed = ag.application_details(s[s["case_id"] == "a"].iloc[0])
    values = {k: dict(zip(v["Field"], v["Value"])) for k, v in closed.items()}
    assert values["Application"]["Final status"] == DENIED
    assert values["Events and offers"]["Recorded events"] == "6"
    assert values["Events and offers"]["O_Cancelled events (offer cancellation)"] == "0"
    assert "Last recorded activity (not an outcome)" in values["Application"]
    opened = ag.application_details(s[s["case_id"] == "b"].iloc[0])
    timing = dict(zip(opened["Timing (UTC)"]["Field"], opened["Timing (UTC)"]["Value"]))
    assert timing["Final-status event"] == "None by 1 Feb 2017 (open)" and timing["Time to final status"] == "—"


# --------------------------------------------------------------------------- #
# Real data: milestone counts reconcile to the (filtered) application population
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def real_summary():
    events = m.prepare_events(loaders.load_event_log(m.METRIC_EVENT_COLUMNS))
    return m.application_summary(events, dataset_end=definitions.DATASET_END)


def _reconcile(summary):
    sk = ag.milestone_sankey(summary)
    inflow, outflow = _flows(sk)
    assert outflow.get(ag.NODE_ALL, 0) == len(summary)
    dist = m.final_status_distribution(summary).set_index("label")["applications"]
    assert inflow.reindex(dist.index, fill_value=0).astype(int).to_dict() == dist.astype(int).to_dict()
    return sk


@pytest.mark.integration
def test_real_milestones_reconcile_to_population(real_summary):
    sk = _reconcile(real_summary)
    by_link = {(a, b): v for a, b, v in zip(sk.links["source_label"], sk.links["target_label"], sk.links["value"])}
    assert by_link[(ag.NODE_ALL, ag.NODE_OFFER_SENT)] == 31_362
    assert by_link[(ag.NODE_ALL, ag.NODE_NO_OFFER_SENT)] == 147
    assert by_link[(ag.NODE_OFFER_SENT, ag.NODE_OFFER_RETURNED)] == 21_768
    assert (real_summary["offers_sent"] > 0).sum() == 31_362


@pytest.mark.integration
@pytest.mark.parametrize(
    "state",
    [
        FilterState(start_month_from="2016-03", start_month_to="2016-06"),
        FilterState(final_statuses=(CANCELLED,)),
        FilterState(loan_goals=("Car",), final_statuses=(PENDING, OPEN)),
        FilterState(start_month_from="2016-12"),
    ],
)
def test_real_milestones_reconcile_to_filtered_population(real_summary, state):
    filtered = apply_filters(real_summary, state)
    assert len(filtered) > 0
    _reconcile(filtered)


def test_case_for_rows():
    tbl = pd.DataFrame({"Application": ["a", "b", "c"]})
    assert ag.case_for_rows(tbl, [1], "Application") == "b"
    assert ag.case_for_rows(tbl, [7, 2], "Application") == "c"  # out-of-range rows skipped
    assert ag.case_for_rows(tbl, [], "Application") is None
    assert ag.case_for_rows(tbl.iloc[0:0], [0], "Application") is None


def test_final_status_counts():
    cases = pd.DataFrame({"final_status": ["A_Cancelled", "A_Cancelled", None]})
    assert ag.final_status_counts(cases) == {"A_Cancelled": 2, config.NO_FINAL_STATUS_LABEL: 1}
    assert ag.final_status_counts(cases.iloc[0:0]) == {}
