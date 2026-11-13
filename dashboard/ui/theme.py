"""Colour tokens and the shared Plotly chart style.

Final statuses are categories (identity), so they use the first three slots of
the validated categorical palette, in fixed order, plus a neutral grey for "no
final status". The same status always gets the same colour on every page and
under every filter. Light and dark steps were validated separately
(adjacent-pair CVD dE >= 9.2, normal-vision dE >= 26.5). Light aqua is below
3:1 contrast on the light surface, so status charts always carry direct value
labels and a table view.
"""

from __future__ import annotations

from dataclasses import dataclass

from core import config


@dataclass(frozen=True)
class Tokens:
    surface: str
    text_primary: str
    text_secondary: str
    text_muted: str
    gridline: str
    baseline: str
    status_colors: dict[str, str]


_LIGHT = Tokens(
    surface="#fcfcfb",
    text_primary="#0b0b0b",
    text_secondary="#52514e",
    text_muted="#898781",
    gridline="#e1e0d9",
    baseline="#c3c2b7",
    status_colors={
        config.APPLICATION_PENDING: "#2a78d6",  # slot 1 blue
        config.APPLICATION_CANCELLED: "#eb6834",  # slot 2 orange
        config.APPLICATION_DENIED: "#1baf7a",  # slot 3 aqua
        config.NO_FINAL_STATUS_LABEL: "#898781",  # neutral grey
    },
)

_DARK = Tokens(
    surface="#1a1a19",
    text_primary="#f0efec",
    text_secondary="#c3c2b7",
    text_muted="#898781",
    gridline="#2c2c2a",
    baseline="#383835",
    status_colors={
        config.APPLICATION_PENDING: "#3987e5",
        config.APPLICATION_CANCELLED: "#d95926",
        config.APPLICATION_DENIED: "#199e70",
        config.NO_FINAL_STATUS_LABEL: "#898781",
    },
)


def tokens(mode: str | None = "light") -> Tokens:
    return _DARK if mode == "dark" else _LIGHT


def status_color(status: str | None, mode: str | None = "light") -> str:
    """Colour for a final-status code (None = no final status)."""
    key = status if status in config.FINAL_STATUS_ACTIVITIES else config.NO_FINAL_STATUS_LABEL
    return tokens(mode).status_colors[key]


def base_layout(mode: str | None = "light", *, height: int = 320) -> dict:
    """Plotly layout shared by every chart: recessive grid and axes, no chart junk."""
    t = tokens(mode)
    return dict(
        height=height,
        margin=dict(l=8, r=24, t=8, b=8),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        font=dict(family='system-ui, -apple-system, "Segoe UI", sans-serif', size=13, color=t.text_secondary),
        hoverlabel=dict(bgcolor=t.surface, bordercolor=t.baseline, font=dict(color=t.text_primary, size=13)),
        xaxis=dict(gridcolor=t.gridline, zeroline=False, linecolor=t.baseline, tickfont=dict(color=t.text_muted)),
        yaxis=dict(gridcolor=t.gridline, zeroline=False, linecolor=t.baseline, tickfont=dict(color=t.text_secondary)),
        showlegend=False,
    )


# --------------------------------------------------------------------------- #
# Phase 5 additions
# --------------------------------------------------------------------------- #
#: Event-stream lanes (timeline). Validated as a set under --pairs all in both
#: modes (light worst CVD dE 17.6, dark 13.0) and distinct from the status hues.
#: Light magenta is < 3:1 on the light surface: lanes are always direct-labelled
#: on the axis and carry distinct marker shapes as secondary encoding.
STREAMS: tuple[str, ...] = ("Application", "Offer", "Workflow")
_STREAM_COLORS = {
    "light": {"Application": "#6250d6", "Offer": "#008300", "Workflow": "#e87ba4"},
    "dark": {"Application": "#9085e9", "Offer": "#008300", "Workflow": "#d55181"},
}
STREAM_SYMBOLS = {"Application": "circle", "Offer": "diamond", "Workflow": "square"}

#: Sequential single-hue ramp (blue) for magnitude. Light runs light->dark; dark
#: mode flips the anchor so small values recede into the dark surface.
_SEQUENTIAL = {
    "light": ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"],
    "dark": ["#0d366b", "#184f95", "#256abf", "#3987e5", "#6da7ec", "#9ec5f4", "#cde2fb"],
}

#: Neutral tones for non-status nodes/marks and for "other" categories.
NEUTRAL = {"light": "#898781", "dark": "#898781"}
NEUTRAL_SOFT = {"light": "#c3c2b7", "dark": "#52514e"}
#: Single hue for one-series bar charts (no identity encoded by colour).
SINGLE_SERIES = {"light": "#2a78d6", "dark": "#3987e5"}
#: Opacity for de-emphasised marks (not selected / censored months).
DIMMED_OPACITY = 0.35
CENSORED_OPACITY = 0.45


def _mode(mode: str | None) -> str:
    return "dark" if mode == "dark" else "light"


def stream_color(stream: str, mode: str | None = "light") -> str:
    return _STREAM_COLORS[_mode(mode)].get(stream, NEUTRAL[_mode(mode)])


def sequential_scale(mode: str | None = "light") -> list[list]:
    steps = _SEQUENTIAL[_mode(mode)]
    return [[i / (len(steps) - 1), c] for i, c in enumerate(steps)]


def neutral(mode: str | None = "light", *, soft: bool = False) -> str:
    return (NEUTRAL_SOFT if soft else NEUTRAL)[_mode(mode)]


def single_series(mode: str | None = "light") -> str:
    return SINGLE_SERIES[_mode(mode)]


def rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgba({r},{g},{b},{alpha})"


#: Plotly config: no mode bar clutter, no logo.
PLOTLY_CONFIG = {"displayModeBar": False, "displaylogo": False, "responsive": True}
#: Charts that need zoom and pan (the application timeline) show a reduced toolbar.
ZOOM_PLOTLY_CONFIG = {
    "displayModeBar": True, "displaylogo": False, "responsive": True, "scrollZoom": False,
    "modeBarButtonsToRemove": ["select2d", "lasso2d", "autoScale2d", "toImage"],
}

#: Page-level CSS kept minimal; Streamlit's theme does the rest.
PAGE_CSS = """
<style>
  [data-testid="stMetricValue"] { font-variant-numeric: normal; }
  [data-testid="stCaptionContainer"] { line-height: 1.4; }
  .block-container { padding-top: 2rem; max-width: 1400px; }
</style>
"""
