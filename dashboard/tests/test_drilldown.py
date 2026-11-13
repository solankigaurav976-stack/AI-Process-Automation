"""core.drilldown must reproduce core.metrics.transition_metrics exactly."""

from __future__ import annotations

import math

import pandas as pd
import pytest

from core import drilldown as dd
from core import loaders
from core import metrics as m
from tests.test_metrics import ev


def _aggregate(instances: pd.DataFrame) -> pd.DataFrame:
    g = instances.groupby(["activity", "next_activity"])["wait_hours"]
    return pd.DataFrame({"transition_count": g.size(), "median_wait_hours": g.median(), "p90_wait_hours": g.quantile(0.9)})


def _assert_equivalent(events: pd.DataFrame) -> int:
    expected = m.transition_metrics(events, min_count=1).set_index(["activity", "next_activity"])[
        ["transition_count", "median_wait_hours", "p90_wait_hours"]
    ].sort_index()
    got = _aggregate(dd.transition_instances(events)).sort_index()
    assert list(got.index) == list(expected.index)
    assert (got["transition_count"].to_numpy() == expected["transition_count"].to_numpy()).all()
    assert (got["median_wait_hours"].to_numpy() == expected["median_wait_hours"].to_numpy()).all()  # exact
    assert (got["p90_wait_hours"].to_numpy() == expected["p90_wait_hours"].to_numpy()).all()  # exact
    return len(expected)


@pytest.fixture
def log():
    rows = []
    for i in range(7):
        c = f"c{i}"
        rows += [(c, "A_Create Application", 0), (c, "W_Handle leads", 0.5 + i), (c, "W_Handle leads", 1 + 2 * i),
                 (c, "A_Submitted", 3 + i * i), (c, "A_Cancelled", 24 * (i + 1))]
    return ev(*rows)


def test_instances_reproduce_transition_metrics_exactly(log):
    assert _assert_equivalent(log) == 4


def test_unsorted_input_gives_identical_instances(log):
    a = dd.transition_instances(log)
    b = dd.transition_instances(log.sample(frac=1, random_state=11))
    pd.testing.assert_frame_equal(a, b)


def test_pair_filter_does_not_change_the_next_event(log):
    one = dd.transition_instances(log, "W_Handle leads", "A_Submitted")
    assert len(one) == 7 and set(one["activity"]) == {"W_Handle leads"}
    full = dd.transition_instances(log)
    expected = full[(full["activity"] == "W_Handle leads") & (full["next_activity"] == "A_Submitted")]
    assert one["wait_hours"].tolist() == expected["wait_hours"].tolist()
    assert dd.instance_statistics(one) == {
        "transition_count": 7,
        "median_wait_hours": float(expected["wait_hours"].median()),
        "p90_wait_hours": float(expected["wait_hours"].quantile(0.9)),
    }


def test_single_side_filters(log):
    assert set(dd.transition_instances(log, activity="A_Create Application")["next_activity"]) == {"W_Handle leads"}
    assert set(dd.transition_instances(log, next_activity="A_Cancelled")["activity"]) == {"A_Submitted"}


def test_instances_never_cross_applications():
    out = dd.transition_instances(ev(("a", "X", 0), ("b", "Y", 1)))
    assert out.empty


def test_columns_types_and_resource(log):
    out = dd.transition_instances(log)
    assert list(out.columns) == list(dd.INSTANCE_COLUMNS)
    assert out["case_id"].dtype == "str" and out["activity"].dtype == "str"
    assert set(out["next_resource"]) == {"User_10"}
    no_res = dd.transition_instances(log.drop(columns=[m.R]))
    assert no_res["next_resource"].isna().all()


def test_empty_and_unknown_pair(log):
    empty = dd.transition_instances(log.iloc[0:0])
    assert empty.empty and list(empty.columns) == list(dd.INSTANCE_COLUMNS)
    none = dd.transition_instances(log, "NOPE", "NADA")
    stats = dd.instance_statistics(none)
    assert stats["transition_count"] == 0 and math.isnan(stats["median_wait_hours"])


def test_input_not_modified(log):
    before = log.copy()
    dd.transition_instances(log, "W_Handle leads")
    pd.testing.assert_frame_equal(log, before)


@pytest.mark.integration
def test_real_log_all_pairs_reconcile_exactly():
    events = m.prepare_events(loaders.load_event_log(m.METRIC_EVENT_COLUMNS))
    assert _assert_equivalent(events) == 178
    timer_pair = dd.transition_instances(events, "W_Call after offers", "A_Cancelled")
    assert dd.instance_statistics(timer_pair)["transition_count"] == 8539
