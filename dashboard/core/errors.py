"""Typed errors raised by the dashboard data layer.

Every error carries a plain-language ``user_message`` suitable for display in
the UI, plus optional technical ``details`` for logs and tests.
"""

from __future__ import annotations

from collections.abc import Sequence


class DashboardError(Exception):
    """Base class for all expected, user-reportable dashboard failures."""

    def __init__(self, user_message: str, *, source: str | None = None, details: str | None = None):
        self.user_message = user_message
        self.source = source
        self.details = details
        text = user_message if details is None else f"{user_message} ({details})"
        super().__init__(text)


class DataSourceMissingError(DashboardError):
    """A required dataset or report file does not exist."""


class DataSourceReadError(DashboardError):
    """A file exists but cannot be read or parsed (empty, corrupt, wrong encoding, not a file)."""


class SchemaValidationError(DashboardError):
    """A file was read but its contents do not match the expected schema."""

    def __init__(self, user_message: str, *, problems: Sequence[str], source: str | None = None):
        self.problems = tuple(problems)
        super().__init__(user_message, source=source, details="; ".join(self.problems))


class UnknownReportError(DashboardError):
    """A report name was requested that the dashboard does not know about."""


class UnknownColumnError(DashboardError):
    """A column was requested that is not part of the declared schema."""


class InvalidPathError(DashboardError):
    """A path is malformed (empty, contains NUL bytes, or cannot be resolved)."""


class ProtectedPathError(DashboardError):
    """A write was attempted to a path inside a read-only protected directory."""
