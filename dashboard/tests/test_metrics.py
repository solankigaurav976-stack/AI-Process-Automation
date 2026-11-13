"""Unit tests for core.metrics on small hand-built event logs."""

from __future__ import annotations

import math
import re

import numpy as np
import pandas as pd
import pytest

from core import config, definitions
from core import metrics as m

C, A, T, L, R = m.C, m.A, m.T, m.L, m.R
BASE = pd.Timestamp("2016-01-01 09:00", tz="UTC")


def ev(*rows, resource="User_10") -> pd.DataFrame:
    """Build events from (case, activity, hours_after_BASE[, extras dict]) tuples."""
    records = []
    for i, row in enumerate(rows):
        case, activity, hours = row[:3]
        extra = row[3] if len(row) > 3 else {}
        rec = {
            C: case,
            A: activity,
            T: BASE + pd.Timedelta(hours=hours),
            L: "complete",
            R: resource,
            m.EVENT_ID: f"E{i}",
            m.OFFER_ID: None,
        }
        rec.update(extra)
        records.append(rec)
    df = pd.DataFrame(records)
    df[m.OFFER_ID] = df[m.OFFER_ID].astype("str").where(df[m.OFFER_ID].notna())
    df[T] = df[T].astype("datetime64[us, UTC]")
    return df


def offer(case, activity, hours, offer_id, **extra):
    if activity == m.OFFER_CREATE:
        return (case, activity, hours, {m.EVENT_ID: offer_id, **extra})
    return (case, activity, hours, {m.OFFER_ID: offer_id, **extra})


EMPTY = ev(("x", "A_Create Application", 0)).iloc[0:0]


# --------------------------------------------------------------------------- #
# Input preparation
# --------------------------------------------------------------------------- #
def test_prepare_orders_by_case_then_time_and_keeps_input_order_on_ties():
    df = ev(("b", "X", 5), ("a", "Y", 2), ("a", "Z", 1), ("a", "TIE2", 3), ("a", "TIE1", 3))
    out = m.prepare_events(df)
    assert out[A].astype(str).tolist() == ["Z", "Y", "TIE2", "TIE1", "X"]


def test_prepare_does_not_modify_input():
    df = ev(("b", "X", 5), ("a", "Y", 2))
    before = df.copy()
    m.prepare_events(df)
    m.application_summary(df)
    pd.testing.assert_frame_equal(df, before)


@pytest.mark.parametrize(
    "mutate,match",
    [
        (lambda d: d.drop(columns=[T]), "missing column"),
        (lambda d: d.assign(**{T: d[T].dt.tz_localize(None)}), "time-zone-aware"),
        (lambda d: d.assign(**{T: pd.Series([pd.NaT, d[T].iloc[1]], dtype="datetime64[us, UTC]")}), "no timestamp"),
        (lambda d: d.assign(**{C: [None, "a"]}), "no application ID"),
    ],
)
def test_malformed_input_raises(mutate, match):
    with pytest.raises(m.MetricInputError, match=match):
        m.application_summary(mutate(ev(("a", "X", 0), ("a", "Y", 1))))


def test_non_dataframe_input_raises():
    with pytest.raises(m.MetricInputError, match="expected a DataFrame"):
        m.application_summary([1, 2, 3])


# --------------------------------------------------------------------------- #
# Application summary and final status
# --------------------------------------------------------------------------- #
@pytest.fixture
def three_apps():
    return ev(
        ("a", "A_Create Application", 0),
        offer("a", m.OFFER_CREATE, 1, "O1"),
        offer("a", m.OFFER_CREATED_DUPLICATE, 1.01, "O1"),
        offer("a", m.OFFER_SENT_MAIL_ONLINE, 2, "O1"),
        offer("a", m.OFFER_CANCELLED, 10, "O1"),  # offer cancelled ...
        offer("a", m.OFFER_CREATE, 11, "O2"),
        offer("a", m.OFFER_SENT_ONLINE_ONLY, 12, "O2"),
        offer("a", m.OFFER_RETURNED, 20, "O2"),
        ("a", "A_Pending", 48),  # ... but the application ends with an accepted offer
        offer("a", m.OFFER_ACCEPTED, 48.001, "O2"),
        ("a", "W_Call after offers", 50),
        ("b", "A_Create Application", 0),
        ("b", "A_Denied", 24),
        ("c", "A_Create Application", 0),
        ("c", "W_Handle leads", 5),
    )


