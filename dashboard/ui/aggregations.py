"""Presentation helpers: select, count and reshape approved metric outputs for charts.

Nothing here defines a new metric. Inputs are outputs of ``core.metrics`` /
``core.drilldown`` (application summary, transition table, follow-up table, ...);
outputs are the same facts arranged for a chart. Counts of applications always
count each application exactly once within the population shown.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from core import config
from core import metrics as m
from ui.filters import STATUS_LABEL_ORDER
from ui.formatting import fmt_hours

# =========================================================================== #
# Milestone Sankey (each application follows exactly one path)
# =========================================================================== #
NODE_ALL = "All applications"
NODE_OFFER_SENT = "Offer sent"
NODE_NO_OFFER_SENT = "No offer sent"
NODE_OFFER_RETURNED = "Offer returned"
NODE_OFFER_NOT_RETURNED = "Offer sent, none returned"

#: Registered definition for each milestone node (core.definitions.METRICS).
MILESTONE_DEFINITION_KEYS = {
    NODE_OFFER_SENT: "milestone_offer_sent",
    NODE_NO_OFFER_SENT: "milestone_no_offer_sent",
    NODE_OFFER_RETURNED: "milestone_offer_returned",
    NODE_OFFER_NOT_RETURNED: "milestone_offer_not_returned",
}
#: Terminal offer milestone of each application, in display order.
OFFER_MILESTONES = (NODE_OFFER_RETURNED, NODE_OFFER_NOT_RETURNED, NODE_NO_OFFER_SENT)
SANKEY_NODES: tuple[str, ...] = (
    NODE_ALL, NODE_OFFER_SENT, NODE_NO_OFFER_SENT, NODE_OFFER_RETURNED, NODE_OFFER_NOT_RETURNED, *STATUS_LABEL_ORDER,
)

_MILESTONE_COLUMNS = ("case_id", "offers_sent", "offers_returned", "final_status_label")


def milestone_paths(summary: pd.DataFrame) -> pd.DataFrame:
    """One row per application: its terminal offer milestone and final status label."""
    missing = [c for c in _MILESTONE_COLUMNS if c not in summary.columns]
    if missing:
        raise KeyError(f"Application summary is missing column(s) {missing}")
    sent = summary["offers_sent"].to_numpy() > 0
    returned = summary["offers_returned"].to_numpy() > 0
    milestone = np.select([~sent, returned], [NODE_NO_OFFER_SENT, NODE_OFFER_RETURNED], default=NODE_OFFER_NOT_RETURNED)
    return pd.DataFrame(
        {
            "case_id": summary["case_id"].to_numpy(),
            "offer_milestone": milestone,
            "final_status_label": summary["final_status_label"].to_numpy(),
        }
    )


def milestone_path_counts(summary: pd.DataFrame) -> pd.DataFrame:
    """Applications per (offer milestone, final status), fixed order, zero rows omitted."""
    paths = milestone_paths(summary)
    counts = paths.groupby(["offer_milestone", "final_status_label"]).size().rename("applications").reset_index()
    counts["_m"] = counts["offer_milestone"].map({k: i for i, k in enumerate(OFFER_MILESTONES)})
    counts["_s"] = counts["final_status_label"].map({k: i for i, k in enumerate(STATUS_LABEL_ORDER)})
    counts = counts.sort_values(["_m", "_s"]).drop(columns=["_m", "_s"]).reset_index(drop=True)
    counts["applications"] = counts["applications"].astype("int64")
    return counts


@dataclass(frozen=True)
class SankeyData:
    nodes: tuple[str, ...]
    links: pd.DataFrame  # source, target (indices), source_label, target_label, value, share_of_source
    total: int


def milestone_sankey(summary: pd.DataFrame) -> SankeyData:
    """Links: All -> offer sent / no offer sent -> returned / not returned -> final status."""
    paths = milestone_paths(summary)
    total = len(paths)
    n_sent = int((paths["offer_milestone"] != NODE_NO_OFFER_SENT).sum())
    rows = [
        (NODE_ALL, NODE_OFFER_SENT, n_sent),
        (NODE_ALL, NODE_NO_OFFER_SENT, total - n_sent),
        (NODE_OFFER_SENT, NODE_OFFER_RETURNED, int((paths["offer_milestone"] == NODE_OFFER_RETURNED).sum())),
        (NODE_OFFER_SENT, NODE_OFFER_NOT_RETURNED, int((paths["offer_milestone"] == NODE_OFFER_NOT_RETURNED).sum())),
    ]
    for row in milestone_path_counts(summary).itertuples(index=False):
        rows.append((row.offer_milestone, row.final_status_label, int(row.applications)))
    links = pd.DataFrame(rows, columns=["source_label", "target_label", "value"])
    links = links[links["value"] > 0].reset_index(drop=True)
    index = {name: i for i, name in enumerate(SANKEY_NODES)}
    links["source"] = links["source_label"].map(index)
    links["target"] = links["target_label"].map(index)
    outflow = links.groupby("source_label")["value"].transform("sum")
    links["share_of_source"] = links["value"] / outflow * 100
    return SankeyData(nodes=SANKEY_NODES, links=links, total=total)


# =========================================================================== #
# Activities and transitions
# =========================================================================== #
_STREAM_BY_PREFIX = {"A_": "Application", "O_": "Offer", "W_": "Workflow"}
_STREAM_RANK = {"Application": 0, "Offer": 1, "Workflow": 2, "Other": 3}


def stream_of(activity: str) -> str:
    return _STREAM_BY_PREFIX.get(str(activity)[:2], "Other")


def activity_order(frequency: pd.DataFrame, extra: list[str] | tuple[str, ...] = ()) -> list[str]:
    """Fixed display order: stream (Application, Offer, Workflow), then events desc, then name."""
    freq = frequency[["activity", "total_events"]].copy()
    for act in extra:
        if act not in set(freq["activity"]):
            freq.loc[len(freq)] = [act, 0]
    freq["_stream"] = freq["activity"].map(lambda a: _STREAM_RANK[stream_of(a)])
    freq = freq.sort_values(["_stream", "total_events", "activity"], ascending=[True, False, True], kind="mergesort")
    return freq["activity"].tolist()


def transition_matrix(transitions: pd.DataFrame, order: list[str]) -> pd.DataFrame:
    """Long-format matrix cells for existing pairs, with row/column positions from ``order``."""
    pos = {a: i for i, a in enumerate(order)}
    cells = transitions[transitions["activity"].isin(pos) & transitions["next_activity"].isin(pos)].copy()
    cells["row"] = cells["activity"].map(pos).astype("int64")
    cells["col"] = cells["next_activity"].map(pos).astype("int64")
    return cells.reset_index(drop=True)


def top_waits(transitions: pd.DataFrame, n: int = 15) -> pd.DataFrame:
    """The ``n`` pairs with the longest median wait (ties broken by name)."""
    ordered = transitions.sort_values(
        ["median_wait_hours", "activity", "next_activity"], ascending=[False, True, True], kind="mergesort"
    )
    return ordered.head(n).reset_index(drop=True)


# =========================================================================== #
# Wait-time histogram (log-spaced bins, binned server-side)
# =========================================================================== #
def _fmt_edge(hours: float) -> str:
    if hours < 1 / 60:
        return f"{hours * 3600:.0f} s"
    if hours < 1:
        return f"{hours * 60:.0f} min"
    if hours < 48:
        return f"{hours:.0f} h" if hours >= 10 else f"{hours:.1f} h"
    return f"{hours / 24:.0f} d"


def wait_histogram(wait_hours: pd.Series, *, bins_per_decade: int = 4, floor_hours: float = 1 / 3600) -> pd.DataFrame:
    """Counts of waits in log-spaced bins. The first bin collects waits below ``floor_hours``.

    Counts always sum to the number of non-missing waits.
    """
    w = pd.to_numeric(wait_hours, errors="coerce").dropna().to_numpy()
    columns = ["bin", "lower_hours", "upper_hours", "count"]
    if w.size == 0:
        return pd.DataFrame(columns=columns)
    rows = []
    below = int((w < floor_hours).sum())
    if below:
        rows.append((f"< {_fmt_edge(floor_hours)}", 0.0, floor_hours, below))
    rest = w[w >= floor_hours]
    if rest.size:
        lo_exp = math.floor(math.log10(floor_hours) * bins_per_decade) / bins_per_decade
        hi_exp = math.ceil(math.log10(rest.max()) * bins_per_decade) / bins_per_decade
        if hi_exp <= lo_exp:
            hi_exp = lo_exp + 1 / bins_per_decade
        edges = 10 ** np.arange(lo_exp, hi_exp + 1e-9, 1 / bins_per_decade)
        edges[0] = floor_hours
        counts, _ = np.histogram(rest, bins=edges)
        for lo, hi, c in zip(edges[:-1], edges[1:], counts):
            rows.append((f"{_fmt_edge(lo)}–{_fmt_edge(hi)}", float(lo), float(hi), int(c)))
    out = pd.DataFrame(rows, columns=columns)
    # Trim empty bins at the tails only (keep gaps in the middle visible).
    nz = np.flatnonzero(out["count"].to_numpy())
    return out.iloc[nz[0] : nz[-1] + 1].reset_index(drop=True)


def describe_wait(stats: dict) -> str:
    return (
        f"{stats['transition_count']:,} occurrences · median {fmt_hours(stats['median_wait_hours'])} · "
        f"90th percentile {fmt_hours(stats['p90_wait_hours'])}"
    )


# =========================================================================== #
# Cancellation selections
# =========================================================================== #
def cases_for_pattern(followup: pd.DataFrame, pattern: str | None) -> pd.DataFrame:
    return followup if pattern is None else followup[followup["pattern"] == pattern].reset_index(drop=True)


def cases_for_sequence(table: pd.DataFrame, sequence: str | None) -> pd.DataFrame:
    return table if sequence is None else table[table["cancellation_sequence"] == sequence].reset_index(drop=True)


def offer_cancellation_cases(summary: pd.DataFrame, status_label: str | None = None) -> pd.DataFrame:
    """Applications with >= 1 O_Cancelled, optionally with one final status."""
    s = summary[summary["has_offer_cancellation"].astype(bool)]
    if status_label is not None:
        s = s[s["final_status_label"] == status_label]
    return s.reset_index(drop=True)


def final_status_counts(cases: pd.DataFrame, column: str = "final_status") -> dict[str, int]:
    """Applications per final status in a case table (missing shown as 'No final status recorded')."""
    values = cases[column].fillna(config.NO_FINAL_STATUS_LABEL)
    return {str(k): int(v) for k, v in values.value_counts().items()}


def case_for_rows(table: pd.DataFrame, rows: list[int], column: str = "case_id") -> str | None:
    """Application ID of the first selected table row (positions refer to ``table``)."""
    for r in rows:
        if 0 <= r < len(table):
            value = table.iloc[r][column]
            return None if pd.isna(value) else str(value)
    return None


COMPOSITION_COMPONENTS = (
    ("applications_with_new_offers", "New offer created (O_Create Offer)"),
    ("applications_with_offers_sent", "Offer sent (O_Sent)"),
    ("applications_with_workflow_activity", "Workflow activity (W_)"),
    ("applications_with_returned_offers", "Offer returned (O_Returned)"),
)


def pattern_composition(pattern_summary: pd.DataFrame, pattern: str) -> pd.DataFrame:
    """Activity components recorded in a pattern (existing summary columns, reshaped)."""
    row = pattern_summary[pattern_summary["pattern"] == pattern]
    if row.empty:
        return pd.DataFrame(columns=["component", "applications", "pattern_applications"])
    r = row.iloc[0]
    return pd.DataFrame(
        {
            "component": [label for _, label in COMPOSITION_COMPONENTS],
            "applications": [int(r[col]) for col, _ in COMPOSITION_COMPONENTS],
            "pattern_applications": int(r["applications"]),
        }
    )


# =========================================================================== #
# Application timeline
# =========================================================================== #
TIMELINE_COLUMNS = [
    "order", "time", "activity", "stream", "lifecycle", "resource", "offer_id", "event_id", "offer", "is_final_status",
]


def events_for_application(events: pd.DataFrame, case_id: str) -> pd.DataFrame:
    """One application's events in time order (a new frame with plain-text columns)."""
    ev = m.prepare_events(events, purpose="Application timeline")
    sub = ev[ev[m.C] == case_id]
    return sub.reset_index(drop=True)


