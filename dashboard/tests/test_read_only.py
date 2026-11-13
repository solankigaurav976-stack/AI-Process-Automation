"""Read-only verification: loading everything leaves the source files unchanged.

Fingerprints every file in data/, reports/ and src/ (SHA-256, size, modification
time) plus the directory listing, loads all reports and the full event log, then
fingerprints again. ``.DS_Store`` files are excluded because macOS Finder can
rewrite them at any time; they are not project data.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from core import config, loaders

pytestmark = pytest.mark.integration

_EXCLUDED_NAMES = {".DS_Store"}


def _fingerprint(roots) -> dict[str, tuple]:
    result: dict[str, tuple] = {}
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
            dirnames.sort()
            rel_dir = os.path.relpath(dirpath, config.PROJECT_ROOT)
            result[rel_dir + os.sep] = ("dir",)
            for name in sorted(filenames):
                if name in _EXCLUDED_NAMES:
                    continue
                path = Path(dirpath) / name
                st = os.lstat(path)
                digest = hashlib.sha256()
                with open(path, "rb") as fh:
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        digest.update(chunk)
                result[str(path.relative_to(config.PROJECT_ROOT))] = (
                    digest.hexdigest(),
                    st.st_size,
                    st.st_mtime_ns,
                )
    return result


def test_loading_everything_leaves_protected_files_unchanged():
    before = _fingerprint(config.PROTECTED_DIRS)
    assert any(k.endswith(".parquet") for k in before)
    assert sum(k.startswith("reports" + os.sep) and k.endswith(".csv") for k in before) == 7

    bundle = loaders.load_all_reports()
    assert bundle.all_loaded
    loaders.load_event_log()

    after = _fingerprint(config.PROTECTED_DIRS)
    assert after.keys() == before.keys(), "files were added or removed in a protected directory"
    changed = [k for k in before if before[k] != after[k]]
    assert changed == [], f"protected files changed: {changed}"


def test_no_dashboard_file_lives_in_protected_dirs():
    for path in config.DASHBOARD_DIR.rglob("*"):
        if ".pytest_cache" in path.parts:
            continue
        assert not config.is_protected_path(path), path