def test_summary_basic_fields(three_apps):
    s = m.application_summary(three_apps).set_index("case_id")
    assert list(s.index) == ["a", "b", "c"]
    assert s.loc["a", "duration_days"] == pytest.approx(50 / 24)
    assert s.loc["a", "days_to_final_status"] == pytest.approx(2.0)
    assert s.loc["a", "final_status"] == "A_Pending"
    assert s.loc["a", "final_status_label"] == "A_Pending (offer accepted)"
    assert s.loc["a", "events_after_final_status"] == 2
    assert s.loc["a", "offers_created"] == 2  # O_Created duplicates not counted
    assert s.loc["a", "offers_sent"] == 2 and s.loc["a", "offers_returned"] == 1
    assert bool(s.loc["a", "has_offer_cancellation"]) and not bool(s.loc["a", "has_application_cancellation"])
    assert s.loc["b", "final_status"] == "A_Denied"
    assert bool(s.loc["c", "is_open"]) and pd.isna(s.loc["c", "final_status"])
    assert s.loc["c", "final_status_label"] == config.NO_FINAL_STATUS_LABEL
    assert pd.isna(s.loc["c", "days_to_final_status"])
    assert s.index.dtype == "str"  # categorical working dtype is not leaked


def test_summary_columns_and_dataset_end(three_apps):
    end = pd.Timestamp("2016-01-11 09:00", tz="UTC")
    s = m.application_summary(three_apps, dataset_end=end)
    assert list(s.columns)[: len(m.SUMMARY_COLUMNS)] == list(m.SUMMARY_COLUMNS)
    assert s.attrs["dataset_end"] == end
    assert s["observed_days_until_dataset_end"].tolist() == [10.0, 10.0, 10.0]
    assert s["start_month"].tolist() == ["2016-01"] * 3


def test_final_status_distribution_includes_all_categories(three_apps):
    dist = m.final_status_distribution(m.application_summary(three_apps))
    assert dist["label"].tolist() == [
        "A_Pending (offer accepted)",
        "A_Cancelled (application cancelled)",
        "A_Denied (application denied)",
        "No final status recorded",
    ]
    assert dist["applications"].tolist() == [1, 0, 1, 1]
    assert dist["percentage"].sum() == pytest.approx(100.0)


def test_conflicting_final_statuses_use_last_and_are_flagged():
    s = m.application_summary(ev(("a", "A_Denied", 0), ("a", "A_Pending", 5))).iloc[0]
    assert s["final_status"] == "A_Pending"
    assert bool(s["has_conflicting_final_status"]) and s["final_status_events"] == 2


def test_repeated_same_final_status_is_not_a_conflict():
    s = m.application_summary(ev(("a", "A_Denied", 0), ("a", "A_Denied", 5))).iloc[0]
    assert s["final_status_events"] == 2 and not bool(s["has_conflicting_final_status"])


def test_out_of_order_lifecycle_is_handled_by_time():
    # A_Cancelled timestamped before A_Create Application: ordering follows time.
    s = m.application_summary(ev(("a", "A_Create Application", 5), ("a", "A_Cancelled", 1))).iloc[0]
    assert s["first_activity"] == "A_Cancelled" and s["last_activity"] == "A_Create Application"
    assert s["events_after_final_status"] == 1


def test_single_event_application():
    s = m.application_summary(ev(("a", "A_Create Application", 0))).iloc[0]
    assert s["duration_days"] == 0 and bool(s["is_open"]) and s["total_events"] == 1


