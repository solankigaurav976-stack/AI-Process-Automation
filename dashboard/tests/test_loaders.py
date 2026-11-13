"""Unit tests for loaders and validation, using synthetic files only."""

from __future__ import annotations

import pandas as pd
import pytest

from core import loaders
from core.errors import (
    DataSourceMissingError,
    DataSourceReadError,
    SchemaValidationError,
    UnknownColumnError,
    UnknownReportError,
)
from core.schemas import EVENT_LOG, ColumnSpec, TableSchema
from tests.conftest import VALID_REPORTS, write_report


# --------------------------------------------------------------------------- #
# Valid loads
# --------------------------------------------------------------------------- #
def test_all_valid_reports_load(reports_dir):
    bundle = loaders.load_all_reports(reports_dir=reports_dir)
    assert bundle.all_loaded
    assert set(bundle.reports) == set(VALID_REPORTS)


def test_types_are_coerced(reports_dir):
    perf = loaders.load_report("application_performance", reports_dir=reports_dir)
    assert str(perf["start_time"].dtype) == "datetime64[us, UTC]"
    assert perf["total_events"].dtype == "int64"
    assert perf["duration_days"].dtype == "float64"
    follow = loaders.load_report("offer_cancellation_followup_patterns", reports_dir=reports_dir)
    assert follow["new_offer"].dtype == "boolean"
    assert follow["new_offer"].tolist() == [True, False]


def test_values_are_preserved_exactly_including_trailing_space(reports_dir):
    freq = loaders.load_report("activity_frequency", reports_dir=reports_dir)
    assert "W_Shortened completion " in set(freq["concept:name"])


def test_utf8_bom_is_tolerated(reports_dir):
    write_report(reports_dir, "activity_frequency", "﻿" + VALID_REPORTS["activity_frequency"])
    freq = loaders.load_report("activity_frequency", reports_dir=reports_dir)
    assert list(freq.columns) == ["concept:name", "total_events", "applications"]


def test_columns_returned_in_schema_order(reports_dir):
    write_report(
        reports_dir,
        "activity_frequency",
        "applications,concept:name,total_events\n3,A_Create Application,3\n",
    )
    freq = loaders.load_report("activity_frequency", reports_dir=reports_dir)
    assert list(freq.columns) == ["concept:name", "total_events", "applications"]


def test_files_are_opened_read_only(reports_dir, event_log_path, monkeypatch):
    modes: list[str] = []
    real_open = open

    def spy(file, mode="r", *args, **kwargs):
        modes.append(mode)
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(loaders, "open", spy, raising=False)
    loaders.load_all_reports(reports_dir=reports_dir)
    loaders.load_event_log(path=event_log_path)
    assert modes and set(modes) == {"rb"}


# --------------------------------------------------------------------------- #
# File-level failures
# --------------------------------------------------------------------------- #
def test_missing_report_raises_clear_error(reports_dir):
    (reports_dir / "process_bottlenecks.csv").unlink()
    with pytest.raises(DataSourceMissingError) as info:
        loaders.load_report("process_bottlenecks", reports_dir=reports_dir)
    assert "not found" in info.value.user_message


def test_one_failing_report_does_not_block_others(reports_dir):
    (reports_dir / "process_bottlenecks.csv").unlink()
    write_report(reports_dir, "activity_frequency", "concept:name,total_events\nX,1\n")
    bundle = loaders.load_all_reports(reports_dir=reports_dir)
    assert set(bundle.errors) == {"process_bottlenecks", "activity_frequency"}
    assert isinstance(bundle.errors["process_bottlenecks"], DataSourceMissingError)
    assert isinstance(bundle.errors["activity_frequency"], SchemaValidationError)
    assert len(bundle.reports) == 5
    assert not bundle.all_loaded


def test_unknown_report_name():
    with pytest.raises(UnknownReportError):
        loaders.load_report("not_a_report")


def test_zero_byte_file(reports_dir):
    write_report(reports_dir, "activity_frequency", "")
    with pytest.raises(DataSourceReadError, match="empty"):
        loaders.load_report("activity_frequency", reports_dir=reports_dir)


def test_whitespace_only_file(reports_dir):
    write_report(reports_dir, "activity_frequency", "\n\n")
    with pytest.raises(DataSourceReadError, match="no header"):
        loaders.load_report("activity_frequency", reports_dir=reports_dir)


def test_header_only_file_has_no_rows(reports_dir):
    write_report(reports_dir, "activity_frequency", "concept:name,total_events,applications\n")
    with pytest.raises(SchemaValidationError) as info:
        loaders.load_report("activity_frequency", reports_dir=reports_dir)
    assert any("at least 1 row" in p for p in info.value.problems)


