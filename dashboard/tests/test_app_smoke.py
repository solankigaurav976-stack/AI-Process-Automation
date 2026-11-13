"""Smoke tests of the real Streamlit app (streamlit.testing AppTest) on the real data.

Covers: all five pages render, KPI values equal the core metrics, filters work
(including an empty selection and reset), downloads are offered, the Data Quality
page separates reconciliation results from informational findings, rendered text
respects the approved terminology, cached data cannot be mutated by callers,
missing data produces an error panel instead of a crash, and the protected files
are byte-for-byte unchanged after a full app session.
"""

from __future__ import annotations

import re
import shutil

import pytest
from streamlit.testing.v1 import AppTest

from core import config, definitions
from core import metrics as m
from tests.test_read_only import _fingerprint

pytestmark = [pytest.mark.integration, pytest.mark.ui]

APP = str(config.DASHBOARD_DIR / "app.py")
TIMEOUT = 600
PAGES = {
    "views/executive.py": "Executive Overview",
    "views/process.py": "Process Analysis",
    "views/cancellation.py": "Cancellation Intelligence",
    "views/explorer.py": "Application Explorer",
    "views/data_quality.py": "Data Quality & Reconciliation",
}


@pytest.fixture(scope="module")
def fingerprint_before():
    return _fingerprint(config.PROTECTED_DIRS)


@pytest.fixture(scope="module")
def app(fingerprint_before):
    from ui import data

    data.clear_caches()
    at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


@pytest.fixture(scope="module")
def expected():
    from core import loaders

    ev = m.prepare_events(loaders.load_event_log(m.METRIC_EVENT_COLUMNS))
    s = m.application_summary(ev, dataset_end=definitions.DATASET_END)
    return {
        "summary": s,
        "offer": m.offer_cancellation_metrics(ev, s),
        "application": m.application_cancellation_metrics(ev, s),
        "durations": m.duration_statistics(s).iloc[0],
    }


def metrics_of(at) -> dict[str, str]:
    return {x.label: x.value for x in at.metric}


def all_text(at) -> str:
    parts = []
    for group in (at.title, at.subheader, at.markdown, at.caption, at.info, at.warning, at.error, at.success):
        parts += [x.value for x in group]
    parts += [f"{x.label} {x.value} {getattr(x, 'help', '') or ''}" for x in at.metric]
    return "\n".join(parts)


def goto(at, page):
    at.switch_page(page).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("page,title", PAGES.items())
def test_every_page_renders(app, page, title):
    goto(app, page)
    assert [t.value for t in app.title] == [title]
    assert "BPI Challenge 2017" in " ".join(c.value for c in app.sidebar.caption)


def test_sidebar_shows_dataset_period_and_utc(app):
    goto(app, "views/executive.py")
    text = " ".join(c.value for c in app.sidebar.caption)
    assert "1 Jan 2016 – 1 Feb 2017" in text and "UTC" in text and "1,202,267 events" in text


def test_executive_kpis_equal_core_metrics(app, expected):
    goto(app, "views/executive.py")
    shown = metrics_of(app)
    s = expected["summary"]
    assert shown["Total applications"] == f"{len(s):,}"
    assert shown["A_Pending (offer accepted)"] == f"{(s['final_status'] == 'A_Pending').sum():,}"
    assert shown["A_Cancelled (application cancelled)"] == "10,431"
    assert shown["A_Denied (application denied)"] == "3,752"
    assert shown["No final status recorded"] == "98"
    assert shown["Median processing duration"] == f"{expected['durations']['median']:.1f} days"
    assert shown["90th percentile processing duration"] == f"{expected['durations']['p90']:.1f} days"
    assert "Median time to final status" in shown
    assert any("Data ends on 1 February 2017" in w.value for w in app.warning)  # censoring warning
    assert any("Dec 2016" in w.value for w in app.warning)
    assert len(app.get("plotly_chart")) == 3  # status, monthly volume, monthly duration (Phase 5b)


