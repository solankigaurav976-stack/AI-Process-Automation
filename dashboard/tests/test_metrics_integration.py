"""Integration: metrics on the real event log satisfy internal consistency rules.

Comparison against the existing CSV reports is Phase 3 (reconciliation); this
module only checks that the metrics are coherent with each other and with the
documented dataset facts.
"""

from __future__ import annotations

import pytest

from core import config, definitions, loaders
from core import metrics as m

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def events():
    return m.prepare_events(loaders.load_event_log(m.METRIC_EVENT_COLUMNS))


@pytest.fixture(scope="module")
def summary(events):
    return m.application_summary(events, dataset_end=definitions.DATASET_END)


def test_dataset_end_constant_matches_last_event(events):
    assert events[m.T].max() == definitions.DATASET_END


def test_final_status_categories_partition_all_applications(summary):
    dist = m.final_status_distribution(summary)
    assert dist["applications"].sum() == len(summary)
    assert dist["percentage"].sum() == pytest.approx(100.0)
    assert not summary["has_conflicting_final_status"].any()


def test_open_applications_are_concentrated_at_dataset_end(summary):
    trend = m.monthly_trend(summary)
    flagged = set(trend.loc[trend["censoring_warning"], "start_month"])
    assert flagged and flagged <= {"2016-10", "2016-11", "2016-12"}
    assert "2016-12" in set(trend.loc[trend["short_observation_window"], "start_month"])


def test_offer_counts_are_consistent(events, summary):
    om = m.offer_metrics(events)
    assert om.orphan_offer_events == 0
    assert om.o_created_events == om.o_create_offer_events == om.offers_created
    assert om.offers_returned <= om.offers_sent <= om.offers_created
    assert om.offers_accepted + om.offers_refused + om.offers_cancelled + om.offers_without_outcome == om.offers_created
    assert om.offers_sent == om.offers_sent_mail_and_online + om.offers_sent_online_only
    assert summary["offers_created"].sum() == om.offers_created


def test_offer_and_application_cancellations_differ(events, summary):
    oc = m.offer_cancellation_metrics(events, summary)
    ac = m.application_cancellation_metrics(events, summary)
    assert oc.applications_with_offer_cancellation != ac.applications_with_application_cancellation
    pending_after_offer_cancel = oc.final_status_breakdown.set_index("final_status").loc[
        config.APPLICATION_PENDING, "applications"
    ]
    assert pending_after_offer_cancel > 0  # offer cancellation does not imply application cancellation
    assert ac.sequence_breakdown["applications"].sum() == ac.applications_with_application_cancellation


def test_offer_first_population_all_ended_cancelled(events):
    fu = m.offer_first_cancellation_followup(events)
    assert len(fu) > 0
    assert (fu["final_status"] == config.APPLICATION_CANCELLED).all()
    assert set(fu["pattern"]) <= set(m.PATTERN_PRECEDENCE)


def test_timer_flags_only_on_transitions_into_application_cancellation(events):
    tr = m.transition_metrics(events)
    flagged = tr[tr["likely_timer_driven"].fillna(False)]
    assert len(flagged) >= 1
    assert set(flagged["next_activity"]) == {config.APPLICATION_CANCELLED}


def test_credit_score_zero_values_are_reported_not_averaged(events):
    cs = m.credit_score_summary(m.offer_table(events)).iloc[0]
    assert cs["score_zero_potentially_missing"] > 0
    assert cs["min_nonzero"] > 0


def test_cancellation_closures_are_near_instant(events):
    cl = m.cancellation_closure_events(events)
    assert len(cl) > 0 and cl["delay_hours"].max() < 0.01
