"""Plotly figure builders (pure: data in, ``go.Figure`` out; no Streamlit, no I/O).

Conventions
-----------
* Fixed category orders and colours (status colour follows the status, never its rank).
* One y-axis per chart; thin marks; recessive grid; direct labels where useful.
* Every clickable mark carries ``customdata`` identifying what was clicked; the
  first element is the value used by ``ui.selection``.
* Selection highlighting is drawn by the builder (``selected`` arguments) and
  Plotly's own selected/unselected dimming is neutralised, so the highlight always
  reflects the applied filter rather than transient client-side state.
* Censored months and likely timer-driven transitions are marked visually and in
  hover text, never with a warning colour.

``INTERACTIONS`` documents, per chart, whether clicking is supported or hover only.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from core import config, definitions
from core import metrics as m
from ui import theme
from ui.aggregations import SankeyData, stream_of
from ui.filters import STATUS_LABEL_ORDER
from ui.formatting import fmt_date, fmt_days, fmt_hours, fmt_int, fmt_month, fmt_pct

CLICK, HOVER = "click", "hover"


@dataclass(frozen=True)
class Interaction:
    kind: str  # CLICK or HOVER
    note: str


INTERACTIONS: dict[str, Interaction] = {
    "status_bars": Interaction(CLICK, "Click a bar to filter every page by that final status; click it again to clear."),
    "monthly_status": Interaction(CLICK, "Click a month to filter every page to that start month; click it again to clear. "
                                         "Clicking a legend entry only hides that series in this chart."),
    "monthly_duration": Interaction(HOVER, "Hover for values. Lines are not clickable."),
    "milestone_sankey": Interaction(HOVER, "Hover for counts. Sankey diagrams cannot be clicked in Streamlit; "
                                           "use the table view for exact figures."),
    "transition_matrix": Interaction(CLICK, "Click a cell to open the waiting-time drill-down for that pair."),
    "ranked_waits": Interaction(CLICK, "Click a bar to open the waiting-time drill-down for that pair."),
    "wait_histogram": Interaction(HOVER, "Hover for counts per waiting-time band."),
    "activity_frequency": Interaction(CLICK, "Click an activity to highlight its row and column in the transition matrix."),
    "offer_cancellation_status": Interaction(CLICK, "Click a bar to list those applications below."),
    "cancellation_sequence": Interaction(CLICK, "Click a bar to list those applications below."),
    "expiry_histogram": Interaction(HOVER, "Hover for counts. Exploratory indicator; not clickable."),
    "pattern_bars": Interaction(CLICK, "Click a pattern to update the charts and table below; click it again to clear."),
    "gap_strip": Interaction(CLICK, "Click a point to select that application for the Application Explorer."),
    "pattern_composition": Interaction(HOVER, "Hover for counts."),
    "application_timeline": Interaction(CLICK, "Hover for event details; click an event to highlight it in the event "
                                               "table. Drag to zoom in on a period, use the toolbar to pan, "
                                               "double-click to reset."),
}

_NO_DIM = dict(selected=dict(marker=dict(opacity=1)), unselected=dict(marker=dict(opacity=1)))


def _layout(fig: go.Figure, mode: str, *, height: int, **overrides) -> go.Figure:
    layout = theme.base_layout(mode, height=height)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(layout.get(key), dict):
            layout[key] = {**layout[key], **value}
        else:
            layout[key] = value
    fig.update_layout(**layout)
    return fig


def _legend(mode: str) -> dict:
    t = theme.tokens(mode)
    return dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0, font=dict(color=t.text_secondary))


def _opacities(values: Sequence, selected: Sequence) -> list[float]:
    chosen = set(selected or ())
    if not chosen:
        return [1.0] * len(values)
    return [1.0 if v in chosen else theme.DIMMED_OPACITY for v in values]


# =========================================================================== #
# Executive
# =========================================================================== #
def status_bars(
    distribution: pd.DataFrame, mode: str = "light", *, selected: Sequence[str] = (), action: str = "filter every page"
) -> go.Figure:
    """Final status bars (fixed order and colours, direct labels). customdata[0] = status label."""
    dist = distribution.iloc[::-1].reset_index(drop=True)  # first status on top
    labels = dist["label"].tolist()
    codes = [s if isinstance(s, str) else None for s in dist["final_status"]]
    t = theme.tokens(mode)
    fig = go.Figure(
        go.Bar(
            x=dist["applications"],
            y=labels,
            orientation="h",
            marker=dict(color=[theme.status_color(c, mode) for c in codes], cornerradius=4,
                        opacity=_opacities(labels, selected), line=dict(width=0)),
            text=[f"{fmt_int(n)}  ({fmt_pct(p)})" for n, p in zip(dist["applications"], dist["percentage"])],
            textposition="outside",
            textfont=dict(color=t.text_primary),
            cliponaxis=False,
            customdata=np.column_stack([labels, [c or "" for c in codes]]),
            hovertemplate=f"<b>%{{y}}</b><br>%{{x:,}} applications<br><i>Click to {action}</i><extra></extra>",
            **_NO_DIM,
        )
    )
    return _layout(fig, mode, height=240, margin=dict(r=130), bargap=0.35,
                   xaxis=dict(showgrid=True, rangemode="tozero"), yaxis=dict(showgrid=False, ticksuffix="  "),
                   clickmode="event+select", dragmode=False)


def monthly_status_bars(
    trend: pd.DataFrame,
    mode: str = "light",
    *,
    normalised: bool = False,
    selected_month: str | None = None,
    selected_range: tuple[str, str] | None = None,
) -> go.Figure:
    """Applications by start month stacked by final status. customdata[0] = 'YYYY-MM'.

    Censored months (``censoring_warning``) are faded and covered by a labelled band.
    ``selected_month`` or ``selected_range`` (inclusive 'YYYY-MM' bounds) keep the
    selected months at full strength and dim the others.
    """
    months = trend["start_month"].tolist()
    x = list(range(len(months)))
    censored = trend["censoring_warning"].astype(bool).to_numpy()
    base = np.where(censored, theme.CENSORED_OPACITY, 1.0)
    if selected_month is not None:
        selected_range = (selected_month, selected_month)
    if selected_range is not None:
        lo, hi = sorted(selected_range)
        inside = np.array([lo <= mm <= hi for mm in months], dtype=bool)
        base = np.where(inside, base, base * theme.DIMMED_OPACITY)
    series = [(s, config.FINAL_STATUS_LABELS[s], s) for s in config.FINAL_STATUS_ACTIVITIES]
    series.append(("no_final_status", config.NO_FINAL_STATUS_LABEL, None))
    extra = np.column_stack([
        months,
        [fmt_month(mm) for mm in months],
        trend["applications"].astype(int),
        trend["no_final_status"].astype(int),
        [f"{d:.0f}" for d in trend["days_observed_after_month_end"]],
        np.where(censored, "Incomplete: data ends 1 Feb 2017", "Complete observation window"),
    ])
    fig = go.Figure()
    for column, label, code in series:
        fig.add_bar(
            x=x, y=trend[column], name=label,
            marker=dict(color=theme.status_color(code, mode), opacity=base.tolist(), line=dict(width=0)),
            customdata=extra,
            hovertemplate=(
                "<b>%{customdata[1]}</b> · " + label + "<br>%{y:,} applications"
                + ("" if not normalised else " (share of month)")
                + "<br>Month total: %{customdata[2]:,} · open: %{customdata[3]:,}"
                + "<br>%{customdata[5]} (%{customdata[4]} days observed after month end)"
                + "<br><i>Click to filter every page to this month</i><extra></extra>"
            ),
            **_NO_DIM,
        )
    if censored.any():
        first, last = int(np.argmax(censored)), int(len(censored) - 1 - np.argmax(censored[::-1]))
        fig.add_vrect(x0=first - 0.5, x1=last + 0.5, fillcolor=theme.rgba(theme.neutral(mode), 0.10), line_width=0,
                      layer="below")
        fig.add_annotation(x=(first + last) / 2, y=1.0, yref="paper", yanchor="bottom", showarrow=False,
                           text="Incomplete months · data ends 1 Feb 2017",
                           font=dict(size=11, color=theme.tokens(mode).text_secondary))
    return _layout(
        fig, mode, height=360, barmode="stack", barnorm="percent" if normalised else None, bargap=0.25,
        showlegend=True, legend=_legend(mode), margin=dict(t=48),
        xaxis=dict(tickmode="array", tickvals=x, ticktext=[fmt_month(mm) for mm in months], showgrid=False),
        yaxis=dict(title="Share of applications (%)" if normalised else "Applications", showgrid=True,
                   ticksuffix="%" if normalised else ""),
        clickmode="event+select", dragmode=False,
    )


def monthly_duration_lines(trend: pd.DataFrame, mode: str = "light") -> go.Figure:
    """Median and 90th percentile duration by start month; censored months dashed with hollow markers."""
    months = trend["start_month"].tolist()
    x = np.arange(len(months))
    censored = trend["censoring_warning"].astype(bool).to_numpy()
    t = theme.tokens(mode)
    fig = go.Figure()
    for column, name, color in (
        ("median_duration_days", definitions.METRICS["median_duration_days"].name, theme.single_series(mode)),
        ("p90_duration_days", definitions.METRICS["p90_duration_days"].name, theme.neutral(mode)),
    ):
        y = trend[column].to_numpy(dtype=float)
        complete = ~censored
        # Solid segment for complete months; dashed segment from the last complete month onward.
        last_complete = int(np.flatnonzero(complete)[-1]) if complete.any() else None
        solid_y = np.where(complete, y, np.nan)
        hover = [f"{fmt_month(mm)}: {fmt_days(v)}" + (" · incomplete month" if c else "")
                 for mm, v, c in zip(months, y, censored)]
        fig.add_scatter(x=x, y=solid_y, mode="lines+markers", name=name, line=dict(color=color, width=2),
                        marker=dict(size=8, color=color), text=hover, hovertemplate="%{text}<extra>" + name + "</extra>")
        if censored.any():
            dashed = np.full(len(y), np.nan)
            idx = np.flatnonzero(censored)
            if last_complete is not None:
                idx = np.concatenate([[last_complete], idx])
            dashed[idx] = y[idx]
            fig.add_scatter(x=x, y=dashed, mode="lines+markers", name=f"{name} (incomplete months)", showlegend=False,
                            line=dict(color=color, width=2, dash="dash"),
                            marker=dict(size=8, color=t.surface, line=dict(color=color, width=2)),
                            text=hover, hovertemplate="%{text}<extra>" + name + "</extra>")
        fig.add_annotation(x=x[-1], y=y[-1], text=name.replace(" processing duration", ""), showarrow=False,
                           xanchor="left", xshift=8, font=dict(size=11, color=t.text_secondary))
    return _layout(
        fig, mode, height=320, showlegend=True, legend=_legend(mode), margin=dict(t=40, r=110),
        xaxis=dict(tickmode="array", tickvals=x.tolist(), ticktext=[fmt_month(mm) for mm in months], showgrid=False),
        yaxis=dict(title="Days", rangemode="tozero", showgrid=True), hovermode="closest",
    )


# =========================================================================== #
# Process
# =========================================================================== #
def milestone_sankey(data: SankeyData, mode: str = "light") -> go.Figure:
    """Hover-only Sankey; each application follows exactly one path."""
    t = theme.tokens(mode)
    status_by_label = {config.FINAL_STATUS_LABELS[s]: s for s in config.FINAL_STATUS_ACTIVITIES}
    status_by_label[config.NO_FINAL_STATUS_LABEL] = None

    def node_color(label: str) -> str:
        return theme.status_color(status_by_label[label], mode) if label in status_by_label else theme.neutral(mode)

    colors = [node_color(n) for n in data.nodes]
    links = data.links
    fig = go.Figure(
        go.Sankey(
            arrangement="snap",
            valueformat=",d",
            node=dict(label=list(data.nodes), color=colors, pad=18, thickness=14, line=dict(width=0),
                      hovertemplate="<b>%{label}</b><br>%{value:,} applications<extra></extra>"),
            link=dict(
                source=links["source"].tolist(), target=links["target"].tolist(), value=links["value"].tolist(),
                color=[theme.rgba(colors[tg], 0.30) for tg in links["target"]],
                customdata=[fmt_pct(s) for s in links["share_of_source"]],
                hovertemplate="%{source.label} → %{target.label}<br>%{value:,} applications "
                              "(%{customdata} of %{source.label})<extra></extra>",
            ),
        )
    )
    fig.update_layout(height=380, margin=dict(l=8, r=8, t=8, b=8), paper_bgcolor="rgba(0,0,0,0)",
                      font=dict(family='system-ui, -apple-system, "Segoe UI", sans-serif', size=12, color=t.text_primary))
    return fig


def _log_color(values: pd.Series) -> np.ndarray:
    return np.log10(np.clip(values.to_numpy(dtype=float), 1e-4, None))


def _log_colorbar(values: pd.Series, unit: str, mode: str) -> dict:
    lo, hi = _log_color(values).min(), _log_color(values).max()
    ticks = np.arange(np.floor(lo), np.ceil(hi) + 1)
    fmt = (lambda v: fmt_hours(v, 0) if v >= 1 else fmt_hours(v, 3)) if unit == "hours" else (lambda v: fmt_int(v))
    return dict(title=dict(text=unit, side="right"), tickvals=ticks.tolist(), ticktext=[fmt(10 ** v) for v in ticks],
                thickness=10, outlinewidth=0, tickfont=dict(color=theme.tokens(mode).text_muted))


def transition_matrix(
    cells: pd.DataFrame,
    order: list[str],
    mode: str = "light",
    *,
    value: str = "median_wait_hours",
    selected_pair: tuple[str, str] | None = None,
    highlight_activity: str | None = None,
) -> go.Figure:
    """Clickable matrix (square scatter markers). Rows = activity, columns = next activity.

    customdata = [activity, next_activity, count, median, p90, timer text, stream text].
    """
    if value not in ("median_wait_hours", "transition_count"):
        raise ValueError(f"Unsupported matrix value '{value}'")
    n = max(len(order), 1)
    cell_px = 22
    t = theme.tokens(mode)
    timer = cells["likely_timer_driven"].fillna(False).astype(bool).to_numpy()
    timer_text = np.where(timer, "Likely timer-driven (exploratory) - not a staff wait", "")
    stream_text = np.where(cells["crosses_event_streams"].astype(bool), "Crosses event streams (interleaving)", "Same event stream")
    custom = np.column_stack([
        cells["activity"], cells["next_activity"], cells["transition_count"].astype(int),
        [fmt_hours(v) for v in cells["median_wait_hours"]], [fmt_hours(v) for v in cells["p90_wait_hours"]],
        timer_text, stream_text,
    ]) if len(cells) else np.empty((0, 7))
    opacity = np.ones(len(cells))
    if highlight_activity is not None and len(cells):
        on = (cells["activity"] == highlight_activity) | (cells["next_activity"] == highlight_activity)
        opacity = np.where(on, 1.0, theme.DIMMED_OPACITY)
    unit = "hours" if value == "median_wait_hours" else "transitions"
    fig = go.Figure(
        go.Scatter(
            x=cells["col"], y=cells["row"], mode="markers",
            marker=dict(symbol="square", size=cell_px - 3, color=_log_color(cells[value]) if len(cells) else [],
                        colorscale=theme.sequential_scale(mode), opacity=opacity,
                        colorbar=_log_colorbar(cells[value], unit, mode) if len(cells) else None,
                        line=dict(width=0)),
            customdata=custom,
            hovertemplate=("<b>%{customdata[0]} → %{customdata[1]}</b><br>%{customdata[2]:,} transitions"
                           "<br>Median wait %{customdata[3]} · 90th percentile %{customdata[4]}"
                           "<br>%{customdata[6]}<br>%{customdata[5]}<extra></extra>"),
            name="Transitions", showlegend=False, **_NO_DIM,
        )
    )
    if timer.any():
        tc = cells[timer]
        fig.add_scatter(x=tc["col"], y=tc["row"], mode="markers+text", text=["⏱"] * len(tc), textfont=dict(size=10),
                        marker=dict(symbol="square-open", size=cell_px, line=dict(width=2, color=t.text_primary)),
                        name="Likely timer-driven (exploratory)", hoverinfo="skip", showlegend=True)
    if selected_pair is not None:
        sel = cells[(cells["activity"] == selected_pair[0]) & (cells["next_activity"] == selected_pair[1])]
        if len(sel):
            fig.add_scatter(x=sel["col"], y=sel["row"], mode="markers", hoverinfo="skip", showlegend=False,
                            marker=dict(symbol="square-open", size=cell_px + 6,
                                        line=dict(width=3, color=theme.single_series(mode))))
    ticks = list(range(n))
    return _layout(
        fig, mode, height=cell_px * n + 220, showlegend=bool(timer.any()), legend=_legend(mode),
        margin=dict(l=8, t=40, b=8),
        xaxis=dict(tickmode="array", tickvals=ticks, ticktext=order, tickangle=-50, side="bottom", showgrid=False,
                   range=[-0.5, n - 0.5], title="Next activity", tickfont=dict(size=10)),
        yaxis=dict(tickmode="array", tickvals=ticks, ticktext=order, autorange="reversed", showgrid=False,
                   title="Activity", tickfont=dict(size=10)),
        clickmode="event+select", dragmode=False,
    )


def ranked_waits(top: pd.DataFrame, mode: str = "light", *, selected_pair: tuple[str, str] | None = None) -> go.Figure:
    """Longest median waits; likely timer-driven bars are grey and labelled. customdata[0:2] = pair."""
    rows = top.iloc[::-1].reset_index(drop=True)
    labels = [f"{a} → {b}" for a, b in zip(rows["activity"], rows["next_activity"])]
    timer = rows["likely_timer_driven"].fillna(False).astype(bool).to_numpy()
    colors = np.where(timer, theme.neutral(mode, soft=True), theme.single_series(mode)).tolist()
    pairs = list(zip(rows["activity"], rows["next_activity"]))
    opacity = [1.0 if selected_pair is None or p == tuple(selected_pair) else theme.DIMMED_OPACITY for p in pairs]
    t = theme.tokens(mode)
    custom = np.column_stack([rows["activity"], rows["next_activity"], rows["transition_count"].astype(int),
                              [fmt_hours(v) for v in rows["p90_wait_hours"]],
                              np.where(timer, "Likely timer-driven (exploratory) - not a staff wait", "Wait between recorded events")])
    fig = go.Figure()
    fig.add_bar(x=rows["median_wait_hours"], y=labels, orientation="h", name="Median wait",
                marker=dict(color=colors, cornerradius=4, opacity=opacity, line=dict(width=0)),
                text=[("⏱ likely timer-driven · " if tm else "") + fmt_hours(v) for v, tm in zip(rows["median_wait_hours"], timer)],
                textposition="outside", textfont=dict(color=t.text_secondary, size=11), cliponaxis=False,
                customdata=custom,
                hovertemplate="<b>%{y}</b><br>Median %{x:,.1f} h · 90th percentile %{customdata[3]}"
                              "<br>%{customdata[2]:,} transitions<br>%{customdata[4]}<extra></extra>",
                **_NO_DIM)
    fig.add_scatter(x=rows["p90_wait_hours"], y=labels, mode="markers", name="90th percentile",
                    marker=dict(symbol="line-ns", size=14, line=dict(width=2, color=t.text_primary)),
                    hoverinfo="skip")
    return _layout(fig, mode, height=max(260, 30 * len(rows) + 80), showlegend=True, legend=_legend(mode),
                   margin=dict(t=40, r=170), bargap=0.3,
                   xaxis=dict(title="Hours", showgrid=True, rangemode="tozero"),
                   yaxis=dict(showgrid=False, ticksuffix="  ", tickfont=dict(size=11)),
                   clickmode="event+select", dragmode=False)


def wait_histogram(bins: pd.DataFrame, mode: str = "light", *, timer_driven: bool = False) -> go.Figure:
    """Log-spaced waiting-time bands for one pair (binned server-side)."""
    color = theme.neutral(mode, soft=True) if timer_driven else theme.single_series(mode)
    fig = go.Figure(
        go.Bar(x=bins["bin"], y=bins["count"], marker=dict(color=color, cornerradius=3, line=dict(width=0)),
               hovertemplate="<b>%{x}</b><br>%{y:,} occurrences<extra></extra>")
    )
    return _layout(fig, mode, height=280, bargap=0.08,
                   xaxis=dict(title="Wait until next event (log-spaced bands)", showgrid=False, tickangle=-35),
                   yaxis=dict(title="Occurrences", showgrid=True, rangemode="tozero"))


def activity_frequency_bars(frequency: pd.DataFrame, mode: str = "light", *, selected_activity: str | None = None) -> go.Figure:
    """Events per activity coloured by event stream (legend + shapes). customdata[0] = activity."""
    rows = frequency.iloc[::-1].reset_index(drop=True)
    streams = rows["activity"].map(stream_of)
    fig = go.Figure()
    for stream in theme.STREAMS:
        sub = rows[streams == stream]
        if sub.empty:
            continue
        fig.add_bar(x=sub["total_events"], y=sub["activity"], orientation="h", name=f"{stream} events",
                    marker=dict(color=theme.stream_color(stream, mode), cornerradius=3, line=dict(width=0),
                                opacity=_opacities(sub["activity"].tolist(), [selected_activity] if selected_activity else [])),
                    customdata=np.column_stack([sub["activity"], sub["applications"].astype(int)]),
                    hovertemplate="<b>%{y}</b><br>%{x:,} events in %{customdata[1]:,} applications<extra></extra>",
                    **_NO_DIM)
    return _layout(fig, mode, height=max(300, 20 * len(rows) + 80), showlegend=True, legend=_legend(mode),
                   margin=dict(t=40), barmode="overlay", bargap=0.25,
                   xaxis=dict(title="Events", showgrid=True), yaxis=dict(showgrid=False, tickfont=dict(size=11),
                                                                         categoryorder="array", categoryarray=rows["activity"].tolist()),
                   clickmode="event+select", dragmode=False)


# =========================================================================== #
# Cancellation
# =========================================================================== #
def sequence_bars(breakdown: pd.DataFrame, mode: str = "light", *, selected: str | None = None) -> go.Figure:
    """A_Cancelled order relative to O_Cancelled. customdata[0] = sequence."""
    rows = breakdown.iloc[::-1].reset_index(drop=True)
    t = theme.tokens(mode)
    fig = go.Figure(
        go.Bar(x=rows["applications"], y=rows["sequence"], orientation="h",
               marker=dict(color=theme.single_series(mode), cornerradius=4, line=dict(width=0),
                           opacity=_opacities(rows["sequence"].tolist(), [selected] if selected else [])),
               text=[f"{fmt_int(n)}  ({fmt_pct(p)})" for n, p in zip(rows["applications"], rows["percentage"])],
               textposition="outside", textfont=dict(color=t.text_primary), cliponaxis=False,
               customdata=rows[["sequence"]].to_numpy(),
               hovertemplate="<b>%{y}</b><br>%{x:,} applications with A_Cancelled<br><i>Click to list them</i><extra></extra>",
               **_NO_DIM)
    )
    return _layout(fig, mode, height=240, margin=dict(r=130), bargap=0.35,
                   xaxis=dict(showgrid=True, rangemode="tozero"), yaxis=dict(showgrid=False, ticksuffix="  "),
                   clickmode="event+select", dragmode=False)


def expiry_histogram(table: pd.DataFrame, mode: str = "light") -> go.Figure:
    """Days from last offer sent to A_Cancelled, split by the exploratory 'likely automatic expiry' flag."""
    days = table["days_since_last_offer_sent"]
    flag = table["likely_automatic_expiry"].fillna(False).astype(bool)
    fig = go.Figure()
    for mask, name, color in (
        (flag, "Likely automatic expiry (exploratory)", theme.stream_color("Application", mode)),
        (~flag, "Other application cancellations", theme.neutral(mode, soft=True)),
    ):
        fig.add_histogram(x=days[mask & days.notna()], name=name, marker=dict(color=color, line=dict(width=0)),
                          xbins=dict(start=0, size=1), hovertemplate="%{x} days: %{y:,} applications<extra>" + name + "</extra>")
    threshold = m.EXPIRY_MIN_DAYS_AFTER_LAST_OFFER_SENT
    fig.add_vline(x=threshold, line=dict(color=theme.tokens(mode).text_secondary, width=1, dash="dot"))
    fig.add_annotation(x=threshold, y=1, yref="paper", yanchor="bottom", showarrow=False, xanchor="left",
                       text=f" {threshold:.0f} days", font=dict(size=11, color=theme.tokens(mode).text_secondary))
    return _layout(fig, mode, height=300, barmode="stack", bargap=0.05, showlegend=True, legend=_legend(mode),
                   margin=dict(t=48), xaxis=dict(title="Days from last offer sent to A_Cancelled", showgrid=False),
                   yaxis=dict(title="Applications", showgrid=True))


def pattern_bars(pattern_summary: pd.DataFrame, mode: str = "light", *, selected: str | None = None) -> go.Figure:
    """The five follow-up patterns in the report's precedence order. customdata[0] = pattern."""
    order = [p for p in m.PATTERN_PRECEDENCE if p in set(pattern_summary["pattern"])]
    rows = pattern_summary.set_index("pattern").loc[order[::-1]].reset_index()
    t = theme.tokens(mode)
    fig = go.Figure(
        go.Bar(x=rows["applications"], y=rows["pattern"], orientation="h",
               marker=dict(color=theme.single_series(mode), cornerradius=4, line=dict(width=0),
                           opacity=_opacities(rows["pattern"].tolist(), [selected] if selected else [])),
               text=[f"{fmt_int(n)}  ({fmt_pct(p)})" for n, p in zip(rows["applications"], rows["share_percent"])],
               textposition="outside", textfont=dict(color=t.text_primary), cliponaxis=False,
               customdata=np.column_stack([rows["pattern"], [m.PATTERN_DESCRIPTIONS[p] for p in rows["pattern"]]]),
               hovertemplate="<b>%{y}</b><br>%{x:,} applications<br>%{customdata[1]}<extra></extra>",
               **_NO_DIM)
    )
    return _layout(fig, mode, height=280, margin=dict(r=130), bargap=0.35,
                   xaxis=dict(showgrid=True, rangemode="tozero"), yaxis=dict(showgrid=False, ticksuffix="  "),
                   clickmode="event+select", dragmode=False)