def timeline_frame(case_events: pd.DataFrame) -> pd.DataFrame:
    """Events shaped for the timeline: stream lane, readable fields, final-status flag."""
    if case_events.empty:
        return pd.DataFrame(columns=TIMELINE_COLUMNS)

    def col(name):
        return case_events[name].astype("str").where(case_events[name].notna()) if name in case_events else pd.NA

    activity = case_events[m.A].astype("str")
    out = pd.DataFrame(
        {
            "order": np.arange(1, len(case_events) + 1),
            "time": case_events[m.T].to_numpy(),
            "activity": activity.to_numpy(),
            "stream": activity.map(stream_of).to_numpy(),
            "lifecycle": col(m.L),
            "resource": col(m.R),
            "offer_id": col(m.OFFER_ID),
            "event_id": col(m.EVENT_ID),
        }
    )
    out["time"] = pd.to_datetime(out["time"], utc=True)
    # The offer an event belongs to: OfferID as recorded, or the EventID of O_Create Offer
    # (offer creation events carry the new offer's ID in EventID and no OfferID).
    creates = (out["activity"] == m.OFFER_CREATE).to_numpy()
    out["offer"] = out["offer_id"].where(~creates, out["event_id"])
    final_mask = out["activity"].isin(config.FINAL_STATUS_ACTIVITIES)
    out["is_final_status"] = False
    if final_mask.any():
        out.loc[final_mask[final_mask].index[-1], "is_final_status"] = True  # last final-status event
    return out[TIMELINE_COLUMNS]


