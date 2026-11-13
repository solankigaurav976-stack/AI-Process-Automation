"""Declared schemas for every source file the dashboard reads.

A schema lists each column, its logical type, whether it may be missing, and
simple value rules. :func:`core.loaders.validate_frame` applies these rules and
collects every problem it finds rather than stopping at the first one.

Schemas are structural only. Business rules (for example the >=100 transition
threshold used by ``process_bottlenecks.csv``) are verified separately by the
reconciliation checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from core import config

ColumnKind = Literal["string", "integer", "float", "datetime", "boolean"]


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    kind: ColumnKind
    nullable: bool = False
    min_value: float | None = None
    max_value: float | None = None
    allowed_values: frozenset[str] | None = None
    description: str = ""


@dataclass(frozen=True)
class TableSchema:
    name: str
    columns: tuple[ColumnSpec, ...]
    unique_key: tuple[str, ...] = ()
    min_rows: int = 1
    allow_extra_columns: bool = False
    description: str = ""
    _by_name: dict[str, ColumnSpec] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        names = [c.name for c in self.columns]
        if len(names) != len(set(names)):
            raise ValueError(f"Schema {self.name!r} declares a column more than once")
        missing_key = [k for k in self.unique_key if k not in names]
        if missing_key:
            raise ValueError(f"Schema {self.name!r} unique key uses undeclared columns {missing_key}")
        object.__setattr__(self, "_by_name", {c.name: c for c in self.columns})

    @property
    def column_names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.columns)

    def column(self, name: str) -> ColumnSpec:
        return self._by_name[name]

    def subset(self, names: tuple[str, ...]) -> "TableSchema":
        """Schema restricted to ``names`` (used when loading selected columns)."""
        cols = tuple(self._by_name[n] for n in names)
        key = self.unique_key if all(k in names for k in self.unique_key) else ()
        return TableSchema(
            name=self.name,
            columns=cols,
            unique_key=key,
            min_rows=self.min_rows,
            allow_extra_columns=self.allow_extra_columns,
            description=self.description,
        )


FOLLOWUP_PATTERNS = frozenset(
    {
        "No recorded intermediate activity",
        "Offer sent, no new offer creation recorded",
        "Other activity only",
        "Replacement offer created",
        "Workflow activity only",
    }
)

_NON_NEG = 0.0

# --------------------------------------------------------------------------- #
# Report schemas
# --------------------------------------------------------------------------- #
ACTIVITY_FREQUENCY = TableSchema(
    name="activity_frequency",
    description="Event count and distinct applications per activity (all lifecycle transitions).",
    columns=(
        ColumnSpec("concept:name", "string", description="Activity name"),
        ColumnSpec("total_events", "integer", min_value=_NON_NEG),
        ColumnSpec("applications", "integer", min_value=_NON_NEG),
    ),
    unique_key=("concept:name",),
)

APPLICATION_OUTCOMES = TableSchema(
    name="application_outcomes",
    description="Distribution of the LAST RECORDED ACTIVITY per application (not a final status).",
    columns=(
        ColumnSpec("final_activity", "string", description="Last recorded activity"),
        ColumnSpec("applications", "integer", min_value=_NON_NEG),
        ColumnSpec("percentage", "float", min_value=0.0, max_value=100.0),
    ),
    unique_key=("final_activity",),
)

APPLICATION_PERFORMANCE = TableSchema(
    name="application_performance",
    description="Per-application first/last event time, event counts and duration.",
    columns=(
        ColumnSpec("case:concept:name", "string", description="Application ID"),
        ColumnSpec("start_time", "datetime"),
        ColumnSpec("end_time", "datetime"),
        ColumnSpec("total_events", "integer", min_value=1),
        ColumnSpec("unique_activities", "integer", min_value=1),
        ColumnSpec("duration_days", "float", min_value=_NON_NEG),
    ),
    unique_key=("case:concept:name",),
)

CANCELLATION_COMPLETION_DELAYS = TableSchema(
    name="cancellation_completion_delays",
    description="A_Cancelled time to the next W_Complete application event, per application.",
    columns=(
        ColumnSpec("case_id", "string", description="Application ID"),
        ColumnSpec("cancellation_time", "datetime"),
        ColumnSpec("completion_time", "datetime"),
        ColumnSpec("delay_hours", "float", min_value=_NON_NEG),
    ),
    unique_key=("case_id",),
)

OFFER_CANCELLATION_FOLLOWUP_PATTERNS = TableSchema(
    name="offer_cancellation_followup_patterns",
    description=(
        "Applications whose first O_Cancelled occurs strictly before A_Cancelled; "
        "gap_days = A_Cancelled time - first O_Cancelled time."
    ),
    columns=(
        ColumnSpec("case_id", "string", description="Application ID"),
        ColumnSpec("gap_days", "float", min_value=_NON_NEG),
        ColumnSpec("events_between", "integer", min_value=_NON_NEG),
        ColumnSpec("pattern", "string", allowed_values=FOLLOWUP_PATTERNS),
        ColumnSpec("new_offer", "boolean"),
        ColumnSpec("offer_sent", "boolean"),
        ColumnSpec("workflow_activity", "boolean"),
        ColumnSpec("returned_offer", "boolean"),
    ),
    unique_key=("case_id",),
)

OFFER_CANCELLATION_PATTERN_SUMMARY = TableSchema(
    name="offer_cancellation_pattern_summary",
    description="Follow-up patterns aggregated over offer_cancellation_followup_patterns.",
    columns=(
        ColumnSpec("pattern", "string", allowed_values=FOLLOWUP_PATTERNS),
        ColumnSpec("applications", "integer", min_value=_NON_NEG),
        ColumnSpec("median_gap_days", "float", min_value=_NON_NEG),
        ColumnSpec("average_gap_days", "float", min_value=_NON_NEG),
        ColumnSpec("median_events", "float", min_value=_NON_NEG),
        ColumnSpec("average_events", "float", min_value=_NON_NEG),
        ColumnSpec("applications_with_new_offers", "integer", min_value=_NON_NEG),
        ColumnSpec("applications_with_offers_sent", "integer", min_value=_NON_NEG),
        ColumnSpec("applications_with_workflow_activity", "integer", min_value=_NON_NEG),
        ColumnSpec("applications_with_returned_offers", "integer", min_value=_NON_NEG),
        ColumnSpec("share_percent", "float", min_value=0.0, max_value=100.0),
    ),
    unique_key=("pattern",),
)

PROCESS_BOTTLENECKS = TableSchema(
    name="process_bottlenecks",
    description="Waiting time between directly consecutive events (pairs with >=100 occurrences).",
    columns=(
        ColumnSpec("concept:name", "string", description="Activity"),
        ColumnSpec("next_activity", "string"),
        ColumnSpec("transition_count", "integer", min_value=1),
        ColumnSpec("average_wait_hours", "float", min_value=_NON_NEG),
        ColumnSpec("median_wait_hours", "float", min_value=_NON_NEG),
        ColumnSpec("p90_wait_hours", "float", min_value=_NON_NEG),
    ),
    unique_key=("concept:name", "next_activity"),
)

REPORT_SCHEMAS = MappingProxyType(
    {
        s.name: s
        for s in (
            ACTIVITY_FREQUENCY,
            APPLICATION_OUTCOMES,
            APPLICATION_PERFORMANCE,
            CANCELLATION_COMPLETION_DELAYS,
            OFFER_CANCELLATION_FOLLOWUP_PATTERNS,
            OFFER_CANCELLATION_PATTERN_SUMMARY,
            PROCESS_BOTTLENECKS,
        )
    }
)

# --------------------------------------------------------------------------- #
# Event-log schema (processed parquet)
# --------------------------------------------------------------------------- #
EVENT_LOG = TableSchema(
    name="event_log",
    description="BPI Challenge 2017 loan application event log (processed parquet).",
    columns=(
        ColumnSpec(config.COL_ACTION, "string"),
        ColumnSpec(config.COL_RESOURCE, "string"),
        ColumnSpec(config.COL_ACTIVITY, "string"),
        ColumnSpec(config.COL_ORIGIN, "string", allowed_values=frozenset(config.EVENT_ORIGINS)),
        ColumnSpec(config.COL_EVENT_ID, "string"),
        ColumnSpec(
            config.COL_LIFECYCLE, "string", allowed_values=frozenset(config.LIFECYCLE_TRANSITIONS)
        ),
        ColumnSpec(config.COL_TIMESTAMP, "datetime"),
        ColumnSpec(config.COL_LOAN_GOAL, "string"),
        ColumnSpec(config.COL_APPLICATION_TYPE, "string"),
        ColumnSpec(config.COL_CASE_ID, "string"),
        ColumnSpec(config.COL_REQUESTED_AMOUNT, "float", min_value=_NON_NEG),
        # Offer attributes: present only on offer events, missing elsewhere by design.
        ColumnSpec("FirstWithdrawalAmount", "float", nullable=True, min_value=_NON_NEG),
        ColumnSpec("NumberOfTerms", "float", nullable=True, min_value=_NON_NEG),
        ColumnSpec("Accepted", "boolean", nullable=True),
        ColumnSpec("MonthlyCost", "float", nullable=True, min_value=_NON_NEG),
        ColumnSpec("Selected", "boolean", nullable=True),
        ColumnSpec("CreditScore", "float", nullable=True, min_value=_NON_NEG),
        ColumnSpec("OfferedAmount", "float", nullable=True, min_value=_NON_NEG),
        ColumnSpec(config.COL_OFFER_ID, "string", nullable=True),
    ),
    unique_key=(config.COL_EVENT_ID,),
    allow_extra_columns=True,
)
