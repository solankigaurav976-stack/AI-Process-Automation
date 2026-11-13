"""Unit tests for core.reconciliation.

A synthetic event log is generated, the seven "reports" are produced from it in
the same shape (and rounding) as the real CSVs, and then deliberately perturbed
to prove that every kind of discrepancy is detected.
"""

from __future__ import annotations

import pandas as pd
import pytest

from core import config
from core import metrics as m
from core import reconciliation as rc

C, A, T, L, R = m.C, m.A, m.T, m.L, m.R
BASE = pd.Timestamp("2016-01-04 09:00", tz="UTC")


def _synthetic_events(n: int = 120) -> pd.DataFrame:
    """n applications; enough repetitions for transitions to pass the >=100 threshold."""
    rows = []

    def add(case, activity, hours, **extra):
        rows.append({C: case, A: activity, T: BASE + pd.Timedelta(hours=hours), L: extra.pop("lc", "complete"),
                     R: extra.pop("res", "User_10"), m.EVENT_ID: extra.pop("eid", f"E{len(rows)}"),
                     m.OFFER_ID: extra.pop("oid", None), "CreditScore": extra.pop("cs", None)})

    for i in range(n):
        case, t0, offer_id = f"App_{i:03d}", i * 5.0, f"Offer_{i}"
        add(case, "A_Create Application", t0)
        add(case, "W_Handle leads", t0 + 1)
        add(case, m.OFFER_CREATE, t0 + 2, eid=offer_id, cs=0.0 if i % 2 else 900.0)
        add(case, m.OFFER_CREATED_DUPLICATE, t0 + 2.001, oid=offer_id)
        add(case, m.OFFER_SENT_MAIL_ONLINE, t0 + 3, oid=offer_id)
        kind = i % 4
        if kind == 0:  # offer cancelled first, workflow activity, then application cancelled
            add(case, m.OFFER_CANCELLED, t0 + 4, oid=offer_id)
            add(case, "W_Call after offers", t0 + 5)
            if i % 8 == 0:
                add(case, m.OFFER_RETURNED, t0 + 5.5, oid=offer_id)  # non-workflow event in 'workflow only'
            add(case, "A_Cancelled", t0 + 24 * 31, res=m.SYSTEM_RESOURCE)
            add(case, "W_Complete application", t0 + 24 * 31 + 0.0001, lc="ate_abort")
        elif kind == 1:  # accepted
            add(case, m.OFFER_RETURNED, t0 + 10, oid=offer_id)
            add(case, "A_Pending", t0 + 20)
            add(case, m.OFFER_ACCEPTED, t0 + 20.001, oid=offer_id)
        elif kind == 2:  # application cancelled first, offer cancelled as a consequence
            add(case, "A_Cancelled", t0 + 30)
            add(case, m.OFFER_CANCELLED, t0 + 30.001, oid=offer_id)
        else:  # still open
            add(case, "W_Call after offers", t0 + 6)
    df = pd.DataFrame(rows)
    df[m.OFFER_ID] = df[m.OFFER_ID].astype("str").where(df[m.OFFER_ID].notna())
    df[T] = df[T].astype("datetime64[us, UTC]")
    df["CreditScore"] = df["CreditScore"].astype("float64")
    return df


