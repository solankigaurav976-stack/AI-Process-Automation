"""Pure metric functions over the loan application event log.

Inputs are event DataFrames as returned by :func:`core.loaders.load_event_log`
(the processed Parquet file is the primary source). Functions never modify
their inputs, never read or write files and never import Streamlit.

Conventions
-----------
* Events are ordered by application, then timestamp, then original row order
  (a deterministic tie-break; the real log has no same-timestamp ties).
* Derived tables use ``case_id`` for the application identifier.
* Every function accepts an empty event log and returns empty results with the
  documented columns rather than raising.
* Malformed input (missing columns, missing or time-zone-naive timestamps)
  raises :class:`MetricInputError`.

Interpretation rules (see ``core.definitions`` for the user-facing wording)
---------------------------------------------------------------------------
* O_Cancelled (one offer) and A_Cancelled (whole application) are never merged.
* Activity after an offer cancellation is described, never labelled recovery.
* CreditScore = 0 is treated as potentially missing and excluded from statistics.
* Transitions with an automated signature are flagged, not presented as staff waits.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from core import config
from core.errors import DashboardError

C = config.COL_CASE_ID
A = config.COL_ACTIVITY
T = config.COL_TIMESTAMP
L = config.COL_LIFECYCLE
R = config.COL_RESOURCE
EVENT_ID = config.COL_EVENT_ID
OFFER_ID = config.COL_OFFER_ID

# Activities ---------------------------------------------------------------- #
OFFER_CREATE = "O_Create Offer"
OFFER_CREATED_DUPLICATE = "O_Created"  # recorded alongside every O_Create Offer
OFFER_SENT_MAIL_ONLINE = "O_Sent (mail and online)"
OFFER_SENT_ONLINE_ONLY = "O_Sent (online only)"
OFFER_SENT_ACTIVITIES = (OFFER_SENT_MAIL_ONLINE, OFFER_SENT_ONLINE_ONLY)
OFFER_RETURNED = "O_Returned"
OFFER_ACCEPTED = "O_Accepted"
OFFER_REFUSED = "O_Refused"
OFFER_CANCELLED = config.OFFER_CANCELLED
OFFER_OUTCOMES = (OFFER_ACCEPTED, OFFER_REFUSED, OFFER_CANCELLED)
APPLICATION_CANCELLED = config.APPLICATION_CANCELLED
W_COMPLETE_APPLICATION = "W_Complete application"
FINAL_STATUSES = config.FINAL_STATUS_ACTIVITIES

# Evidence-based thresholds (documented in the Phase 2 report) --------------- #
#: Likely, but not confirmed, a system account (creates every online application,
#: records 76% of A_Cancelled events, mostly 06:00-08:00 UTC).
SYSTEM_RESOURCE = "User_1"
#: UTC hours in which the automated morning batch runs.
BATCH_WINDOW_HOURS_UTC = (6, 7)
#: A transition is flagged as likely timer-driven when at least this share of its
#: target events is recorded by SYSTEM_RESOURCE ...
TIMER_MIN_SYSTEM_SHARE = 0.8
#: ... and its median wait is at least this long.
TIMER_MIN_MEDIAN_HOURS = 24.0
#: A_Cancelled by SYSTEM_RESOURCE at least this many days after the last offer
#: was sent is classified as a likely automatic expiry.
EXPIRY_MIN_DAYS_AFTER_LAST_OFFER_SENT = 30.0
#: Start months whose end lies fewer than this many days before the dataset end
#: are flagged as having a short observation window (~99th percentile of time
#: to final status is 59 days).
CENSORING_WINDOW_DAYS = 60
DEFAULT_MIN_TRANSITION_COUNT = 100

# Follow-up pattern labels (as used by the existing report) ----------------- #
PATTERN_NO_ACTIVITY = "No recorded intermediate activity"
PATTERN_REPLACEMENT_OFFER = "Replacement offer created"
PATTERN_OFFER_SENT = "Offer sent, no new offer creation recorded"
PATTERN_WORKFLOW = "Workflow activity only"
PATTERN_OTHER = "Other activity only"
#: Precedence used to assign exactly one pattern per application.
PATTERN_PRECEDENCE = (
    PATTERN_NO_ACTIVITY,
    PATTERN_REPLACEMENT_OFFER,
    PATTERN_OFFER_SENT,
    PATTERN_WORKFLOW,
    PATTERN_OTHER,
)
PATTERN_DESCRIPTIONS = {
    PATTERN_NO_ACTIVITY: "No event recorded between the first O_Cancelled and A_Cancelled.",
    PATTERN_REPLACEMENT_OFFER: "At least one new offer (O_Create Offer) was created in between. "
    "This is not recovery: these applications still ended with A_Cancelled.",
    PATTERN_OFFER_SENT: "An offer was sent in between, but no new offer creation was recorded.",
    PATTERN_WORKFLOW: "Workflow (W_) activity in between, with no offer creation or sending. "
    "May also contain O_Returned or application events.",
    PATTERN_OTHER: "Only non-workflow events in between, in practice further O_Cancelled events.",
}

#: Columns needed by all metric functions (load only these for speed).
METRIC_EVENT_COLUMNS: tuple[str, ...] = (
    C,
    A,
    T,
    L,
    R,
    EVENT_ID,
    OFFER_ID,
    config.COL_LOAN_GOAL,
    config.COL_APPLICATION_TYPE,
    config.COL_REQUESTED_AMOUNT,
    "CreditScore",
    "OfferedAmount",
    "MonthlyCost",
    "NumberOfTerms",
    "FirstWithdrawalAmount",
)

_OFFER_ATTRIBUTES = ("CreditScore", "OfferedAmount", "MonthlyCost", "NumberOfTerms", "FirstWithdrawalAmount")
_CASE_ATTRIBUTES = {
    config.COL_LOAN_GOAL: "loan_goal",
    config.COL_APPLICATION_TYPE: "application_type",
    config.COL_REQUESTED_AMOUNT: "requested_amount",
}


class MetricInputError(DashboardError):
    """The event data passed to a metric function is unusable."""


# =========================================================================== #
# Input preparation
# =========================================================================== #
def _require(events: pd.DataFrame, columns: Iterable[str], purpose: str) -> None:
    if not isinstance(events, pd.DataFrame):
        raise MetricInputError(f"{purpose}: expected a DataFrame of events, got {type(events).__name__}.")
    missing = [c for c in columns if c not in events.columns]
    if missing:
        raise MetricInputError(f"{purpose}: the event data is missing column(s) {missing}.")


def _is_case_time_sorted(events: pd.DataFrame) -> bool:
    """True if each application's events are contiguous and in time order (C categorical)."""
    if len(events) < 2:
        return True
    case = events[C].cat.codes.to_numpy()
    ts = events[T].array.asi8  # int64 UTC; avoids object Timestamps
    same_case = case[1:] == case[:-1]
    runs = int((~same_case).sum()) + 1
    if runs != len(np.unique(case)):
        return False  # an application's events are not contiguous
    return bool(((ts[1:] >= ts[:-1]) | ~same_case).all())