def test_directory_instead_of_file(reports_dir):
    (reports_dir / "process_bottlenecks.csv").unlink()
    (reports_dir / "process_bottlenecks.csv").mkdir()
    with pytest.raises(DataSourceReadError, match="not a regular file"):
        loaders.load_report("process_bottlenecks", reports_dir=reports_dir)


def test_invalid_encoding(reports_dir):
    write_report(reports_dir, "activity_frequency", b"concept:name,total_events,applications\n\xff\xfe,1,1\n")
    with pytest.raises(DataSourceReadError, match="could not be parsed"):
        loaders.load_report("activity_frequency", reports_dir=reports_dir)


def test_malformed_csv_rows(reports_dir):
    write_report(reports_dir, "activity_frequency", 'concept:name,total_events,applications\n"unclosed,1,1\n')
    with pytest.raises(DataSourceReadError):
        loaders.load_report("activity_frequency", reports_dir=reports_dir)


# --------------------------------------------------------------------------- #
# Schema failures
# --------------------------------------------------------------------------- #
def _problems(reports_dir, name, content):
    write_report(reports_dir, name, content)
    with pytest.raises(SchemaValidationError) as info:
        loaders.load_report(name, reports_dir=reports_dir)
    return info.value.problems


def test_missing_column(reports_dir):
    problems = _problems(reports_dir, "activity_frequency", "concept:name,total_events\nX,1\n")
    assert any("Missing required column(s): ['applications']" in p for p in problems)


def test_unexpected_column(reports_dir):
    problems = _problems(
        reports_dir, "activity_frequency", "concept:name,total_events,applications,extra\nX,1,1,9\n"
    )
    assert any("Unexpected column(s): ['extra']" in p for p in problems)


def test_unparseable_timestamp(reports_dir):
    content = (
        "case:concept:name,start_time,end_time,total_events,unique_activities,duration_days\n"
        "Application_1,not-a-date,2016-01-03 09:00:00+00:00,5,4,2.0\n"
    )
    problems = _problems(reports_dir, "application_performance", content)
    assert any("'start_time'" in p and "not valid timestamps" in p for p in problems)


def test_timestamp_without_timezone(reports_dir):
    content = (
        "case:concept:name,start_time,end_time,total_events,unique_activities,duration_days\n"
        "Application_1,2016-01-01 09:00:00,2016-01-03 09:00:00+00:00,5,4,2.0\n"
    )
    problems = _problems(reports_dir, "application_performance", content)
    assert any("no time-zone offset" in p for p in problems)


def test_non_numeric_value(reports_dir):
    problems = _problems(reports_dir, "activity_frequency", "concept:name,total_events,applications\nX,many,1\n")
    assert any("'total_events'" in p and "not numbers" in p for p in problems)


def test_fractional_integer(reports_dir):
    problems = _problems(reports_dir, "activity_frequency", "concept:name,total_events,applications\nX,1.5,1\n")
    assert any("not whole numbers" in p for p in problems)


def test_negative_duration(reports_dir):
    content = (
        "case:concept:name,start_time,end_time,total_events,unique_activities,duration_days\n"
        "Application_1,2016-01-01 09:00:00+00:00,2016-01-03 09:00:00+00:00,5,4,-1.0\n"
    )
    problems = _problems(reports_dir, "application_performance", content)
    assert any("below minimum" in p for p in problems)


def test_percentage_out_of_range(reports_dir):
    problems = _problems(
        reports_dir, "application_outcomes", "final_activity,applications,percentage\nX,1,140.0\n"
    )
    assert any("above maximum 100.0" in p for p in problems)


def test_missing_required_value(reports_dir):
    problems = _problems(reports_dir, "activity_frequency", "concept:name,total_events,applications\n,1,1\n")
    assert any("missing value" in p for p in problems)


def test_invalid_boolean(reports_dir):
    content = (
        "case_id,gap_days,events_between,pattern,new_offer,offer_sent,workflow_activity,returned_offer\n"
        "Application_1,1.0,1,Workflow activity only,yes,True,True,False\n"
    )
    problems = _problems(reports_dir, "offer_cancellation_followup_patterns", content)
    assert any("'new_offer'" in p and "True/False" in p for p in problems)


def test_unknown_pattern_label(reports_dir):
    content = (
        "case_id,gap_days,events_between,pattern,new_offer,offer_sent,workflow_activity,returned_offer\n"
        "Application_1,1.0,1,Customer recovered,False,False,False,False\n"
    )
    problems = _problems(reports_dir, "offer_cancellation_followup_patterns", content)
    assert any("unexpected value" in p and "Customer recovered" in p for p in problems)