def test_shuffled_input_gives_identical_results(three_apps):
    shuffled = three_apps.sample(frac=1, random_state=7)
    pd.testing.assert_frame_equal(m.application_summary(shuffled), m.application_summary(three_apps))
    pd.testing.assert_frame_equal(m.offer_table(shuffled), m.offer_table(three_apps))


# --------------------------------------------------------------------------- #
# Durations and monthly trend
# --------------------------------------------------------------------------- #
@pytest.fixture
def ten_durations():
    rows = []
    for i in range(1, 11):
        rows += [(f"c{i}", "A_Create Application", 0), (f"c{i}", "A_Pending" if i < 10 else "W_X", 24 * i)]
    return m.application_summary(ev(*rows))


def test_duration_statistics_median_and_p90(ten_durations):
    st = m.duration_statistics(ten_durations).iloc[0]
    assert st["count"] == 10
    assert st["median"] == pytest.approx(5.5)
    assert st["p90"] == pytest.approx(9.1)  # linear interpolation
    assert st["open_applications"] == 1


def test_duration_statistics_excluding_open_and_by_group(ten_durations):
    st = m.duration_statistics(ten_durations, include_open=False).iloc[0]
    assert st["count"] == 9 and st["open_applications"] == 0
    by = m.duration_statistics(ten_durations, by="final_status_label").set_index("group")
    assert by.loc[config.NO_FINAL_STATUS_LABEL, "count"] == 1
    tf = m.duration_statistics(ten_durations, column="days_to_final_status").iloc[0]
    assert tf["count"] == 9  # open application has no time to final status


def test_duration_statistics_rejects_unknown_column(ten_durations):
    with pytest.raises(m.MetricInputError):
        m.duration_statistics(ten_durations, column="total_events")


def test_monthly_trend_flags_censoring():
    rows = [
        ("jan", "A_Create Application", 0), ("jan", "A_Pending", 24),
        ("feb", "A_Create Application", 24 * 40), ("feb", "A_Cancelled", 24 * 41),
        ("mar", "A_Create Application", 24 * 70),  # open
    ]
    s = m.application_summary(ev(*rows), dataset_end=pd.Timestamp("2016-04-15", tz="UTC"))
    tr = m.monthly_trend(s).set_index("start_month")
    assert tr.loc["2016-01", "A_Pending"] == 1 and tr.loc["2016-02", "A_Cancelled"] == 1
    assert not tr.loc["2016-01", "censoring_warning"]
    assert tr.loc["2016-02", "short_observation_window"]  # 45 days < 60
    assert tr.loc["2016-03", "has_open_applications"] and tr.loc["2016-03", "no_final_status"] == 1
    assert tr.loc["2016-03", "days_observed_after_month_end"] == pytest.approx(14.0)


def test_monthly_trend_uses_global_dataset_end_after_filtering():
    s = m.application_summary(
        ev(("a", "A_Create Application", 0), ("b", "A_Create Application", 24 * 100)),
        dataset_end=pd.Timestamp("2016-12-31", tz="UTC"),
    )
    filtered = s[s["case_id"] == "a"]
    assert filtered.attrs["dataset_end"] == pd.Timestamp("2016-12-31", tz="UTC")
    assert m.monthly_trend(filtered)["days_observed_after_month_end"].iloc[0] == pytest.approx(334.0)


# --------------------------------------------------------------------------- #
# Activity frequency and transitions
# --------------------------------------------------------------------------- #
def test_activity_frequency_all_vs_completed_lifecycle():
    df = ev(("a", "W_Task", 0), ("a", "W_Task", 1), ("b", "W_Task", 2), ("b", "A_X", 3))
    df[L] = ["schedule", "complete", "complete", "complete"]
    allf = m.activity_frequency(df).set_index("activity")
    assert allf.loc["W_Task", "total_events"] == 3 and allf.loc["W_Task", "applications"] == 2
    comp = m.activity_frequency(df, lifecycle=["complete"]).set_index("activity")
    assert comp.loc["W_Task", "total_events"] == 2
    lb = m.activity_lifecycle_breakdown(df).set_index("activity")
    assert lb.loc["W_Task", "schedule"] == 1 and lb.loc["W_Task", "total_events"] == 3


