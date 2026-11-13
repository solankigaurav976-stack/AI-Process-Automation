"""Phase 6: the documentation states what the code and data actually produce.

Every headline number quoted in README.md and docs/DEMO_GUIDE.md is recalculated with
the approved core functions; referenced files must exist; the analytical wording rules
(A_Pending, cancellations, User_1, timers, task durations, censoring) are enforced.
"""

from __future__ import annotations

import re

import pytest

from core import config, definitions, loaders
from core import metrics as m
from core import reconciliation as rc

DASHBOARD = config.DASHBOARD_DIR
DOCS = {name: (DASHBOARD / name).read_text() for name in ("README.md", "docs/DEMO_GUIDE.md")}
README = DOCS["README.md"]


def fmt(n) -> str:
    return f"{int(n):,}"


# --------------------------------------------------------------------------- #
# Structure and references (no data needed)
# --------------------------------------------------------------------------- #
def test_referenced_project_files_exist():
    for path in re.findall(r"\]\(((?:core|ui|views|docs|tests)/[^)#]+)\)", README):
        assert (DASHBOARD / path).exists(), path
    for path in ("app.py", "requirements.txt", "requirements-dev.txt", ".streamlit/config.toml",
                 "docs/proposed_root.gitignore", "../src/ingest_data.py"):
        assert (DASHBOARD / path).exists(), path
    assert config.EVENT_LOG_PATH.relative_to(config.PROJECT_ROOT).as_posix() in README
    assert config.RAW_EVENT_LOG_PATH.relative_to(config.PROJECT_ROOT).as_posix() in README


def test_documented_pages_match_the_navigation():
    app = (DASHBOARD / "app.py").read_text()
    titles = re.findall(r'st\.Page\([^)]*?title="([^"]+)"', app)
    assert len(titles) == 5
    for title in titles:
        assert title in README and title in DOCS["docs/DEMO_GUIDE.md"], title


def test_documented_versions_match_requirements():
    pins = dict(line.split("==") for line in (DASHBOARD / "requirements.txt").read_text().splitlines()
                if "==" in line and not line.startswith("#"))
    for package, version in pins.items():
        assert f"{version}" in README, (package, version)


def test_metric_registry_size_and_thresholds_are_documented():
    assert f"{len(definitions.METRICS)} entries" in README
    assert f"less than {m.CENSORING_WINDOW_DAYS} days" in README
    assert f"≥ {int(m.TIMER_MIN_SYSTEM_SHARE * 100)} %" in README
    assert f"≥ {int(m.TIMER_MIN_MEDIAN_HOURS)} h" in README
    assert f"≥ {m.DEFAULT_MIN_TRANSITION_COUNT} occurrences" in README


@pytest.mark.parametrize("name", list(DOCS))
def test_wording_rules(name):
    text = DOCS[name]
    for word in ("approved", "funded"):
        for match in re.finditer(word, text, flags=re.IGNORECASE):
            context = text[max(0, match.start() - 60): match.end()].lower()
            assert any(n in context for n in ("not", "never", "no ")), (name, context)
    assert "the system account" not in text.lower()
    for match in re.finditer(r"User_1\b", text):
        assert "likely, but not confirmed" in text, name
    for match in re.finditer(r"timer-driven|timer", text):
        window = text[max(0, match.start() - 40): match.end() + 40].lower()
        assert any(w in window for w in ("likely", "exploratory", "probably")), (name, window)
    for match in re.finditer(r"task duration", text, flags=re.IGNORECASE):
        context = text[max(0, match.start() - 60): match.end()].lower()
        assert any(n in context for n in ("no ", "not", "never", "why no")), (name, context)
    for match in re.finditer(r"recover\w*", text, flags=re.IGNORECASE):
        assert "not" in text[max(0, match.start() - 40): match.start()].lower(), name
    assert "A_Pending (offer accepted)" in text


def test_readme_discloses_the_required_limitations():
    for phrase in ("98", "1 February 2017", "October, November and December 2016", "O_Cancelled", "A_Cancelled",
                   "last recorded activity", "potentially missing", "no task durations are calculated"):
        assert phrase in README, phrase


# --------------------------------------------------------------------------- #
# Numbers (real data)
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def results():
    ev = m.prepare_events(loaders.load_event_log(m.METRIC_EVENT_COLUMNS))
    s = m.application_summary(ev, dataset_end=definitions.DATASET_END)
    bundle = loaders.load_all_reports()
    return {
        "ev": ev, "summary": s, "dist": m.final_status_distribution(s),
        "duration": m.duration_statistics(s, column="duration_days").iloc[0],
        "final": m.duration_statistics(s, column="days_to_final_status", include_open=False).iloc[0],
        "offers": m.offer_metrics(ev), "oc": m.offer_cancellation_metrics(ev, s),
        "ac": m.application_cancellation_metrics(ev, s),
        "patterns": m.followup_pattern_summary(m.offer_first_cancellation_followup(ev)),
        "transitions": m.transition_metrics(ev), "all_pairs": m.transition_metrics(ev, min_count=1),
        "credit": m.credit_score_summary(m.offer_table(ev)).iloc[0],
        "trend": m.monthly_trend(s, dataset_end=definitions.DATASET_END),
        "recon": rc.reconcile_all(ev, bundle.reports, bundle.errors, dataset_end=definitions.DATASET_END),
    }