def test_duplicate_single_key(reports_dir):
    problems = _problems(
        reports_dir, "activity_frequency", "concept:name,total_events,applications\nX,1,1\nX,2,2\n"
    )
    assert any("is not unique" in p for p in problems)


def test_duplicate_composite_key(reports_dir):
    content = (
        "concept:name,next_activity,transition_count,average_wait_hours,median_wait_hours,p90_wait_hours\n"
        "A,B,100,1,1,1\nA,B,200,2,2,2\nA,C,100,1,1,1\n"
    )
    problems = _problems(reports_dir, "process_bottlenecks", content)
    assert any("is not unique: 2 row(s)" in p for p in problems)


def test_all_problems_are_collected_together(reports_dir):
    problems = _problems(
        reports_dir, "activity_frequency", "concept:name,total_events,applications\nX,-1,abc\nX,1,1\n"
    )
    assert len(problems) >= 3  # negative, non-numeric, duplicate key


def test_duplicate_headers_are_reported():
    df = pd.DataFrame([[1, 2]], columns=["a", "a"])
    schema = TableSchema("t", (ColumnSpec("a", "integer"),))
    with pytest.raises(SchemaValidationError, match="Duplicate column header"):
        loaders.validate_frame(df, schema)


def test_validate_frame_does_not_modify_input():
    df = pd.DataFrame({"a": ["1", "2"]})
    loaders.validate_frame(df, TableSchema("t", (ColumnSpec("a", "integer"),)))
    assert df["a"].tolist() == ["1", "2"]


def test_wrong_dtype_for_text_column():
    df = pd.DataFrame({"a": [1.0, 2.0]})
    with pytest.raises(SchemaValidationError, match="expected text"):
        loaders.validate_frame(df, TableSchema("t", (ColumnSpec("a", "string"),)))


def test_mixed_object_text_column():
    df = pd.DataFrame({"a": pd.Series(["x", 5], dtype="object")})
    with pytest.raises(SchemaValidationError, match="non-text"):
        loaders.validate_frame(df, TableSchema("t", (ColumnSpec("a", "string"),)))


def test_bool_dtype_rejected_for_numeric_column():
    df = pd.DataFrame({"a": [True, False]})
    with pytest.raises(SchemaValidationError, match="expected a number"):
        loaders.validate_frame(df, TableSchema("t", (ColumnSpec("a", "integer"),)))


def test_nullable_integer_column_uses_int64_extension():
    df = pd.DataFrame({"a": ["1", None]})
    out = loaders.validate_frame(df, TableSchema("t", (ColumnSpec("a", "integer", nullable=True),)))
    assert str(out["a"].dtype) == "Int64"


def test_naive_datetime_dtype_rejected():
    df = pd.DataFrame({"a": pd.to_datetime(["2016-01-01"])})
    with pytest.raises(SchemaValidationError, match="no time zone"):
        loaders.validate_frame(df, TableSchema("t", (ColumnSpec("a", "datetime"),)))


def test_tz_aware_datetime_converted_to_utc():
    df = pd.DataFrame({"a": pd.to_datetime(["2016-01-01 10:00"]).tz_localize("Europe/Amsterdam")})
    out = loaders.validate_frame(df, TableSchema("t", (ColumnSpec("a", "datetime"),)))
    assert str(out["a"].dt.tz) == "UTC"
    assert out["a"].iloc[0].hour == 9


# --------------------------------------------------------------------------- #
# Event log
# --------------------------------------------------------------------------- #
def test_event_log_loads_with_types(event_log_path):
    df = loaders.load_event_log(path=event_log_path)
    assert df.shape == (4, len(EVENT_LOG.columns))
    assert str(df["time:timestamp"].dtype).endswith("UTC]")
    assert str(df["Accepted"].dtype) == "boolean"
    assert df["Accepted"].isna().sum() == 3


def test_event_log_column_selection(event_log_path):
    df = loaders.load_event_log(["case:concept:name", "concept:name", "case:concept:name"], path=event_log_path)
    assert list(df.columns) == ["case:concept:name", "concept:name"]


def test_event_log_unknown_column(event_log_path):
    with pytest.raises(UnknownColumnError):
        loaders.load_event_log(["not_a_column"], path=event_log_path)


def test_event_log_empty_column_request(event_log_path):
    with pytest.raises(UnknownColumnError):
        loaders.load_event_log([], path=event_log_path)