def prepare_events(events: pd.DataFrame, *, purpose: str = "Metric calculation") -> pd.DataFrame:
    """Validate the core columns and return a working copy for metric functions.

    * application ID and activity become categoricals (fast grouping);
    * events are ordered by application, timestamp, then original row order.

    The input is never modified. Already-prepared input is returned as is, so
    call this once and pass the result to several metric functions.
    """
    _require(events, (C, A, T), purpose)
    ts = events[T]
    if not isinstance(ts.dtype, pd.DatetimeTZDtype):
        raise MetricInputError(f"{purpose}: '{T}' must be time-zone-aware timestamps, found {ts.dtype}.")
    if ts.isna().any():
        raise MetricInputError(f"{purpose}: {int(ts.isna().sum())} event(s) have no timestamp.")
    if events[C].isna().any() or events[A].isna().any():
        raise MetricInputError(f"{purpose}: some events have no application ID or activity.")

    ev = events
    if not (isinstance(ev[C].dtype, pd.CategoricalDtype) and isinstance(ev[A].dtype, pd.CategoricalDtype)):
        ev = events.copy(deep=False)
        for col in (C, A):
            if not isinstance(ev[col].dtype, pd.CategoricalDtype):
                ev[col] = ev[col].astype("str").astype("category")
    if _is_case_time_sorted(ev):
        return ev
    keys = pd.DataFrame(
        {"case": ev[C].cat.codes.to_numpy(), "ts": ts.array.asi8, "row": np.arange(len(ev))}
    )
    order = keys.sort_values(["case", "ts", "row"], kind="mergesort").index.to_numpy()
    return ev.iloc[order]


def _fast_isin(values, candidates) -> np.ndarray:
    """Set-based membership test (pandas' Arrow string ``isin`` is slow here).

    Used for offer IDs only (~150k values). Missing values are never members.
    """
    lookup = {v for v in np.asarray(candidates, dtype=object).tolist() if isinstance(v, str)}
    items = np.asarray(values, dtype=object).tolist()  # bulk conversion; per-element Arrow access is slow
    return np.fromiter((v in lookup for v in items), dtype=bool, count=len(items))


def _as_str(series: pd.Series) -> pd.Series:
    """Categorical/object labels back to plain text (missing values preserved)."""
    return series.astype("str").where(series.notna())


def _empty(columns: dict[str, str]) -> pd.DataFrame:
    return pd.DataFrame({name: pd.Series(dtype=dtype) for name, dtype in columns.items()})


def _activity_counts(ev: pd.DataFrame, activities: Sequence[str], index: pd.Index) -> pd.DataFrame:
    sub = ev[ev[A].isin(activities)]
    counts = sub.groupby([C, A]).size().unstack(fill_value=0)
    return counts.reindex(index=index, columns=list(activities), fill_value=0).astype("int64")


def _percent(part, whole):
    return (part / whole * 100.0) if whole else float("nan")


# =========================================================================== #
# Application-level summary
# =========================================================================== #
SUMMARY_COLUMNS = {
    "case_id": "str",
    "start_time": "datetime64[us, UTC]",
    "end_time": "datetime64[us, UTC]",
    "duration_days": "float64",
    "total_events": "int64",
    "unique_activities": "int64",
    "first_activity": "str",
    "last_activity": "str",
    "final_status": "str",
    "final_status_label": "str",
    "final_status_time": "datetime64[us, UTC]",
    "days_to_final_status": "float64",
    "final_status_events": "int64",
    "has_conflicting_final_status": "bool",
    "events_after_final_status": "int64",
    "is_open": "bool",
    "offer_cancellation_events": "int64",
    "application_cancellation_events": "int64",
    "has_offer_cancellation": "bool",
    "has_application_cancellation": "bool",
    "offers_created": "int64",
    "offers_sent": "int64",
    "offers_returned": "int64",
    "start_month": "str",
    "observed_days_until_dataset_end": "float64",
}


