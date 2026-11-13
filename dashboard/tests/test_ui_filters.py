"""Unit tests for ui.filters (pure selection logic)."""

from __future__ import annotations

import pandas as pd
import pytest

from core import config
from ui.filters import STATUS_LABEL_ORDER, FilterState, apply_filters, describe, filter_options

END = pd.Timestamp("2017-02-01", tz="UTC")


@pytest.fixture
def summary():
    s = pd.DataFrame(
        {
            "case_id": ["a", "b", "c", "d", "e"],
            "start_month": ["2016-01", "2016-02", "2016-03", "2016-03", "2016-12"],
            "loan_goal": ["Car", "Car", "Home improvement", "Car", "Boat"],
            "application_type": ["New credit", "Limit raise", "New credit", "New credit", "New credit"],
            "final_status_label": [
                config.FINAL_STATUS_LABELS["A_Pending"],
                config.FINAL_STATUS_LABELS["A_Cancelled"],
                config.FINAL_STATUS_LABELS["A_Denied"],
                config.FINAL_STATUS_LABELS["A_Pending"],
                config.NO_FINAL_STATUS_LABEL,
            ],
        }
    )
    s.attrs["dataset_end"] = END
    return s


def ids(df):
    return df["case_id"].tolist()


def test_default_state_selects_everything_and_keeps_attrs(summary):
    out = apply_filters(summary, FilterState())
    assert ids(out) == ["a", "b", "c", "d", "e"]
    assert out.attrs["dataset_end"] == END
    assert FilterState().is_default


def test_month_range_inclusive_and_reversed_bounds(summary):
    assert ids(apply_filters(summary, FilterState("2016-02", "2016-03"))) == ["b", "c", "d"]
    assert ids(apply_filters(summary, FilterState("2016-03", "2016-02"))) == ["b", "c", "d"]
    assert ids(apply_filters(summary, FilterState(start_month_from="2016-12"))) == ["e"]
    assert ids(apply_filters(summary, FilterState(start_month_to="2016-01"))) == ["a"]


def test_each_dimension_and_combination(summary):
    assert ids(apply_filters(summary, FilterState(loan_goals=("Car",)))) == ["a", "b", "d"]
    assert ids(apply_filters(summary, FilterState(application_types=("Limit raise",)))) == ["b"]
    pending = config.FINAL_STATUS_LABELS["A_Pending"]
    assert ids(apply_filters(summary, FilterState(final_statuses=(pending,)))) == ["a", "d"]
    assert ids(apply_filters(summary, FilterState(final_statuses=(config.NO_FINAL_STATUS_LABEL,)))) == ["e"]
    combo = FilterState("2016-03", "2016-03", loan_goals=("Car",), final_statuses=(pending,))
    assert ids(apply_filters(summary, combo)) == ["d"]


def test_empty_result_keeps_columns_and_attrs(summary):
    out = apply_filters(summary, FilterState(loan_goals=("Unknown goal",)))
    assert out.empty and list(out.columns) == list(summary.columns)
    assert out.attrs["dataset_end"] == END


def test_input_is_not_modified_and_output_is_independent(summary):
    before = summary.copy()
    out = apply_filters(summary, FilterState(loan_goals=("Car",)))
    out.loc[out.index[0], "loan_goal"] = "CHANGED"
    out.attrs["dataset_end"] = None
    pd.testing.assert_frame_equal(summary, before)
    assert summary.attrs["dataset_end"] == END


def test_filter_state_is_hashable_for_caching():
    a = FilterState("2016-01", "2016-02", ("Car",))
    b = FilterState("2016-01", "2016-02", ("Car",))
    assert hash(a) == hash(b) and a == b
    assert len({a, b, FilterState()}) == 2


def test_describe(summary):
    assert describe(FilterState()) == "All applications (no filters applied)"
    text = describe(FilterState("2016-02", "2016-03", ("Car",), ("New credit",), (STATUS_LABEL_ORDER[0],)))
    assert "Feb 2016 – Mar 2016" in text and "Loan goal: Car" in text
    assert "Final status: A_Pending (offer accepted)" in text


def test_filter_options(summary):
    opts = filter_options(summary)
    assert opts.months == ("2016-01", "2016-02", "2016-03", "2016-12")
    assert opts.loan_goals == ("Boat", "Car", "Home improvement")
    assert opts.final_statuses == STATUS_LABEL_ORDER
    assert STATUS_LABEL_ORDER[0] == "A_Pending (offer accepted)"
    assert STATUS_LABEL_ORDER[-1] == config.NO_FINAL_STATUS_LABEL


def test_describe_single_month():
    assert describe(FilterState("2016-12", "2016-12")) == "Started Dec 2016"
