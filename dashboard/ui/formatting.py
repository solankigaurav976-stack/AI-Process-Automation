"""Display formatting for numbers, percentages, durations and dates.

Pure functions (no Streamlit). Missing or non-finite values render as an em dash
so that an absent value is never shown as a misleading 0.
"""

from __future__ import annotations

import math
from numbers import Number

import pandas as pd

MISSING = "—"


def _missing(value) -> bool:
    if value is None or value is pd.NA or value is pd.NaT:
        return True
    if isinstance(value, Number) and not isinstance(value, bool):
        return not math.isfinite(float(value))
    return False


def fmt_int(value) -> str:
    """31509 -> '31,509'."""
    if _missing(value):
        return MISSING
    return f"{int(round(float(value))):,}"


def fmt_number(value, decimals: int = 1) -> str:
    """897.78 -> '897.8'."""
    if _missing(value):
        return MISSING
    return f"{float(value):,.{decimals}f}"


def fmt_pct(value, decimals: int = 1) -> str:
    """Value already in percent units: 54.676 -> '54.7%'."""
    if _missing(value):
        return MISSING
    return f"{float(value):,.{decimals}f}%"


def fmt_share(part, whole, decimals: int = 1) -> str:
    """Share of a whole as a percentage string; '—' when the whole is 0."""
    if _missing(part) or _missing(whole) or float(whole) == 0:
        return MISSING
    return fmt_pct(float(part) / float(whole) * 100, decimals)


def fmt_days(value, decimals: int = 1) -> str:
    """19.087 -> '19.1 days'; 1.0 -> '1.0 day'."""
    if _missing(value):
        return MISSING
    unit = "day" if round(float(value), decimals) == 1 else "days"
    return f"{float(value):,.{decimals}f} {unit}"


def fmt_hours(value, decimals: int = 1) -> str:
    """640.93 -> '640.9 h'."""
    if _missing(value):
        return MISSING
    return f"{float(value):,.{decimals}f} h"


def fmt_date(value) -> str:
    """Timestamp -> '1 Feb 2017' (UTC)."""
    if _missing(value):
        return MISSING
    ts = pd.Timestamp(value)
    if ts.tzinfo is not None:
        ts = ts.tz_convert("UTC")
    return f"{ts.day} {ts:%b %Y}"


def fmt_datetime(value) -> str:
    """Timestamp -> '1 Feb 2017, 14:11 UTC'."""
    if _missing(value):
        return MISSING
    ts = pd.Timestamp(value)
    ts = ts.tz_convert("UTC") if ts.tzinfo is not None else ts.tz_localize("UTC")
    return f"{ts.day} {ts:%b %Y, %H:%M} UTC"


def fmt_month(value: str) -> str:
    """'2016-12' -> 'Dec 2016'."""
    if _missing(value) or not value:
        return MISSING
    return f"{pd.Period(value, freq='M'):%b %Y}"


def fmt_value(value, unit: str) -> str:
    """Format a KPI value according to its registered unit."""
    if unit == "days":
        return fmt_days(value)
    if unit == "%":
        return fmt_pct(value)
    if unit == "score":
        return fmt_number(value, 0)
    if unit == "hours":
        return fmt_hours(value)
    if unit in ("applications", "events", "offers", "transitions"):
        return fmt_int(value)
    return MISSING if _missing(value) else str(value)