def application_summary(events: pd.DataFrame, *, dataset_end: pd.Timestamp | None = None) -> pd.DataFrame:
    """One row per application.

    * ``duration_days`` = last event - first event (same as application_performance.csv).
    * ``final_status`` = activity of the LAST A_Pending / A_Cancelled / A_Denied event;
      missing when none exists (``is_open``). Applications with more than one distinct
      final-status activity are flagged in ``has_conflicting_final_status``.
    * ``observed_days_until_dataset_end`` = dataset end - first event, the longest
      duration that could have been observed for this application.

    ``dataset_end`` defaults to the latest event in ``events``; pass the global end
    (``core.definitions.DATASET_END``) when ``events`` is a filtered subset. It is
    stored in ``result.attrs["dataset_end"]`` for downstream functions.
    """
    ev = prepare_events(events, purpose="Application summary")
    if ev.empty:
        out = _empty(SUMMARY_COLUMNS)
        out.attrs["dataset_end"] = dataset_end
        return out
    end = dataset_end if dataset_end is not None else ev[T].max()

    g = ev.groupby(C, sort=True)
    s = g.agg(
        start_time=(T, "min"),
        end_time=(T, "max"),
        total_events=(A, "size"),
        unique_activities=(A, "nunique"),
        first_activity=(A, "first"),
        last_activity=(A, "last"),
    )
    s["duration_days"] = (s["end_time"] - s["start_time"]).dt.total_seconds() / 86400

    term = ev[ev[A].isin(FINAL_STATUSES)]
    tg = term.groupby(C)
    s["final_status"] = _as_str(tg[A].last().reindex(s.index))
    s["final_status_time"] = tg[T].last().reindex(s.index)
    s["final_status_events"] = tg.size().reindex(s.index, fill_value=0).astype("int64")
    s["has_conflicting_final_status"] = tg[A].nunique().reindex(s.index, fill_value=0).gt(1)
    s["days_to_final_status"] = (s["final_status_time"] - s["start_time"]).dt.total_seconds() / 86400
    # Per event: is it later than its application's final-status event? (vectorised by code)
    final_by_code = s["final_status_time"].reindex(ev[C].cat.categories).array
    codes = ev[C].cat.codes.to_numpy()
    after = (ev[T].array.asi8 > final_by_code.asi8[codes]) & ~final_by_code.isna()[codes]
    s["events_after_final_status"] = (
        pd.Series(after, index=ev.index).groupby(ev[C]).sum().reindex(s.index, fill_value=0).astype("int64")
    )
    s["is_open"] = s["final_status"].isna()
    labels = dict(config.FINAL_STATUS_LABELS)
    s["final_status_label"] = s["final_status"].map(labels).fillna(config.NO_FINAL_STATUS_LABEL)

    counts = _activity_counts(
        ev, (OFFER_CANCELLED, APPLICATION_CANCELLED, OFFER_CREATE, *OFFER_SENT_ACTIVITIES, OFFER_RETURNED), s.index
    )
    s["offer_cancellation_events"] = counts[OFFER_CANCELLED]
    s["application_cancellation_events"] = counts[APPLICATION_CANCELLED]
    s["has_offer_cancellation"] = s["offer_cancellation_events"] > 0
    s["has_application_cancellation"] = s["application_cancellation_events"] > 0
    s["offers_created"] = counts[OFFER_CREATE]
    s["offers_sent"] = counts[list(OFFER_SENT_ACTIVITIES)].sum(axis=1)
    s["offers_returned"] = counts[OFFER_RETURNED]

    s["start_month"] = s["start_time"].dt.strftime("%Y-%m")
    s["observed_days_until_dataset_end"] = (end - s["start_time"]).dt.total_seconds() / 86400

    for source, target in _CASE_ATTRIBUTES.items():
        if source in ev.columns:
            s[target] = g[source].first()

    out = s.reset_index().rename(columns={C: "case_id"})
    for col in ("case_id", "first_activity", "last_activity"):
        out[col] = _as_str(out[col])
    ordered = list(SUMMARY_COLUMNS) + [c for c in _CASE_ATTRIBUTES.values() if c in out.columns]
    out = out[ordered]
    out.attrs["dataset_end"] = end
    return out


def final_status_distribution(summary: pd.DataFrame) -> pd.DataFrame:
    """Applications per final status, always including all four categories."""
    order = [*FINAL_STATUSES, None]
    total = len(summary)
    rows = []
    for status in order:
        if status is None:
            n = int(summary["is_open"].sum()) if total else 0
            label = config.NO_FINAL_STATUS_LABEL
        else:
            n = int((summary["final_status"] == status).sum()) if total else 0
            label = config.FINAL_STATUS_LABELS[status]
        rows.append({"final_status": status, "label": label, "applications": n, "percentage": _percent(n, total)})
    return pd.DataFrame(rows)


def _describe(values: pd.Series) -> dict:
    v = values.dropna()
    if v.empty:
        return {"count": 0, "mean": np.nan, "median": np.nan, "p90": np.nan, "min": np.nan, "max": np.nan}
    return {
        "count": int(v.size),
        "mean": float(v.mean()),
        "median": float(v.median()),
        "p90": float(v.quantile(0.9)),
        "min": float(v.min()),
        "max": float(v.max()),
    }


def duration_statistics(
    summary: pd.DataFrame,
    *,
    column: str = "duration_days",
    include_open: bool = True,
    by: str | None = None,
) -> pd.DataFrame:
    """Count, mean, median, 90th percentile, min and max of a duration column.

    ``open_applications`` reports how many applications in each group have no final
    status (their durations are cut off by the dataset end).
    """
    if column not in ("duration_days", "days_to_final_status"):
        raise MetricInputError(f"Unsupported duration column '{column}'.")
    data = summary if include_open else summary[~summary["is_open"].astype(bool)]

    def row(frame):
        return {**_describe(frame[column]), "open_applications": int(frame["is_open"].sum())}

    if by is None:
        return pd.DataFrame([{"group": "All applications", **row(data)}])
    if data.empty:
        return pd.DataFrame(columns=["group", "count", "mean", "median", "p90", "min", "max", "open_applications"])
    rows = [{"group": key, **row(frame)} for key, frame in data.groupby(by, sort=True)]
    return pd.DataFrame(rows)