def _reports_from(events: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Build the seven reports in the same shape and rounding as the real CSVs."""
    ev = m.prepare_events(events)
    summary = m.application_summary(ev)
    followup = m.offer_first_cancellation_followup(ev)
    outcomes = rc.recalc_application_outcomes(summary)
    outcomes["percentage"] = outcomes["percentage"].round(2)
    delays = m.cancellation_closure_events(ev)[["case_id", "cancellation_time", "completion_time", "delay_hours"]]
    delays = delays.assign(delay_hours=delays["delay_hours"].round(2))
    return {
        "activity_frequency": rc.recalc_activity_frequency(ev),
        "application_outcomes": outcomes,
        "application_performance": rc.recalc_application_performance(summary),
        "cancellation_completion_delays": delays,
        "offer_cancellation_followup_patterns": followup[list(m.FOLLOWUP_COLUMNS)[:8]].copy(),
        "offer_cancellation_pattern_summary": m.followup_pattern_summary(followup).round(2),
        "process_bottlenecks": rc.recalc_process_bottlenecks(ev),
    }


@pytest.fixture(scope="module")
def events():
    return _synthetic_events()


@pytest.fixture
def reports(events):
    return _reports_from(events)


@pytest.fixture(scope="module")
def baseline(events):
    """One reconciliation of the unmodified synthetic reports, shared by read-only tests."""
    return rc.reconcile_all(events, _reports_from(events))


def _status(result, check_substring, report=None):
    rows = result.checks[result.checks["check"].str.contains(check_substring, regex=False)]
    if report:
        rows = rows[rows["report"] == report]
    assert len(rows) >= 1, f"no check matching {check_substring!r}"
    return rows["status"].tolist()


# --------------------------------------------------------------------------- #
# Baseline
# --------------------------------------------------------------------------- #
def test_synthetic_reports_reconcile_cleanly(baseline, reports):
    result = baseline
    assert result.passed, result.failures()[["check_id", "check", "expected", "calculated"]]
    counts = result.status_counts()
    assert counts["FAIL"] == 0 and counts["ERROR"] == 0 and counts["PASS"] > 50
    assert len(reports["process_bottlenecks"]) > 0  # threshold exercised


def test_check_table_structure(baseline):
    result = baseline
    assert list(result.checks.columns) == rc.CHECK_COLUMNS
    assert result.checks["check_id"].is_unique
    assert set(result.checks["status"]) <= {rc.PASS, rc.FAIL, rc.INFO, rc.ERROR}
    prefixes = {cid.split("-")[0] for cid in result.checks["check_id"]}
    assert prefixes == {"AF", "AO", "AP", "CD", "FP", "PS", "PB", "KPI"}


def test_report_row_order_does_not_matter(events, reports):
    shuffled = {k: v.sample(frac=1, random_state=3) for k, v in reports.items()}
    assert rc.reconcile_all(events, shuffled).passed


def test_inputs_are_not_modified(events, reports):
    before = {k: v.copy() for k, v in reports.items()}
    events_before = events.copy()
    rc.reconcile_all(events, reports)
    for k in reports:
        pd.testing.assert_frame_equal(reports[k], before[k])
    pd.testing.assert_frame_equal(events, events_before)


# --------------------------------------------------------------------------- #
# Discrepancies are detected
# --------------------------------------------------------------------------- #
def test_changed_count_is_detected_with_details(events, reports):
    af = reports["activity_frequency"]
    af.loc[af["concept:name"] == "W_Handle leads", "total_events"] += 1
    result = rc.reconcile_all(events, reports)
    row = result.checks[result.checks["check"] == "Activities: 'total_events' matches row by row"].iloc[0]
    assert row["status"] == rc.FAIL and row["difference"] == -1 and row["max_abs_difference"] == 1
    detail = result.details[row["check_id"]]
    assert detail["concept:name"].tolist() == ["W_Handle leads"]
    assert not result.passed


def test_missing_and_extra_rows_are_detected(events, reports):
    perf = reports["application_performance"]
    extra = perf.iloc[[0]].assign(**{"case:concept:name": "Ghost_Application"})
    reports["application_performance"] = pd.concat([perf.iloc[1:], extra], ignore_index=True)
    result = rc.reconcile_all(events, reports)
    assert _status(result, "Applications: keys in report but not recalculated") == [rc.FAIL]
    assert _status(result, "Applications: keys recalculated but not in report") == [rc.FAIL]
    missing_id = result.checks.loc[
        result.checks["check"] == "Applications: keys in report but not recalculated", "check_id"
    ].iloc[0]
    assert result.details[missing_id]["case:concept:name"].tolist() == ["Ghost_Application"]


def test_float_tolerance(events, reports):
    perf = reports["application_performance"]
    perf.loc[0, "duration_days"] += 1e-12  # floating-point noise: accepted
    assert _status(rc.reconcile_all(events, reports), "'duration_days' matches") == [rc.PASS]
    perf.loc[0, "duration_days"] += 1e-3  # real difference: rejected
    assert _status(rc.reconcile_all(events, reports), "'duration_days' matches") == [rc.FAIL]


def test_rounded_values_must_equal_recalculated_value_rounded(events, reports):
    out = reports["application_outcomes"]
    out.loc[0, "percentage"] += 0.004  # within +/-0.005 but not a correct rounding
    assert _status(rc.reconcile_all(events, reports), "'percentage' matches") == [rc.FAIL]


def test_changed_pattern_label_is_detected(events, reports):
    fp = reports["offer_cancellation_followup_patterns"]
    fp.loc[0, "pattern"] = m.PATTERN_OTHER
    result = rc.reconcile_all(events, reports)
    assert _status(result, "'pattern' matches row by row") == [rc.FAIL]
    assert rc.FAIL in _status(result, "Applications with pattern")


def test_changed_timestamp_is_detected(events, reports):
    cd = reports["cancellation_completion_delays"]
    cd.loc[0, "completion_time"] += pd.Timedelta(milliseconds=1)
    assert _status(rc.reconcile_all(events, reports), "'completion_time' matches") == [rc.FAIL]


def test_bottleneck_below_threshold_is_detected(events, reports):
    pb = reports["process_bottlenecks"]
    pb.loc[0, "transition_count"] = 50
    result = rc.reconcile_all(events, reports)
    assert _status(result, "below the 100-occurrence threshold") == [rc.FAIL]


def test_missing_report_gives_error_and_others_still_run(events, reports):
    del reports["process_bottlenecks"]
    from core.errors import DataSourceMissingError

    err = DataSourceMissingError("The report 'process_bottlenecks.csv' file was not found.")
    result = rc.reconcile_all(events, reports, {"process_bottlenecks": err})
    errors = result.checks[result.checks["status"] == rc.ERROR]
    assert errors["report"].tolist() == ["process_bottlenecks.csv"]
    assert "not found" in errors["note"].iloc[0]
    assert not result.passed
    assert (result.checks.loc[result.checks["report"] == "activity_frequency.csv", "status"] == rc.PASS).all()


def test_kpi_checks_tolerate_missing_reports(events, reports):
    for name in ("activity_frequency", "application_performance", "application_outcomes",
                 "offer_cancellation_followup_patterns"):
        del reports[name]
    result = rc.reconcile_all(events, reports)
    kpi = result.checks[result.checks["report"] == "Headline KPIs (cross-report)"]
    assert kpi["status"].tolist() == [rc.INFO]  # only the O_ vs A_ explanation remains


# --------------------------------------------------------------------------- #
# Interpretation safeguards
# --------------------------------------------------------------------------- #
def test_application_outcomes_is_reconciled_as_last_activity(baseline, events):
    result = baseline
    ao = result.checks[result.checks["report"] == "application_outcomes.csv"]
    assert ao["check"].str.contains("Last-activity").any()
    info = ao[ao["status"] == rc.INFO].iloc[0]
    assert "not compared" in info["check"] and "must not be used as an outcome report" in info["note"]
    assert "A_Pending (offer accepted)" in info["calculated"]


def test_workflow_only_composition_flags_without_relabelling(baseline, events):
    result = baseline
    row = result.checks[result.checks["check"].str.contains("'Workflow activity only' applications that also")].iloc[0]
    assert row["status"] == rc.INFO and "kept as in the original report" in row["note"]
    detail = result.details[row["check_id"]]
    assert len(detail) == 15  # i % 8 == 0 for i < 120
    assert detail["has_offer_events"].all() and not detail["has_application_events"].any()
    assert detail["non_workflow_activities"].eq("O_Returned").all()
    followup = m.offer_first_cancellation_followup(events)
    assert (followup.set_index("case_id").loc[detail["case_id"], "pattern"] == m.PATTERN_WORKFLOW).all()


def test_followup_final_status_reported_as_not_recovery(baseline, events):
    result = baseline
    row = result.checks[result.checks["check"] == "Final status of the offer-first population"].iloc[0]
    assert row["calculated"] == "A_Cancelled: 30" and "not recovery" in row["note"]


def test_timer_info_is_labelled_likely(baseline, events):
    result = baseline
    row = result.checks[result.checks["check"].str.contains("likely timer-driven")].iloc[0]
    assert "Likely, not confirmed" in row["note"] and "likely system account" in row["note"]


def test_workflow_only_composition_empty():
    no_events = pd.DataFrame(
        {C: pd.Series(dtype="str"), A: pd.Series(dtype="str"), T: pd.Series(dtype="datetime64[us, UTC]")}
    )
    followup = m.offer_first_cancellation_followup(no_events)
    assert followup.empty
    assert rc.workflow_only_composition(no_events, followup).empty


def test_rounded_spec_scalar_comparison():
    col = rc._Collector("x", "X")
    col.value("ok", 21.88, 21.875, tolerance=rc.ROUNDED_2DP)
    col.value("bad", 21.87, 21.875, tolerance=rc.ROUNDED_2DP)
    col.value("text equal", "a", "a")
    col.value("text differ", "a", "b")
    assert [r["status"] for r in col.rows] == [rc.PASS, rc.FAIL, rc.PASS, rc.FAIL]
    assert col.rows[0]["tolerance"] == pytest.approx(0.005)
