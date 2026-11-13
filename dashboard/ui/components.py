"""Reusable presentation components.

KPI labels, definitions, calculations and sources come exclusively from
``core.definitions.METRICS``; warning texts come from ``core.definitions``.
Components never compute metrics.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core import config, definitions
from core.errors import DashboardError
from ui import theme
from ui.formatting import fmt_int, fmt_pct, fmt_value

# --------------------------------------------------------------------------- #
# Layout
# --------------------------------------------------------------------------- #


def page_header(title: str, description: str) -> None:
    st.title(title)
    st.caption(description)


def section(title: str, caption: str | None = None) -> None:
    st.subheader(title)
    if caption:
        st.caption(caption)


def source_caption(*sources: str) -> None:
    st.caption("Source: " + " · ".join(sources))


def filter_caption(description: str) -> None:
    st.caption(f":material/filter_alt: {description}")


def theme_mode() -> str:
    try:
        return st.context.theme.type or "light"
    except Exception:  # pragma: no cover - older runtimes / bare mode
        return "light"


# --------------------------------------------------------------------------- #
# KPIs
# --------------------------------------------------------------------------- #
def kpi_help(key: str) -> str:
    """Tooltip text for a KPI, built only from its registered definition."""
    d = definitions.get_definition(key)
    lines = [d.definition, "", f"**Calculation:** {d.calculation}", "", f"**Source:** {d.source}"]
    if d.comparable_report:
        lines += ["", f"**Reconciled against:** {d.comparable_report}"]
    for caveat in d.caveats:
        lines += ["", f"**Note:** {caveat}"]
    return "\n".join(lines)


def kpi_label(key: str) -> str:
    return definitions.get_definition(key).name


def kpi_display_value(key: str, value) -> str:
    return fmt_value(value, definitions.get_definition(key).unit)


@dataclass(frozen=True)
class Kpi:
    key: str
    value: object
    context: str | None = None


def kpi_card(kpi: Kpi) -> None:
    st.metric(kpi_label(kpi.key), kpi_display_value(kpi.key, kpi.value), help=kpi_help(kpi.key), border=True)
    if kpi.context:
        st.caption(kpi.context)


def kpi_row(kpis: Sequence[Kpi]) -> None:
    for col, kpi in zip(st.columns(len(kpis)), kpis):
        with col:
            kpi_card(kpi)


# --------------------------------------------------------------------------- #
# Notices (text from core.definitions)
# --------------------------------------------------------------------------- #
LIFECYCLE_NOTE = (
    "Activity counts include every lifecycle transition (schedule, start, suspend, resume, "
    "complete, ...). A workflow task suspended and resumed several times is counted several "
    "times, so counts are not numbers of tasks."
)
CLOSURE_NOTE = (
    "Cancellation-to-completion 'delays' are milliseconds: the open work item is closed "
    "automatically when the application is cancelled. They are system closures, not "
    "operational bottlenecks."
)

_NOTICES = {
    "censoring": ("warning", ":material/event_busy:", "Data ends on 1 February 2017", definitions.DATASET_END_WARNING),
    "timer": ("info", ":material/schedule:", "Exploratory: likely timer-driven steps", definitions.TIMER_WARNING),
    "not_recovery": ("info", ":material/info:", "Not customer recovery", definitions.NOT_RECOVERY_NOTE),
    "cancellation_types": (
        "info", ":material/call_split:", "O_Cancelled ≠ A_Cancelled", definitions.OFFER_VS_APPLICATION_CANCELLATION,
    ),
    "last_activity": (
        "warning", ":material/rule:", "Last recorded activity is not a final status", definitions.LAST_ACTIVITY_NOT_OUTCOME,
    ),
    "a_pending": ("info", ":material/info:", "About A_Pending", definitions.A_PENDING_NOTE),
    "credit_score": ("info", ":material/info:", "CreditScore = 0", definitions.CREDIT_SCORE_WARNING),
    "lifecycle": ("info", ":material/info:", "How activities are counted", LIFECYCLE_NOTE),
    "interleaving": ("info", ":material/merge:", "Mixed event streams", definitions.INTERLEAVING_NOTE),
    "closure": ("info", ":material/info:", "System closures", CLOSURE_NOTE),
}


def notice_text(kind: str) -> str:
    return _NOTICES[kind][3]


def notice(kind: str, *, extra: str | None = None) -> None:
    level, icon, title, text = _NOTICES[kind]
    body = f"**{title}.** {text}" + (f"\n\n{extra}" if extra else "")
    getattr(st, level)(body, icon=icon)


# --------------------------------------------------------------------------- #
# Tables, downloads, states
# --------------------------------------------------------------------------- #
def to_csv_bytes(df: pd.DataFrame) -> bytes:
    """CSV built in memory (nothing is written to disk)."""
    return df.to_csv(index=False).encode("utf-8")


def csv_payload(df: pd.DataFrame):
    """Deferred CSV builder: runs only when the user clicks the download button.

    A private copy is captured, so later changes to ``df`` cannot alter the download.
    """
    frozen = display_frame(df)
    return lambda: to_csv_bytes(frozen)


ZIP_LABEL = "CSV + Attribution (ZIP)"
ATTRIBUTION_FILE_NAME = "ATTRIBUTION.txt"
#: Fixed metadata so the same data always produces byte-identical ZIP files (1980-01-01 is the ZIP epoch).
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_ZIP_FILE_MODE = 0o644
_DATASET_CITATION = (
    "van Dongen, Boudewijn (2017): BPI Challenge 2017. 4TU.ResearchData. Dataset. "
    f"{definitions.DATASET_DOI_URL}"
)


def attribution_text(csv_file_name: str) -> str:
    """Plain-text acknowledgement packed next to a downloaded CSV (4TU General Terms of Use, Art. 1 and 4)."""
    width = max(len(label) for label, _ in definitions.DATASET_ATTRIBUTION) + 1
    fields = "\n".join(f"  {label + ':':<{width}} {value}" for label, value in definitions.DATASET_ATTRIBUTION)
    return (
        "DATASET ATTRIBUTION\n"
        "===================\n\n"
        f"{csv_file_name} is derived from the BPI Challenge 2017 dataset.\n\n"
        "Citation:\n"
        f"  {_DATASET_CITATION}\n\n"
        f"{fields}\n"
        f"  {'DOI:':<{width}} {definitions.DATASET_DOI}\n"
        f"  {'':<{width}} {definitions.DATASET_DOI_URL}\n\n"
        "Terms of use:\n"
        f"  Use of this data is subject to the {definitions.DATASET_TERMS_NAME}:\n"
        f"  {definitions.DATASET_TERMS_URL}\n"
        "  Non-commercial use only.\n\n"
        "Acknowledgement requirement:\n"
        "  Keep this acknowledgement (this file, or the citation above) with the data whenever you\n"
        "  share or redistribute it, and require anyone you share it with to keep it as well.\n\n"
        "This file summarises the dataset's terms of use; it is not legal advice. Read the full\n"
        "terms at the address above.\n"
    )


def zip_file_name(csv_file_name: str) -> str:
    stem = csv_file_name[:-4] if csv_file_name.lower().endswith(".csv") else csv_file_name
    return f"{stem}_with_attribution.zip"


def zip_key(csv_key: str) -> str:
    """Widget key of the ZIP button (``dl_x`` -> ``zip_x``, so it never contains the CSV key)."""
    return "zip_" + csv_key.removeprefix("dl_")


def zip_payload(df: pd.DataFrame, csv_file_name: str):
    """Deferred ZIP builder: the CSV (byte-identical to the plain download) plus ATTRIBUTION.txt.

    Built in memory with the standard library only, with fixed timestamps and file modes.
    """
    frozen = display_frame(df)

    def build() -> bytes:
        members = ((csv_file_name, to_csv_bytes(frozen)),
                   (ATTRIBUTION_FILE_NAME, attribution_text(csv_file_name).encode("utf-8")))
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, payload in members:
                info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 3  # Unix, regardless of the server's platform
                info.external_attr = _ZIP_FILE_MODE << 16
                archive.writestr(info, payload)
        return buffer.getvalue()

    return build


def download_csv(df: pd.DataFrame, file_name: str, *, label: str = "Download CSV", key: str) -> None:
    with st.container(horizontal=True):
        st.download_button(
            label, data=csv_payload(df), file_name=file_name, mime="text/csv", key=key,
            icon=":material/download:", on_click="ignore", disabled=df.empty,
        )
        st.download_button(
            ZIP_LABEL, data=zip_payload(df, file_name), file_name=zip_file_name(file_name),
            mime="application/zip", key=zip_key(key), icon=":material/folder_zip:", on_click="ignore",
            disabled=df.empty, help=f"The same CSV plus {ATTRIBUTION_FILE_NAME} with the dataset acknowledgement.",
        )
    st.caption(definitions.DOWNLOAD_NOTICE)  # dataset acknowledgement travels with every download control


def display_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Copy for display/export without pandas ``attrs`` (Streamlit cannot serialise timestamps there)."""
    out = df.copy()
    out.attrs = {}
    return out


