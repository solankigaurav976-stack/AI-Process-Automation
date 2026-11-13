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


EXPECTED_ATTRIBUTION = ("**Dataset:** BPI Challenge 2017", "**Creator:** Boudewijn van Dongen",
                        "**Year:** 2017", "**Publisher:** 4TU.ResearchData")
# Official URLs, written out literally so the test checks the real addresses, not the constants.
DOI_URL = "https://doi.org/10.4121/uuid:5f3067df-f10b-45da-b98b-86ae4c7a310b"
TERMS_URL = "https://doi.org/10.4121/resource:terms_of_use"
EXPECTED_DOI_LINK = f"**DOI:** [10.4121/uuid:5f3067df-f10b-45da-b98b-86ae4c7a310b]({DOI_URL})"
EXPECTED_TERMS_LINK = f"**Terms:** [4TU General Terms of Use]({TERMS_URL})"


def assert_dataset_attribution(at):
    """The sidebar acknowledges the dataset source (4TU terms, Art. 1 and 4a).

    Four fields, then a clickable DOI link and a clickable link to the official terms.
    """
    boxes = [m.value for m in at.sidebar.markdown if m.value.startswith("**Data source**")]
    assert len(boxes) == 1, boxes
    lines = boxes[0].split("  \n")
    assert tuple(lines[1:5]) == EXPECTED_ATTRIBUTION, lines
    assert tuple(f"**{k}:** {v}" for k, v in definitions.DATASET_ATTRIBUTION) == EXPECTED_ATTRIBUTION
    assert lines[5] == EXPECTED_DOI_LINK, lines  # DOI is a Markdown link to the DOI resolver
    assert lines[6] == EXPECTED_TERMS_LINK, lines  # terms link to the official 4TU record
    assert len(lines) == 7, lines


ZIP_LABEL = "CSV + Attribution (ZIP)"


def assert_download_notices(at, page):
    """Every CSV download button is followed by its ZIP (CSV + attribution) button and then the
    dataset notice with the terms link; there is exactly one ZIP button and one notice per CSV button."""
    notices = [c.value for c in at.caption if c.value.startswith("Derived from BPI Challenge 2017")]
    buttons = at.get("download_button")
    csv_buttons = [b for b in buttons if b.proto.label != ZIP_LABEL]
    zip_buttons = [b for b in buttons if b.proto.label == ZIP_LABEL]
    assert len(notices) == len(csv_buttons) == len(zip_buttons), page

    def leaves(node):  # rendered elements in display order
        for child in getattr(node, "children", {}).values():
            if getattr(child, "children", None):
                yield from leaves(child)
            else:
                yield child

    ordered = list(leaves(at.main))
    for i, element in enumerate(ordered):
        if element.type == "download_button" and element.proto.label != ZIP_LABEL:
            zip_button, notice = (ordered[i + 1: i + 3] + [None, None])[:2]
            assert zip_button is not None and zip_button.type == "download_button", (page, element.proto.label)
            assert zip_button.proto.label == ZIP_LABEL, (page, zip_button.proto.label)
            assert zip_button.proto.disabled == element.proto.disabled, page  # both offered (or not) together
            assert notice is not None and str(getattr(notice, "value", "")).startswith(
                "Derived from BPI Challenge 2017"), (page, getattr(notice, "type", None))
        elif element.type == "download_button":  # a ZIP button always directly follows its CSV button
            previous = ordered[i - 1]
            assert previous.type == "download_button" and previous.proto.label != ZIP_LABEL, page
    for notice in notices:
        assert "Boudewijn van Dongen, 2017, 4TU.ResearchData" in notice
        assert "Keep this acknowledgement with any downloaded or shared data" in notice
        assert f"[4TU General Terms of Use]({TERMS_URL})" in notice
        assert "non-commercial use only" in notice


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("page,title", PAGES.items())
def test_every_page_renders(app, page, title):
    goto(app, page)
    assert [t.value for t in app.title] == [title]
    assert "BPI Challenge 2017" in " ".join(c.value for c in app.sidebar.caption)
    assert_dataset_attribution(app)


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


class _RecordingStreamlit:
    """Stands in for ``streamlit`` inside ui.components: records the data handed to each
    ``st.download_button`` (built twice, to check determinism) and forwards everything."""

    def __init__(self, real, served):
        self._real, self._served = real, served

    def __getattr__(self, name):
        return getattr(self._real, name)

    def download_button(self, label, *, data, file_name, mime, key, **kwargs):
        build = data if callable(data) else (lambda: data)
        self._served[key] = (label, file_name, mime, build(), build())
        return self._real.download_button(label, data=data, file_name=file_name, mime=mime, key=key, **kwargs)


