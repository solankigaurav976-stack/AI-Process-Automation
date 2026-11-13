"""Integration: the real CSV reports reconcile with the Parquet event log.

Read-only: reports and the event log are loaded through core.loaders.
"""

from __future__ import annotations

import pytest

from core import definitions, loaders
from core import metrics as m
from core import reconciliation as rc

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def result():
    bundle = loaders.load_all_reports()
    assert bundle.all_loaded
    events = loaders.load_event_log(m.METRIC_EVENT_COLUMNS)
    return rc.reconcile_all(events, bundle.reports, bundle.errors, dataset_end=definitions.DATASET_END)


def _row(result, check):
    rows = result.checks[result.checks["check"] == check]
    assert len(rows) == 1, check
    return rows.iloc[0]


def test_all_real_reports_reconcile(result):
    failures = result.failures()
    assert failures.empty, failures[["check_id", "check", "expected", "calculated", "note"]].to_string()
    counts = result.status_counts()
    assert counts["PASS"] >= 80 and counts["ERROR"] == 0


def test_every_report_is_covered(result):
    reports = set(result.checks["report"])
    assert {f"{n}.csv" for n in loaders.config.REPORT_FILES} <= reports


@pytest.mark.parametrize(
    "check,expected",
    [
        ("Activities: row count", 26),
        ("Last-activity categories: row count", 12),
        ("Applications: row count", 31_509),
        ("Cancellation closures: row count", 110),
        ("Offer-first applications: row count", 576),
        ("Patterns: row count", 5),
        ("Transitions: row count", 92),
        ("Applications with pattern 'Replacement offer created'", 230),
        ("Applications with pattern 'No recorded intermediate activity'", 126),
        ("Applications with pattern 'Workflow activity only'", 102),
        ("Applications with pattern 'Offer sent, no new offer creation recorded'", 85),
        ("Applications with pattern 'Other activity only'", 33),
        ("O_Cancelled events (offer cancellation)", 20_898),
        ("A_Cancelled events (application cancellation)", 10_431),
    ],
)
def test_key_values(result, check, expected):
    row = _row(result, check)
    assert row["expected"] == expected and row["calculated"] == expected and row["status"] == rc.PASS


def test_floating_point_differences_are_negligible(result):
    numeric = result.checks[(result.checks["status"] == rc.PASS) & (result.checks["tolerance"] == rc.FULL_PRECISION_TOL)]
    assert numeric["max_abs_difference"].max() < 1e-12


def test_workflow_only_composition_is_reported(result):
    row = _row(result, "'Workflow activity only' applications that also contain non-workflow events")
    assert row["status"] == rc.INFO and row["calculated"].startswith("16 of 102")


def test_offer_first_population_all_cancelled(result):
    assert _row(result, "Final status of the offer-first population")["calculated"] == "A_Cancelled: 576"


def test_delays_are_milliseconds(result):
    assert _row(result, "Largest true delay (seconds)")["calculated"] < 1.0