def test_cancellation_page_keeps_offer_and_application_separate(app, expected):
    goto(app, "views/cancellation.py")
    shown = metrics_of(app)
    assert shown["Offer cancellations (O_Cancelled events)"] == f"{expected['offer'].offer_cancellation_events:,}"
    assert shown["Applications with an offer cancellation"] == "15,682"
    assert shown["Application cancellations (A_Cancelled events)"] == "10,431"
    assert shown["Likely automatic expiry cancellations"] == f"{expected['application'].likely_automatic_expiry:,}"
    assert shown["Offer-first cancellation population"] == "576"
    subheaders = [h.value for h in app.subheader]
    assert any("O_Cancelled" in h for h in subheaders) and any("A_Cancelled" in h for h in subheaders)
    text = all_text(app)
    assert "Exploratory indicator" in text and "Not customer recovery" in text
    assert "likely, but not confirmed, a system account" in text


def test_process_page_shows_exploratory_timer_notice(app):
    goto(app, "views/process.py")
    text = all_text(app)
    assert "Exploratory: likely timer-driven steps" in text and "appear to be driven" in text
    assert "How activities are counted" in text


def test_data_quality_separates_results_from_findings(app):
    goto(app, "views/data_quality.py")
    shown = metrics_of(app)
    assert shown["Checks failed"] == "0" and shown["Reports not loaded"] == "0"
    assert int(shown["Checks passed"]) >= 80 and int(shown["Informational findings"]) == 7
    assert any("reconciliation checks passed" in s.value for s in app.success)
    assert [t.label for t in app.tabs] == [
        "Reconciliation results", "Informational findings", "Final status vs last activity", "Known limitations"
    ]
    results_table = app.dataframe[0].value
    assert "INFO" not in set(results_table["Result"])  # findings never appear as results
    assert any("These are findings, not failures" in i.value for i in app.info)
    assert any("Last recorded activity is not a final status" in w.value for w in app.warning)
    text = all_text(app)
    assert "16 of 102" in text  # 'Workflow activity only' composition, as a finding
    assert "not operational bottlenecks" in text  # system closures


def test_explorer_lists_applications_and_searches(app):
    goto(app, "views/explorer.py")
    assert app.dataframe[0].value.shape[0] == 31_509
    app.text_input(key="explorer_query").set_value("Application_1000086665").run()
    assert not app.exception
    assert app.dataframe[0].value["Application"].tolist() == ["Application_1000086665"]
    app.text_input(key="explorer_query").set_value("no-such-id").run()
    assert any("No application ID contains" in i.value for i in app.info)
    app.text_input(key="explorer_query").set_value("").run()


def test_downloads_are_offered_on_data_pages(app):
    for page, minimum in (("views/executive.py", 1), ("views/cancellation.py", 1),
                          ("views/explorer.py", 1), ("views/data_quality.py", 2)):
        goto(app, page)
        assert len(app.get("download_button")) >= minimum, page


# --------------------------------------------------------------------------- #
# Filters
# --------------------------------------------------------------------------- #
def test_final_status_filter(app):
    goto(app, "views/executive.py")
    app.multiselect(key="flt_final_statuses").set_value([config.FINAL_STATUS_LABELS["A_Cancelled"]]).run()
    assert not app.exception
    shown = metrics_of(app)
    assert shown["Total applications"] == "10,431"
    assert shown["A_Pending (offer accepted)"] == "0"
    assert "Final status: A_Cancelled (application cancelled)" in " ".join(c.value for c in app.caption)
    goto(app, "views/cancellation.py")  # filter persists across pages
    assert metrics_of(app)["Application cancellations (A_Cancelled events)"] == "10,431"
    assert metrics_of(app)["Applications with an offer cancellation"] == "10,431"


def test_month_filter_and_censoring_warning(app):
    app.multiselect(key="flt_final_statuses").set_value([]).run()
    goto(app, "views/executive.py")
    app.select_slider(key="flt_months").set_range("2016-01", "2016-03").run()
    assert not app.exception
    shown = metrics_of(app)
    assert shown["No final status recorded"] == "0"
    assert not any("Data ends on 1 February 2017" in w.value for w in app.warning)  # early months: no censoring