def test_transition_waits_and_min_count():
    rows = []
    for i in range(5):
        rows += [(f"c{i}", "A_Create Application", 0), (f"c{i}", "W_Handle leads", 2 + i)]
    tr = m.transition_metrics(ev(*rows), min_count=5).iloc[0]
    assert (tr["activity"], tr["next_activity"]) == ("A_Create Application", "W_Handle leads")
    assert tr["transition_count"] == 5
    assert tr["median_wait_hours"] == pytest.approx(4.0)
    assert tr["average_wait_hours"] == pytest.approx(4.0)
    assert tr["p90_wait_hours"] == pytest.approx(5.6)
    assert bool(tr["crosses_event_streams"])
    assert m.transition_metrics(ev(*rows), min_count=6).empty


def test_timer_flag_requires_system_target_and_long_wait():
    rows, system_rows = [], []
    for i in range(5):
        rows += [(f"c{i}", "W_Call after offers", 0), (f"c{i}", "A_Cancelled", 24 * 30)]
    timer_df = ev(*rows)
    timer_df.loc[timer_df[A] == "A_Cancelled", R] = m.SYSTEM_RESOURCE
    timer_df.loc[timer_df[A] == "A_Cancelled", T] = BASE + pd.Timedelta(days=30, hours=-2)  # 07:00 UTC
    tr = m.transition_metrics(timer_df, min_count=1).iloc[0]
    assert bool(tr["likely_timer_driven"]) and "not a staff waiting time" in tr["interpretation"]
    assert tr["target_system_share"] == 1.0 and tr["target_batch_window_share"] == 1.0

    staff = m.transition_metrics(ev(*rows), min_count=1).iloc[0]  # same wait, done by staff
    assert not bool(staff["likely_timer_driven"])

    short = timer_df.copy()
    short.loc[short[A] == "A_Cancelled", T] = BASE + pd.Timedelta(hours=1)
    assert not bool(m.transition_metrics(short, min_count=1).iloc[0]["likely_timer_driven"])


def test_timer_flag_unknown_without_resource_column():
    df = ev(("a", "X", 0), ("a", "Y", 50)).drop(columns=[R])
    tr = m.transition_metrics(df, min_count=1).iloc[0]
    assert pd.isna(tr["likely_timer_driven"]) and "Cannot assess" in tr["interpretation"]


def test_transitions_never_cross_applications():
    tr = m.transition_metrics(ev(("a", "X", 0), ("b", "Y", 1)), min_count=1)
    assert tr.empty


# --------------------------------------------------------------------------- #
# Offers and credit score
# --------------------------------------------------------------------------- #
def test_offer_table_and_metrics(three_apps):
    ot = m.offer_table(three_apps).set_index("offer_id")
    assert ot.loc["O1", "sent_channel"] == "mail and online" and ot.loc["O1", "offer_outcome"] == "O_Cancelled"
    assert ot.loc["O2", "sent_channel"] == "online only" and bool(ot.loc["O2", "was_returned"])
    assert ot.loc["O2", "offer_outcome"] == "O_Accepted"
    om = m.offer_metrics(three_apps)
    assert om.offers_created == 2 and om.o_created_events == 1 and om.o_create_offer_events == 2
    assert om.offers_sent == 2 and om.offers_returned == 1
    assert om.offer_return_rate_percent == pytest.approx(50.0)
    assert (om.offers_accepted, om.offers_refused, om.offers_cancelled, om.offers_without_outcome) == (1, 0, 1, 0)
    assert om.applications_with_offers == 1 and om.orphan_offer_events == 0


