"""Integration tests: the real reports and event log load and validate.

Expected shapes come from inspecting the existing files during planning.
These tests only read the files.
"""

from __future__ import annotations

import pandas as pd
import pytest

from core import config, loaders
from core.schemas import EVENT_LOG, REPORT_SCHEMAS

pytestmark = pytest.mark.integration

EXPECTED_ROWS = {
    "activity_frequency": 26,
    "application_outcomes": 12,
    "application_performance": 31_509,
    "cancellation_completion_delays": 110,
    "offer_cancellation_followup_patterns": 576,
    "offer_cancellation_pattern_summary": 5,
    "process_bottlenecks": 92,
}


@pytest.fixture(scope="module")
def bundle():
    return loaders.load_all_reports()


@pytest.fixture(scope="module")
def event_log():
    return loaders.load_event_log()


def test_all_real_reports_load_without_errors(bundle):
    assert dict(bundle.errors) == {}, {k: v.details for k, v in bundle.errors.items()}


@pytest.mark.parametrize("name,rows", EXPECTED_ROWS.items())
def test_real_report_shapes(bundle, name, rows):
    df = bundle.reports[name]
    assert len(df) == rows
    assert tuple(df.columns) == REPORT_SCHEMAS[name].column_names


@pytest.mark.parametrize("name", list(config.REPORT_FILES))
def test_schema_matches_real_csv_header_exactly(name):
    path = loaders.report_path(name)
    with open(path, "rb") as fh:
        header = pd.read_csv(fh, nrows=0, encoding="utf-8-sig").columns
    assert set(header) == set(REPORT_SCHEMAS[name].column_names)


def test_schema_matches_real_parquet_columns_exactly():
    import pyarrow.parquet as pq

    with open(config.EVENT_LOG_PATH, "rb") as fh:
        names = pq.read_schema(fh).names
    assert set(names) == set(EVENT_LOG.column_names)


def test_real_event_log_shape_and_types(event_log):
    assert event_log.shape == (1_202_267, 19)
    assert event_log[config.COL_CASE_ID].nunique() == 31_509
    assert str(event_log[config.COL_TIMESTAMP].dtype) == "datetime64[us, UTC]"
    assert event_log[config.COL_TIMESTAMP].isna().sum() == 0


def test_real_event_log_date_range(event_log):
    ts = event_log[config.COL_TIMESTAMP]
    assert ts.min() >= pd.Timestamp("2016-01-01", tz="UTC")
    assert ts.max() < pd.Timestamp("2017-02-02", tz="UTC")


def test_real_reports_and_event_log_agree_on_application_count(bundle, event_log):
    perf = bundle.reports["application_performance"]
    outcomes = bundle.reports["application_outcomes"]
    assert len(perf) == event_log[config.COL_CASE_ID].nunique()
    assert outcomes["applications"].sum() == len(perf)


def test_trailing_space_activity_name_is_preserved(bundle):
    assert "W_Shortened completion " in set(bundle.reports["activity_frequency"]["concept:name"])


def test_selected_columns_load(event_log):
    small = loaders.load_event_log([config.COL_CASE_ID, config.COL_ACTIVITY, config.COL_TIMESTAMP])
    assert list(small.columns) == [config.COL_CASE_ID, config.COL_ACTIVITY, config.COL_TIMESTAMP]
    assert len(small) == len(event_log)