pytestmark_integration = pytest.mark.integration


@pytest.mark.integration
def test_dataset_and_status_numbers(results):
    ev, s, dist = results["ev"], results["summary"], results["dist"]
    for text in DOCS.values():
        assert fmt(len(s)) in text and fmt(len(ev)) in text
    assert f"{ev[m.A].nunique()} activities" in README
    for row in dist.itertuples():
        assert f"{fmt(row.applications)} | {row.percentage:.1f} %" in README, row.label
    assert f"median {results['duration']['median']:.1f} days" in README
    assert f"(90th percentile {results['duration']['p90']:.1f} days)" in README
    assert f"{results['final']['median']:.1f} days for the {fmt(results['final']['count'])}" in README


@pytest.mark.integration
def test_offer_and_milestone_numbers(results):
    o, s = results["offers"], results["summary"]
    for value in (o.offers_created, o.offers_sent, o.offers_returned, o.offers_accepted, o.offers_refused,
                  o.offers_cancelled, o.offers_without_outcome):
        assert fmt(value) in README, value
    assert f"{o.offer_return_rate_percent:.1f} % return rate" in README
    sent = int((s["offers_sent"] > 0).sum())
    returned = int((s["offers_returned"] > 0).sum())
    assert f"{fmt(sent)} applications had at least one offer sent ({len(s) - sent} none)" in README
    assert f"{fmt(returned)} had an offer returned" in README and f"{fmt(sent - returned)} had offers sent" in README


@pytest.mark.integration
def test_cancellation_numbers(results):
    oc, ac, patterns = results["oc"], results["ac"], results["patterns"]
    assert f"{fmt(oc.offer_cancellation_events)} events in {fmt(oc.applications_with_offer_cancellation)} applications" \
           f" ({oc.share_of_applications_percent:.1f} %)" in README
    by_status = dict(zip(oc.final_status_breakdown["final_status"].fillna("open"), oc.final_status_breakdown["applications"]))
    assert f"{fmt(by_status['A_Pending'])} of those applications still" in README
    seq = dict(zip(ac.sequence_breakdown["sequence"], zip(ac.sequence_breakdown["applications"],
                                                           ac.sequence_breakdown["percentage"])))
    first, share = seq["A_Cancelled before any O_Cancelled"]
    assert f"{fmt(first)}\n  ({share:.1f} %)" in README or f"{fmt(first)} ({share:.1f} %)" in README
    offer_first = int(patterns["applications"].sum())
    assert f"{offer_first} (5.5 %)" in README and f"{offer_first} applications" in README
    assert f"{fmt(ac.likely_automatic_expiry)} of the {fmt(ac.application_cancellation_events)}" in README
    assert f"({round(100 * ac.likely_automatic_expiry / ac.application_cancellation_events)} %)" in README
    for row in patterns.itertuples():
        assert f"{row.applications}" in README, row.pattern


@pytest.mark.integration
def test_wait_numbers_and_timer_flags(results):
    top = results["transitions"].sort_values("median_wait_hours", ascending=False)
    timers = top[top["likely_timer_driven"].fillna(False).astype(bool)]
    assert len(timers) == 2 and set(timers["next_activity"]) == {"A_Cancelled"}
    for row in timers.itertuples():
        assert f"{row.activity} → {row.next_activity} (median {row.median_wait_hours:.0f} h" in README \
            or f"{row.activity} → {row.next_activity} ({row.median_wait_hours:.0f} h" in README
    rest = top[~top["likely_timer_driven"].fillna(False).astype(bool)]
    assert f"{rest['median_wait_hours'].iloc[0]:.1f} h" in README
    lo, hi = rest["median_wait_hours"].iloc[:4].min(), rest["median_wait_hours"].iloc[0]
    assert f"{lo:.0f}-{hi:.0f} hours" in README
    assert f"all {len(results['all_pairs'])} activity pairs" in README


@pytest.mark.integration
def test_quality_and_censoring_numbers(results):
    cs, trend, checks = results["credit"], results["trend"], results["recon"].checks
    assert f"{fmt(cs['score_zero_potentially_missing'])} of {fmt(cs['offers'])} offers ({cs['zero_share_percent']:.1f} %)" in README
    assert f"median non-zero score {int(cs['median_nonzero'])}" in README
    counts = checks["status"].value_counts()
    assert f"{counts[rc.PASS]} checks PASS, 0 FAIL" in README and counts.get(rc.FAIL, 0) == 0
    assert f"{counts[rc.INFO]} documented informational findings" in README
    censored = trend[trend["censoring_warning"].astype(bool)]["start_month"].tolist()
    assert censored == ["2016-10", "2016-11", "2016-12"]
    open_by_month = results["summary"][results["summary"]["is_open"]].groupby("start_month").size()
    assert f"({', '.join(str(open_by_month[mth]) for mth in censored[:2])} and {open_by_month[censored[2]]})" in README