def test_orphan_offer_events_and_offer_without_outcome():
    df = ev(offer("a", m.OFFER_CREATE, 0, "O1"), offer("a", m.OFFER_CANCELLED, 1, "GHOST"))
    om = m.offer_metrics(df)
    assert om.orphan_offer_events == 1 and om.offers_without_outcome == 1 and om.offers_cancelled == 0


def test_no_offers_at_all():
    om = m.offer_metrics(ev(("a", "A_Create Application", 0)))
    assert om.offers_created == 0 and math.isnan(om.offer_return_rate_percent)
    assert om.max_offers_per_application == 0


def test_credit_score_zero_is_excluded_from_statistics():
    offers = pd.DataFrame({"CreditScore": [0.0, 0.0, 800.0, 900.0, np.nan]})
    cs = m.credit_score_summary(offers).iloc[0]
    assert cs["score_zero_potentially_missing"] == 2 and cs["score_missing"] == 1
    assert cs["score_nonzero"] == 2
    assert cs["mean_nonzero"] == pytest.approx(850.0)  # not 425 (which would include zeros)
    assert cs["zero_share_percent"] == pytest.approx(50.0)


def test_credit_score_all_zero():
    cs = m.credit_score_summary(pd.DataFrame({"CreditScore": [0.0, 0.0]})).iloc[0]
    assert cs["score_nonzero"] == 0 and math.isnan(cs["mean_nonzero"])


def test_credit_score_requires_column():
    with pytest.raises(m.MetricInputError):
        m.credit_score_summary(pd.DataFrame({"x": [1]}))


# --------------------------------------------------------------------------- #
# Cancellations: offer vs application kept separate
# --------------------------------------------------------------------------- #
def test_offer_and_application_cancellations_are_counted_separately(three_apps):
    extra = ev(("d", "A_Create Application", 0), offer("d", m.OFFER_CANCELLED, 1, "O9"), ("d", "A_Cancelled", 1.5))
    df = pd.concat([three_apps, extra], ignore_index=True)
    s = m.application_summary(df)
    oc = m.offer_cancellation_metrics(df, s)
    ac = m.application_cancellation_metrics(df, s)
    assert oc.offer_cancellation_events == 2 and oc.applications_with_offer_cancellation == 2
    breakdown = oc.final_status_breakdown.set_index("final_status")["applications"]
    assert breakdown["A_Pending"] == 1 and breakdown["A_Cancelled"] == 1
    assert ac.application_cancellation_events == 1 and ac.applications_final_status_cancelled == 1
    assert oc.share_of_applications_percent == pytest.approx(50.0)
    assert ac.share_of_applications_percent == pytest.approx(25.0)


def test_cancellation_sequences_and_expiry():
    sys_ = {R: m.SYSTEM_RESOURCE}
    df = ev(
        # offer first, then automatic expiry 31 days after last offer sent
        offer("first", m.OFFER_SENT_MAIL_ONLINE, 0, "O1"),
        offer("first", m.OFFER_CANCELLED, 1, "O1"),
        ("first", "A_Cancelled", 24 * 31, sys_),
        # same timestamp
        ("same", "A_Cancelled", 5),
        offer("same", m.OFFER_CANCELLED, 5, "O2"),
        # application first, by staff
        offer("app", m.OFFER_SENT_MAIL_ONLINE, 0, "O3"),
        ("app", "A_Cancelled", 24 * 40),
        offer("app", m.OFFER_CANCELLED, 24 * 40 + 0.001, "O3"),
        # system cancellation but only 10 days after sending: not expiry
        offer("early", m.OFFER_SENT_MAIL_ONLINE, 0, "O4"),
        ("early", "A_Cancelled", 24 * 10, sys_),
        # no offer cancellation at all
        ("none", "A_Cancelled", 1),
    )
    t = m.application_cancellation_table(df).set_index("case_id")
    assert t.loc["first", "cancellation_sequence"] == m.SEQ_OFFER_FIRST
    assert t.loc["same", "cancellation_sequence"] == m.SEQ_SAME_TIME
    assert t.loc["app", "cancellation_sequence"] == m.SEQ_APPLICATION_FIRST
    assert t.loc["none", "cancellation_sequence"] == m.SEQ_NO_OFFER_CANCEL
    assert t.loc["first", "days_since_last_offer_sent"] == pytest.approx(31.0)
    assert bool(t.loc["first", "likely_automatic_expiry"])
    assert not bool(t.loc["app", "likely_automatic_expiry"])  # staff, despite 40 days
    assert not bool(t.loc["early", "likely_automatic_expiry"])  # system, but < 30 days
    assert not bool(t.loc["none", "likely_automatic_expiry"])  # no offer sent
    ac = m.application_cancellation_metrics(df)
    assert ac.likely_automatic_expiry == 1 and ac.other_application_cancellations == 4
    assert ac.sequence_breakdown["applications"].sum() == 5


