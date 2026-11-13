"""Read-only, schema-validated loading of the event log and existing reports.

Every file is opened explicitly in binary read mode (``"rb"``); nothing in this
module writes to disk. Failures are raised as typed
:class:`~core.errors.DashboardError` subclasses carrying a plain-language
message, so the UI can show a clear explanation instead of a stack trace.

This module does not import Streamlit; caching is added by the UI layer.
"""

from __future__ import annotations

import os
import re
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from core import config
from core.errors import (
    DashboardError,
    DataSourceMissingError,
    DataSourceReadError,
    SchemaValidationError,
    UnknownColumnError,
    UnknownReportError,
)
from core.schemas import EVENT_LOG, REPORT_SCHEMAS, ColumnSpec, TableSchema

_MAX_EXAMPLES = 3
_TZ_SUFFIX = re.compile(r"(?:Z|[+-]\d{2}:?\d{2})$")
_BOOL_MAP = {"True": True, "False": False, "true": True, "false": False}


# --------------------------------------------------------------------------- #
# File access
# --------------------------------------------------------------------------- #
def _check_readable_file(path: Path, label: str) -> None:
    try:
        st = os.stat(path)
    except FileNotFoundError as exc:
        raise DataSourceMissingError(
            f"The {label} file was not found. Expected it at: {path}", source=str(path)
        ) from exc
    except OSError as exc:
        raise DataSourceReadError(
            f"The {label} file could not be accessed.", source=str(path), details=str(exc)
        ) from exc
    if not stat.S_ISREG(st.st_mode):
        raise DataSourceReadError(
            f"The {label} path exists but is not a regular file.", source=str(path)
        )
    if st.st_size == 0:
        raise DataSourceReadError(f"The {label} file is empty (0 bytes).", source=str(path))


def _open_read_only(path: Path, label: str):
    """Open ``path`` for binary reading only."""
    _check_readable_file(path, label)
    try:
        return open(path, "rb")
    except OSError as exc:
        raise DataSourceReadError(
            f"The {label} file could not be opened for reading.", source=str(path), details=str(exc)
        ) from exc


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
def _examples(values: pd.Series) -> str:
    shown = [repr(v) for v in values.head(_MAX_EXAMPLES).tolist()]
    more = "" if len(values) <= _MAX_EXAMPLES else ", ..."
    return ", ".join(shown) + more


def _coerce_column(series: pd.Series, spec: ColumnSpec, problems: list[str]) -> pd.Series:
    """Convert ``series`` to the declared type; record unconvertible values."""
    present = series.notna()

    if spec.kind == "string":
        if isinstance(series.dtype, pd.StringDtype):
            return series  # every non-missing value is already text
        if not pd.api.types.is_object_dtype(series):
            problems.append(f"Column '{spec.name}': expected text, found {series.dtype}.")
            return series
        is_text = series.map(lambda v: isinstance(v, str), na_action="ignore")
        non_text = present & (is_text != True)  # noqa: E712 - elementwise comparison
        if non_text.any():
            problems.append(
                f"Column '{spec.name}': {int(non_text.sum())} non-text value(s), e.g. {_examples(series[non_text])}."
            )
            return series
        return series.astype("str")

    if spec.kind in ("integer", "float"):
        if pd.api.types.is_bool_dtype(series):
            problems.append(f"Column '{spec.name}': expected a number, found {series.dtype}.")
            return series
        converted = pd.to_numeric(series, errors="coerce")
        bad = present & converted.isna()
        if bad.any():
            problems.append(
                f"Column '{spec.name}': {int(bad.sum())} value(s) are not numbers, e.g. {_examples(series[bad])}."
            )
            return converted
        if spec.kind == "integer":
            fractional = converted.notna() & (converted % 1 != 0)
            if fractional.any():
                problems.append(
                    f"Column '{spec.name}': {int(fractional.sum())} value(s) are not whole numbers, "
                    f"e.g. {_examples(series[fractional])}."
                )
                return converted
            # Nullable Int64 when values are missing; the null check reports it if not allowed.
            return converted.astype("Int64" if converted.isna().any() else "int64")
        return converted.astype("float64")

    if spec.kind == "datetime":
        if isinstance(series.dtype, pd.DatetimeTZDtype):
            return series.dt.tz_convert("UTC")
        if pd.api.types.is_datetime64_dtype(series):
            problems.append(f"Column '{spec.name}': timestamps have no time zone.")
            return series
        text = series.astype("str").where(present)
        naive = present & ~text.str.contains(_TZ_SUFFIX, na=False)
        if naive.any():
            problems.append(
                f"Column '{spec.name}': {int(naive.sum())} timestamp(s) have no time-zone offset, "
                f"e.g. {_examples(series[naive])}."
            )
        converted = pd.to_datetime(text, utc=True, errors="coerce", format="ISO8601")
        bad = present & converted.isna()
        if bad.any():
            problems.append(
                f"Column '{spec.name}': {int(bad.sum())} value(s) are not valid timestamps, "
                f"e.g. {_examples(series[bad])}."
            )
        return converted

    if spec.kind == "boolean":
        if pd.api.types.is_bool_dtype(series):
            return series.astype("boolean")

        def to_bool(v):
            if isinstance(v, bool):
                return v
            if hasattr(v, "dtype") and v.dtype == bool:  # numpy.bool_
                return bool(v)
            if isinstance(v, str):
                return _BOOL_MAP.get(v)
            return None

        # Convert only present values (offer attributes are ~96% missing by design).
        converted = pd.Series(pd.NA, index=series.index, dtype="object")
        converted[present] = series[present].map(to_bool)
        bad = present & converted.isna()
        if bad.any():
            problems.append(
                f"Column '{spec.name}': {int(bad.sum())} value(s) are not True/False, e.g. {_examples(series[bad])}."
            )
            return series
        return converted.astype("boolean")

    raise ValueError(f"Unsupported column kind {spec.kind!r}")  # pragma: no cover - guarded by Literal


