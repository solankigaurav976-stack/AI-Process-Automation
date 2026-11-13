"""Tests for paths, labels and the read-only path guard.

Guard tests that need symlinks or hard links build their own protected
directories under pytest's temporary directory, so the real project files are
never linked to, opened for writing, or otherwise touched. Tests against the
real protected directories only create symlinks *pointing to* them (which does
not modify the target) or use plain path strings.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from core import config
from core.config import ensure_writable_path, is_protected_path, protection_reason
from core.errors import InvalidPathError, ProtectedPathError


# --------------------------------------------------------------------------- #
# Paths and labels
# --------------------------------------------------------------------------- #
def test_project_paths_resolve_to_expected_layout():
    assert config.DASHBOARD_DIR.name == "dashboard"
    assert config.PROJECT_ROOT == config.DASHBOARD_DIR.parent
    assert config.EVENT_LOG_PATH == config.PROJECT_ROOT / "data/processed/loan_application_events.parquet"
    assert set(config.PROTECTED_DIRS) == {
        config.PROJECT_ROOT / "data",
        config.PROJECT_ROOT / "reports",
        config.PROJECT_ROOT / "src",
    }


def test_report_registry_lists_all_seven_reports():
    assert len(config.REPORT_FILES) == 7
    assert all(f.endswith(".csv") for f in config.REPORT_FILES.values())
    with pytest.raises(TypeError):
        config.REPORT_FILES["new"] = "x.csv"  # read-only mapping


def test_a_pending_label_is_offer_accepted_not_approved():
    label = config.FINAL_STATUS_LABELS["A_Pending"]
    assert label == "A_Pending (offer accepted)"
    for forbidden in ("approved", "funded"):
        assert all(forbidden not in v.lower() for v in config.FINAL_STATUS_LABELS.values())


def test_offer_and_application_cancellation_are_distinct():
    assert config.OFFER_CANCELLED == "O_Cancelled"
    assert config.APPLICATION_CANCELLED == "A_Cancelled"
    assert config.OFFER_CANCELLED not in config.FINAL_STATUS_ACTIVITIES
    assert config.APPLICATION_CANCELLED in config.FINAL_STATUS_ACTIVITIES


# --------------------------------------------------------------------------- #
# Guard against the real protected directories (no file is touched)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "relative",
    [
        "data/processed/loan_application_events.parquet",
        "data/new_file.csv",
        "data",
        "reports/application_outcomes.csv",
        "reports/new_report.csv",
        "reports/sub/dir/new.csv",
        "src/analyze_process.py",
        "src/ src/analyze_outcomes.py",
    ],
)
def test_real_protected_paths_are_blocked(relative):
    target = config.PROJECT_ROOT / relative
    assert is_protected_path(target)
    with pytest.raises(ProtectedPathError) as info:
        ensure_writable_path(target)
    assert "read-only" in info.value.user_message


@pytest.mark.parametrize(
    "traversal",
    [
        "dashboard/../reports/x.csv",
        "dashboard/core/../../data/raw/x.xes",
        "reports/./x.csv",
        "dashboard/./../src/x.py",
        "reports//x.csv",
        "dashboard/nonexistent/../../reports/x.csv",
    ],
)
def test_path_traversal_into_protected_dirs_is_blocked(traversal):
    assert is_protected_path(str(config.PROJECT_ROOT) + "/" + traversal)


def test_relative_traversal_from_working_directory_is_blocked(monkeypatch):
    monkeypatch.chdir(config.DASHBOARD_DIR)
    assert is_protected_path("../reports/x.csv")
    assert is_protected_path("../data/../data/processed/out.parquet")
    assert not is_protected_path("exports/x.csv")


@pytest.mark.parametrize("variant", ["REPORTS/x.csv", "Reports/x.csv", "DATA/raw/x", "Src/x.py"])
def test_case_variants_are_blocked(variant):
    # macOS file systems are case-insensitive by default, so these reach the real dirs.
    assert is_protected_path(config.PROJECT_ROOT / variant)


def test_symlink_to_real_protected_dir_is_blocked(tmp_path):
    link = tmp_path / "innocent"
    link.symlink_to(config.REPORTS_DIR, target_is_directory=True)
    assert is_protected_path(link / "application_outcomes.csv")
    assert is_protected_path(link / "brand_new.csv")


def test_symlink_to_real_protected_file_is_blocked(tmp_path):
    link = tmp_path / "file_link.csv"
    link.symlink_to(config.REPORTS_DIR / "application_outcomes.csv")
    assert is_protected_path(link)


def test_tilde_expansion_is_resolved(tmp_path, monkeypatch):
    (tmp_path / "r").symlink_to(config.REPORTS_DIR, target_is_directory=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    assert is_protected_path("~/r/x.csv")
    assert not is_protected_path("~/elsewhere/x.csv")


@pytest.mark.parametrize(
    "allowed",
    [
        "dashboard/exports/x.csv",
        "reports_backup/x.csv",  # sibling with protected name as prefix
        "data2/x.csv",
        "srcfoo/x.py",
        "x_reports.csv",
    ],
)
def test_lookalike_and_sibling_paths_are_allowed(allowed):
    target = config.PROJECT_ROOT / allowed
    assert not is_protected_path(target)
    assert ensure_writable_path(target) == Path(os.path.realpath(target))


def test_tmp_path_is_writable(tmp_path):
    assert ensure_writable_path(tmp_path / "out.csv").name == "out.csv"


# --------------------------------------------------------------------------- #
# Guard against synthetic protected directories (links built safely in tmp)
# --------------------------------------------------------------------------- #
@pytest.fixture
def sandbox(tmp_path):
    protected = tmp_path / "protected"
    protected.mkdir()
    (protected / "source.csv").write_text("a,b\n1,2\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    return protected, outside


def test_chained_symlinks_are_followed(sandbox):
    protected, outside = sandbox
    (outside / "hop1").symlink_to(outside / "hop2", target_is_directory=True)
    (outside / "hop2").symlink_to(protected, target_is_directory=True)
    assert is_protected_path(outside / "hop1" / "new.csv", [protected])


def test_dangling_symlink_into_protected_dir_is_blocked(sandbox):
    protected, outside = sandbox
    dangling = outside / "dangling.csv"
    dangling.symlink_to(protected / "does_not_exist_yet.csv")
    assert not dangling.exists()
    assert is_protected_path(dangling, [protected])


def test_relative_symlink_into_protected_dir_is_blocked(sandbox):
    protected, outside = sandbox
    (outside / "rel").symlink_to(Path("..") / "protected", target_is_directory=True)
    assert is_protected_path(outside / "rel" / "x.csv", [protected])


def test_symlink_loop_fails_closed(sandbox):
    protected, outside = sandbox
    (outside / "a").symlink_to(outside / "b")
    (outside / "b").symlink_to(outside / "a")
    with pytest.raises(InvalidPathError):
        ensure_writable_path(outside / "a" / "x.csv", [protected])
    assert is_protected_path(outside / "a" / "x.csv", [protected]) is True


def test_protected_dir_that_is_itself_a_symlink(tmp_path):
    real = tmp_path / "real_store"
    real.mkdir()
    alias = tmp_path / "alias_store"
    alias.symlink_to(real, target_is_directory=True)
    # Protect via the alias; writes via the real location must also be blocked.
    assert is_protected_path(real / "x.csv", [alias])
    assert is_protected_path(alias / "x.csv", [alias])


def test_hard_link_to_protected_file_is_blocked(sandbox):
    protected, outside = sandbox
    hard = outside / "hard.csv"
    os.link(protected / "source.csv", hard)
    assert protection_reason(hard, [protected]) == "is a hard link to a file in a protected directory"
    with pytest.raises(ProtectedPathError):
        ensure_writable_path(hard, [protected])


def test_unrelated_hard_link_is_allowed(sandbox):
    _protected, outside = sandbox
    (outside / "own.csv").write_text("x")
    os.link(outside / "own.csv", outside / "own_link.csv")
    assert not is_protected_path(outside / "own_link.csv", [sandbox[0]])


def test_path_outside_sandbox_is_allowed(sandbox):
    protected, outside = sandbox
    assert protection_reason(outside / "fine.csv", [protected]) is None


# --------------------------------------------------------------------------- #
# Malformed input
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bad", ["", "   ", "reports/x\x00.csv", b"/tmp/x.csv", 42, None])
def test_malformed_paths_are_rejected_and_fail_closed(bad):
    with pytest.raises(InvalidPathError):
        ensure_writable_path(bad)
    assert is_protected_path(bad) is True


def test_hard_link_check_tolerates_missing_protected_dir(sandbox):
    protected, outside = sandbox
    (outside / "own.csv").write_text("x")
    os.link(outside / "own.csv", outside / "own_link.csv")
    assert not is_protected_path(outside / "own_link.csv", [protected, outside.parent / "missing_dir"])