def test_event_log_missing_file(tmp_path):
    with pytest.raises(DataSourceMissingError):
        loaders.load_event_log(path=tmp_path / "nope.parquet")


def test_event_log_corrupt_file(tmp_path):
    bad = tmp_path / "bad.parquet"
    bad.write_bytes(b"this is not parquet at all")
    with pytest.raises(DataSourceReadError, match="corrupt"):
        loaders.load_event_log(path=bad)


def test_event_log_missing_column(tmp_path, event_log_frame):
    path = tmp_path / "partial.parquet"
    event_log_frame.drop(columns=["OfferID"]).to_parquet(path, index=False)
    with pytest.raises(SchemaValidationError, match="OfferID"):
        loaders.load_event_log(path=path)
    # Selecting only present columns still works.
    assert len(loaders.load_event_log(["EventID"], path=path)) == 4


def test_event_log_extra_columns_are_ignored(tmp_path, event_log_frame):
    path = tmp_path / "extra.parquet"
    event_log_frame.assign(unrelated=1).to_parquet(path, index=False)
    assert "unrelated" not in loaders.load_event_log(path=path).columns


def test_event_log_duplicate_event_id(tmp_path, event_log_frame):
    frame = event_log_frame.copy()
    frame.loc[1, "EventID"] = "E1"
    path = tmp_path / "dupe.parquet"
    frame.to_parquet(path, index=False)
    with pytest.raises(SchemaValidationError, match="not unique"):
        loaders.load_event_log(path=path)


def test_event_log_unknown_lifecycle_and_origin(tmp_path, event_log_frame):
    frame = event_log_frame.copy()
    frame.loc[0, "lifecycle:transition"] = "teleport"
    frame.loc[0, "EventOrigin"] = "Robot"
    path = tmp_path / "bad_values.parquet"
    frame.to_parquet(path, index=False)
    with pytest.raises(SchemaValidationError) as info:
        loaders.load_event_log(path=path)
    assert any("teleport" in p for p in info.value.problems)
    assert any("Robot" in p for p in info.value.problems)


def test_event_log_missing_case_id(tmp_path, event_log_frame):
    frame = event_log_frame.copy()
    frame.loc[2, "case:concept:name"] = None
    path = tmp_path / "null_case.parquet"
    frame.to_parquet(path, index=False)
    with pytest.raises(SchemaValidationError, match="missing value"):
        loaders.load_event_log(path=path)


def test_event_log_invalid_boolean_values(tmp_path, event_log_frame):
    frame = event_log_frame.copy()
    frame["Accepted"] = pd.Series([None, "maybe", None, None], dtype="object")
    path = tmp_path / "bad_bool.parquet"
    frame.to_parquet(path, index=False)
    with pytest.raises(SchemaValidationError, match="True/False"):
        loaders.load_event_log(path=path)


def test_numpy_bool_values_are_accepted():
    import numpy as np

    df = pd.DataFrame({"a": pd.Series([np.True_, None, np.False_], dtype="object")})
    out = loaders.validate_frame(df, TableSchema("t", (ColumnSpec("a", "boolean", nullable=True),)))
    assert out["a"].tolist()[0] is True or out["a"].tolist()[0] == True  # noqa: E712
    assert out["a"].isna().sum() == 1


def test_object_text_column_is_converted_to_str():
    df = pd.DataFrame({"a": pd.Series(["x", "y"], dtype="object")})
    out = loaders.validate_frame(df, TableSchema("t", (ColumnSpec("a", "string"),)))
    assert isinstance(out["a"].dtype, pd.StringDtype)


def test_native_bool_dtype_is_accepted():
    df = pd.DataFrame({"a": [True, False]})
    out = loaders.validate_frame(df, TableSchema("t", (ColumnSpec("a", "boolean"),)))
    assert str(out["a"].dtype) == "boolean"


def test_number_in_boolean_column_is_rejected():
    df = pd.DataFrame({"a": pd.Series([1, None], dtype="object")})
    with pytest.raises(SchemaValidationError, match="True/False"):
        loaders.validate_frame(df, TableSchema("t", (ColumnSpec("a", "boolean", nullable=True),)))


def test_unreadable_file_permission(reports_dir):
    import os

    path = reports_dir / "activity_frequency.csv"
    os.chmod(path, 0)
    try:
        if os.access(path, os.R_OK):  # e.g. running as root
            pytest.skip("permissions are not enforced for this user")
        with pytest.raises(DataSourceReadError, match="could not be opened"):
            loaders.load_report("activity_frequency", reports_dir=reports_dir)
    finally:
        os.chmod(path, 0o644)