#: The ten CSV downloads (widget keys) and their ZIP counterparts.
CSV_DOWNLOAD_KEYS = {"dl_exec_status", "dl_exec_monthly", "dl_proc_milestones", "dl_proc_occurrences",
                     "dl_proc_freq", "dl_cancel_cases", "dl_explorer", "dl_explorer_events",
                     "dl_dq_checks", "dl_dq_info"}
# Written out literally so the checks test the real values rather than the constants.
DOI = "10.4121/uuid:5f3067df-f10b-45da-b98b-86ae4c7a310b"
CITATION = f"van Dongen, Boudewijn (2017): BPI Challenge 2017. 4TU.ResearchData. Dataset. https://doi.org/{DOI}"


def assert_zip_download(served, csv_key):
    """The ZIP beside a CSV button holds exactly that CSV (byte for byte) and ATTRIBUTION.txt."""
    import io
    import zipfile

    csv_label, csv_name, csv_mime, csv_bytes, csv_rebuilt = served[csv_key]
    zip_label, zip_name, zip_mime, zip_bytes, zip_rebuilt = served["zip_" + csv_key.removeprefix("dl_")]
    assert csv_mime == "text/csv" and csv_name.endswith(".csv") and csv_label != ZIP_LABEL  # plain CSV unchanged
    assert csv_bytes == csv_rebuilt and csv_bytes, csv_key
    assert zip_label == ZIP_LABEL and zip_mime == "application/zip", csv_key
    assert zip_name == csv_name.removesuffix(".csv") + "_with_attribution.zip"
    assert zip_bytes == zip_rebuilt, f"{csv_key}: ZIP generation must be deterministic"
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        assert archive.testzip() is None
        assert archive.namelist() == [csv_name, "ATTRIBUTION.txt"], csv_key  # exactly two files
        assert archive.read(csv_name) == csv_bytes, csv_key  # byte-for-byte the plain CSV download
        for info in archive.infolist():  # fixed metadata
            assert info.date_time == (1980, 1, 1, 0, 0, 0) and info.external_attr >> 16 == 0o644, info
        text = archive.read("ATTRIBUTION.txt").decode("utf-8")
    flat = " ".join(text.split())
    assert f"{csv_name} is derived from the BPI Challenge 2017 dataset." in flat
    assert CITATION in flat
    for field in (r"Dataset:\s+BPI Challenge 2017", r"Creator:\s+Boudewijn van Dongen", r"Year:\s+2017",
                  r"Publisher:\s+4TU\.ResearchData", rf"DOI:\s+{re.escape(DOI)}"):
        assert re.search(field, text), field
    assert f"https://doi.org/{DOI}" in text and TERMS_URL in text and "4TU General Terms of Use" in text
    assert "Non-commercial use only." in flat
    assert ("Keep this acknowledgement (this file, or the citation above) with the data whenever you share "
            "or redistribute it, and require anyone you share it with to keep it as well.") in flat


def test_downloads_are_offered_on_data_pages(app, monkeypatch):
    from ui import components, selection

    served: dict = {}
    monkeypatch.setattr(components, "st", _RecordingStreamlit(components.st, served))
    # Selections that make the two selection-dependent downloads appear (transition occurrences, events).
    transition, application = selection.SELECTION_KEYS["transition"], selection.SELECTION_KEYS["application"]
    app.session_state[transition] = ("A_Create Application", "A_Submitted")
    app.session_state[application] = "Application_1000334415"
    try:
        for page, minimum in (("views/executive.py", 1), ("views/process.py", 1), ("views/cancellation.py", 1),
                              ("views/explorer.py", 1), ("views/data_quality.py", 2)):
            goto(app, page)
            assert len(app.get("download_button")) >= minimum, page
            assert_download_notices(app, page)
    finally:
        app.session_state[transition] = None  # the app's own "no selection" value (selection.clear_selection)
        app.session_state[application] = None

    # All ten downloads were offered, each with its ZIP counterpart, and nothing else.
    assert set(served) == CSV_DOWNLOAD_KEYS | {"zip_" + k.removeprefix("dl_") for k in CSV_DOWNLOAD_KEYS}
    for key in sorted(CSV_DOWNLOAD_KEYS):
        assert_zip_download(served, key)


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
        assert_dataset_attribution(at)  # the source is still acknowledged
    finally:
        monkeypatch.undo()
        data.clear_caches()


def test_protected_files_unchanged_after_app_session(fingerprint_before):
    after = _fingerprint(config.PROTECTED_DIRS)
    assert after.keys() == fingerprint_before.keys()
    assert [k for k in after if after[k] != fingerprint_before[k]] == []
