"""Unit tests for ui.components, ui.theme and UI-layer safeguards (no data needed)."""

from __future__ import annotations

import io
import re
from pathlib import Path

import pandas as pd
import pytest

from core import config, definitions
from ui import components, theme

DASHBOARD = Path(__file__).resolve().parent.parent
UI_SOURCES = [*sorted((DASHBOARD / "views").glob("*.py")), *sorted((DASHBOARD / "ui").glob("*.py")), DASHBOARD / "app.py"]


def _kpi_keys_in_views() -> set[str]:
    keys = set()
    for path in (DASHBOARD / "views").glob("*.py"):
        keys |= set(re.findall(r'Kpi\(\s*"([a-z0-9_]+)"', path.read_text()))
    return keys


# --------------------------------------------------------------------------- #
# KPI definitions are centralised
# --------------------------------------------------------------------------- #
def test_every_kpi_used_in_views_is_registered():
    keys = _kpi_keys_in_views()
    assert len(keys) >= 12
    assert keys <= set(definitions.METRICS), keys - set(definitions.METRICS)


@pytest.mark.parametrize("key", sorted(definitions.METRICS))
def test_kpi_label_and_help_come_from_definitions(key):
    d = definitions.METRICS[key]
    assert components.kpi_label(key) == d.name
    text = components.kpi_help(key)
    assert d.definition in text and d.calculation in text and d.source in text
    for caveat in d.caveats:
        assert caveat in text


def test_views_do_not_hardcode_kpi_labels():
    """KPI names must not be retyped in views (they come from METRICS)."""
    for path in (DASHBOARD / "views").glob("*.py"):
        source = path.read_text()
        for d in definitions.METRICS.values():
            assert f'st.metric("{d.name}"' not in source, (path.name, d.name)


def test_ui_does_not_recalculate_metrics():
    """Pages present results; aggregation belongs to core.metrics."""
    forbidden = re.compile(r"\.(groupby|value_counts|median|mean|quantile|pivot_table|crosstab)\(")
    for path in (DASHBOARD / "views").glob("*.py"):
        assert not forbidden.search(path.read_text()), path.name


def test_ui_never_writes_files():
    forbidden = re.compile(r"\.to_csv\(\s*['\"/]|\.to_parquet\(|open\([^)]*['\"][wa]|write_text|write_bytes")
    for path in UI_SOURCES:
        assert not forbidden.search(path.read_text()), path.name


def test_ui_text_respects_terminology():
    for path in UI_SOURCES:
        text = path.read_text()
        for word in ("Approved", "Funded"):
            assert f'"{word}' not in text and f" {word} " not in text, (path.name, word)
        assert "the system account" not in text.lower(), path.name
        for match in re.finditer(r"recover\w*", text, flags=re.IGNORECASE):
            assert "not" in text[max(0, match.start() - 40) : match.start()].lower(), path.name


# --------------------------------------------------------------------------- #
# Notices
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "kind,text",
    [
        ("censoring", definitions.DATASET_END_WARNING),
        ("timer", definitions.TIMER_WARNING),
        ("not_recovery", definitions.NOT_RECOVERY_NOTE),
        ("cancellation_types", definitions.OFFER_VS_APPLICATION_CANCELLATION),
        ("last_activity", definitions.LAST_ACTIVITY_NOT_OUTCOME),
        ("a_pending", definitions.A_PENDING_NOTE),
        ("credit_score", definitions.CREDIT_SCORE_WARNING),
        ("interleaving", definitions.INTERLEAVING_NOTE),
    ],
)
def test_notices_use_definition_texts(kind, text):
    assert components.notice_text(kind) == text


def test_closure_note_does_not_call_closures_bottlenecks():
    note = components.notice_text("closure")
    assert "not operational bottlenecks" in note and "system closures" in note.lower()


# --------------------------------------------------------------------------- #
# Downloads
# --------------------------------------------------------------------------- #
def test_csv_payload_round_trip_and_is_frozen():
    df = pd.DataFrame({"Application": ["a", "b"], "Duration (days)": [1.5, 2.0]})
    df.attrs["dataset_end"] = pd.Timestamp("2017-02-01", tz="UTC")
    payload = components.csv_payload(df)
    df.loc[0, "Application"] = "CHANGED"  # later changes must not leak into the download
    back = pd.read_csv(io.BytesIO(payload()))
    assert back["Application"].tolist() == ["a", "b"]
    assert back["Duration (days)"].tolist() == [1.5, 2.0]


def test_display_frame_strips_attrs_without_touching_input():
    df = pd.DataFrame({"x": [1]})
    df.attrs["dataset_end"] = pd.Timestamp("2017-02-01", tz="UTC")
    out = components.display_frame(df)
    assert out.attrs == {} and "dataset_end" in df.attrs


# --------------------------------------------------------------------------- #
# Theme and chart
# --------------------------------------------------------------------------- #
def test_status_colours_are_fixed_and_distinct():
    for mode in ("light", "dark"):
        colors = [theme.status_color(s, mode) for s in (*config.FINAL_STATUS_ACTIVITIES, None)]
        assert len(set(colors)) == 4
    assert theme.status_color("A_Pending") == "#2a78d6"
    assert theme.status_color(None) == theme.status_color("anything else")


def _distribution():
    return pd.DataFrame(
        {
            "final_status": ["A_Pending", "A_Cancelled", "A_Denied", None],
            "label": ["A_Pending (offer accepted)", "A_Cancelled (application cancelled)",
                      "A_Denied (application denied)", "No final status recorded"],
            "applications": [17228, 10431, 3752, 98],
            "percentage": [54.68, 33.10, 11.91, 0.31],
        }
    )


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_final_status_chart(mode):
    fig = components.final_status_chart(_distribution(), mode)
    bar = fig.data[0]
    assert list(bar.y) == ["No final status recorded", "A_Denied (application denied)",
                           "A_Cancelled (application cancelled)", "A_Pending (offer accepted)"]
    assert list(bar.marker.color) == [theme.status_color(s, mode) for s in (None, "A_Denied", "A_Cancelled", "A_Pending")]
    assert bar.text[-1] == "17,228  (54.7%)"  # direct labels (relief for the low-contrast aqua)
    assert bar.marker.cornerradius == 4
    assert fig.layout.showlegend is False and fig.layout.yaxis.showgrid is False


def test_status_colour_follows_entity_not_rank():
    """Filtering out statuses must not repaint the remaining bars."""
    full = components.final_status_chart(_distribution())
    partial = components.final_status_chart(_distribution().iloc[[1, 3]])
    colour = dict(zip(full.data[0].y, full.data[0].marker.color))
    for label, c in zip(partial.data[0].y, partial.data[0].marker.color):
        assert colour[label] == c


def test_zoom_config_keeps_zoom_and_pan_only():
    cfg = theme.ZOOM_PLOTLY_CONFIG
    assert cfg["displayModeBar"] is True and cfg["displaylogo"] is False
    assert {"select2d", "lasso2d"} <= set(cfg["modeBarButtonsToRemove"])
    assert theme.PLOTLY_CONFIG["displayModeBar"] is False  # other charts unchanged