def _check_values(series: pd.Series, spec: ColumnSpec, problems: list[str]) -> None:
    nulls = int(series.isna().sum())
    if nulls and not spec.nullable:
        problems.append(f"Column '{spec.name}': {nulls} missing value(s) in a required column.")

    if spec.min_value is not None or spec.max_value is not None:
        if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
            if spec.min_value is not None:
                low = series.notna() & (series < spec.min_value)
                if low.any():
                    problems.append(
                        f"Column '{spec.name}': {int(low.sum())} value(s) below minimum {spec.min_value}, "
                        f"e.g. {_examples(series[low])}."
                    )
            if spec.max_value is not None:
                high = series.notna() & (series > spec.max_value)
                if high.any():
                    problems.append(
                        f"Column '{spec.name}': {int(high.sum())} value(s) above maximum {spec.max_value}, "
                        f"e.g. {_examples(series[high])}."
                    )

    if spec.allowed_values is not None:
        unexpected = series.notna() & ~series.isin(spec.allowed_values)
        if unexpected.any():
            values = pd.Series(series[unexpected].unique())
            problems.append(
                f"Column '{spec.name}': {int(unexpected.sum())} unexpected value(s), e.g. {_examples(values)}."
            )


def validate_frame(df: pd.DataFrame, schema: TableSchema, *, source: str | None = None) -> pd.DataFrame:
    """Validate ``df`` against ``schema`` and return a type-coerced copy.

    All problems are collected and raised together as one
    :class:`SchemaValidationError`. The input frame is never modified.
    """
    problems: list[str] = []

    missing = [c for c in schema.column_names if c not in df.columns]
    if missing:
        problems.append(f"Missing required column(s): {missing}.")
    extra = [c for c in df.columns if c not in schema.column_names]
    if extra and not schema.allow_extra_columns:
        problems.append(f"Unexpected column(s): {extra}.")
    duplicated_headers = df.columns[df.columns.duplicated()].tolist()
    if duplicated_headers:
        problems.append(f"Duplicate column header(s): {duplicated_headers}.")

    if len(df) < schema.min_rows:
        problems.append(f"Expected at least {schema.min_rows} row(s), found {len(df)}.")

    out = df.copy()
    if not duplicated_headers:
        for spec in schema.columns:
            if spec.name not in out.columns:
                continue
            out[spec.name] = _coerce_column(out[spec.name], spec, problems)
            _check_values(out[spec.name], spec, problems)

        key = [k for k in schema.unique_key if k in out.columns]
        if key and len(key) == len(schema.unique_key):
            # Fast path for single-column keys; details only computed on failure.
            if len(key) == 1 and out[key[0]].is_unique:
                dupes = None
            else:
                dupes = out.duplicated(subset=key, keep=False)
            if dupes is not None and dupes.any():
                examples = out.loc[dupes, key].drop_duplicates().head(_MAX_EXAMPLES)
                problems.append(
                    f"Key {key} is not unique: {int(dupes.sum())} row(s) share a key, "
                    f"e.g. {examples.to_dict('records')}."
                )

    if problems:
        raise SchemaValidationError(
            f"The {schema.name} data does not match the expected format.",
            problems=problems,
            source=source,
        )
    if schema.allow_extra_columns:
        return out
    return out[list(schema.column_names)]