def test_expiry_unknown_without_resource_column():
    df = ev(offer("a", m.OFFER_SENT_MAIL_ONLINE, 0, "O1"), ("a", "A_Cancelled", 24 * 31)).drop(columns=[R])
    t = m.application_cancellation_table(df)
    assert pd.isna(t["likely_automatic_expiry"].iloc[0])
    assert m.application_cancellation_metrics(df).likely_automatic_expiry == 0


# --------------------------------------------------------------------------- #
# Offer-first cancellation follow-up patterns
# --------------------------------------------------------------------------- #
@pytest.fixture
def followup_log():
    return ev(
        # No recorded intermediate activity
        offer("none", m.OFFER_CANCELLED, 0, "O1"), ("none", "A_Cancelled", 1),
        # Replacement offer created (takes precedence over sent / workflow)
        offer("repl", m.OFFER_CANCELLED, 0, "O2"), offer("repl", m.OFFER_CREATE, 1, "O3"),
        offer("repl", m.OFFER_SENT_MAIL_ONLINE, 2, "O3"), ("repl", "W_Call after offers", 3),
        ("repl", "A_Cancelled", 24 * 30),
        # Offer sent, no new offer creation
        offer("sent", m.OFFER_CANCELLED, 0, "O4"), offer("sent", m.OFFER_SENT_ONLINE_ONLY, 1, "O5"),
        offer("sent", m.OFFER_RETURNED, 2, "O5"), ("sent", "A_Cancelled", 5),
        # Workflow activity only
        offer("wf", m.OFFER_CANCELLED, 0, "O6"), ("wf", "W_Validate application", 1), ("wf", "A_Cancelled", 2),
        # Other activity only (another offer cancelled)
        offer("other", m.OFFER_CANCELLED, 0, "O7"), offer("other", m.OFFER_CANCELLED, 1, "O8"),
        ("other", "A_Cancelled", 2),
        # NOT in population: offer cancelled after / at the same time as the application
        ("after", "A_Cancelled", 0), offer("after", m.OFFER_CANCELLED, 1, "O9"),
        ("tie", "A_Cancelled", 0), offer("tie", m.OFFER_CANCELLED, 0, "O10"),
        # NOT in population: offer cancelled but application accepted
        offer("pend", m.OFFER_CANCELLED, 0, "O11"), ("pend", "A_Pending", 1),
    )


