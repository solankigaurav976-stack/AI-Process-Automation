"""Drill-down: individual occurrences behind the transition statistics.

Additive module (Phase 5). It does not change any existing calculation. It
returns the individual waits that :func:`core.metrics.transition_metrics`
summarises, using exactly the same event ordering (:func:`prepare_events`),
the same next-event rule (next event of the same application) and the same
wait formula, so that for every activity pair:

    count, median and 90th percentile of ``transition_instances(...)["wait_hours"]``
    == transition_count, median_wait_hours and p90_wait_hours of ``transition_metrics``.

This equivalence is enforced by tests on synthetic and real data.
"""

from __future__ import annotations

import pandas as pd

from core import metrics as m

INSTANCE_COLUMNS = {
    "case_id": "str",
    "activity": "str",
    "next_activity": "str",
    "event_time": "datetime64[us, UTC]",
    "next_event_time": "datetime64[us, UTC]",
    "wait_hours": "float64",
    "next_resource": "str",
}


def transition_instances(
    events: pd.DataFrame,
    activity: str | None = None,
    next_activity: str | None = None,
) -> pd.DataFrame:
    """One row per occurrence of a directly-follows pair (optionally one pair only).

    The next event is determined on the full, ordered event log *before* any pair
    filter is applied, so filtering never changes which event counts as "next".
    ``next_resource`` is the resource recorded on the next event (missing when the
    resource column is not loaded); it is shown as recorded, with no role implied.
    """
    ev = m.prepare_events(events, purpose="Transition drill-down")
    if ev.empty:
        return m._empty(INSTANCE_COLUMNS)

    g = ev.groupby(m.C, sort=False)
    next_ts = g[m.T].shift(-1)
    out = pd.DataFrame(
        {
            "case_id": ev[m.C],
            "activity": ev[m.A],
            "next_activity": g[m.A].shift(-1),
            "event_time": ev[m.T],
            "next_event_time": next_ts,
            "wait_hours": (next_ts - ev[m.T]).dt.total_seconds() / 3600,
            "next_resource": g[m.R].shift(-1) if m.R in ev.columns else pd.NA,
        }
    )
    out = out.dropna(subset=["next_activity", "wait_hours"])
    if activity is not None:
        out = out[out["activity"] == activity]
    if next_activity is not None:
        out = out[out["next_activity"] == next_activity]
    out = out.reset_index(drop=True)
    for col in ("case_id", "activity", "next_activity"):
        out[col] = m._as_str(out[col])
    if m.R in ev.columns:
        out["next_resource"] = m._as_str(out["next_resource"])
    return out[list(INSTANCE_COLUMNS)]


def instance_statistics(instances: pd.DataFrame) -> dict:
    """Count, median and 90th percentile of ``wait_hours`` (same methods as transition_metrics)."""
    w = instances["wait_hours"]
    if w.empty:
        return {"transition_count": 0, "median_wait_hours": float("nan"), "p90_wait_hours": float("nan")}
    return {
        "transition_count": int(w.size),
        "median_wait_hours": float(w.median()),
        "p90_wait_hours": float(w.quantile(0.9)),
    }