# --------------------------------------------------------------------------- #
# Reports (CSV)
# --------------------------------------------------------------------------- #
def report_path(name: str, *, reports_dir: str | os.PathLike[str] | None = None) -> Path:
    """Path of report ``name`` inside ``reports_dir`` (default: project reports/)."""
    if name not in config.REPORT_FILES:
        raise UnknownReportError(
            f"Unknown report '{name}'. Known reports: {sorted(config.REPORT_FILES)}.", source=name
        )
    base = Path(reports_dir) if reports_dir is not None else config.REPORTS_DIR
    return base / config.REPORT_FILES[name]


def load_report(name: str, *, reports_dir: str | os.PathLike[str] | None = None) -> pd.DataFrame:
    """Load and validate one existing report. Never modifies the file."""
    path = report_path(name, reports_dir=reports_dir)
    schema = REPORT_SCHEMAS[name]
    label = f"report '{config.REPORT_FILES[name]}'"
    with _open_read_only(path, label) as fh:
        try:
            # Read everything as text first so validation sees the raw values.
            # Only empty cells count as missing; values are not stripped.
            raw = pd.read_csv(
                fh, dtype=str, keep_default_na=False, na_values=[""], encoding="utf-8-sig"
            )
        except pd.errors.EmptyDataError as exc:
            raise DataSourceReadError(f"The {label} has no header or data.", source=str(path)) from exc
        except (pd.errors.ParserError, UnicodeDecodeError, ValueError) as exc:
            raise DataSourceReadError(
                f"The {label} could not be parsed as CSV.", source=str(path), details=str(exc)
            ) from exc
    return validate_frame(raw, schema, source=str(path))


@dataclass(frozen=True)
class ReportBundle:
    """Result of loading all reports: successes and failures, kept apart."""

    reports: Mapping[str, pd.DataFrame]
    errors: Mapping[str, DashboardError]

    @property
    def all_loaded(self) -> bool:
        return not self.errors


def load_all_reports(*, reports_dir: str | os.PathLike[str] | None = None) -> ReportBundle:
    """Load every known report; one failing report does not stop the others."""
    loaded: dict[str, pd.DataFrame] = {}
    failed: dict[str, DashboardError] = {}
    for name in config.REPORT_FILES:
        try:
            loaded[name] = load_report(name, reports_dir=reports_dir)
        except DashboardError as exc:
            failed[name] = exc
    return ReportBundle(MappingProxyType(loaded), MappingProxyType(failed))


# --------------------------------------------------------------------------- #
# Event log (parquet)
# --------------------------------------------------------------------------- #
def load_event_log(
    columns: Iterable[str] | None = None,
    *,
    path: str | os.PathLike[str] | None = None,
) -> pd.DataFrame:
    """Load and validate the processed event log (all schema columns by default)."""
    path = Path(path) if path is not None else config.EVENT_LOG_PATH
    label = "event log"

    if columns is None:
        wanted = EVENT_LOG.column_names
    else:
        wanted = tuple(dict.fromkeys(columns))
        unknown = [c for c in wanted if c not in EVENT_LOG.column_names]
        if unknown:
            raise UnknownColumnError(f"Unknown event-log column(s) requested: {unknown}.", source=str(path))
        if not wanted:
            raise UnknownColumnError("At least one event-log column must be requested.", source=str(path))
    schema = EVENT_LOG.subset(wanted)

    with _open_read_only(path, label) as fh:
        try:
            available = pq.read_schema(fh).names
            missing = [c for c in wanted if c not in available]
            if missing:
                raise SchemaValidationError(
                    "The event log does not match the expected format.",
                    problems=[f"Missing required column(s): {missing}."],
                    source=str(path),
                )
            fh.seek(0)
            df = pd.read_parquet(fh, columns=list(wanted), engine="pyarrow")
        except DashboardError:
            raise
        except (pa.ArrowException, OSError, ValueError) as exc:
            raise DataSourceReadError(
                "The event log file could not be read as Parquet; it may be corrupt.",
                source=str(path),
                details=str(exc),
            ) from exc
    return validate_frame(df, schema, source=str(path))