def test_followup_population_and_patterns(followup_log):
    fu = m.offer_first_cancellation_followup(followup_log).set_index("case_id")
    assert sorted(fu.index) == ["none", "other", "repl", "sent", "wf"]
    assert fu.loc["none", "pattern"] == m.PATTERN_NO_ACTIVITY and fu.loc["none", "events_between"] == 0
    assert fu.loc["repl", "pattern"] == m.PATTERN_REPLACEMENT_OFFER
    assert fu.loc["repl", ["new_offer", "offer_sent", "workflow_activity"]].all()
    assert fu.loc["sent", "pattern"] == m.PATTERN_OFFER_SENT and bool(fu.loc["sent", "returned_offer"])
    assert fu.loc["wf", "pattern"] == m.PATTERN_WORKFLOW
    assert fu.loc["other", "pattern"] == m.PATTERN_OTHER and fu.loc["other", "events_between"] == 1
    assert fu.loc["repl", "gap_days"] == pytest.approx(30.0)
    assert (fu["final_status"] == "A_Cancelled").all()


def test_followup_counts_only_events_strictly_between():
    df = ev(
        offer("a", m.OFFER_CANCELLED, 0, "O1"),
        ("a", "W_Same_time_as_cancel", 0),  # same timestamp as first O_Cancelled: excluded
        ("a", "W_Inside", 1),
        ("a", "A_Cancelled", 2),
        ("a", "W_Same_time_as_app_cancel", 2),  # excluded
    )
    fu = m.offer_first_cancellation_followup(df).iloc[0]
    assert fu["events_between"] == 1


def test_followup_pattern_summary(followup_log):
    fu = m.offer_first_cancellation_followup(followup_log)
    ps = m.followup_pattern_summary(fu).set_index("pattern")
    assert list(ps.columns) == m.PATTERN_SUMMARY_COLUMNS[1:]
    assert ps["applications"].sum() == 5 and ps["share_percent"].sum() == pytest.approx(100.0)
    assert ps.loc[m.PATTERN_REPLACEMENT_OFFER, "applications_with_offers_sent"] == 1
    assert ps.loc[m.PATTERN_OFFER_SENT, "applications_with_returned_offers"] == 1
    assert list(ps.index) == sorted(ps.index)


def test_cancellation_closure_events():
    df = ev(
        ("a", "W_Complete application", 0),  # before cancellation: ignored
        ("a", "A_Cancelled", 1),
        ("a", "W_Complete application", 1.5, {L: "ate_abort"}),
        ("a", "W_Complete application", 3),
        ("b", "A_Cancelled", 1),  # no completion afterwards
    )
    cl = m.cancellation_closure_events(df)
    assert cl["case_id"].tolist() == ["a"]
    assert cl["delay_hours"].iloc[0] == pytest.approx(0.5)
    assert cl["completion_lifecycle"].iloc[0] == "ate_abort"


# --------------------------------------------------------------------------- #
# Empty data
# --------------------------------------------------------------------------- #
def test_every_function_handles_an_empty_event_log():
    s = m.application_summary(EMPTY)
    assert s.empty and list(s.columns) == list(m.SUMMARY_COLUMNS)
    dist = m.final_status_distribution(s)
    assert dist["applications"].sum() == 0 and dist["percentage"].isna().all()
    assert m.duration_statistics(s).iloc[0]["count"] == 0
    assert m.duration_statistics(s, by="final_status_label").empty
    assert m.monthly_trend(s).empty
    assert m.activity_frequency(EMPTY).empty and m.activity_lifecycle_breakdown(EMPTY).empty
    assert m.transition_metrics(EMPTY).empty
    assert m.offer_table(EMPTY).empty
    om = m.offer_metrics(EMPTY)
    assert om.offers_created == 0
    oc = m.offer_cancellation_metrics(EMPTY)
    assert oc.offer_cancellation_events == 0 and math.isnan(oc.share_of_applications_percent)
    ac = m.application_cancellation_metrics(EMPTY)
    assert ac.application_cancellation_events == 0 and ac.sequence_breakdown["applications"].sum() == 0
    fu = m.offer_first_cancellation_followup(EMPTY)
    assert fu.empty and list(fu.columns) == list(m.FOLLOWUP_COLUMNS)
    assert m.followup_pattern_summary(fu).empty
    assert m.cancellation_closure_events(EMPTY).empty
    cs = m.credit_score_summary(m.offer_table(EMPTY.assign(CreditScore=pd.Series(dtype="float64"))))
    kpis = m.headline_kpis(s, om, oc, ac, fu, cs)
    assert kpis.set_index("key").loc["total_applications", "value"] == 0