def monthly_trend(
    summary: pd.DataFrame,
    *,
    dataset_end: pd.Timestamp | None = None,
    censoring_window_days: int = CENSORING_WINDOW_DAYS,
) -> pd.DataFrame:
    """Applications by start month with final-status mix and censoring flags.

    ``censoring_warning`` is True when a month still has open applications or its
    end lies less than ``censoring_window_days`` before the dataset end.
    """
    columns = [
        "start_month", "applications", *FINAL_STATUSES, "no_final_status", "median_duration_days",
        "p90_duration_days", "days_observed_after_month_end", "has_open_applications",
        "short_observation_window", "censoring_warning",
    ]
    if summary.empty:
        return pd.DataFrame(columns=columns)
    end = dataset_end
    if end is None:
        end = summary.attrs.get("dataset_end")
    if end is None:
        end = summary["end_time"].max()

    rows = []
    for month, frame in summary.groupby("start_month", sort=True):
        month_end = (pd.Period(month, freq="M") + 1).start_time.tz_localize("UTC")
        observed = (end - month_end).total_seconds() / 86400
        open_n = int(frame["is_open"].sum())
        status_counts = {s: int((frame["final_status"] == s).sum()) for s in FINAL_STATUSES}
        short = observed < censoring_window_days
        rows.append(
            {
                "start_month": month,
                "applications": len(frame),
                **status_counts,
                "no_final_status": open_n,
                "median_duration_days": float(frame["duration_days"].median()),
                "p90_duration_days": float(frame["duration_days"].quantile(0.9)),
                "days_observed_after_month_end": observed,
                "has_open_applications": open_n > 0,
                "short_observation_window": short,
                "censoring_warning": open_n > 0 or short,
            }
        )
    return pd.DataFrame(rows, columns=columns)


# =========================================================================== #
# Activity frequency and transitions
# =========================================================================== #
def activity_frequency(events: pd.DataFrame, *, lifecycle: Iterable[str] | None = None) -> pd.DataFrame:
    """Events and distinct applications per activity.

    With ``lifecycle=None`` every lifecycle transition counts (as in
    activity_frequency.csv). Pass e.g. ``["complete"]`` to count completions only.
    """
    _require(events, (C, A), "Activity frequency")
    ev = events
    if lifecycle is not None:
        _require(events, (L,), "Activity frequency by lifecycle")
        ev = events[events[L].isin(list(lifecycle))]
    if ev.empty:
        return _empty({"activity": "str", "total_events": "int64", "applications": "int64"})
    out = ev.groupby(A, observed=True).agg(total_events=(A, "size"), applications=(C, "nunique")).reset_index()
    out = out.rename(columns={A: "activity"})
    out["activity"] = _as_str(out["activity"])
    return out.sort_values(["total_events", "activity"], ascending=[False, True], kind="mergesort").reset_index(drop=True)


def activity_lifecycle_breakdown(events: pd.DataFrame) -> pd.DataFrame:
    """Event counts per activity and lifecycle transition (shows count inflation)."""
    _require(events, (A, L), "Lifecycle breakdown")
    if events.empty:
        return pd.DataFrame(columns=["activity", "total_events"])
    table = pd.crosstab(events[A], events[L])
    table["total_events"] = table.sum(axis=1)
    table = table.sort_values("total_events", ascending=False, kind="mergesort")
    table.columns.name = None
    table = table.reset_index().rename(columns={A: "activity"})
    table["activity"] = _as_str(table["activity"])
    return table


def _origin(activity: pd.Series) -> pd.Series:
    prefix = activity.str[:2]
    return prefix.map({"A_": "Application", "O_": "Offer", "W_": "Workflow"}).fillna("Other")


TRANSITION_COLUMNS = {
    "activity": "str",
    "next_activity": "str",
    "transition_count": "int64",
    "average_wait_hours": "float64",
    "median_wait_hours": "float64",
    "p90_wait_hours": "float64",
    "origin": "str",
    "next_origin": "str",
    "crosses_event_streams": "bool",
    "target_system_share": "float64",
    "target_batch_window_share": "float64",
    "likely_timer_driven": "boolean",
    "interpretation": "str",
}


def transition_metrics(events: pd.DataFrame, *, min_count: int = DEFAULT_MIN_TRANSITION_COUNT) -> pd.DataFrame:
    """Waiting time between directly consecutive events within each application.

    Matches process_bottlenecks.csv for the first six columns, plus:

    * ``crosses_event_streams`` - source and target come from different streams
      (Application / Offer / Workflow); such pairs often reflect interleaving, not hand-offs.
    * ``target_system_share`` - share of target events recorded by User_1 (likely, but
      not confirmed, a system account).
    * ``target_batch_window_share`` - share of target events in the 06:00-08:00 UTC batch window.
    * ``likely_timer_driven`` - target_system_share >= 0.8 and median wait >= 24 h.
      Missing (NA) if the resource column is unavailable.
    """
    ev = prepare_events(events, purpose="Transition metrics")
    if ev.empty:
        return _empty(TRANSITION_COLUMNS)
    g = ev.groupby(C, sort=False)
    nxt = pd.DataFrame(
        {
            "activity": ev[A],
            "next_activity": g[A].shift(-1),
            "wait_hours": (g[T].shift(-1) - ev[T]).dt.total_seconds() / 3600,
            "next_hour": g[T].shift(-1).dt.hour,
        }
    )
    has_resource = R in ev.columns
    if has_resource:
        nxt["next_is_system"] = g[R].shift(-1) == SYSTEM_RESOURCE
    nxt = nxt.dropna(subset=["next_activity", "wait_hours"])
    if nxt.empty:
        return _empty(TRANSITION_COLUMNS)
    nxt["in_batch_window"] = nxt["next_hour"].isin(BATCH_WINDOW_HOURS_UTC)

    tg = nxt.groupby(["activity", "next_activity"], sort=True)
    out = tg["wait_hours"].agg(
        transition_count="size", average_wait_hours="mean", median_wait_hours="median"
    )
    out["p90_wait_hours"] = tg["wait_hours"].quantile(0.9)
    out["target_batch_window_share"] = tg["in_batch_window"].mean()
    out["target_system_share"] = tg["next_is_system"].mean() if has_resource else np.nan
    out = out.reset_index()
    out = out[out["transition_count"] >= min_count].copy()
    out["activity"] = _as_str(out["activity"])
    out["next_activity"] = _as_str(out["next_activity"])

    out["origin"] = _origin(out["activity"])
    out["next_origin"] = _origin(out["next_activity"])
    out["crosses_event_streams"] = out["origin"] != out["next_origin"]
    if has_resource:
        timer = (out["target_system_share"] >= TIMER_MIN_SYSTEM_SHARE) & (
            out["median_wait_hours"] >= TIMER_MIN_MEDIAN_HOURS
        )
        out["likely_timer_driven"] = timer.astype("boolean")
        out["interpretation"] = np.where(
            timer,
            "Likely automated timer/batch step - not a staff waiting time",
            "Waiting time between recorded events",
        )
    else:
        out["likely_timer_driven"] = pd.array([pd.NA] * len(out), dtype="boolean")
        out["interpretation"] = "Cannot assess automation (no resource data)"
    out = out.sort_values(
        ["median_wait_hours", "activity", "next_activity"], ascending=[False, True, True], kind="mergesort"
    )
    return out[list(TRANSITION_COLUMNS)].reset_index(drop=True)