def table(df: pd.DataFrame, **kwargs) -> None:
    st.dataframe(display_frame(df), hide_index=True, **kwargs)



def empty_state(message: str = "No applications match the current filters.") -> None:
    st.info(message, icon=":material/search_off:")


def error_panel(error: DashboardError) -> None:
    st.error(f"**The dashboard could not load its data.** {error.user_message}", icon=":material/error:")
    st.markdown(
        "Check that the processed event log exists at "
        f"`data/processed/{config.EVENT_LOG_PATH.name}` and that the reports in `reports/` are present, "
        "then reload the page. The dashboard only reads these files; it never modifies them."
    )
    if error.details or error.source:
        with st.expander("Technical details"):
            if error.source:
                st.code(str(error.source), language=None)
            if error.details:
                st.code(str(error.details), language=None)


def dataset_attribution() -> None:
    """Sidebar box acknowledging the dataset source (shown on every page, even if data fails to load)."""
    fields = [f"**{label}:** {value}" for label, value in definitions.DATASET_ATTRIBUTION]
    fields += [
        f"**DOI:** [{definitions.DATASET_DOI}]({definitions.DATASET_DOI_URL})",
        f"**Terms:** [{definitions.DATASET_TERMS_NAME}]({definitions.DATASET_TERMS_URL})",
    ]
    with st.sidebar.container(border=True, key="dataset_attribution"):
        st.markdown("**Data source**  \n" + "  \n".join(fields))