def _jitter(keys: pd.Series, width: float = 0.32) -> np.ndarray:
    """Deterministic vertical jitter from the application ID (stable across reruns)."""
    h = pd.util.hash_pandas_object(keys.astype(str), index=False).to_numpy()
    return (h % 1000) / 999 * 2 * width - width


def gap_strip(followup: pd.DataFrame, mode: str = "light", *, selected_pattern: str | None = None,
              selected_application: str | None = None) -> go.Figure:
    """Per-application gap (days) by pattern: box summary + clickable points. customdata[0] = case_id."""
    order = [p for p in m.PATTERN_PRECEDENCE if p in set(followup["pattern"])][::-1]
    pos = {p: i for i, p in enumerate(order)}
    t = theme.tokens(mode)
    fig = go.Figure()
    for p in order:
        sub = followup[followup["pattern"] == p]
        fig.add_box(x=sub["gap_days"], y=[pos[p]] * len(sub), orientation="h", boxpoints=False, width=0.6,
                    marker=dict(color=theme.neutral(mode)), line=dict(color=theme.neutral(mode), width=1),
                    fillcolor="rgba(0,0,0,0)", hoverinfo="skip", showlegend=False)
    y = followup["pattern"].map(pos).to_numpy(dtype=float) + _jitter(followup["case_id"])
    in_sel = np.ones(len(followup), dtype=bool) if selected_pattern is None else (followup["pattern"] == selected_pattern).to_numpy()
    fig.add_scatter(
        x=followup["gap_days"], y=y, mode="markers", name="Applications", showlegend=False,
        marker=dict(size=8, color=theme.single_series(mode), opacity=np.where(in_sel, 0.85, 0.15).tolist(),
                    line=dict(width=1, color=t.surface)),
        customdata=np.column_stack([followup["case_id"], followup["pattern"], followup["events_between"].astype(int)]),
        hovertemplate="<b>%{customdata[0]}</b><br>%{customdata[1]}<br>Gap %{x:.1f} days · "
                      "%{customdata[2]} events in between<br><i>Click to select for the Explorer</i><extra></extra>",
        **_NO_DIM,
    )
    if selected_application is not None:
        sel = followup["case_id"] == selected_application
        if sel.any():
            fig.add_scatter(x=followup.loc[sel, "gap_days"], y=y[sel.to_numpy()], mode="markers", hoverinfo="skip",
                            showlegend=False, marker=dict(size=14, symbol="circle-open", line=dict(width=2, color=t.text_primary)))
    return _layout(fig, mode, height=320,
                   xaxis=dict(title=definitions.METRICS["followup_gap_days"].name + " (days)", showgrid=True, rangemode="tozero"),
                   yaxis=dict(tickmode="array", tickvals=list(range(len(order))), ticktext=order, showgrid=False,
                              range=[-0.6, len(order) - 0.4], ticksuffix="  "),
                   clickmode="event+select", dragmode=False)