# =========================================================================== #
# Offers
# =========================================================================== #
OFFER_TABLE_COLUMNS = {
    "offer_id": "str",
    "case_id": "str",
    "created_time": "datetime64[us, UTC]",
    "was_sent": "bool",
    "sent_channel": "str",
    "was_returned": "bool",
    "offer_outcome": "str",
    "offer_outcome_time": "datetime64[us, UTC]",
}


def offer_table(events: pd.DataFrame) -> pd.DataFrame:
    """One row per offer created (identified by the O_Create Offer EventID).

    Later offer events reference the offer through OfferID. ``offer_outcome`` is the
    last of O_Accepted / O_Refused / O_Cancelled, or "No outcome recorded".
    Offer attributes (CreditScore, OfferedAmount, ...) come from the creation event.
    """
    ev = prepare_events(events, purpose="Offer table")
    _require(ev, (EVENT_ID, OFFER_ID), "Offer table")
    created = ev[ev[A] == OFFER_CREATE]
    if created.empty:
        cols = dict(OFFER_TABLE_COLUMNS)
        cols.update({a: "float64" for a in _OFFER_ATTRIBUTES if a in ev.columns})
        return _empty(cols)

    out = pd.DataFrame(
        {"offer_id": created[EVENT_ID].to_numpy(), "case_id": created[C].to_numpy(), "created_time": created[T].to_numpy()}
    )
    for attr in _OFFER_ATTRIBUTES:
        if attr in created.columns:
            out[attr] = created[attr].to_numpy()
    out = out.drop_duplicates("offer_id", keep="first").set_index("offer_id")

    later = ev[ev[OFFER_ID].notna()]
    sent = _as_str(later[later[A].isin(OFFER_SENT_ACTIVITIES)].groupby(OFFER_ID)[A].last())
    out["was_sent"] = _fast_isin(out.index, sent.index)
    out["sent_channel"] = sent.reindex(out.index).str.replace("O_Sent ", "", regex=False).str.strip("()")
    out["was_returned"] = _fast_isin(out.index, later.loc[later[A] == OFFER_RETURNED, OFFER_ID].unique())
    outcomes = later[later[A].isin(OFFER_OUTCOMES)].groupby(OFFER_ID)
    out["offer_outcome"] = _as_str(outcomes[A].last().reindex(out.index)).fillna("No outcome recorded")
    out["offer_outcome_time"] = outcomes[T].last().reindex(out.index)

    out["case_id"] = _as_str(out["case_id"])
    ordered = list(OFFER_TABLE_COLUMNS)[1:] + [a for a in _OFFER_ATTRIBUTES if a in out.columns]
    return out[ordered].reset_index()


@dataclass(frozen=True)
class OfferMetrics:
    offers_created: int
    o_create_offer_events: int
    o_created_events: int
    offers_sent: int
    offers_sent_mail_and_online: int
    offers_sent_online_only: int
    offers_returned: int
    offers_accepted: int
    offers_refused: int
    offers_cancelled: int
    offers_without_outcome: int
    applications_with_offers: int
    median_offers_per_application: float
    max_offers_per_application: int
    offer_return_rate_percent: float
    orphan_offer_events: int

    def as_dict(self) -> dict:
        return asdict(self)


def offer_metrics(events: pd.DataFrame, offers: pd.DataFrame | None = None) -> OfferMetrics:
    """Offer creation, sending, return and outcome counts (distinct offers).

    ``orphan_offer_events`` counts offer events whose OfferID matches no created offer.
    """
    ev = prepare_events(events, purpose="Offer metrics")
    _require(ev, (EVENT_ID, OFFER_ID), "Offer metrics")
    offers = offer_table(ev) if offers is None else offers
    later = ev[ev[OFFER_ID].notna()]
    per_app = offers.groupby("case_id").size()
    channel = offers["sent_channel"]
    n_sent = int(offers["was_sent"].sum())
    n_ret = int(offers["was_returned"].sum())
    outcome = offers["offer_outcome"]
    return OfferMetrics(
        offers_created=len(offers),
        o_create_offer_events=int((ev[A] == OFFER_CREATE).sum()),
        o_created_events=int((ev[A] == OFFER_CREATED_DUPLICATE).sum()),
        offers_sent=n_sent,
        offers_sent_mail_and_online=int((channel == "mail and online").sum()),
        offers_sent_online_only=int((channel == "online only").sum()),
        offers_returned=n_ret,
        offers_accepted=int((outcome == OFFER_ACCEPTED).sum()),
        offers_refused=int((outcome == OFFER_REFUSED).sum()),
        offers_cancelled=int((outcome == OFFER_CANCELLED).sum()),
        offers_without_outcome=int((outcome == "No outcome recorded").sum()),
        applications_with_offers=int(per_app.size),
        median_offers_per_application=float(per_app.median()) if per_app.size else float("nan"),
        max_offers_per_application=int(per_app.max()) if per_app.size else 0,
        offer_return_rate_percent=_percent(n_ret, n_sent),
        orphan_offer_events=int((~_fast_isin(later[OFFER_ID], offers["offer_id"])).sum()),
    )