def test_empty_selection_shows_empty_state(app):
    app.select_slider(key="flt_months").set_range("2016-01", "2016-01").run()
    app.multiselect(key="flt_final_statuses").set_value([config.NO_FINAL_STATUS_LABEL]).run()
    for page in ("views/executive.py", "views/cancellation.py", "views/explorer.py"):
        goto(app, page)
        assert any("No applications match" in i.value for i in app.info), page


def test_reset_filters(app):
    [reset] = [b for b in app.button if b.label == "Reset filters"]
    reset.click().run()
    goto(app, "views/executive.py")
    assert metrics_of(app)["Total applications"] == "31,509"


# --------------------------------------------------------------------------- #
# Text safeguards across every rendered page
# --------------------------------------------------------------------------- #
def test_rendered_text_respects_terminology(app):
    texts = []
    for page in PAGES:
        goto(app, page)
        texts.append(all_text(app))
    text = "\n".join(texts)
    for word in ("Approved", "Funded"):
        for match in re.finditer(word, text):
            assert "not labelled" in text[max(0, match.start() - 60) : match.start()], word
    for match in re.finditer(r"recover\w*", text, flags=re.IGNORECASE):
        assert "not" in text[max(0, match.start() - 40) : match.start()].lower()
    assert "the system account" not in text.lower()
    for match in re.finditer(r"system account", text):
        assert "likely" in text[max(0, match.start() - 40) : match.start()].lower()


# --------------------------------------------------------------------------- #
# Caching safety
# --------------------------------------------------------------------------- #
def test_cached_results_cannot_be_mutated_by_callers(app):
    from ui import data
    from ui.filters import FilterState

    first = data.filtered_summary(FilterState())
    first.loc[first.index[0], "final_status_label"] = "TAMPERED"
    first["total_events"] = 0
    again = data.filtered_summary(FilterState())
    assert len(again) == 31_509 and "TAMPERED" not in set(again["final_status_label"])
    assert int(again["total_events"].sum()) == 1_202_267
    base = data.summary()
    base["duration_days"] = -1.0
    assert (data.summary()["duration_days"] >= 0).all()


# --------------------------------------------------------------------------- #
# Missing data (runs last: it replaces the caches)
# --------------------------------------------------------------------------- #
def test_missing_report_is_reported_on_data_quality_page(tmp_path, monkeypatch):
    from ui import data

    reports = tmp_path / "reports"
    reports.mkdir()
    for name, file in config.REPORT_FILES.items():
        if name != "process_bottlenecks":
            shutil.copyfile(config.REPORTS_DIR / file, reports / file)  # read-only copy of originals
    monkeypatch.setattr(config, "REPORTS_DIR", reports)
    data.clear_caches()
    try:
        at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
        goto(at, "views/data_quality.py")
        shown = metrics_of(at)
        assert shown["Reports not loaded"] == "1"
        assert any("process_bottlenecks.csv" in e.value and "not found" in e.value for e in at.error)
        assert any("did not pass" in e.value for e in at.error)
    finally:
        monkeypatch.undo()
        data.clear_caches()


def test_missing_event_log_shows_error_panel(tmp_path, monkeypatch):
    from ui import data

    monkeypatch.setattr(config, "EVENT_LOG_PATH", tmp_path / "missing.parquet")
    data.clear_caches()
    try:
        at = AppTest.from_file(APP, default_timeout=TIMEOUT).run()
        assert not at.exception, [e.value for e in at.exception]
        errors = " ".join(e.value for e in at.error)
        assert "could not load its data" in errors and "not found" in errors
        assert not at.metric  # no page content rendered with missing data
    finally:
        monkeypatch.undo()
        data.clear_caches()


def test_protected_files_unchanged_after_app_session(fingerprint_before):
    after = _fingerprint(config.PROTECTED_DIRS)
    assert after.keys() == fingerprint_before.keys()
    assert [k for k in after if after[k] != fingerprint_before[k]] == []