def test_application_without_cancellations_has_empty_cancellation_tables():
    df = ev(("a", "A_Create Application", 0), ("a", "A_Pending", 1))
    assert m.application_cancellation_table(df).empty
    assert m.offer_first_cancellation_followup(df).empty
    assert m.cancellation_closure_events(df).empty


# --------------------------------------------------------------------------- #
# KPIs and definitions
# --------------------------------------------------------------------------- #
def test_headline_kpis_have_definitions_and_sources(three_apps):
    s = m.application_summary(three_apps)
    ot = m.offer_table(three_apps.assign(CreditScore=[0.0] * len(three_apps)))
    kpis = m.headline_kpis(
        s,
        m.offer_metrics(three_apps, ot),
        m.offer_cancellation_metrics(three_apps, s),
        m.application_cancellation_metrics(three_apps, s),
        m.offer_first_cancellation_followup(three_apps),
        m.credit_score_summary(ot),
    )
    assert set(kpis["key"]) <= set(definitions.METRICS)
    assert kpis["definition"].str.len().gt(0).all() and kpis["source"].str.len().gt(0).all()
    values = kpis.set_index("key")["value"]
    assert values["total_applications"] == 3 and values["final_status_pending"] == 1
    assert values["offer_cancellation_events"] == 1 and values["application_cancellation_events"] == 0
    assert values["credit_score_zero_offers"] == 2 and pd.isna(values["median_credit_score_nonzero"])
    assert kpis.set_index("key").loc["final_status_pending", "name"] == "A_Pending (offer accepted)"


def test_no_text_presents_activity_as_recovery():
    texts = [*m.PATTERN_DESCRIPTIONS.values(), *(
        " ".join([d.name, d.definition, d.calculation, *d.caveats]) for d in definitions.METRICS.values()
    )]
    for text in texts:
        for match in re.finditer(r"recover\w*", text, flags=re.IGNORECASE):
            preceding = text[max(0, match.start() - 40) : match.start()].lower()
            assert "not" in preceding, f"'recovery' used without negation: {text!r}"


def test_no_text_labels_a_pending_as_approved_or_funded():
    for d in definitions.METRICS.values():
        if "A_Pending" in d.name:
            assert "approved" not in d.name.lower() and "funded" not in d.name.lower()


def test_definitions_registry_lookup():
    assert definitions.get_definition("total_applications").unit == "applications"
    with pytest.raises(KeyError, match="No definition"):
        definitions.get_definition("nope")


def test_user_1_is_never_described_as_a_confirmed_system_account():
    """User-facing texts may call User_1 a system account only with 'likely'."""
    texts = [
        definitions.TIMER_WARNING,
        definitions.DATASET_END_WARNING,
        definitions.NOT_RECOVERY_NOTE,
        definitions.OFFER_VS_APPLICATION_CANCELLATION,
        definitions.CREDIT_SCORE_WARNING,
        definitions.LAST_ACTIVITY_NOT_OUTCOME,
        definitions.A_PENDING_NOTE,
        *m.PATTERN_DESCRIPTIONS.values(),
        *(" ".join([d.name, d.definition, d.calculation, *d.caveats]) for d in definitions.METRICS.values()),
    ]
    for text in texts:
        for match in re.finditer(r"system (?:account|user)", text, flags=re.IGNORECASE):
            preceding = text[max(0, match.start() - 40) : match.start()].lower()
            assert "likely" in preceding, f"User_1 described as a confirmed system account: {text!r}"
        assert "the system account" not in text.lower()
    assert "likely, but not confirmed, a system account" in definitions.TIMER_WARNING
    assert "likely, but not confirmed" in definitions.METRICS["likely_automatic_expiry_cancellations"].definition