def pattern_composition_bars(composition: pd.DataFrame, mode: str = "light") -> go.Figure:
    rows = composition.iloc[::-1].reset_index(drop=True)
    total = int(rows["pattern_applications"].iloc[0]) if len(rows) else 0
    t = theme.tokens(mode)
    fig = go.Figure(
        go.Bar(x=rows["applications"], y=rows["component"], orientation="h",
               marker=dict(color=theme.neutral(mode), cornerradius=4, line=dict(width=0)),
               text=[f"{fmt_int(n)} of {fmt_int(total)}" for n in rows["applications"]], textposition="outside",
               textfont=dict(color=t.text_primary), cliponaxis=False,
               hovertemplate="<b>%{y}</b><br>%{x:,} of " + f"{total:,}" + " applications in this pattern<extra></extra>")
    )
    return _layout(fig, mode, height=220, margin=dict(r=110), bargap=0.35,
                   xaxis=dict(showgrid=True, rangemode="tozero", range=[0, max(total, 1) * 1.05]),
                   yaxis=dict(showgrid=False, ticksuffix="  "))


# =========================================================================== #
# Application timeline
# =========================================================================== #
_LANE_Y = {"Application": 2.0, "Offer": 1.0, "Workflow": 0.0, "Other": -1.0}


def _ts(value) -> str:
    return pd.Timestamp(value).strftime("%d %b %Y, %H:%M:%S UTC")


