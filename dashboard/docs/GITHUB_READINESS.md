# GitHub Readiness Audit

Audit of `AI-Process-Automation/` before it is put under version control. Nothing has
been committed, published, deleted or moved. Initialising Git needs the owner's approval.

## Current state

| Item | Finding |
|---|---|
| Git repository | **None yet** (not initialised, by decision) |
| `.gitignore` | **Created at the project root** ([`/.gitignore`](../../.gitignore)). It is based on the reviewed [`proposed_root.gitignore`](proposed_root.gitignore), with two extra rules, `*.xes` and `*.parquet`, so a copy of the event log anywhere in the tree is also excluded |
| README | **Root [`README.md`](../../README.md) created.** It introduces the project, points to `dashboard/` and gives the quick start. The full documentation stays in [`dashboard/README.md`](../README.md) |
| Licence | **[`LICENSE`](../../LICENSE): MIT for the project's own code and documentation only.** It states that the BPI Challenge 2017 dataset and all derived data (including `reports/`) are excluded and remain under the 4TU General Terms of Use |
| Data-preparation dependencies | **[`requirements-data.txt`](../../requirements-data.txt)** includes `dashboard/requirements.txt` and pins `pm4py==2.7.23.12` |
| Secrets / credentials | **None found.** No `.env`, `secrets.toml`, key or certificate files; no API keys, tokens or passwords in code or configuration |
| Personal information | **None** beyond the author's name, which the owner chose to show. No e-mail addresses, home paths or invented profile links. The dataset's resources are anonymised IDs (`User_1` ...) |
| Streamlit config | Usage statistics disabled; server bound to `localhost`; no secrets |

## Files excluded by `.gitignore`

| Path | Size | Reason |
|---|---:|---|
| `data/raw/BPI Challenge 2017.xes` | ~552 MB | Above GitHub's 100 MB file limit; downloadable from its DOI |
| `data/processed/loan_application_events.parquet` | ~24 MB | Derived from the raw file; regenerated with `src/ingest_data.py` |
| `.venv/` | ~724 MB | Local environment |
| `__pycache__/`, `.pytest_cache/`, `dashboard/.coverage` | small | Generated |
| `.DS_Store` (root, `data/`, `src/`, `dashboard/`) | small | macOS metadata |
| `.env`, `.streamlit/secrets.toml` (none exist) | - | Never commit secrets |

**Dry run (rechecked in Phase 6.2).** `git add -A` was run on an empty-file copy of the
current tree, using the root `.gitignore`, outside the project. It stages **72 files**
(about 4.1 MB):

* the root `README.md`, `LICENSE`, `.gitignore` and `requirements-data.txt`;
* the dashboard code, its 25 test files and its documentation;
* the seven `reports/*.csv` (largest 3.2 MB);
* the four `src/` scripts.

It stages no data, environment, cache, secret or OS files. Earlier counts were lower
because files were added since: 66 in Phase 6, then 69 with the root README and two
Phase 6 files, then 72 with `LICENSE`, `requirements-data.txt` and
`tests/test_root_readme.py`.

The suite repeats this check automatically (`tests/test_root_readme.py`): the data,
environment and secret files must be ignored, and every project file must stay included.

## Dataset terms and derived reports

The official 4TU.ResearchData record names the licence as the
[4TU General Terms of Use](https://doi.org/10.4121/resource:terms_of_use) (August 2016).
Read from the terms document itself:

* **Art. 1, 4a - acknowledge the source.** Name the rights-holder (the creator, Boudewijn
  van Dongen). Anyone the data is passed to must be required to keep this acknowledgement.
* **Art. 4b - non-commercial only.** The data may be remixed, transformed or built upon
  only for non-commercial purposes.
* **Art. 3 - report publications.** Publications that contain or are based on the data
  must be reported to 4TU.ResearchData.

The seven reports in `reports/` are derived data. They are kept in the repository because
both READMEs and `LICENSE` state their non-commercial restriction and the required
attribution. Their contents are unchanged.

## Known repository limitations (documented, not changed)

1. **Unusual folders in `src/`.** `src/` is read-only, so these are documented rather than
   renamed or deleted.
   * `src/ src/` has a leading space in its name and holds `analyze_outcomes.py`. It works,
     but it is awkward on GitHub and on the command line.
   * `src/reports/` and the nested `src/src/` folders are empty. Git does not track empty
     folders, so they will not appear in the repository.
2. **Reports without a generating script.** `cancellation_completion_delays.csv`,
   `offer_cancellation_followup_patterns.csv` and `offer_cancellation_pattern_summary.csv`
   have no script in `src/`. The dashboard reconciles them but cannot regenerate them.

## Owner decisions

| Decision | Status |
|---|---|
| Root README and `.gitignore` | Done (Phase 6.1) |
| `pm4py` dependency | Done: `requirements-data.txt` (Phase 6.2) |
| Dataset licence | Verified and documented (4TU General Terms of Use) |
| Code licence | MIT, code only (Phase 6.2) |
| Author | Gaurav Solanki; **profile links still to be supplied** (placeholders in both READMEs) |
| Folder names in `src/` | Kept as-is; documented as a limitation |
| Initialise Git and first commit | **Awaiting approval** |

## Setup instructions - verification

| Step in the README | Verified |
|---|---|
| Pinned versions in `requirements*.txt` | Match the installed, tested environment exactly (streamlit 1.67.0, pandas 3.1.1, numpy 2.5.4, pyarrow 26.0.1, plotly 7.1.0, pytest 9.2.0, pytest-cov 7.1.0, pm4py 2.7.23.12; Python 3.14.6); `pip check` reports no conflicts |
| `requirements-data.txt` | Its `-r dashboard/requirements.txt` path resolves from the project root; pm4py is pinned once, with no extras |
| `src/ingest_data.py` paths | Resolves `data/raw/BPI Challenge 2017.xes` and `data/processed/` from its own location, so it runs from any working directory |
| `cd dashboard && ../.venv/bin/streamlit run app.py` | Starts; health endpoint returns `ok` (checked on a spare port in Phase 6, then stopped) |
| Test commands | Complete suite and the `-m "not integration"` subset both run from `dashboard/` |
| Not verified | A fresh `pip install` into a new environment (it would download packages), and the raw XES conversion (unchanged original script) |

## Suggested first commit (after approval)

```bash
git init
git add -A
git status            # confirm: no data/, .venv/, caches, .DS_Store or secrets
git commit -m "Loan application process intelligence dashboard"
```
