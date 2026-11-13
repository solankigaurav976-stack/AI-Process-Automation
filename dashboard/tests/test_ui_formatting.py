"""Unit tests for ui.formatting."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ui import formatting as f


@pytest.mark.parametrize("missing", [None, float("nan"), np.nan, pd.NA, pd.NaT, float("inf"), -float("inf")])
def test_missing_values_render_as_dash(missing):
    for fn in (f.fmt_int, f.fmt_number, f.fmt_pct, f.fmt_days, f.fmt_hours, f.fmt_date, f.fmt_datetime):
        assert fn(missing) == f.MISSING


def test_integers_and_numbers():
    assert f.fmt_int(31509) == "31,509"
    assert f.fmt_int(np.int64(1202267)) == "1,202,267"
    assert f.fmt_int(0) == "0"
    assert f.fmt_int(2.6) == "3"
    assert f.fmt_number(897.7838) == "897.8"
    assert f.fmt_number(1_234_567.891, 2) == "1,234,567.89"


def test_percentages_and_shares():
    assert f.fmt_pct(54.676442) == "54.7%"
    assert f.fmt_pct(0.0) == "0.0%"
    assert f.fmt_pct(100) == "100.0%"
    assert f.fmt_share(17228, 31509) == "54.7%"
    assert f.fmt_share(5, 0) == f.MISSING  # never a fake 0%
    assert f.fmt_share(None, 10) == f.MISSING


def test_durations():
    assert f.fmt_days(19.087739) == "19.1 days"
    assert f.fmt_days(1.0) == "1.0 day"
    assert f.fmt_days(0.04) == "0.0 days"
    assert f.fmt_hours(640.928761) == "640.9 h"


def test_dates_are_utc():
    ts = pd.Timestamp("2017-02-01 14:11:03.499", tz="UTC")
    assert f.fmt_date(ts) == "1 Feb 2017"
    assert f.fmt_datetime(ts) == "1 Feb 2017, 14:11 UTC"
    amsterdam = pd.Timestamp("2017-02-01 00:30", tz="Europe/Amsterdam")  # 31 Jan 23:30 UTC
    assert f.fmt_date(amsterdam) == "31 Jan 2017"
    assert f.fmt_datetime(pd.Timestamp("2016-01-01 09:51")) == "1 Jan 2016, 09:51 UTC"


def test_months():
    assert f.fmt_month("2016-12") == "Dec 2016"
    assert f.fmt_month("") == f.MISSING
    assert f.fmt_month(None) == f.MISSING


@pytest.mark.parametrize(
    "value,unit,expected",
    [
        (31509, "applications", "31,509"),
        (20898, "events", "20,898"),
        (42995, "offers", "42,995"),
        (19.087, "days", "19.1 days"),
        (55.84, "%", "55.8%"),
        (904.0, "score", "904"),
        (float("nan"), "score", f.MISSING),
        ("x", "other", "x"),
    ],
)
def test_fmt_value_by_unit(value, unit, expected):
    assert f.fmt_value(value, unit) == expected