def _lifeline_y(lifelines: pd.DataFrame) -> list[float]:
    """One row per offer inside the Offer lane (spread over +-0.3 when there are several)."""
    n = len(lifelines)
    offsets = np.linspace(-0.3, 0.3, num=n) if n > 1 else np.zeros(n)
    return [_LANE_Y["Offer"] + float(dy) for dy in offsets]


def timeline_y(timeline: pd.DataFrame, lifelines: pd.DataFrame) -> pd.Series:
    """Vertical position of each event: its stream lane; offer events sit on their offer's lifeline."""
    offer_y = dict(zip(lifelines["offer_id"], _lifeline_y(lifelines)))
    y = timeline["stream"].map(_LANE_Y).astype(float)
    if "offer" in timeline and offer_y:
        on_offer = (timeline["stream"] == "Offer") & timeline["offer"].isin(list(offer_y))
        y[on_offer] = timeline.loc[on_offer, "offer"].map(offer_y)
    return y


def application_timeline(
    timeline: pd.DataFrame,
    lifelines: pd.DataFrame,
    mode: str = "light",
    *,
    is_open: bool = False,
    dataset_end: pd.Timestamp = definitions.DATASET_END,
    selected_event: str | None = None,
) -> go.Figure:
    """Event-by-event timeline in three stream lanes. customdata[0] = event_id.

    One marker per recorded event. Offer lifelines run from offer creation to the
    offer's last recorded outcome; an offer with no outcome is drawn dotted to the
    dataset end (it was still without outcome when the data ends). Offer events sit
    on their offer's lifeline. The final-status event is ringed; open applications
    show the dataset end. Workflow events are points only: lifecycle stages cannot be
    paired reliably into task durations.
    """
    t = theme.tokens(mode)
    fig = go.Figure()
    ys = timeline_y(timeline, lifelines)
    line_color = theme.rgba(theme.stream_color("Offer", mode), 0.45)
    for i, ((_, offer), y) in enumerate(zip(lifelines.iterrows(), _lifeline_y(lifelines))):
        has_outcome = pd.notna(offer["offer_outcome_time"])
        end = offer["offer_outcome_time"] if has_outcome else dataset_end
        outcome = (f"Outcome: {offer['offer_outcome']} at {_ts(end)}" if has_outcome else
                   "No outcome recorded by the data end (1 Feb 2017).<br>The dotted line runs to the data end; "
                   "it is not an outcome date.")
        fig.add_scatter(
            x=[offer["created_time"], end], y=[y, y], mode="lines", name="Offer lifeline (creation → outcome)",
            legendgroup="lifelines", showlegend=i == 0,
            line=dict(color=line_color, width=3, dash="solid" if has_outcome else "dot"),
            hovertemplate=f"<b>{offer['offer_id']}</b><br>Created (O_Create Offer) at {_ts(offer['created_time'])}"
                          f"<br>{outcome}<extra>Offer lifeline</extra>",
        )
    offer_col = timeline["offer"] if "offer" in timeline else timeline["offer_id"]
    for stream in (*theme.STREAMS, "Other"):
        mask = timeline["stream"] == stream
        sub = timeline[mask]
        if sub.empty:
            continue
        custom = np.column_stack([
            sub["event_id"].fillna(""), sub["activity"], sub["lifecycle"].fillna("—"), sub["resource"].fillna("—"),
            offer_col[mask].fillna("—"), [_ts(ts) for ts in sub["time"]], sub["order"],
        ])
        fig.add_scatter(
            x=sub["time"], y=ys[mask], mode="markers", name=f"{stream} events",
            marker=dict(symbol=theme.STREAM_SYMBOLS.get(stream, "circle"), size=10,
                        color=theme.stream_color(stream, mode), line=dict(width=1, color=t.surface)),
            customdata=custom,
            hovertemplate=("<b>%{customdata[1]}</b> (event #%{customdata[6]})<br>%{customdata[5]}"
                           "<br>Lifecycle: %{customdata[2]} · Resource (as recorded): %{customdata[3]}"
                           "<br>Offer: %{customdata[4]} · Event ID: %{customdata[0]}"
                           "<br><i>Click to highlight in the event table</i><extra>" + stream + "</extra>"),
            **_NO_DIM,
        )
    final = timeline[timeline["is_final_status"].astype(bool)]
    if len(final):
        row = final.iloc[0]
        label = config.FINAL_STATUS_LABELS.get(row["activity"], row["activity"])
        fig.add_scatter(x=[row["time"]], y=[ys[final.index[0]]], mode="markers", hoverinfo="skip", showlegend=False,
                        marker=dict(size=20, symbol="circle-open", line=dict(width=2, color=t.text_primary)))
        fig.add_annotation(x=row["time"], y=ys[final.index[0]], text=f"Final status: {label}",
                           showarrow=False, yshift=18, font=dict(size=11, color=t.text_primary))
    if selected_event is not None:
        hit = timeline["event_id"] == selected_event
        if hit.any():
            fig.add_scatter(x=timeline.loc[hit, "time"], y=ys[hit], mode="markers", hoverinfo="skip",
                            showlegend=False, name="Selected event",
                            marker=dict(size=18, symbol="circle-open", line=dict(width=3, color=theme.single_series(mode))))
    if is_open:
        fig.add_vline(x=dataset_end, line=dict(color=t.text_secondary, width=1, dash="dot"))
        fig.add_annotation(x=dataset_end, y=1, yref="paper", yanchor="bottom", xanchor="right", showarrow=False,
                           text="Data ends 1 Feb 2017 (no final status) ", font=dict(size=11, color=t.text_secondary))
    lanes = [s for s in (*theme.STREAMS, "Other") if (timeline["stream"] == s).any() or s != "Other"]
    return _layout(
        fig, mode, height=360, showlegend=True, legend=_legend(mode), margin=dict(t=56),
        xaxis=dict(type="date", showgrid=True, title="Time (UTC)"),
        yaxis=dict(tickmode="array", tickvals=[_LANE_Y[s] for s in lanes], ticktext=lanes, showgrid=False,
                   range=[min(_LANE_Y[s] for s in lanes) - 0.6, 2.6], ticksuffix="  ", fixedrange=True),
        clickmode="event+select", dragmode="zoom", hovermode="closest",
    )
