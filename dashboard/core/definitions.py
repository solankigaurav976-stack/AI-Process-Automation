"""Metric definitions, data sources and standard caveats.

Every KPI shown by the dashboard must have an entry in :data:`METRICS`. The UI
renders the definition, calculation and source next to each value so that no
number appears without an explanation of where it comes from.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType

import pandas as pd

# --------------------------------------------------------------------------- #
# Dataset facts
# --------------------------------------------------------------------------- #
#: Timestamp of the last event in the processed event log (verified by tests).
DATASET_END = pd.Timestamp("2017-02-01 14:11:03.499", tz="UTC")
DATASET_END_DISPLAY = "1 February 2017"

SOURCE_EVENT_LOG = "Event log: data/processed/loan_application_events.parquet (recalculated)"

#: Dataset attribution shown in the sidebar on every page (4TU General Terms of Use,
#: Art. 1 and 4a: acknowledge the source and its rights-holder). Order is display order.
DATASET_ATTRIBUTION: tuple[tuple[str, str], ...] = (
    ("Dataset", "BPI Challenge 2017"),
    ("Creator", "Boudewijn van Dongen"),
    ("Year", "2017"),
    ("Publisher", "4TU.ResearchData"),
)
#: Persistent identifier of the dataset (official 4TU.ResearchData record).
DATASET_DOI = "10.4121/uuid:5f3067df-f10b-45da-b98b-86ae4c7a310b"
DATASET_DOI_URL = f"https://doi.org/{DATASET_DOI}"
#: Licence named on the dataset's official 4TU.ResearchData record (licence URL as given there).
DATASET_TERMS_NAME = "4TU General Terms of Use"
DATASET_TERMS_URL = "https://doi.org/10.4121/resource:terms_of_use"

#: Shown next to every CSV download button: downloads are derived from the dataset.
DOWNLOAD_NOTICE = (
    "Derived from BPI Challenge 2017 (Boudewijn van Dongen, 2017, 4TU.ResearchData). "
    "Keep this acknowledgement with any downloaded or shared data. Use is subject to the "
    f"[{DATASET_TERMS_NAME}]({DATASET_TERMS_URL}): non-commercial use only."
)


def report_source(filename: str) -> str:
    return f"Existing report: reports/{filename}"


# --------------------------------------------------------------------------- #
# Standard caveats (shown wherever the related metric appears)
# --------------------------------------------------------------------------- #
DATASET_END_WARNING = (
    f"The event log ends on {DATASET_END_DISPLAY}. Applications still open at that date "
    "have no final status, and their durations are cut off at the last recorded event. "
    "Recent start months (especially November-December 2016) are therefore incomplete: "
    "they look faster and less cancelled than they will eventually be."
)

TIMER_WARNING = (
    "Some waits appear to be driven by automated rules, not by staff. Most A_Cancelled "
    "events are recorded by User_1 (likely, but not confirmed, a system account), early in "
    "the morning (06:00-08:00 UTC), about 30 days after the last offer was sent - a pattern "
    "consistent with an automatic expiry batch job. Long waits ending in such events should "
    "not be read as staff performance problems."
)

NOT_RECOVERY_NOTE = (
    "Activity recorded after an offer cancellation (including a replacement offer) is not "
    "evidence of customer recovery. In the offer-first cancellation population, every "
    "application still ended with A_Cancelled."
)

OFFER_VS_APPLICATION_CANCELLATION = (
    "O_Cancelled cancels a single offer; the application can continue and may still end "
    "with an accepted offer. A_Cancelled cancels the whole application. They are counted "
    "separately and must not be added together."
)

CREDIT_SCORE_WARNING = (
    "CreditScore is 0 on most offers. A score of 0 is treated as potentially missing and "
    "is excluded from all averages and percentiles; zero values are reported as a count."
)

LAST_ACTIVITY_NOT_OUTCOME = (
    "reports/application_outcomes.csv lists the last recorded activity of each application, "
    "which is usually a workflow task. It is not the application's final status."
)

A_PENDING_NOTE = (
    "A_Pending means the customer accepted an offer. The log does not show whether the "
    "loan was approved or paid out, so it is not labelled 'Approved' or 'Funded'."
)


# --------------------------------------------------------------------------- #
# Metric registry
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class MetricDefinition:
    key: str
    name: str
    definition: str
    calculation: str
    unit: str
    source: str = SOURCE_EVENT_LOG
    comparable_report: str | None = None
    caveats: tuple[str, ...] = ()


_DEFS = (
    # Applications and final status ------------------------------------------------
    MetricDefinition(
        "total_applications",
        "Total applications",
        "Number of distinct loan applications in the event log.",
        "Count of distinct case:concept:name values.",
        "applications",
        comparable_report="application_performance.csv (row count)",
    ),
    MetricDefinition(
        "final_status_pending",
        "A_Pending (offer accepted)",
        "Applications whose final status is A_Pending: the customer accepted an offer.",
        "Applications whose last final-status event (A_Pending, A_Cancelled, A_Denied) is A_Pending.",
        "applications",
        caveats=(A_PENDING_NOTE,),
    ),
    MetricDefinition(
        "final_status_cancelled",
        "A_Cancelled (application cancelled)",
        "Applications whose final status is A_Cancelled: the whole application was cancelled.",
        "Applications whose last final-status event is A_Cancelled.",
        "applications",
        caveats=(OFFER_VS_APPLICATION_CANCELLATION, TIMER_WARNING),
    ),
    MetricDefinition(
        "final_status_denied",
        "A_Denied (application denied)",
        "Applications whose final status is A_Denied: the bank declined the application.",
        "Applications whose last final-status event is A_Denied.",
        "applications",
    ),
    MetricDefinition(
        "no_final_status",
        "No final status recorded",
        "Applications with none of A_Pending, A_Cancelled or A_Denied in the log.",
        "Applications with no final-status event.",
        "applications",
        caveats=(DATASET_END_WARNING,),
    ),
    # Duration ---------------------------------------------------------------
    MetricDefinition(
        "median_duration_days",
        "Median processing duration",
        "Median time from an application's first to its last recorded event.",
        "median(last event time - first event time), in days, over the selected applications.",
        "days",
        comparable_report="application_performance.csv (duration_days)",
        caveats=(DATASET_END_WARNING,),
    ),
    MetricDefinition(
        "p90_duration_days",
        "90th percentile processing duration",
        "Duration that 90% of applications do not exceed (first to last recorded event).",
        "90th percentile (linear interpolation) of duration_days.",
        "days",
        comparable_report="application_performance.csv (duration_days)",
        caveats=(DATASET_END_WARNING,),
    ),
    MetricDefinition(
        "median_days_to_final_status",
        "Median time to final status",
        "Median time from the first event to the final-status event (A_Pending, A_Cancelled "
        "or A_Denied). Excludes trailing workflow clean-up events.",
        "median(final-status event time - first event time), in days; open applications excluded.",
        "days",
        caveats=(DATASET_END_WARNING,),
    ),
    # Offer cancellation (O_Cancelled) ---------------------------------------------
    MetricDefinition(
        "offer_cancellation_events",
        "Offer cancellations (O_Cancelled events)",
        "Number of O_Cancelled events. Each cancels one offer, not the application.",
        "Count of events with activity O_Cancelled.",
        "events",
        comparable_report="activity_frequency.csv (O_Cancelled total_events)",
        caveats=(OFFER_VS_APPLICATION_CANCELLATION,),
    ),
    MetricDefinition(
        "applications_with_offer_cancellation",
        "Applications with an offer cancellation",
        "Applications with at least one O_Cancelled event, whatever their final status.",
        "Distinct applications with >=1 O_Cancelled event.",
        "applications",
        comparable_report="activity_frequency.csv (O_Cancelled applications)",
        caveats=(OFFER_VS_APPLICATION_CANCELLATION,),
    ),
    # Application cancellation (A_Cancelled) ----------------------------------------
    MetricDefinition(
        "application_cancellation_events",
        "Application cancellations (A_Cancelled events)",
        "Number of A_Cancelled events. Each cancels a whole application.",
        "Count of events with activity A_Cancelled.",
        "events",
        comparable_report="activity_frequency.csv (A_Cancelled total_events)",
        caveats=(OFFER_VS_APPLICATION_CANCELLATION,),
    ),
    MetricDefinition(
        "likely_automatic_expiry_cancellations",
        "Likely automatic expiry cancellations",
        "A_Cancelled events recorded by User_1 (likely, but not confirmed, a system account) "
        "at least 30 days after the application's last offer was sent - a pattern consistent "
        "with an automatic expiry rule.",
        "A_Cancelled events where org:resource = User_1 and time since the last O_Sent event >= 30 days.",
        "applications",
        caveats=(TIMER_WARNING,),
    ),
    # Offer-first cancellation population ------------------------------------------
    MetricDefinition(
        "offer_first_cancellation_population",
        "Offer-first cancellation population",
        "Applications whose first O_Cancelled happened strictly before their A_Cancelled.",
        "Applications with both events where min(O_Cancelled time) < min(A_Cancelled time). "
        "gap_days = A_Cancelled time - first O_Cancelled time; activity is counted strictly between them.",
        "applications",
        comparable_report="offer_cancellation_followup_patterns.csv",
        caveats=(NOT_RECOVERY_NOTE,),
    ),
    # Offers -----------------------------------------------------------------
    MetricDefinition(
        "offers_created",
        "Offers created",
        "Distinct offers created. O_Create Offer and O_Created are recorded together for each "
        "offer, so only O_Create Offer is counted.",
        "Distinct EventID values of O_Create Offer events.",
        "offers",
        comparable_report="activity_frequency.csv (O_Create Offer total_events)",
    ),
    MetricDefinition(
        "offers_sent",
        "Offers sent",
        "Distinct offers with an O_Sent (mail and online) or O_Sent (online only) event.",
        "Distinct OfferID values of O_Sent events.",
        "offers",
    ),
    MetricDefinition(
        "offers_returned",
        "Offers returned",
        "Distinct offers with an O_Returned event (the customer sent the offer back).",
        "Distinct OfferID values of O_Returned events.",
        "offers",
        comparable_report="activity_frequency.csv (O_Returned total_events)",
    ),
    MetricDefinition(
        "offer_return_rate",
        "Offer return rate",
        "Share of sent offers that were returned.",
        "offers_returned / offers_sent x 100.",
        "%",
    ),
    MetricDefinition(
        "credit_score_zero_offers",
        "Offers with CreditScore = 0",
        "Offers whose CreditScore is 0, treated as potentially missing.",
        "Count of offers with CreditScore == 0.",
        "offers",
        caveats=(CREDIT_SCORE_WARNING,),
    ),
    MetricDefinition(
        "median_credit_score_nonzero",
        "Median CreditScore (non-zero only)",
        "Median CreditScore over offers with a score above 0.",
        "median(CreditScore) where CreditScore > 0.",
        "score",
        caveats=(CREDIT_SCORE_WARNING,),
    ),
)

# --------------------------------------------------------------------------- #
# Chart measures (Phase 5 additions; existing entries above are unchanged)
# --------------------------------------------------------------------------- #
INTERLEAVING_NOTE = (
    "Consecutive events can come from different streams (Application, Offer, Workflow) "
    "recorded in parallel, so a transition is the order of recorded events, not "
    "necessarily a hand-off between steps."
)

_CHART_DEFS = (
    MetricDefinition(
        "transition_count",
        "Transition count",
        "Number of times one recorded event is directly followed by another within the same application.",
        "Count of consecutive event pairs (activity, next activity) per application, ordered by timestamp.",
        "transitions",
        comparable_report="process_bottlenecks.csv (transition_count)",
        caveats=(INTERLEAVING_NOTE,),
    ),
    MetricDefinition(
        "median_wait_hours",
        "Median wait between events",
        "Median time between an event and the next recorded event of the same application, for one activity pair.",
        "median(next event time - event time), in hours, over all occurrences of the pair.",
        "hours",
        comparable_report="process_bottlenecks.csv (median_wait_hours)",
        caveats=(INTERLEAVING_NOTE, TIMER_WARNING),
    ),
    MetricDefinition(
        "p90_wait_hours",
        "90th percentile wait between events",
        "Wait that 90% of occurrences of an activity pair do not exceed.",
        "90th percentile (linear interpolation) of the wait in hours over all occurrences of the pair.",
        "hours",
        comparable_report="process_bottlenecks.csv (p90_wait_hours)",
        caveats=(INTERLEAVING_NOTE, TIMER_WARNING),
    ),
    MetricDefinition(
        "activity_events_all",
        "Activity events (all lifecycle stages)",
        "Events recorded for an activity, counting every lifecycle stage (schedule, start, suspend, "
        "resume, complete, ...).",
        "Count of events per activity.",
        "events",
        comparable_report="activity_frequency.csv (total_events)",
    ),
    MetricDefinition(
        "activity_events_completed",
        "Activity events (completed only)",
        "Events recorded for an activity with lifecycle stage 'complete' only.",
        "Count of events per activity where lifecycle:transition = complete.",
        "events",
    ),
    MetricDefinition(
        "followup_gap_days",
        "Gap from first O_Cancelled to A_Cancelled",
        "Time between an application's first offer cancellation and its application cancellation, "
        "for the offer-first cancellation population.",
        "A_Cancelled time - first O_Cancelled time, in days.",
        "days",
        comparable_report="offer_cancellation_followup_patterns.csv (gap_days)",
        caveats=(NOT_RECOVERY_NOTE,),
    ),
    MetricDefinition(
        "followup_events_between",
        "Events between first O_Cancelled and A_Cancelled",
        "Number of events recorded strictly between an application's first O_Cancelled and its A_Cancelled.",
        "Count of events with first O_Cancelled time < timestamp < A_Cancelled time.",
        "events",
        comparable_report="offer_cancellation_followup_patterns.csv (events_between)",
        caveats=(NOT_RECOVERY_NOTE,),
    ),
    MetricDefinition(
        "days_since_last_offer_sent",
        "Days from last offer sent to A_Cancelled",
        "Time between the last O_Sent event before an application's A_Cancelled and the A_Cancelled itself.",
        "A_Cancelled time - latest O_Sent time at or before it, in days; missing if no offer was sent.",
        "days",
        caveats=(TIMER_WARNING,),
    ),
    MetricDefinition(
        "milestone_offer_sent",
        "Applications with at least one offer sent",
        "Applications with one or more O_Sent (mail and online) or O_Sent (online only) events.",
        "Count of applications where offers_sent > 0.",
        "applications",
    ),
    MetricDefinition(
        "milestone_no_offer_sent",
        "Applications with no offer sent",
        "Applications with no O_Sent event.",
        "Count of applications where offers_sent = 0.",
        "applications",
    ),
    MetricDefinition(
        "milestone_offer_returned",
        "Applications with at least one offer returned",
        "Applications with at least one offer sent and one or more O_Returned events.",
        "Count of applications where offers_sent > 0 and offers_returned > 0.",
        "applications",
    ),
    MetricDefinition(
        "milestone_offer_not_returned",
        "Applications with an offer sent but none returned",
        "Applications with at least one offer sent and no O_Returned event.",
        "Count of applications where offers_sent > 0 and offers_returned = 0.",
        "applications",
    ),
)

METRICS = MappingProxyType({d.key: d for d in (*_DEFS, *_CHART_DEFS)})


def get_definition(key: str) -> MetricDefinition:
    try:
        return METRICS[key]
    except KeyError:
        raise KeyError(f"No definition registered for metric '{key}'") from None