def credit_score_summary(offers: pd.DataFrame) -> pd.DataFrame:
    """CreditScore statistics per offer, excluding 0 (treated as potentially missing)."""
    if "CreditScore" not in offers.columns:
        raise MetricInputError("Credit score summary: the offer table has no CreditScore column.")
    score = pd.to_numeric(offers["CreditScore"], errors="coerce")
    total = len(score)
    recorded = int(score.notna().sum())
    zero = int((score == 0).sum())
    valid = score[score > 0]
    stats = _describe(valid)
    return pd.DataFrame(
        [
            {
                "offers": total,
                "score_recorded": recorded,
                "score_missing": total - recorded,
                "score_zero_potentially_missing": zero,
                "zero_share_percent": _percent(zero, recorded),
                "score_nonzero": stats["count"],
                "mean_nonzero": stats["mean"],
                "median_nonzero": stats["median"],
                "p90_nonzero": stats["p90"],
                "min_nonzero": stats["min"],
                "max_nonzero": stats["max"],
            }
        ]
    )


# =========================================================================== #
# Cancellations: O_Cancelled (offer) and A_Cancelled (application), kept separate
# =========================================================================== #
@dataclass(frozen=True)
class OfferCancellationMetrics:
    """O_Cancelled: individual offers withdrawn. The application may continue."""

    offer_cancellation_events: int
    offers_cancelled: int
    applications_with_offer_cancellation: int
    share_of_applications_percent: float
    final_status_breakdown: pd.DataFrame  # final status of applications with >=1 O_Cancelled


def offer_cancellation_metrics(events: pd.DataFrame, summary: pd.DataFrame | None = None) -> OfferCancellationMetrics:
    ev = prepare_events(events, purpose="Offer cancellation metrics")
    summary = application_summary(ev) if summary is None else summary
    oc = ev[ev[A] == OFFER_CANCELLED]
    offers_cancelled = int(oc[OFFER_ID].nunique()) if OFFER_ID in oc.columns else len(oc)
    affected = summary[summary["has_offer_cancellation"].astype(bool)]
    return OfferCancellationMetrics(
        offer_cancellation_events=len(oc),
        offers_cancelled=offers_cancelled,
        applications_with_offer_cancellation=len(affected),
        share_of_applications_percent=_percent(len(affected), len(summary)),
        final_status_breakdown=final_status_distribution(affected),
    )


APPLICATION_CANCELLATION_COLUMNS = {
    "case_id": "str",
    "application_cancellation_time": "datetime64[us, UTC]",
    "cancelled_by": "str",
    "first_offer_cancellation_time": "datetime64[us, UTC]",
    "cancellation_sequence": "str",
    "last_offer_sent_time": "datetime64[us, UTC]",
    "days_since_last_offer_sent": "float64",
    "likely_automatic_expiry": "boolean",
}
SEQ_OFFER_FIRST = "O_Cancelled before A_Cancelled"
SEQ_SAME_TIME = "O_Cancelled at the same time as A_Cancelled"
SEQ_APPLICATION_FIRST = "A_Cancelled before any O_Cancelled"
SEQ_NO_OFFER_CANCEL = "No O_Cancelled recorded"


def application_cancellation_table(events: pd.DataFrame) -> pd.DataFrame:
    """One row per application with an A_Cancelled event (first occurrence).

    ``cancellation_sequence`` compares the first O_Cancelled with A_Cancelled.
    ``likely_automatic_expiry`` = cancelled by User_1 (likely, but not confirmed, a
    system account) at least 30 days
    after the last offer was sent (NA when the resource column is unavailable).
    """
    ev = prepare_events(events, purpose="Application cancellation table")
    ac = ev[ev[A] == APPLICATION_CANCELLED]
    if ac.empty:
        return _empty(APPLICATION_CANCELLATION_COLUMNS)
    first_ac = ac.groupby(C).first()
    out = pd.DataFrame(index=first_ac.index)
    out["application_cancellation_time"] = first_ac[T]
    out["cancelled_by"] = first_ac[R] if R in first_ac.columns else pd.NA
    out["first_offer_cancellation_time"] = ev[ev[A] == OFFER_CANCELLED].groupby(C)[T].min().reindex(out.index)

    oc_t, ac_t = out["first_offer_cancellation_time"], out["application_cancellation_time"]
    out["cancellation_sequence"] = np.select(
        [oc_t.isna(), oc_t < ac_t, oc_t == ac_t],
        [SEQ_NO_OFFER_CANCEL, SEQ_OFFER_FIRST, SEQ_SAME_TIME],
        default=SEQ_APPLICATION_FIRST,
    )

    sent = ev[ev[A].isin(OFFER_SENT_ACTIVITIES)].merge(
        out[["application_cancellation_time"]], left_on=C, right_index=True
    )
    sent = sent[sent[T] <= sent["application_cancellation_time"]]
    out["last_offer_sent_time"] = sent.groupby(C)[T].max().reindex(out.index)
    out["days_since_last_offer_sent"] = (ac_t - out["last_offer_sent_time"]).dt.total_seconds() / 86400
    if R in first_ac.columns:
        expiry = (out["cancelled_by"] == SYSTEM_RESOURCE) & (
            out["days_since_last_offer_sent"] >= EXPIRY_MIN_DAYS_AFTER_LAST_OFFER_SENT
        )
        out["likely_automatic_expiry"] = expiry.astype("boolean")
    else:
        out["likely_automatic_expiry"] = pd.array([pd.NA] * len(out), dtype="boolean")
    out = out.reset_index().rename(columns={C: "case_id"})
    out["case_id"] = _as_str(out["case_id"])
    return out[list(APPLICATION_CANCELLATION_COLUMNS)]


