"""Paths, column names, labels and the read-only path guard.

The source datasets (``data/``), the existing reports (``reports/``) and the
original analysis scripts (``src/``) are strictly read-only for the dashboard.
Any future code that writes a file must first pass its target through
:func:`ensure_writable_path`, which refuses every path that resolves into a
protected directory - including via ``..`` traversal, ``~`` expansion,
symbolic links (also dangling and chained ones), case variants on
case-insensitive file systems, and hard links to protected files.

The dashboard itself never writes files: CSV downloads are produced in memory.
"""

from __future__ import annotations

import os
import stat
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from types import MappingProxyType

from core.errors import InvalidPathError, ProtectedPathError

# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
DASHBOARD_DIR = Path(__file__).resolve().parent.parent
PROJECT_ROOT = DASHBOARD_DIR.parent

DATA_DIR = PROJECT_ROOT / "data"
REPORTS_DIR = PROJECT_ROOT / "reports"
SRC_DIR = PROJECT_ROOT / "src"

EVENT_LOG_PATH = DATA_DIR / "processed" / "loan_application_events.parquet"
RAW_EVENT_LOG_PATH = DATA_DIR / "raw" / "BPI Challenge 2017.xes"

#: Directories the dashboard must never write into.
PROTECTED_DIRS: tuple[Path, ...] = (DATA_DIR, REPORTS_DIR, SRC_DIR)

#: Logical report name -> file name inside ``REPORTS_DIR``.
REPORT_FILES = MappingProxyType(
    {
        "activity_frequency": "activity_frequency.csv",
        "application_outcomes": "application_outcomes.csv",
        "application_performance": "application_performance.csv",
        "cancellation_completion_delays": "cancellation_completion_delays.csv",
        "offer_cancellation_followup_patterns": "offer_cancellation_followup_patterns.csv",
        "offer_cancellation_pattern_summary": "offer_cancellation_pattern_summary.csv",
        "process_bottlenecks": "process_bottlenecks.csv",
    }
)

# --------------------------------------------------------------------------- #
# Event-log column names (BPI Challenge 2017, XES attribute names)
# --------------------------------------------------------------------------- #
COL_CASE_ID = "case:concept:name"
COL_ACTIVITY = "concept:name"
COL_TIMESTAMP = "time:timestamp"
COL_LIFECYCLE = "lifecycle:transition"
COL_ORIGIN = "EventOrigin"
COL_EVENT_ID = "EventID"
COL_OFFER_ID = "OfferID"
COL_RESOURCE = "org:resource"
COL_ACTION = "Action"
COL_LOAN_GOAL = "case:LoanGoal"
COL_APPLICATION_TYPE = "case:ApplicationType"
COL_REQUESTED_AMOUNT = "case:RequestedAmount"

# --------------------------------------------------------------------------- #
# Activities and labels
# --------------------------------------------------------------------------- #
#: Offer cancellation - one offer is withdrawn; the application may continue.
OFFER_CANCELLED = "O_Cancelled"
#: Application cancellation - the whole application ends as cancelled.
APPLICATION_CANCELLED = "A_Cancelled"
APPLICATION_PENDING = "A_Pending"
APPLICATION_DENIED = "A_Denied"

#: Activities that mark an application's final status.
FINAL_STATUS_ACTIVITIES: tuple[str, ...] = (
    APPLICATION_PENDING,
    APPLICATION_CANCELLED,
    APPLICATION_DENIED,
)

#: Display labels for final statuses. A_Pending is deliberately NOT labelled
#: "Approved" or "Funded": the log only shows that the offer was accepted.
FINAL_STATUS_LABELS = MappingProxyType(
    {
        APPLICATION_PENDING: "A_Pending (offer accepted)",
        APPLICATION_CANCELLED: "A_Cancelled (application cancelled)",
        APPLICATION_DENIED: "A_Denied (application denied)",
    }
)
NO_FINAL_STATUS_LABEL = "No final status recorded"

EVENT_ORIGINS: tuple[str, ...] = ("Application", "Offer", "Workflow")
LIFECYCLE_TRANSITIONS: tuple[str, ...] = (
    "ate_abort",
    "complete",
    "resume",
    "schedule",
    "start",
    "suspend",
    "withdraw",
)

# --------------------------------------------------------------------------- #
# Read-only path guard
# --------------------------------------------------------------------------- #


