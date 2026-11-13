"""Shared fixtures.

Unit tests write small synthetic files into pytest's temporary directory; they
never touch the real project data. Integration tests read the real files.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from core import config

# Minimal valid content for every report (header + a few rows).
VALID_REPORTS: dict[str, str] = {
    "activity_frequency": (
        "concept:name,total_events,applications\n"
        "A_Create Application,3,3\n"
        "O_Cancelled,2,1\n"
        "W_Shortened completion ,1,1\n"
    ),
    "application_outcomes": (
        "final_activity,applications,percentage\n"
        "W_Validate application,2,66.67\n"
        "O_Cancelled,1,33.33\n"
    ),
    "application_performance": (
        "case:concept:name,start_time,end_time,total_events,unique_activities,duration_days\n"
        "Application_1,2016-01-01 09:00:00.000+00:00,2016-01-03 09:00:00.000+00:00,5,4,2.0\n"
        "Application_2,2016-02-01 10:00:00.500+00:00,2016-02-01 10:00:00.500+00:00,1,1,0.0\n"
    ),
    "cancellation_completion_delays": (
        "case_id,cancellation_time,completion_time,delay_hours\n"
        "Application_1,2016-01-03 09:00:00.000+00:00,2016-01-03 09:00:00.020+00:00,0.0\n"
    ),
    "offer_cancellation_followup_patterns": (
        "case_id,gap_days,events_between,pattern,new_offer,offer_sent,workflow_activity,returned_offer\n"
        "Application_1,30.5,8,Replacement offer created,True,True,True,False\n"
        'Application_2,0.0,0,No recorded intermediate activity,False,False,False,False\n'
    ),
    "offer_cancellation_pattern_summary": (
        "pattern,applications,median_gap_days,average_gap_days,median_events,average_events,"
        "applications_with_new_offers,applications_with_offers_sent,applications_with_workflow_activity,"
        "applications_with_returned_offers,share_percent\n"
        "Replacement offer created,1,30.5,30.5,8.0,8.0,1,1,1,0,50.0\n"
        "No recorded intermediate activity,1,0.0,0.0,0.0,0.0,0,0,0,0,50.0\n"
    ),
    "process_bottlenecks": (
        "concept:name,next_activity,transition_count,average_wait_hours,median_wait_hours,p90_wait_hours\n"
        "W_Call after offers,A_Cancelled,150,555.1,640.9,648.5\n"
        "A_Create Application,A_Submitted,200,0.0,0.0,0.0\n"
    ),
}


def write_report(directory: Path, name: str, content: str | bytes) -> Path:
    path = directory / config.REPORT_FILES[name]
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def reports_dir(tmp_path: Path) -> Path:
    """A temporary directory containing a valid copy of every report."""
    directory = tmp_path / "reports"
    directory.mkdir()
    for name, content in VALID_REPORTS.items():
        write_report(directory, name, content)
    return directory


def make_event_log() -> pd.DataFrame:
    """Small event log with the same columns and dtypes as the real parquet."""
    ts = pd.to_datetime(
        [
            "2016-01-01 09:00:00+00:00",
            "2016-01-01 09:05:00+00:00",
            "2016-01-02 10:00:00+00:00",
            "2016-01-03 11:00:00+00:00",
        ],
        utc=True,
    )
    return pd.DataFrame(
        {
            "Action": ["Created", "statechange", "Created", "statechange"],
            "org:resource": ["User_1", "User_1", "User_2", "User_2"],
            "concept:name": ["A_Create Application", "O_Create Offer", "O_Cancelled", "A_Cancelled"],
            "EventOrigin": ["Application", "Offer", "Offer", "Application"],
            "EventID": ["E1", "E2", "E3", "E4"],
            "lifecycle:transition": ["complete"] * 4,
            "time:timestamp": ts,
            "case:LoanGoal": ["Car"] * 4,
            "case:ApplicationType": ["New credit"] * 4,
            "case:concept:name": ["Application_1"] * 4,
            "case:RequestedAmount": [10000.0] * 4,
            "FirstWithdrawalAmount": [None, 5000.0, None, None],
            "NumberOfTerms": [None, 60.0, None, None],
            "Accepted": pd.Series([None, True, None, None], dtype="object"),
            "MonthlyCost": [None, 200.0, None, None],
            "Selected": pd.Series([None, False, None, None], dtype="object"),
            "CreditScore": [None, 0.0, None, None],
            "OfferedAmount": [None, 10000.0, None, None],
            "OfferID": [None, "Offer_1", "Offer_1", None],
        }
    )


@pytest.fixture
def event_log_frame() -> pd.DataFrame:
    return make_event_log()


@pytest.fixture
def event_log_path(tmp_path: Path, event_log_frame: pd.DataFrame) -> Path:
    path = tmp_path / "events.parquet"
    event_log_frame.to_parquet(path, index=False)
    return path
