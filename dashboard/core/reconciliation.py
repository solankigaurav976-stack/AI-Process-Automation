"""Reconcile the existing CSV reports against metrics recalculated from the event log.

Every report is recalculated from the Parquet event log with :mod:`core.metrics`
and compared with the CSV produced by the original analysis. Nothing is written:
results are returned as DataFrames.

Check table columns
-------------------
* ``expected`` - value from the existing CSV report.
* ``calculated`` - value recalculated from the event log.
* ``difference`` - calculated - expected (for row-level checks: matching rows -
  compared rows, so 0 means every row matched).
* ``max_abs_difference`` - largest absolute numeric deviation across rows.
* ``tolerance`` - allowed absolute deviation (0 = exact).
* ``status`` - PASS, FAIL, INFO (informational, no pass/fail) or ERROR (report
  could not be loaded).

Tolerances
----------
* Counts, identifiers, categories and timestamps: exact.
* Floats stored at full precision: 1e-6. Observed deviations are at most ~1e-14,
  caused by floating-point summation order, not by different logic.
* Values the original report rounded to 2 decimals: report value must equal the
  recalculated value rounded to 2 decimals (see :class:`Rounded`).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from core import config
from core import metrics as m
from core.errors import DashboardError

PASS, FAIL, INFO, ERROR = "PASS", "FAIL", "INFO", "ERROR"
FULL_PRECISION_TOL = 1e-6


@dataclass(frozen=True)
class Rounded:
    """Compare a report value stored rounded to ``decimals`` places.

    PASS requires report value == round(recalculated, decimals) (within 1e-9),
    which is stricter than a +/- half-unit tolerance and confirms the rounding
    method (pandas/numpy round-half-to-even). ``tolerance`` reports the implied
    half-unit (0.005 for 2 decimals); ``max_abs_difference`` shows the size of the
    rounding itself (unrounded recalculated value vs report value).
    """

    decimals: int

    @property
    def half_unit(self) -> float:
        return 0.5 * 10 ** (-self.decimals)


ROUNDED_2DP = Rounded(2)
#: delay_hours in cancellation_completion_delays.csv is stored rounded: every
#: value is exactly 0.0. Rounding to 2 decimals (0.005 h = 18 s) is assumed.
DELAY_ROUNDING = ROUNDED_2DP

CHECK_COLUMNS = [
    "check_id", "report", "check", "expected", "calculated", "difference",
    "max_abs_difference", "tolerance", "status", "note",
]


@dataclass
class ReconciliationResult:
    checks: pd.DataFrame
    details: dict[str, pd.DataFrame] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return not self.checks["status"].isin([FAIL, ERROR]).any()

    def status_counts(self) -> dict[str, int]:
        counts = self.checks["status"].value_counts()
        return {s: int(counts.get(s, 0)) for s in (PASS, FAIL, INFO, ERROR)}

    def failures(self) -> pd.DataFrame:
        return self.checks[self.checks["status"].isin([FAIL, ERROR])]


class _Collector:
    def __init__(self, report: str, prefix: str):
        self.report, self.prefix = report, prefix
        self.rows: list[dict] = []
        self.details: dict[str, pd.DataFrame] = {}

    def _id(self) -> str:
        return f"{self.prefix}-{len(self.rows) + 1:02d}"

    def value(self, check, expected, calculated, *, tolerance=0.0, note=""):
        """Compare two scalar values (``tolerance`` may be a :class:`Rounded`)."""
        if _is_number(expected) and _is_number(calculated):
            both_int = isinstance(expected, (int, np.integer)) and isinstance(calculated, (int, np.integer))
            diff = int(calculated) - int(expected) if both_int else float(calculated) - float(expected)
            max_abs = float(abs(diff))
            if isinstance(tolerance, Rounded):
                ok = abs(round(float(calculated), tolerance.decimals) - float(expected)) <= 1e-9
                tolerance = tolerance.half_unit
            else:
                ok = (np.isnan(expected) and np.isnan(calculated)) or abs(diff) <= tolerance
        else:
            diff, max_abs = (0 if expected == calculated else None), np.nan
            ok = expected == calculated
        self.rows.append(
            dict(check_id=self._id(), report=self.report, check=check, expected=expected, calculated=calculated,
                 difference=diff, max_abs_difference=max_abs, tolerance=tolerance,
                 status=PASS if ok else FAIL, note=note)
        )

    def info(self, check, calculated, *, expected=None, note=""):
        self.rows.append(
            dict(check_id=self._id(), report=self.report, check=check, expected=expected, calculated=calculated,
                 difference=None, max_abs_difference=np.nan, tolerance=np.nan, status=INFO, note=note)
        )

    def error(self, check, note):
        self.rows.append(
            dict(check_id=self._id(), report=self.report, check=check, expected=None, calculated=None,
                 difference=None, max_abs_difference=np.nan, tolerance=np.nan, status=ERROR, note=note)
        )

    def table(
        self,
        name: str,
        expected: pd.DataFrame,
        calculated: pd.DataFrame,
        key: list[str],
        columns: Mapping[str, "float | Rounded"],
        *,
        notes: Mapping[str, str] | None = None,
    ) -> None:
        """Row-level comparison keyed on ``key``; ``columns`` maps column -> tolerance."""
        notes = notes or {}
        self.value(f"{name}: row count", len(expected), len(calculated))
        merged = expected.merge(calculated, on=key, how="outer", suffixes=("_expected", "_calculated"), indicator=True)
        only_exp = merged[merged["_merge"] == "left_only"]
        only_calc = merged[merged["_merge"] == "right_only"]
        cid = self._id()
        self.value(f"{name}: keys in report but not recalculated", 0, len(only_exp),
                   note=f"Key: {', '.join(key)}")
        if len(only_exp):
            self.details[cid] = only_exp[key]
        cid = self._id()
        self.value(f"{name}: keys recalculated but not in report", 0, len(only_calc))
        if len(only_calc):
            self.details[cid] = only_calc[key]

        both = merged[merged["_merge"] == "both"]
        for col, tol in columns.items():
            e, c = both[f"{col}_expected"], both[f"{col}_calculated"]
            if _numeric(e) and _numeric(c):
                ef, cf = e.astype("float64"), c.astype("float64")
                absdiff = (cf - ef).abs()
                max_abs = float(absdiff.max()) if len(absdiff) else 0.0
                if isinstance(tol, Rounded):
                    match = ((cf.round(tol.decimals) - ef).abs() <= 1e-9) | (ef.isna() & cf.isna())
                    tol = tol.half_unit
                else:
                    match = (absdiff <= tol) | (ef.isna() & cf.isna())
            else:
                match = (e.astype("object") == c.astype("object")) | (e.isna() & c.isna())
                max_abs = np.nan
            n_ok = int(match.sum())
            cid = self._id()
            self.rows.append(
                dict(check_id=cid, report=self.report, check=f"{name}: '{col}' matches row by row",
                     expected=len(both), calculated=n_ok, difference=n_ok - len(both),
                     max_abs_difference=max_abs, tolerance=tol,
                     status=PASS if n_ok == len(both) else FAIL,
                     note=notes.get(col, "expected = rows compared, calculated = rows matching"))
            )
            if n_ok != len(both):
                self.details[cid] = both.loc[~match, [*key, f"{col}_expected", f"{col}_calculated"]]


def _is_number(v) -> bool:
    return isinstance(v, (int, float, np.integer, np.floating)) and not isinstance(v, bool)


def _numeric(s: pd.Series) -> bool:
    return pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)


# =========================================================================== #
# Recalculations of each report (in memory only)
# =========================================================================== #
def recalc_activity_frequency(events: pd.DataFrame) -> pd.DataFrame:
    return m.activity_frequency(events).rename(columns={"activity": "concept:name"})


def recalc_application_outcomes(summary: pd.DataFrame) -> pd.DataFrame:
    """Distribution of the LAST RECORDED ACTIVITY (not the final status)."""
    counts = summary["last_activity"].value_counts()
    out = counts.rename_axis("final_activity").reset_index(name="applications")
    out["percentage"] = out["applications"] / len(summary) * 100 if len(summary) else np.nan
    return out


def recalc_application_performance(summary: pd.DataFrame) -> pd.DataFrame:
    return summary[["case_id", "start_time", "end_time", "total_events", "unique_activities", "duration_days"]].rename(
        columns={"case_id": "case:concept:name"}
    )


def recalc_process_bottlenecks(events: pd.DataFrame, transitions: pd.DataFrame | None = None) -> pd.DataFrame:
    transitions = m.transition_metrics(events) if transitions is None else transitions
    return transitions[
        ["activity", "next_activity", "transition_count", "average_wait_hours", "median_wait_hours", "p90_wait_hours"]
    ].rename(columns={"activity": "concept:name"})


def workflow_only_composition(events: pd.DataFrame, followup: pd.DataFrame) -> pd.DataFrame:
    """For 'Workflow activity only' applications: non-workflow events strictly between
    the first O_Cancelled and A_Cancelled. The pattern label is NOT changed."""
    wf = followup[followup["pattern"] == m.PATTERN_WORKFLOW].set_index("case_id")
    if wf.empty:
        return pd.DataFrame(columns=["case_id", "non_workflow_activities", "has_offer_events", "has_application_events"])
    ev = m.prepare_events(events)
    sub = ev[ev[m.C].isin(wf.index)].join(
        wf[["first_offer_cancellation_time", "application_cancellation_time"]], on=m.C
    )
    between = sub[(sub[m.T] > sub["first_offer_cancellation_time"]) & (sub[m.T] < sub["application_cancellation_time"])]
    act = between[m.A].astype("str")
    non_wf = between[~act.str.startswith("W_")]
    per = non_wf.groupby(non_wf[m.C].astype("str"))[m.A].agg(lambda s: ", ".join(sorted(set(map(str, s)))))
    out = per.rename("non_workflow_activities").rename_axis("case_id").reset_index()
    out["has_offer_events"] = out["non_workflow_activities"].str.contains(r"(?:^|, )O_", regex=True)
    out["has_application_events"] = out["non_workflow_activities"].str.contains(r"(?:^|, )A_", regex=True)
    return out


@dataclass
class _Context:
    """Metrics recalculated once and shared by all report checks."""

    events: pd.DataFrame
    summary: pd.DataFrame
    followup: pd.DataFrame
    transitions: pd.DataFrame
    offers: m.OfferMetrics
    offer_cancellations: m.OfferCancellationMetrics
    application_cancellations: m.ApplicationCancellationMetrics


def _build_context(events: pd.DataFrame, dataset_end) -> _Context:
    ev = m.prepare_events(events, purpose="Reconciliation")
    summary = m.application_summary(ev, dataset_end=dataset_end)
    return _Context(
        events=ev,
        summary=summary,
        followup=m.offer_first_cancellation_followup(ev),
        transitions=m.transition_metrics(ev),
        offers=m.offer_metrics(ev),
        offer_cancellations=m.offer_cancellation_metrics(ev, summary),
        application_cancellations=m.application_cancellation_metrics(ev, summary),
    )


# =========================================================================== #
# Per-report reconciliation
# =========================================================================== #
def _reconcile_activity_frequency(col: _Collector, events, report):
    calc = recalc_activity_frequency(events)
    col.table("Activities", report, calc, ["concept:name"], {"total_events": 0, "applications": 0},
              notes={"total_events": "Counts every lifecycle transition (schedule/start/suspend/...), as the report does."})
    col.value("Total events (sum)", int(report["total_events"].sum()), int(calc["total_events"].sum()))


def _reconcile_application_outcomes(col: _Collector, summary, report):
    calc = recalc_application_outcomes(summary)
    note = "Last recorded activity per application - NOT the final status."
    col.table("Last-activity categories", report, calc, ["final_activity"],
              {"applications": 0, "percentage": ROUNDED_2DP},
              notes={"applications": note, "percentage": "Report value = recalculated value rounded to 2 decimals."})
    col.value("Applications (sum)", int(report["applications"].sum()), len(summary), note=note)
    final = m.final_status_distribution(summary)
    col.info(
        "True final status (for contrast, not compared)",
        "; ".join(f"{r.label}: {r.applications:,}" for r in final.itertuples()),
        note="This report must not be used as an outcome report.",
    )


def _reconcile_application_performance(col: _Collector, summary, report):
    calc = recalc_application_performance(summary)
    col.table(
        "Applications", report, calc, ["case:concept:name"],
        {"start_time": 0, "end_time": 0, "total_events": 0, "unique_activities": 0, "duration_days": FULL_PRECISION_TOL},
        notes={"duration_days": "Full-precision float; tolerance covers floating-point rounding only."},
    )
    for label, q in (("Median duration_days", 0.5), ("90th percentile duration_days", 0.9)):
        col.value(label, float(report["duration_days"].quantile(q)), float(calc["duration_days"].quantile(q)),
                  tolerance=FULL_PRECISION_TOL)


def _reconcile_delays(col: _Collector, events, report):
    calc = m.cancellation_closure_events(events)
    col.table(
        "Cancellation closures", report, calc[list(report.columns)], ["case_id"],
        {"cancellation_time": 0, "completion_time": 0, "delay_hours": DELAY_ROUNDING},
        notes={"delay_hours": "Report stores delay_hours rounded (all 0.0); 2-decimal rounding assumed."},
    )
    own = (report["completion_time"] - report["cancellation_time"]).dt.total_seconds() / 3600
    col.value("Report delay_hours consistent with its own timestamps",
              0.0, float((own.round(2) - report["delay_hours"]).abs().max()), tolerance=0.0,
              note="Recomputed from the report's timestamps; residual is the report's rounding.")
    col.info("Largest true delay (seconds)", round(float(calc["delay_hours"].max()) * 3600, 3),
             note="Millisecond-level: the system closes the work item when the application is cancelled. "
                  "Not a business delay.")
    if "completion_lifecycle" in calc:
        col.info("Lifecycle of the closing W_Complete application event",
                 "; ".join(f"{k}: {v}" for k, v in calc["completion_lifecycle"].value_counts().items()))


def _reconcile_followup(col: _Collector, events, followup, report):
    bool_cols = ["new_offer", "offer_sent", "workflow_activity", "returned_offer"]
    calc = followup[list(report.columns)].copy()
    rep = report.copy()
    for b in bool_cols:
        rep[b] = rep[b].astype(bool)
    col.table(
        "Offer-first applications", rep, calc, ["case_id"],
        {"gap_days": FULL_PRECISION_TOL, "events_between": 0, "pattern": 0, **{b: 0 for b in bool_cols}},
        notes={"pattern": "Original pattern labels and precedence are kept unchanged."},
    )
    for pattern in sorted(m.PATTERN_PRECEDENCE):
        col.value(f"Applications with pattern '{pattern}'",
                  int((report["pattern"] == pattern).sum()), int((followup["pattern"] == pattern).sum()))
    statuses = followup["final_status"].value_counts(dropna=False)
    col.info("Final status of the offer-first population",
             "; ".join(f"{k}: {v}" for k, v in statuses.items()),
             note="All ended A_Cancelled: activity after an offer cancellation is not recovery.")
    comp = workflow_only_composition(events, followup)
    n_wf = int((followup["pattern"] == m.PATTERN_WORKFLOW).sum())
    col.info(
        "'Workflow activity only' applications that also contain non-workflow events",
        f"{len(comp)} of {n_wf} (offer events: {int(comp['has_offer_events'].sum())}, "
        f"application events: {int(comp['has_application_events'].sum())})",
        note="Category kept as in the original report; the label overstates 'only'. "
             "Additional events are O_Returned, O_Cancelled, A_Incomplete and A_Validating.",
    )
    if len(comp):
        col.details[col.rows[-1]["check_id"]] = comp


def _reconcile_pattern_summary(col: _Collector, followup, report, followup_report):
    calc = m.followup_pattern_summary(followup)
    numeric = {c: ROUNDED_2DP for c in report.columns if c != "pattern"}
    for c in ("applications", "applications_with_new_offers", "applications_with_offers_sent",
              "applications_with_workflow_activity", "applications_with_returned_offers"):
        numeric[c] = 0
    col.table("Patterns", report, calc, ["pattern"], numeric,
              notes={c: "Report value = recalculated value rounded to 2 decimals."
                     for c, t in numeric.items() if isinstance(t, Rounded)})
    col.value("Applications (sum) = offer-first population", int(report["applications"].sum()), len(followup))
    if followup_report is not None:
        from_csv = followup_report.assign(
            **{b: followup_report[b].astype(bool) for b in ("new_offer", "offer_sent", "workflow_activity", "returned_offer")}
        )
        internal = m.followup_pattern_summary(from_csv)
        diff = (internal.set_index("pattern")[report.columns[1:]].astype(float).round(2)
                - report.set_index("pattern")[report.columns[1:]].astype(float)).abs().max().max()
        col.value("Summary CSV consistent with follow-up CSV (report vs report)", 0.0, float(diff),
                  note="Aggregating the follow-up CSV and rounding to 2 decimals reproduces the summary CSV exactly.")


def _reconcile_bottlenecks(col: _Collector, ctx: _Context, report):
    calc = recalc_process_bottlenecks(ctx.events, ctx.transitions)
    tol = FULL_PRECISION_TOL
    col.table(
        "Transitions", report, calc, ["concept:name", "next_activity"],
        {"transition_count": 0, "average_wait_hours": tol, "median_wait_hours": tol, "p90_wait_hours": tol},
        notes={"average_wait_hours": "Means can differ by ~1e-14 h from floating-point summation order."},
    )
    col.value("Transitions in report below the 100-occurrence threshold", 0,
              int((report["transition_count"] < m.DEFAULT_MIN_TRANSITION_COUNT).sum()),
              note="Report keeps only pairs occurring at least 100 times; recalculation uses the same threshold.")
    flagged = ctx.transitions[ctx.transitions["likely_timer_driven"].fillna(False)]
    col.info("Transitions flagged as likely timer-driven (exploratory)",
             "; ".join(f"{a} -> {b}" for a, b in zip(flagged["activity"], flagged["next_activity"])),
             note="Likely, not confirmed: target recorded mostly by User_1 (likely system account) and median wait >= 24 h.")


def _reconcile_kpis(col: _Collector, ctx: _Context, reports):
    summary = ctx.summary
    af = reports.get("activity_frequency")
    perf = reports.get("application_performance")
    outcomes = reports.get("application_outcomes")
    fu_rep = reports.get("offer_cancellation_followup_patterns")
    om, oc, ac = ctx.offers, ctx.offer_cancellations, ctx.application_cancellations

    def freq(activity, column):
        row = af.loc[af["concept:name"] == activity, column]
        return int(row.iloc[0]) if len(row) else 0

    if perf is not None:
        col.value("Total applications vs application_performance.csv rows", len(perf), len(summary))
        col.value("Median duration (days) vs application_performance.csv", float(perf["duration_days"].median()),
                  float(m.duration_statistics(summary).iloc[0]["median"]), tolerance=FULL_PRECISION_TOL)
        col.value("90th percentile duration (days) vs application_performance.csv",
                  float(perf["duration_days"].quantile(0.9)),
                  float(m.duration_statistics(summary).iloc[0]["p90"]), tolerance=FULL_PRECISION_TOL)
    if outcomes is not None:
        col.value("Total applications vs application_outcomes.csv sum", int(outcomes["applications"].sum()), len(summary))
    if af is not None:
        col.value("O_Cancelled events (offer cancellation)", freq("O_Cancelled", "total_events"), oc.offer_cancellation_events)
        col.value("Applications with O_Cancelled", freq("O_Cancelled", "applications"), oc.applications_with_offer_cancellation)
        col.value("A_Cancelled events (application cancellation)", freq("A_Cancelled", "total_events"),
                  ac.application_cancellation_events)
        col.value("Final status A_Cancelled vs A_Cancelled applications", freq("A_Cancelled", "applications"),
                  ac.applications_final_status_cancelled)
        col.value("A_Pending (offer accepted) vs A_Pending applications", freq("A_Pending", "applications"),
                  int((summary["final_status"] == config.APPLICATION_PENDING).sum()))
        col.value("A_Denied final status vs A_Denied applications", freq("A_Denied", "applications"),
                  int((summary["final_status"] == config.APPLICATION_DENIED).sum()))
        col.value("Offers created vs O_Create Offer events", freq("O_Create Offer", "total_events"), om.offers_created)
        col.value("O_Created events (duplicate of O_Create Offer)", freq("O_Created", "total_events"), om.o_created_events)
        col.value("Offers sent vs O_Sent events (both channels)",
                  freq("O_Sent (mail and online)", "total_events") + freq("O_Sent (online only)", "total_events"),
                  om.offers_sent, note="Each offer is sent at most once, so events = distinct offers.")
        col.value("Offers returned vs O_Returned events", freq("O_Returned", "total_events"), om.offers_returned)
        col.value("Offers accepted vs O_Accepted events", freq("O_Accepted", "total_events"), om.offers_accepted)
        col.value("Offers refused vs O_Refused events", freq("O_Refused", "total_events"), om.offers_refused)
        col.value("Offers cancelled vs O_Cancelled events", freq("O_Cancelled", "total_events"), om.offers_cancelled)
    if fu_rep is not None:
        col.value("Offer-first cancellation population", len(fu_rep),
                  int((ac.sequence_breakdown.set_index("sequence").loc[m.SEQ_OFFER_FIRST, "applications"])),
                  note="Cross-check: application_cancellation_table sequence 'O_Cancelled before A_Cancelled'.")
    col.info("O_Cancelled and A_Cancelled are different measures",
             f"{oc.applications_with_offer_cancellation:,} applications with O_Cancelled vs "
             f"{ac.applications_with_application_cancellation:,} with A_Cancelled; "
             f"{int(oc.final_status_breakdown.set_index('final_status').loc[config.APPLICATION_PENDING, 'applications']):,} "
             "applications with an O_Cancelled still ended A_Pending (offer accepted).")


# =========================================================================== #
# Entry point
# =========================================================================== #
_REPORT_PREFIX = {
    "activity_frequency": "AF",
    "application_outcomes": "AO",
    "application_performance": "AP",
    "cancellation_completion_delays": "CD",
    "offer_cancellation_followup_patterns": "FP",
    "offer_cancellation_pattern_summary": "PS",
    "process_bottlenecks": "PB",
}


def reconcile_all(
    events: pd.DataFrame,
    reports: Mapping[str, pd.DataFrame],
    report_errors: Mapping[str, DashboardError] | None = None,
    *,
    dataset_end: pd.Timestamp | None = None,
) -> ReconciliationResult:
    """Recalculate all seven reports from ``events`` and compare with ``reports``.

    ``reports`` maps report name -> validated DataFrame (see
    :func:`core.loaders.load_all_reports`). Missing reports produce ERROR rows; the
    remaining reports are still reconciled.
    """
    report_errors = dict(report_errors or {})
    ctx = _build_context(events, dataset_end)
    ev, summary, followup = ctx.events, ctx.summary, ctx.followup

    collectors: list[_Collector] = []
    runners = {
        "activity_frequency": lambda c, r: _reconcile_activity_frequency(c, ev, r),
        "application_outcomes": lambda c, r: _reconcile_application_outcomes(c, summary, r),
        "application_performance": lambda c, r: _reconcile_application_performance(c, summary, r),
        "cancellation_completion_delays": lambda c, r: _reconcile_delays(c, ev, r),
        "offer_cancellation_followup_patterns": lambda c, r: _reconcile_followup(c, ev, followup, r),
        "offer_cancellation_pattern_summary": lambda c, r: _reconcile_pattern_summary(
            c, followup, r, reports.get("offer_cancellation_followup_patterns")
        ),
        "process_bottlenecks": lambda c, r: _reconcile_bottlenecks(c, ctx, r),
    }
    for name, run in runners.items():
        col = _Collector(config.REPORT_FILES[name], _REPORT_PREFIX[name])
        if name not in reports:
            err = report_errors.get(name)
            col.error("Report available", err.user_message if err else "Report was not provided.")
        else:
            run(col, reports[name])
        collectors.append(col)

    kpi = _Collector("Headline KPIs (cross-report)", "KPI")
    _reconcile_kpis(kpi, ctx, reports)
    collectors.append(kpi)

    rows = [r for c in collectors for r in c.rows]
    details = {k: v for c in collectors for k, v in c.details.items()}
    checks = pd.DataFrame({c: pd.Series([r[c] for r in rows], dtype="object") for c in CHECK_COLUMNS})
    for c in ("max_abs_difference", "tolerance"):
        checks[c] = checks[c].astype("float64")
    return ReconciliationResult(checks=checks, details=details)
