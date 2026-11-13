"""Phase 6.2: the root-level repository files stay consistent with the project.

Covers the root README.md, LICENSE, requirements-data.txt and .gitignore:
links resolve, setup commands point at real files with consistent pins, the MIT
licence is kept separate from the dataset terms, author details are not invented,
the documented test count matches the suite, and .gitignore excludes the data,
environment and secrets without excluding anything the project needs.
Nothing here writes inside the project; the .gitignore check runs in tmp_path.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys

import pytest

from core import config

ROOT = config.PROJECT_ROOT
DASHBOARD = config.DASHBOARD_DIR
ROOT_README = (ROOT / "README.md").read_text()
DASH_README = (DASHBOARD / "README.md").read_text()
DEMO = (DASHBOARD / "docs" / "DEMO_GUIDE.md").read_text()
READINESS = (DASHBOARD / "docs" / "GITHUB_READINESS.md").read_text()
LICENSE = (ROOT / "LICENSE").read_text()
REQ_DATA = (ROOT / "requirements-data.txt").read_text()
GITIGNORE = (ROOT / ".gitignore").read_text()

MARKDOWN = {
    "README.md": (ROOT, ROOT_README),
    "dashboard/README.md": (DASHBOARD, DASH_README),
    "dashboard/docs/DEMO_GUIDE.md": (DASHBOARD / "docs", DEMO),
    "dashboard/docs/GITHUB_READINESS.md": (DASHBOARD / "docs", READINESS),
}


def flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def github_slugs(markdown: str) -> set[str]:
    headings = re.findall(r"^#+ (.+)$", markdown, flags=re.MULTILINE)
    return {re.sub(r"[^\w\- ]", "", h.strip().lower()).replace(" ", "-") for h in headings}


def pins(text: str) -> dict[str, str]:
    return dict(line.strip().split("==") for line in text.splitlines()
                if "==" in line and not line.lstrip().startswith("#"))


# --------------------------------------------------------------------------- #
# Links
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("name", list(MARKDOWN))
def test_relative_links_and_anchors_resolve(name):
    base, text = MARKDOWN[name]
    for link in re.findall(r"\]\(([^)\s]+)\)", text):
        if link.startswith(("http://", "https://", "mailto:")):
            continue
        path, _, anchor = link.partition("#")
        target = (base / path) if path else base / name.split("/")[-1]
        if not path:  # same-file anchor
            target = ROOT / name
        assert target.exists(), (name, link)
        if anchor:
            assert anchor in github_slugs(target.read_text()), (name, link)


def test_root_readme_points_to_the_dashboard_and_its_docs():
    for link in ("(dashboard/)", "(dashboard/README.md)", "(dashboard/docs/DEMO_GUIDE.md)",
                 "(dashboard/docs/GITHUB_READINESS.md)", "(LICENSE)"):
        assert link in ROOT_README, link


# --------------------------------------------------------------------------- #
# Dependencies and setup commands
# --------------------------------------------------------------------------- #
def test_requirements_data_includes_dashboard_pins_and_pm4py():
    includes = re.findall(r"^-r\s+(\S+)$", REQ_DATA, flags=re.MULTILINE)
    assert includes == ["dashboard/requirements.txt"]
    assert (ROOT / includes[0]).is_file()
    own = pins(REQ_DATA)
    assert own == {"pm4py": "2.7.23.12"}
    requirement_lines = [line for line in REQ_DATA.splitlines() if line.strip() and not line.startswith("#")]
    assert not any("[" in line for line in requirement_lines), "no extras such as pm4py[stable]"
    dashboard_pins = pins((DASHBOARD / "requirements.txt").read_text())
    assert not set(own) & set(dashboard_pins), "a package must be pinned in one file only"


def test_setup_commands_use_the_requirement_files_consistently():
    data_cmd = ".venv/bin/python -m pip install -r requirements-data.txt"
    dev_cmd = ".venv/bin/python -m pip install -r dashboard/requirements-dev.txt"
    for text in (ROOT_README, DASH_README):
        assert dev_cmd in text and data_cmd in text
        assert ".venv/bin/python src/ingest_data.py" in text
        assert "pip install pm4py" not in text, "install pm4py through requirements-data.txt"
    assert "pm4py 2.7.23.12" in DASH_README and pins(REQ_DATA)["pm4py"] == "2.7.23.12"
    for cmd in ("../.venv/bin/streamlit run app.py", '../.venv/bin/python -m pytest -W error -m "not integration"'):
        assert cmd in ROOT_README and cmd in DASH_README


# --------------------------------------------------------------------------- #
# Licence and attribution
# --------------------------------------------------------------------------- #
CITATION = "van Dongen, Boudewijn (2017): BPI Challenge 2017. 4TU.ResearchData. Dataset."
DATASET_DOI = "https://doi.org/10.4121/uuid:5f3067df-f10b-45da-b98b-86ae4c7a310b"


def test_license_is_mit_for_code_only():
    text = flat(LICENSE)
    assert "MIT License" in text and "Copyright (c) 2026 Gaurav Solanki" in text
    assert "It does NOT apply to the BPI Challenge 2017 dataset" in text
    assert "including the CSV reports in reports/" in text
    assert "4TU General Terms of Use" in text and "non-commercial" in text
    assert "claims no ownership of the dataset" in text
    assert "van Dongen, Boudewijn (2017): BPI Challenge 2017. 4TU.ResearchData. Dataset." in text


@pytest.mark.parametrize("name", ["README.md", "dashboard/README.md"])
def test_readmes_separate_code_licence_from_dataset_terms(name):
    text = flat(MARKDOWN[name][1])
    assert CITATION in text and DATASET_DOI in text
    assert "4TU General Terms of Use" in text and "non-commercial" in text.lower()
    assert "MIT" in text
    assert "does not cover" in text, "the MIT scope must be stated"
    assert "check the dataset page for the current licence" not in text  # superseded wording


def test_root_readme_states_report_restrictions():
    section = flat(ROOT_README.split("## Dataset and terms of use", 1)[1].split("\n## ", 1)[0])
    for phrase in ("reports/", "derived", "non-commercial", "acknowledg", "not** included"):
        assert phrase in section, phrase


# --------------------------------------------------------------------------- #
# Author details: real name and the owner-supplied links only - nothing invented
# --------------------------------------------------------------------------- #
AUTHOR = "Gaurav Solanki"
GITHUB_PROFILE = "https://github.com/solankigaurav976-stack"
PORTFOLIO = "https://solankigaurav976-stack.github.io/gaurav-portfolio/"
REPOSITORY = f"{GITHUB_PROFILE}/AI-Process-Automation.git"
#: Every personal / profile URL the owner has supplied. Any other github / github.io URL is not allowed.
OWNER_SUPPLIED_URLS = {GITHUB_PROFILE, PORTFOLIO, REPOSITORY}
FORBIDDEN_CONTACTS = r"linkedin\.com|twitter\.com|//x\.com|mailto:|[\w.+-]+@[\w-]+\.[\w.]+"


def test_root_readme_author_and_owner_supplied_links():
    text = ROOT_README
    section = text.split("### Author", 1)[1].split("\n### ", 1)[0]
    assert f"**{AUTHOR}**" in section and f"| **Author** | {AUTHOR} |" in text
    assert f"- **GitHub:** [solankigaurav976-stack]({GITHUB_PROFILE})" in section
    assert f"- **Portfolio:** [View my portfolio]({PORTFOLIO})" in section
    assert f"git clone {REPOSITORY}" in text
    profile_urls = {url for url in re.findall(r"https?://[^\s)>\]]+", text)
                    if re.search(r"github\.com|github\.io", url)}
    assert profile_urls == OWNER_SUPPLIED_URLS, profile_urls ^ OWNER_SUPPLIED_URLS
    assert re.search(FORBIDDEN_CONTACTS, text) is None
    assert "to be added" not in section.lower() and "TODO" not in section


def test_dashboard_readme_author_named_and_links_left_as_placeholders():
    text = DASH_README
    assert AUTHOR in text
    assert re.search(r"github\.com/|github\.io|" + FORBIDDEN_CONTACTS, text) is None
    assert "to be added" in text.lower()


# --------------------------------------------------------------------------- #
# Shared facts and wording
# --------------------------------------------------------------------------- #
def test_root_readme_facts_match_the_validated_dashboard_readme():
    titles = re.findall(r'st\.Page\([^)]*?title="([^"]+)"', (DASHBOARD / "app.py").read_text())
    assert len(titles) == 5 and all(t in ROOT_README for t in titles)
    for number in re.findall(r"\b\d{1,3}(?:,\d{3})+\b", ROOT_README):
        assert number in DASH_README, number
    for fact in ("98 % branch coverage", "1 Feb 2017"):
        assert fact in ROOT_README and fact in DASH_README, fact


def test_root_readme_wording_rules():
    text = ROOT_README
    for word in ("approved", "funded"):
        for match in re.finditer(word, text, flags=re.IGNORECASE):
            assert any(n in text[max(0, match.start() - 60): match.end()].lower() for n in ("not", "never", "no "))
    for match in re.finditer(r"timer", text):
        window = text[max(0, match.start() - 40): match.end() + 40].lower()
        assert "likely" in window or "exploratory" in window
    for match in re.finditer(r"recover\w*", text, flags=re.IGNORECASE):
        assert "not" in text[max(0, match.start() - 40): match.start()].lower()
    assert "the system account" not in text.lower()
    assert "A_Pending (offer accepted)" in text
    for phrase in ("O_Cancelled", "A_Cancelled", "1 February 2017"):
        assert phrase in text


def test_known_repository_limitations_are_documented():
    text = flat(ROOT_README)
    for phrase in ("`src/ src/`", "leading space", "`src/reports/`", "`src/src/`", "empty",
                   "cancellation_completion_delays.csv", "offer_cancellation_followup_patterns.csv",
                   "offer_cancellation_pattern_summary.csv", "no generating script"):
        assert phrase in text, phrase


def test_documented_test_count_matches_the_suite():
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PYTEST_", "COV_"))}
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider"],
                         cwd=DASHBOARD, env=env, capture_output=True, text=True, timeout=300)
    match = re.search(r"(\d+) tests? collected", out.stdout)
    assert match, out.stdout[-2000:] + out.stderr[-2000:]
    count = match.group(1)
    assert f"{count} automated tests" in ROOT_README
    assert f"{count} automated tests" in DASH_README and f"**{count} passed**" in DASH_README
    assert f"# {count} tests" in DASH_README
    assert f"{count} automated tests" in DEMO
    stale = set(re.findall(r"(\d{3}) (?:automated )?tests", ROOT_README + DASH_README + DEMO)) - {count}
    assert not stale, stale


# --------------------------------------------------------------------------- #
# .gitignore against the real project tree (simulated in tmp_path)
# --------------------------------------------------------------------------- #
REQUIRED_RULES = ("data/raw/", "data/processed/", "*.xes", "*.parquet", ".venv/", "__pycache__/",
                  ".pytest_cache/", ".coverage", "**/.streamlit/secrets.toml", ".env", ".DS_Store")


def test_gitignore_contains_the_required_rules():
    rules = {line.strip() for line in GITIGNORE.splitlines() if line.strip() and not line.startswith("#")}
    assert set(REQUIRED_RULES) <= rules
    for broad in ("*.csv", "*.py", "*.md", "*.txt", "*.toml", "reports/", "src/", "dashboard/", "docs/", "tests/"):
        assert broad not in rules, broad


@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_gitignore_against_the_project_tree(tmp_path):
    # Mirror every project file name as an empty placeholder (sizes are irrelevant to ignore rules).
    for dirpath, dirnames, filenames in os.walk(ROOT):
        rel = os.path.relpath(dirpath, ROOT)
        dirnames[:] = [d for d in dirnames if d not in (".venv", ".git")]
        for filename in filenames:
            target = tmp_path / rel / filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch()
    (tmp_path / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / ".venv" / "pyvenv.cfg").touch()
    for extra in ("dashboard/.streamlit/secrets.toml", ".env", "dashboard/stray/copy.parquet", "elsewhere/log.xes"):
        (tmp_path / extra).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / extra).touch()
    (tmp_path / ".gitignore").write_text(GITIGNORE)

    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

    def git(*args: str) -> str:
        return subprocess.run(["git", "-c", "core.excludesFile=", "-c", "core.quotePath=false", *args],
                              cwd=tmp_path, env=env, capture_output=True, text=True, check=True).stdout

    git("init", "-q")
    kept = set(git("ls-files", "--others", "--exclude-standard").splitlines())

    for forbidden in ("data/raw/BPI Challenge 2017.xes", "data/processed/loan_application_events.parquet",
                      ".venv/pyvenv.cfg", "dashboard/.streamlit/secrets.toml", ".env",
                      "dashboard/stray/copy.parquet", "elsewhere/log.xes"):
        assert forbidden not in kept, forbidden
    assert not [p for p in kept if re.search(r"__pycache__|\.pytest_cache|\.coverage|\.DS_Store|\.pyc$", p)]

    project = {
        p.relative_to(ROOT).as_posix()
        for p in ROOT.rglob("*")
        if p.is_file() and not {".venv", ".git", "__pycache__", ".pytest_cache", "data"} & set(p.relative_to(ROOT).parts)
        and p.name not in (".DS_Store", ".coverage")
    }
    assert project <= kept, sorted(project - kept)
    for required in ("README.md", "LICENSE", ".gitignore", "requirements-data.txt", "dashboard/app.py",
                     "dashboard/README.md", "dashboard/requirements.txt", "dashboard/requirements-dev.txt",
                     "dashboard/pytest.ini", "dashboard/.streamlit/config.toml", "src/ingest_data.py",
                     "src/ src/analyze_outcomes.py"):
        assert required in kept, required
    assert len([p for p in kept if p.startswith("reports/") and p.endswith(".csv")]) == 7