def placeholder(title: str, items: Sequence[str]) -> None:
    with st.container(border=True):
        st.markdown(f"**{title}**")
        st.caption("Planned for the next build phase:")
        st.markdown("\n".join(f"- {item}" for item in items))


# --------------------------------------------------------------------------- #
# Charts
# --------------------------------------------------------------------------- #
def final_status_chart(distribution: pd.DataFrame, mode: str = "light") -> go.Figure:
    """Horizontal bars, fixed status order and colours, direct value labels."""
    dist = distribution.iloc[::-1]  # first status at the top
    colors = [theme.status_color(s if isinstance(s, str) else None, mode) for s in dist["final_status"]]
    labels = [f"{fmt_int(n)}  ({fmt_pct(p)})" for n, p in zip(dist["applications"], dist["percentage"])]
    t = theme.tokens(mode)
    fig = go.Figure(
        go.Bar(
            x=dist["applications"],
            y=dist["label"],
            orientation="h",
            marker=dict(color=colors, cornerradius=4, line=dict(width=0)),
            text=labels,
            textposition="outside",
            textfont=dict(color=t.text_primary),
            cliponaxis=False,
            hovertemplate="<b>%{y}</b><br>%{x:,} applications<extra></extra>",
        )
    )
    layout = theme.base_layout(mode, height=240)
    layout["xaxis"].update(showgrid=True, title=None, rangemode="tozero")
    layout["yaxis"].update(showgrid=False, title=None, ticksuffix="  ")
    layout["margin"]["r"] = 120
    layout["bargap"] = 0.35
    fig.update_layout(**layout)
    return fig


def plotly(fig: go.Figure, *, key: str, on_select=None, config: dict | None = None) -> None:
    """Render a figure; with ``on_select`` the chart is clickable (single points).

    ``config`` replaces the default Plotly config (e.g. ``theme.ZOOM_PLOTLY_CONFIG``).
    """
    config = theme.PLOTLY_CONFIG if config is None else config
    if on_select is None:
        st.plotly_chart(fig, key=key, config=config, theme=None, width="stretch")
    else:
        st.plotly_chart(fig, key=key, config=config, theme=None, width="stretch",
                        on_select=on_select, selection_mode="points")


LEGEND_NOTE = "Clicking a legend entry only hides that series in this chart; it does not filter the dashboard."


def interaction_caption(chart_id: str, *, extra: str | None = None) -> None:
    """Caption telling the reader what clicking does (or that the chart is hover-only)."""
    from ui.charts import CLICK, INTERACTIONS

    info = INTERACTIONS[chart_id]
    icon = ":material/touch_app:" if info.kind == CLICK else ":material/mouse:"
    st.caption(f"{icon} {info.note}" + (f" {extra}" if extra else ""))


def active_filter_chips(state, months) -> None:
    """Show global filters set by chart clicks or the sidebar, each with a clear button."""
    from ui import selection
    from ui.filters import FILTER_KEYS
    from ui.formatting import fmt_month as _fm

    statuses = list(state.get(FILTER_KEYS["final_statuses"]) or [])
    month_range = state.get(FILTER_KEYS["months"])
    full = (months[0], months[-1]) if months else None
    month_active = bool(month_range) and full is not None and tuple(month_range) != full
    if not statuses and not month_active:
        return
    cols = st.columns([1.3, 3, 3, 4], vertical_alignment="center")
    cols[0].markdown("**Active filters**")
    if statuses:
        cols[1].button(f"Final status: {', '.join(statuses)}", icon=":material/close:", key="chip_status",
                       on_click=selection.clear_status_filter, args=(state,),
                       help="Remove the final-status filter (all pages).")
    if month_active:
        lo, hi = month_range
        text = _fm(lo) if lo == hi else f"{_fm(lo)} – {_fm(hi)}"
        cols[2].button(f"Start month: {text}", icon=":material/close:", key="chip_month",
                       on_click=selection.clear_month_filter, args=(state, months),
                       help="Restore the full start-month range (all pages).")
    cols[3].caption("Filters apply to every page except Data Quality. Click a selected bar again to clear it too.")