def offer_lifelines(offers: pd.DataFrame, case_id: str) -> pd.DataFrame:
    """Offers of one application: created time to outcome time (outcome may be missing)."""
    cols = ["offer_id", "created_time", "offer_outcome_time", "offer_outcome", "sent_channel", "was_returned"]
    sub = offers[offers["case_id"] == case_id]
    return sub[cols].sort_values("created_time", kind="mergesort").reset_index(drop=True)


# =========================================================================== #
# Application Explorer: complete event table and application details (Phase 5e)
# =========================================================================== #
#: Event-log columns that the metric layer does not load (read once for the Explorer).
EXTRA_EVENT_COLUMNS = ("Action", "EventOrigin", "Accepted", "Selected")
OFFER_ATTRIBUTE_COLUMNS = ("CreditScore", "OfferedAmount", "MonthlyCost", "NumberOfTerms", "FirstWithdrawalAmount")


def align_event_extras(extras: pd.DataFrame, shared_events: pd.DataFrame) -> pd.DataFrame:
    """Extra columns indexed like the shared event log (original row positions).

    ``extras`` is loaded from the same file in file order, so row ``i`` of ``extras``
    is the event whose prepared-log index label is ``i``. That is verified on every
    event through EventID; a mismatch raises instead of attaching wrong attributes.
    """
    from core.errors import DashboardError

    ids = extras[m.EVENT_ID].to_numpy(dtype=object)
    labels = shared_events.index.to_numpy()
    if len(extras) != len(shared_events) or not (ids[labels] == shared_events[m.EVENT_ID].to_numpy(dtype=object)).all():
        raise DashboardError(
            "The event attributes could not be matched to the event log.",
            details="EventID order differs between the two reads of the event log.",
        )
    out = extras.drop(columns=[m.EVENT_ID])
    for col in ("Action", "EventOrigin"):
        if col in out:
            out[col] = out[col].astype("category")
    return out