@dataclass(frozen=True)
class ApplicationCancellationMetrics:
    """A_Cancelled: whole applications cancelled."""

    application_cancellation_events: int
    applications_with_application_cancellation: int
    applications_final_status_cancelled: int
    share_of_applications_percent: float
    likely_automatic_expiry: int
    other_application_cancellations: int
    sequence_breakdown: pd.DataFrame


def application_cancellation_metrics(
    events: pd.DataFrame, summary: pd.DataFrame | None = None, table: pd.DataFrame | None = None
) -> ApplicationCancellationMetrics:
    ev = prepare_events(events, purpose="Application cancellation metrics")
    summary = application_summary(ev) if summary is None else summary
    table = application_cancellation_table(ev) if table is None else table
    seq_order = [SEQ_OFFER_FIRST, SEQ_SAME_TIME, SEQ_APPLICATION_FIRST, SEQ_NO_OFFER_CANCEL]
    counts = table["cancellation_sequence"].value_counts().reindex(seq_order, fill_value=0)
    breakdown = pd.DataFrame(
        {"sequence": seq_order, "applications": counts.to_numpy().astype("int64")}
    )
    breakdown["percentage"] = [_percent(n, len(table)) for n in breakdown["applications"]]
    expiry = int(table["likely_automatic_expiry"].fillna(False).sum())
    n_cancelled_final = int((summary["final_status"] == APPLICATION_CANCELLED).sum())
    return ApplicationCancellationMetrics(
        application_cancellation_events=int((ev[A] == APPLICATION_CANCELLED).sum()),
        applications_with_application_cancellation=len(table),
        applications_final_status_cancelled=n_cancelled_final,
        share_of_applications_percent=_percent(n_cancelled_final, len(summary)),
        likely_automatic_expiry=expiry,
        other_application_cancellations=len(table) - expiry,
        sequence_breakdown=breakdown,
    )


FOLLOWUP_COLUMNS = {
    "case_id": "str",
    "gap_days": "float64",
    "events_between": "int64",
    "pattern": "str",
    "new_offer": "bool",
    "offer_sent": "bool",
    "workflow_activity": "bool",
    "returned_offer": "bool",
    "first_offer_cancellation_time": "datetime64[us, UTC]",
    "application_cancellation_time": "datetime64[us, UTC]",
    "final_status": "str",
}


def offer_first_cancellation_followup(events: pd.DataFrame) -> pd.DataFrame:
    """Applications whose first O_Cancelled is strictly before their first A_Cancelled.

    For each, activity strictly between the two timestamps is described:
    ``events_between``, ``new_offer`` (O_Create Offer), ``offer_sent`` (O_Sent*),
    ``workflow_activity`` (W_*), ``returned_offer`` (O_Returned), and one ``pattern``
    assigned by PATTERN_PRECEDENCE. ``final_status`` is included to show the outcome;
    patterns describe activity only and are not evidence of recovery.
    The first eight columns reproduce offer_cancellation_followup_patterns.csv.
    """
    ev = prepare_events(events, purpose="Offer-first cancellation follow-up")
    oc = ev[ev[A] == OFFER_CANCELLED].groupby(C)[T].min()
    ac = ev[ev[A] == APPLICATION_CANCELLED].groupby(C)[T].min()
    pop = pd.concat([oc.rename("oc"), ac.rename("ac")], axis=1, join="inner")
    pop = pop[pop["oc"] < pop["ac"]]
    if pop.empty:
        return _empty(FOLLOWUP_COLUMNS)

    sub = ev[ev[C].isin(pop.index)].join(pop, on=C)
    between = sub[(sub[T] > sub["oc"]) & (sub[T] < sub["ac"])]
    act = between[A]
    flags = pd.DataFrame(
        {
            "events_between": 1,
            "new_offer": act == OFFER_CREATE,
            "offer_sent": act.isin(OFFER_SENT_ACTIVITIES),
            "workflow_activity": act.str.startswith("W_"),
            "returned_offer": act == OFFER_RETURNED,
        },
        index=between.index,
    ).groupby(between[C])
    agg = pd.concat(
        [flags["events_between"].sum(), flags[["new_offer", "offer_sent", "workflow_activity", "returned_offer"]].any()],
        axis=1,
    )
    out = pop.join(agg)
    out["events_between"] = out["events_between"].fillna(0).astype("int64")
    for col in ("new_offer", "offer_sent", "workflow_activity", "returned_offer"):
        out[col] = out[col].fillna(False).astype(bool)
    out["gap_days"] = (out["ac"] - out["oc"]).dt.total_seconds() / 86400
    out["pattern"] = np.select(
        [out["events_between"] == 0, out["new_offer"], out["offer_sent"], out["workflow_activity"]],
        [PATTERN_NO_ACTIVITY, PATTERN_REPLACEMENT_OFFER, PATTERN_OFFER_SENT, PATTERN_WORKFLOW],
        default=PATTERN_OTHER,
    )
    final = ev[ev[A].isin(FINAL_STATUSES)].groupby(C)[A].last()
    out["final_status"] = _as_str(final.reindex(out.index))
    out = out.rename(columns={"oc": "first_offer_cancellation_time", "ac": "application_cancellation_time"})
    out = out.reset_index().rename(columns={C: "case_id"})
    out["case_id"] = _as_str(out["case_id"])
    return out[list(FOLLOWUP_COLUMNS)]