def _resolve(path: str | os.PathLike[str]) -> Path:
    """Resolve ``path`` to an absolute, symlink-free path.

    Uses ``os.path.realpath(strict=ALLOW_MISSING)`` so that missing trailing
    components are allowed (a file about to be created) while symlink loops and
    other resolution failures raise instead of being silently ignored.
    """
    if isinstance(path, bytes):
        raise InvalidPathError("Path must be text, not bytes.", details=repr(path))
    try:
        raw = os.fspath(path)
    except TypeError as exc:
        raise InvalidPathError("Path has an unsupported type.", details=repr(path)) from exc
    if not isinstance(raw, str) or raw.strip() == "":
        raise InvalidPathError("Path is empty.", details=repr(path))
    if "\x00" in raw:
        raise InvalidPathError("Path contains a NUL byte.", details=repr(path))

    expanded = os.path.abspath(os.path.expanduser(raw))
    try:
        resolved = os.path.realpath(expanded, strict=os.path.ALLOW_MISSING)
    except (OSError, ValueError, RuntimeError) as exc:
        raise InvalidPathError(
            "Path cannot be resolved safely (for example a symbolic-link loop).",
            details=f"{raw!r}: {exc}",
        ) from exc
    return Path(resolved)


def _comparison_key(path: Path) -> tuple[str, ...]:
    """Case- and Unicode-normalised path parts.

    macOS (APFS/HFS+) is case-insensitive and normalisation-insensitive by
    default, so ``REPORTS/x.csv`` refers to ``reports/x.csv``. Comparing
    case-folded NFC parts errs on the side of blocking.
    """
    return tuple(unicodedata.normalize("NFC", part).casefold() for part in path.parts)


def _is_within(child: Path, parent: Path) -> bool:
    child_key, parent_key = _comparison_key(child), _comparison_key(parent)
    return child_key[: len(parent_key)] == parent_key


def _protected_roots(protected_dirs: Iterable[str | os.PathLike[str]]) -> list[Path]:
    roots: list[Path] = []
    for directory in protected_dirs:
        roots.append(Path(os.path.abspath(os.fspath(directory))))
        roots.append(_resolve(directory))
    return roots


def _protected_file_identities(roots: Iterable[Path]) -> set[tuple[int, int]]:
    """(device, inode) of every regular file inside the protected directories."""
    identities: set[tuple[int, int]] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
            for name in filenames:
                try:
                    st = os.lstat(os.path.join(dirpath, name))
                except OSError:
                    continue
                if stat.S_ISREG(st.st_mode):
                    identities.add((st.st_dev, st.st_ino))
    return identities


def protection_reason(
    path: str | os.PathLike[str],
    protected_dirs: Iterable[str | os.PathLike[str]] = PROTECTED_DIRS,
) -> str | None:
    """Return why ``path`` is protected, or ``None`` if it may be written.

    Raises :class:`InvalidPathError` for malformed or unresolvable paths.
    """
    resolved = _resolve(path)
    roots = _protected_roots(protected_dirs)

    for root in roots:
        if _is_within(resolved, root):
            return f"resolves inside protected directory {root}"

    # A hard link elsewhere can share the inode of a protected file; writing to
    # it would change the protected file's contents.
    try:
        st = os.stat(resolved)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise InvalidPathError("Path cannot be inspected safely.", details=f"{path!r}: {exc}") from exc
    if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
        if (st.st_dev, st.st_ino) in _protected_file_identities(roots):
            return "is a hard link to a file in a protected directory"
    return None


def is_protected_path(
    path: str | os.PathLike[str],
    protected_dirs: Iterable[str | os.PathLike[str]] = PROTECTED_DIRS,
) -> bool:
    """True if writing to ``path`` would touch a protected directory.

    Fails closed: a path that cannot be resolved safely is treated as protected.
    """
    try:
        return protection_reason(path, protected_dirs) is not None
    except InvalidPathError:
        return True


def ensure_writable_path(
    path: str | os.PathLike[str],
    protected_dirs: Iterable[str | os.PathLike[str]] = PROTECTED_DIRS,
) -> Path:
    """Return the resolved ``path`` if it is safe to write, otherwise raise.

    Raises :class:`ProtectedPathError` for protected targets and
    :class:`InvalidPathError` for malformed or unresolvable paths.

    Note: this is a check at call time. Code that writes must call it
    immediately before opening the file.
    """
    reason = protection_reason(path, protected_dirs)
    if reason is not None:
        raise ProtectedPathError(
            "Writing to this location is not allowed: source datasets, reports and "
            "scripts are read-only.",
            source=os.fspath(path),
            details=reason,
        )
    return _resolve(path)