EVENT_TABLE_COLUMNS = {
    "order": "#", "time": "Time (UTC)", "activity": "Activity", "stream": "Event stream",
    "lifecycle": "Lifecycle stage", "resource": "Resource (as recorded)", "event_id": "Event ID",
    "offer_id": "OfferID (as recorded)", "offer": "Offer", "is_final_status": "Final-status event",
}


def event_table(events: pd.DataFrame, extras: pd.DataFrame | None, case_id: str) -> pd.DataFrame:
    """Every recorded event of one application, in the original event order.

    Columns are shown as recorded (no inferred roles, dates or durations). ``Offer``
    is the offer the event belongs to (OfferID, or the EventID of O_Create Offer).
    ``extras`` (from :func:`align_event_extras`) adds Action, EventOrigin, Accepted
    and Selected; offer attributes appear where the event log records them.
    """
    ev = m.prepare_events(events, purpose="Event table")
    sub = ev[ev[m.C] == case_id]
    tl = timeline_frame(sub.reset_index(drop=True))
    out = tl[list(EVENT_TABLE_COLUMNS)].rename(columns=EVENT_TABLE_COLUMNS)
    if extras is not None and not sub.empty:
        x = extras.loc[sub.index]
        for col, label in (("Action", "Action"), ("EventOrigin", "Event origin")):
            if col in x:
                out[label] = _plain_text(x[col])
    for col in OFFER_ATTRIBUTE_COLUMNS:
        if col in sub:
            out[col] = sub[col].to_numpy()
    if extras is not None and not sub.empty:
        for col in ("Accepted", "Selected"):
            if col in extras:
                out[col] = extras.loc[sub.index, col].to_numpy()
    return out.reset_index(drop=True)