PATTERN_SUMMARY_COLUMNS = [
    "pattern", "applications", "median_gap_days", "average_gap_days", "median_events", "average_events",
    "applications_with_new_offers", "applications_with_offers_sent", "applications_with_workflow_activity",
    "applications_with_returned_offers", "share_percent",
]


def followup_pattern_summary(followup: pd.DataFrame) -> pd.DataFrame:
    """Aggregate the follow-up table by pattern (reproduces offer_cancellation_pattern_summary.csv).

    Values are unrounded; the existing report rounds to 2 decimals.
    """
    if followup.empty:
        return pd.DataFrame(columns=PATTERN_SUMMARY_COLUMNS)
    g = followup.groupby("pattern", sort=True)
    out = g.agg(
        applications=("case_id", "size"),
        median_gap_days=("gap_days", "median"),
        average_gap_days=("gap_days", "mean"),
        median_events=("events_between", "median"),
        average_events=("events_between", "mean"),
        applications_with_new_offers=("new_offer", "sum"),
        applications_with_offers_sent=("offer_sent", "sum"),
        applications_with_workflow_activity=("workflow_activity", "sum"),
        applications_with_returned_offers=("returned_offer", "sum"),
    ).reset_index()
    out["median_events"] = out["median_events"].astype("float64")
    out["share_percent"] = out["applications"] / len(followup) * 100
    return out[PATTERN_SUMMARY_COLUMNS]


CLOSURE_COLUMNS = {
    "case_id": "str",
    "cancellation_time": "datetime64[us, UTC]",
    "completion_time": "datetime64[us, UTC]",
    "delay_hours": "float64",
    "completion_lifecycle": "str",
}


def cancellation_closure_events(events: pd.DataFrame) -> pd.DataFrame:
    """A_Cancelled followed later by a W_Complete application event.

    Population: applications with any W_Complete application event strictly after the
    first A_Cancelled. ``completion_time`` is the first such event. Reproduces
    cancellation_completion_delays.csv. In the real log every delay is milliseconds:
    this is the system closing the open work item, not a business delay.
    """
    ev = prepare_events(events, purpose="Cancellation closure events")
    ac = ev[ev[A] == APPLICATION_CANCELLED].groupby(C)[T].min().rename("cancellation_time")
    w = ev[ev[A] == W_COMPLETE_APPLICATION].join(ac, on=C, how="inner")
    w = w[w[T] > w["cancellation_time"]]
    if w.empty:
        return _empty(CLOSURE_COLUMNS)
    first = w.groupby(C, sort=True).first()
    out = pd.DataFrame(
        {
            "cancellation_time": first["cancellation_time"],
            "completion_time": first[T],
            "delay_hours": (first[T] - first["cancellation_time"]).dt.total_seconds() / 3600,
            "completion_lifecycle": first[L] if L in first.columns else pd.NA,
        }
    )
    out = out.reset_index().rename(columns={C: "case_id"})
    out["case_id"] = _as_str(out["case_id"])
    return out[list(CLOSURE_COLUMNS)]


# =========================================================================== #
# Headline KPIs
# =========================================================================== #
def headline_kpis(
    summary: pd.DataFrame,
    offers: OfferMetrics,
    offer_cancellations: OfferCancellationMetrics,
    application_cancellations: ApplicationCancellationMetrics,
    followup: pd.DataFrame,
    credit_scores: pd.DataFrame,
) -> pd.DataFrame:
    """KPI values joined to their registered definitions (key, name, value, unit, ...)."""
    from core.definitions import METRICS

    dist = final_status_distribution(summary).set_index("final_status", drop=False)
    durations = duration_statistics(summary).iloc[0]
    to_final = duration_statistics(summary, column="days_to_final_status", include_open=False).iloc[0]
    values = {
        "total_applications": len(summary),
        "final_status_pending": int(dist.loc[config.APPLICATION_PENDING, "applications"]),
        "final_status_cancelled": int(dist.loc[config.APPLICATION_CANCELLED, "applications"]),
        "final_status_denied": int(dist.loc[config.APPLICATION_DENIED, "applications"]),
        "no_final_status": int(summary["is_open"].sum()) if len(summary) else 0,
        "median_duration_days": durations["median"],
        "p90_duration_days": durations["p90"],
        "median_days_to_final_status": to_final["median"],
        "offer_cancellation_events": offer_cancellations.offer_cancellation_events,
        "applications_with_offer_cancellation": offer_cancellations.applications_with_offer_cancellation,
        "application_cancellation_events": application_cancellations.application_cancellation_events,
        "likely_automatic_expiry_cancellations": application_cancellations.likely_automatic_expiry,
        "offer_first_cancellation_population": len(followup),
        "offers_created": offers.offers_created,
        "offers_sent": offers.offers_sent,
        "offers_returned": offers.offers_returned,
        "offer_return_rate": offers.offer_return_rate_percent,
        "credit_score_zero_offers": int(credit_scores["score_zero_potentially_missing"].iloc[0]),
        "median_credit_score_nonzero": credit_scores["median_nonzero"].iloc[0],
    }
    missing = set(values) - set(METRICS)
    if missing:  # pragma: no cover - guarded by tests
        raise KeyError(f"KPIs without definitions: {sorted(missing)}")
    rows = []
    for key, value in values.items():
        d = METRICS[key]
        rows.append(
            {
                "key": key,
                "name": d.name,
                "value": value,
                "unit": d.unit,
                "definition": d.definition,
                "calculation": d.calculation,
                "source": d.source,
                "comparable_report": d.comparable_report,
                "caveats": " ".join(d.caveats),
            }
        )
    return pd.DataFrame(rows)