def _plain_text(values: pd.Series) -> np.ndarray:
    text = values.astype("str")
    return text.where(values.notna()).to_numpy()


def find_application(query: str, ids) -> str | None:
    """The application whose ID equals ``query`` (ignoring case and surrounding spaces)."""
    wanted = (query or "").strip().casefold()
    if not wanted:
        return None
    for case_id in ids:
        if isinstance(case_id, str) and case_id.casefold() == wanted:
            return case_id
    return None


def application_details(row: pd.Series) -> dict[str, pd.DataFrame]:
    """The application summary row (core.metrics.application_summary) as labelled tables."""
    from ui.formatting import fmt_datetime, fmt_days, fmt_int, fmt_number

    def frame(items):
        return pd.DataFrame(items, columns=["Field", "Value"])

    def text(value):
        return "—" if value is None or (not isinstance(value, str) and pd.isna(value)) else str(value)

    is_open = bool(row["is_open"])
    return {
        "Application": frame([
            ("Application", row["case_id"]),
            ("Final status", row["final_status_label"]),
            ("Loan goal", text(row.get("loan_goal"))),
            ("Application type", text(row.get("application_type"))),
            ("Requested amount", fmt_number(row.get("requested_amount"), 0)),
            ("First activity", row["first_activity"]),
            ("Last recorded activity (not an outcome)", row["last_activity"]),
        ]),
        "Timing (UTC)": frame([
            ("First event", fmt_datetime(row["start_time"])),
            ("Last event", fmt_datetime(row["end_time"])),
            ("Duration, first to last event", fmt_days(row["duration_days"])),
            ("Final-status event", "None by 1 Feb 2017 (open)" if is_open else fmt_datetime(row["final_status_time"])),
            ("Time to final status", "—" if is_open else fmt_days(row["days_to_final_status"])),
            ("Observed days until the data end", fmt_days(row["observed_days_until_dataset_end"])),
        ]),
        "Events and offers": frame([
            ("Recorded events", fmt_int(row["total_events"])),
            ("Distinct activities", fmt_int(row["unique_activities"])),
            ("Events after the final-status event", "—" if is_open else fmt_int(row["events_after_final_status"])),
            ("Offers created", fmt_int(row["offers_created"])),
            ("Offers sent", fmt_int(row["offers_sent"])),
            ("Offers returned", fmt_int(row["offers_returned"])),
            ("O_Cancelled events (offer cancellation)", fmt_int(row["offer_cancellation_events"])),
            ("A_Cancelled events (application cancellation)", fmt_int(row["application_cancellation_events"])),
        ]),
    }
