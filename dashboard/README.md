# Loan Application Process Intelligence Dashboard

An interactive Streamlit dashboard that turns a real bank's loan-application event log
(BPI Challenge 2017, 1.2 million events) into process, cancellation and data-quality
insight - with every KPI defined in one place, reconciled against independent reports,
and covered by an automated test suite.

| | |
|---|---|
| **Data** | 31,509 loan applications · 1,202,267 events · 26 activities · 1 Jan 2016 - 1 Feb 2017 |
| **Stack** | Python · pandas · Plotly · Streamlit · PyArrow · pytest |
| **Quality** | 566 automated tests · 98 % branch coverage · 0 warnings · 85/85 reconciliation checks pass |
| **Pages** | Executive Overview · Process Analysis · Cancellation Intelligence · Application Explorer · Data Quality & Reconciliation |
| **Author** | Gaurav Solanki |

---

## Contents
1. [Business problem](#1-business-problem)
2. [Dataset](#2-dataset)
3. [Business questions](#3-business-questions-addressed)
4. [Key findings](#4-key-findings)
5. [Dashboard pages](#5-dashboard-pages)
6. [Data definitions and methodology](#6-data-definitions-and-methodology)
7. [Data limitations and right-censoring](#7-data-limitations-and-right-censoring)
8. [Technology stack](#8-technology-stack)
9. [Architecture and project structure](#9-architecture-and-project-structure)
10. [Installation and setup](#10-installation-and-setup)
11. [Running the dashboard](#11-running-the-dashboard)
12. [Testing and quality assurance](#12-testing-and-quality-assurance)
13. [Limitations and future improvements](#13-project-limitations-and-future-improvements)
14. [Author and licence](#14-author-and-licence)

A presenter's walkthrough is in [docs/DEMO_GUIDE.md](docs/DEMO_GUIDE.md); the repository
audit is in [docs/GITHUB_READINESS.md](docs/GITHUB_READINESS.md).

---

## 1. Business problem

A consumer-loan department wants to know **where applications end up, how long they take,
why so many are cancelled and where time is lost** - but its event log records three
interleaved streams (application states, offers and workflow tasks), many of them written
automatically. Naive analysis of such a log produces misleading answers: the *last*
recorded activity is mistaken for the outcome, offer cancellations are confused with
application cancellations, likely automatic timers look like staff bottlenecks, and
recent months look artificially fast because their applications have not finished yet.

The dashboard answers the business questions while making those traps explicit: every
metric has one documented definition, exploratory indicators are labelled as such, and
all figures reconcile to the project's existing analytical reports.

## 2. Dataset

**BPI Challenge 2017** - the loan-application event log of a Dutch financial institute,
published for the Business Process Intelligence Challenge 2017.

* **Source and citation:** van Dongen, Boudewijn (2017): BPI Challenge 2017. 4TU.ResearchData. Dataset.
  <https://doi.org/10.4121/uuid:5f3067df-f10b-45da-b98b-86ae4c7a310b>
* **Terms of use:** the dataset is published under the
  [4TU General Terms of Use](https://doi.org/10.4121/resource:terms_of_use) (August 2016).
  It must be acknowledged with its creator, Boudewijn van Dongen, and it may be remixed,
  transformed or built upon for non-commercial purposes only. These terms also apply to
  everything derived from it, including the CSV reports in `reports/` and the dashboard's
  outputs. This project does not own the dataset (see
  [section 14](#14-author-and-licence)).
* Raw file: `data/raw/BPI Challenge 2017.xes` (XES, about 552 MB) - kept local, not committed.
* Processed file used by the dashboard: `data/processed/loan_application_events.parquet`
  (about 24 MB), produced from the raw file by `src/ingest_data.py`.

| Event stream | Prefix | Examples |
|---|---|---|
| Application | `A_` | A_Create Application, A_Submitted, A_Pending, A_Cancelled, A_Denied |
| Offer | `O_` | O_Create Offer, O_Sent (mail and online), O_Returned, O_Accepted, O_Cancelled |
| Workflow | `W_` | W_Validate application, W_Call after offers (with lifecycle stages schedule / start / suspend / resume / complete ...) |

Each event carries the application ID, activity, timestamp (UTC), lifecycle stage,
resource (`User_1`, `User_2`, ...), EventID and, for offer events, the OfferID.
Application attributes include loan goal, application type and requested amount;
offer-creation events carry CreditScore, OfferedAmount, MonthlyCost, NumberOfTerms,
FirstWithdrawalAmount, Accepted and Selected.

## 3. Business questions addressed

1. What final status do applications reach, and what share remain open?
2. How long do applications take, and how does this change by start month?
3. How many applications receive, return and accept an offer?
4. How common are offer cancellations versus application cancellations, and how are they related?
5. How many application cancellations look like an automatic expiry rather than a decision?
6. What happens between an offer cancellation and a later application cancellation?
7. Where does time pass between recorded events, and which long waits are likely system timers?
8. What exactly happened in one specific application?
9. Do the dashboard's figures agree with the existing analytical reports?

## 4. Key findings

All figures are recalculated from the event log for the full dataset and reconciled
against the reports in `reports/`.

**Final status**

| Final status | Applications | Share |
|---|---:|---:|
| A_Pending (offer accepted) | 17,228 | 54.7 % |
| A_Cancelled (application cancelled) | 10,431 | 33.1 % |
| A_Denied (application denied) | 3,752 | 11.9 % |
| No final status recorded (open on 1 Feb 2017) | 98 | 0.3 % |

* **A_Pending means the customer accepted an offer.** The log does not show whether the
  loan was approved or paid out, so it is never labelled "approved" or "funded".
* **Processing time:** median 19.1 days from first to last event (90th percentile 35.1 days);
  median time to the final-status event 19.1 days for the 31,411 applications that reached one.

**Offers**

* 42,995 offers created, 41,733 sent, 23,305 returned (55.8 % return rate);
  outcomes: 17,228 accepted, 4,695 refused, 20,898 cancelled, 174 without an outcome.
* 31,362 applications had at least one offer sent (147 none); 21,768 had an offer returned,
  while 9,594 had offers sent but none returned.

**Two different cancellations**

* **O_Cancelled (an offer is withdrawn):** 20,898 events in 15,682 applications (49.8 %).
  An offer cancellation does not end the application: 5,050 of those applications still
  reached A_Pending (offer accepted), 10,431 ended A_Cancelled, 195 A_Denied, 6 are open.
* **A_Cancelled (the whole application is cancelled):** 10,431 applications. In 9,855
  (94.5 %) the application was cancelled before any offer cancellation; in 576 (5.5 %) an
  offer was cancelled first.
* **Likely automatic expiry (exploratory):** 7,932 of the 10,431 A_Cancelled events
  (76 %) are recorded by User_1 (likely, but not confirmed, a system account) at least
  30 days after the last offer was sent - consistent with an automatic expiry rule.
* **Offer-first follow-up (576 applications):** replacement offer created 230 (39.9 %),
  workflow activity only 102, offer sent without a new offer 85, no recorded intermediate
  activity 126, other activity only 33. **All 576 still ended A_Cancelled - this is not
  customer recovery.**

**Waiting time between recorded events**

* The two longest median waits end in A_Cancelled and are **likely timer-driven
  (exploratory)**: O_Sent (mail and online) → A_Cancelled (median 736 h ≈ 31 days) and
  W_Call after offers → A_Cancelled (641 h ≈ 27 days). They should not be read as staff
  performance problems.
* Excluding those, the longest median waits are 68-92 hours, for example
  O_Sent (online only) → W_Call after offers (91.6 h) and O_Sent (mail and online) →
  W_Call after offers (72.6 h). These are gaps between consecutive recorded events in
  parallel streams - indicators of where time passes, not confirmed staff bottlenecks.

**Data quality**

* CreditScore is 0 on 27,735 of 42,995 offers (64.5 %); zero is treated as potentially
  missing and excluded from averages (median non-zero score 904).
* `reports/application_outcomes.csv` counts each application's **last recorded activity**,
  which is usually a workflow or offer event - it is not the final application status.
* Reconciliation: **85 checks PASS, 0 FAIL**, plus 7 documented informational findings.

## 5. Dashboard pages

Global filters in the sidebar (start month, loan goal, application type, final status)
apply to every page except Data Quality. Chart clicks either set a global filter (on the
Executive page) or make a page-level selection; each chart says which in its caption, and
active filters appear as removable chips.

| Page | What it shows | Interactions |
|---|---|---|
| **Executive Overview** | Headline KPIs; final status distribution; applications by start month stacked by final status (censored months faded); median / 90th-percentile duration by start month | Click a status bar or a month to filter every page; click again to clear |
| **Process Analysis** | Sankey from offer milestone to final status (with table view); transition matrix of all 178 activity pairs coloured by median wait or count; 15 longest median waits; waiting-time drill-down with histogram and every occurrence; activity frequency (all lifecycle stages / completed only) | Click a matrix cell or ranked bar to drill down; click an activity to highlight it in the matrix |
| **Cancellation Intelligence** | O_Cancelled and A_Cancelled side by side; final status of applications with an offer cancellation; cancellation sequence; likely automatic expiry histogram (exploratory); five offer-first follow-up patterns and their gap distribution; filterable case table | Click a status, sequence, pattern or point to filter the case table; open an application in the Explorer (global filters kept) |
| **Application Explorer** | Search or select an application; summary; event timeline in Application / Offer / Workflow lanes with offer lifelines and the final-status event; complete event table in original order, as recorded | Click an event to select its table row (and vice versa); zoom and pan the timeline; download the events |
| **Data Quality & Reconciliation** | Reconciliation results (PASS / FAIL / ERROR) kept separate from informational findings; final status versus last recorded activity; CreditScore = 0; report availability | Filter checks by report or failures; download checks and findings |

Every table can be downloaded as CSV; downloads contain exactly the rows shown under the
current filters and selections.

## 6. Data definitions and methodology

All KPI names, definitions, calculations, sources and caveats live in one registry,
[`core/definitions.py`](core/definitions.py) (`METRICS`, 31 entries); the dashboard's
tooltips and tests read from it, so a definition cannot drift between pages.

| Concept | Definition used |
|---|---|
| **Final status** | Activity of the application's *last* A_Pending / A_Cancelled / A_Denied event. Applications without one are reported as "No final status recorded" (98) - never dropped. |
| **A_Pending** | "A_Pending (offer accepted)". Not approved, not funded. |
| **O_Cancelled vs A_Cancelled** | O_Cancelled withdraws one offer; A_Cancelled cancels the application. Always counted and shown separately. |
| **Duration** | First to last recorded event (matches `application_performance.csv`). Time to final status is a separate measure. |
| **Event order** | By application, then timestamp; ties keep the original file order. |
| **Transition / wait** | An event and the next recorded event of the same application; wait = time between them. Streams interleave, so a transition is the order of recorded events, not necessarily a hand-off. Pairs with ≥ 100 occurrences are ranked, as in `process_bottlenecks.csv`. |
| **Likely timer-driven (exploratory)** | A pair whose target events are ≥ 80 % recorded by User_1 and whose median wait is ≥ 24 h. |
| **Likely automatic expiry (exploratory)** | A_Cancelled recorded by User_1 ≥ 30 days after the last O_Sent. |
| **User_1** | "Likely, but not confirmed, a system account". Resources are otherwise shown as recorded; no roles are inferred. |
| **Offer-first cancellation** | First O_Cancelled strictly before A_Cancelled; activity strictly between them is classified into five patterns (report order). Not recovery. |
| **Workflow events** | Shown as individual events. Lifecycle stages (schedule, start, suspend, resume, complete ...) cannot be paired reliably, so **no task durations are calculated**. |
| **CreditScore = 0** | Treated as potentially missing; excluded from averages and percentiles and reported as a count. |
| **Dataset end** | Fixed at 1 February 2017 for all censoring calculations. |

**Reconciliation.** [`core/reconciliation.py`](core/reconciliation.py) recalculates each of
the seven CSV reports from the event log and compares them value by value (exact, or exact
after the report's rounding): 85 checks pass and 7 rows are informational findings (for
example, that `application_outcomes.csv` counts last activities rather than final statuses).

## 7. Data limitations and right-censoring

* **The log ends on 1 February 2017.** Applications still open then have no final status,
  and their durations stop at the last recorded event.
* **October, November and December 2016 are incomplete start months.** They contain open
  applications (1, 11 and 86) and/or less than 60 days of observation, so they look faster
  and less cancelled than they will eventually be. The dashboard fades them, labels them
  and repeats the warning wherever monthly trends appear.
* **Interleaved streams.** Application, offer and workflow events are recorded in
  parallel; consecutive-event waits are not task or hand-off durations.
* **System-recorded events.** Many events are recorded by User_1; its nature is not
  documented, so timer and expiry indicators are exploratory ("likely").
* **No loan decision or payout data.** A_Pending marks offer acceptance only.
* **CreditScore** is 0 on most offers and cannot be relied on for credit analysis.

## 8. Technology stack

| Purpose | Tools (versions tested) |
|---|---|
| Language | Python 3.14 |
| Data processing | pandas 3.1.1, NumPy 2.5.4, PyArrow 26.0.1 |
| Visualisation | Plotly 7.1.0 |
| Dashboard | Streamlit 1.67.0 (multipage `st.navigation`, cached data, chart selections) |
| Testing | pytest 9.2.0, pytest-cov 7.1.0, Streamlit AppTest |
| Data preparation (`src/`) | pm4py 2.7.23.12 (XES import only, from `requirements-data.txt`) |

## 9. Architecture and project structure

```
AI-Process-Automation/
├── data/                     # local only (not committed) - read-only to the dashboard
│   ├── raw/BPI Challenge 2017.xes
│   └── processed/loan_application_events.parquet
├── reports/                  # existing analytical reports (CSV) - read-only
├── src/                      # original data-preparation and analysis scripts - read-only
├── requirements-data.txt     # data-preparation dependencies (dashboard pins + pm4py)
├── LICENSE                   # MIT for the project's code; not the dataset or reports
├── README.md                 # repository overview
└── dashboard/
    ├── app.py                # entry point: navigation, sidebar filters, error handling
    ├── core/                 # analytical layer (no Streamlit)
    │   ├── config.py         #   paths, activity names, final-status labels
    │   ├── definitions.py    #   METRICS registry and standard warning texts
    │   ├── loaders.py        #   read-only, schema-validated loading
    │   ├── schemas.py        #   expected columns and types
    │   ├── metrics.py        #   all KPI and table calculations
    │   ├── drilldown.py      #   individual transition waits
    │   ├── reconciliation.py #   report-by-report reconciliation
    │   └── errors.py         #   typed, user-readable errors
    ├── ui/                   # presentation layer
    │   ├── data.py           #   the only bridge to core; Streamlit caching
    │   ├── filters.py        #   global filter state (single source of truth)
    │   ├── selection.py      #   chart / table selections and page hand-offs
    │   ├── aggregations.py   #   shaping core outputs for charts and tables
    │   ├── charts.py         #   Plotly figure builders
    │   ├── components.py     #   KPI cards, notices, tables, downloads
    │   ├── theme.py          #   colour tokens (light and dark), chart style
    │   └── formatting.py     #   number and date formatting
    ├── views/                # one file per page
    ├── tests/                # 566 tests (unit, integration, AppTest UI, documentation)
    ├── docs/                 # demo guide and repository audit
    ├── .streamlit/config.toml
    ├── requirements.txt
    └── requirements-dev.txt
```

**Design principles**

* **Layered:** pages never calculate metrics; they call `ui/data.py`, which calls `core`.
* **One definition per KPI:** labels, tooltips and tests all read `core.definitions.METRICS`.
* **Read-only by construction:** loaders open source files read-only (with path and
  symlink checks); tests fingerprint `data/`, `reports/` and `src/` before and after.
* **Safe caching:** the event log is loaded once (`st.cache_resource`) and never handed to
  pages; every result a page receives is a copy (`st.cache_data`), keyed by filter state.
* **One source of truth for filters:** chart clicks write the sidebar filter keys, so every
  page reads the same state; page selections are separate keys that never change filters.

## 10. Installation and setup

Commands are for macOS / Linux from the project root (`AI-Process-Automation/`); on
Windows use `.venv\Scripts\python` instead of `.venv/bin/python`.

```bash
# 1. Create a virtual environment (Python 3.14 tested)
python3 -m venv .venv

# 2. Install the dashboard and test dependencies
.venv/bin/python -m pip install -r dashboard/requirements-dev.txt
```

**Data.** The dashboard reads `data/processed/loan_application_events.parquet`. If you do
not have it:

```bash
# 3. Download the log from the DOI above and save it as "data/raw/BPI Challenge 2017.xes"
# 4. Install the XES reader (only needed for this step) and build the Parquet file
.venv/bin/python -m pip install -r requirements-data.txt
.venv/bin/python src/ingest_data.py
```

`requirements-data.txt` (project root) includes `dashboard/requirements.txt` and pins
`pm4py==2.7.23.12`. Do not install the `pm4py[stable]` extra: it pins pandas 3.1.0, which
conflicts with the dashboard's pandas 3.1.1.

The four analysis pages need only the Parquet file. The Data Quality page also reads the
seven CSV files in `reports/`; if any is missing it says so and reconciles the rest.

## 11. Running the dashboard

```bash
cd dashboard
../.venv/bin/streamlit run app.py
```

Streamlit serves the app at <http://localhost:8501> (bound to this machine only, see
`.streamlit/config.toml`). The first page load reads the 1.2 million events (a few
seconds); later interactions use cached results.

## 12. Testing and quality assurance

```bash
cd dashboard
../.venv/bin/python -m pytest -W error                     # complete suite, warnings as errors
../.venv/bin/python -m pytest -W error -m "not integration"  # fast unit tests only
../.venv/bin/python -m pytest -W error --cov=core --cov=ui --cov=views --cov-branch
```

| Result (latest run) | |
|---|---|
| Tests | **566 passed**, 0 failed, 0 skipped |
| Warnings | 0 (run with `-W error`) |
| Branch coverage | 98 % |
| Reconciliation | 85 PASS · 0 FAIL · 7 INFO |
| Protected folders | `data/`, `reports/`, `src/` byte-identical before and after the suite |

What the suite covers:

* **Metrics** - every calculation on small hand-built logs (ties, missing values, open
  applications) and on the real data.
* **Reconciliation** - each report, value by value.
* **UI** - every page through Streamlit AppTest: KPIs equal core metrics, chart clicks
  (through the real callbacks), cross-page filters and selections, clearing, stale and
  invalid selections, empty results, downloads (rows, columns, filters) and wording
  (no "approved"/"funded", "likely" on exploratory indicators).
* **Safety** - read-only guards (path traversal, symlinks, hard links), cached data cannot
  be mutated by pages, protected-file fingerprints.
* **Documentation** - every number quoted in this README and the demo guide is
  recalculated from the data. Links, setup commands, dependency pins, the licence scope,
  the `.gitignore` rules (on a copy of the project tree) and the documented test count are
  also checked.

Integration and UI tests need the processed Parquet file and the reports.

## 13. Project limitations and future improvements

**Limitations**

* Browser-level clicks are verified manually; automated tests drive the same callbacks
  through Streamlit AppTest.
* Sankey diagrams cannot be clicked in Streamlit (hover and table view only).
* Streamlit cannot scroll a table to a selected row; the selected event is also summarised
  above the table.
* Dense bursts of events on the timeline overlap until zoomed in.
* Results are cached per filter combination without a size limit (fine for a session or demo).
* The first drill-down of a transition pair and the first opened application each take
  about 2-3 seconds.
* Three reports (`cancellation_completion_delays.csv`, `offer_cancellation_followup_patterns.csv`,
  `offer_cancellation_pattern_summary.csv`) have no generating script in `src/`; the
  dashboard reconciles them but cannot regenerate them.
* `src/` has some unusual folders, kept as-is because `src/` is read-only:
  * `src/ src/` (with a leading space) holds `analyze_outcomes.py`;
  * `src/reports/` and the nested `src/src/` folders are empty, so Git will not track them.

**Possible improvements**

* Confirm the nature of User_1 and the expiry rule with the process owner, then promote
  the exploratory indicators to confirmed metrics.
* Add conformance checking against a reference process model.
* Survival analysis (e.g. Kaplan-Meier) to handle right-censored durations formally.
* Add generating scripts for the three reports that lack one.
* Containerised deployment with authentication if shared beyond a single machine.

## 14. Author and licence

**Author:** Gaurav Solanki

- **GitHub:** [solankigaurav976-stack](https://github.com/solankigaurav976-stack)
- **Portfolio:** [View my portfolio](https://solankigaurav976-stack.github.io/gaurav-portfolio/)

* **Code:** the project's original source code and documentation are released under the
  [MIT License](../LICENSE).
* **Data:** the MIT licence **does not cover** the BPI Challenge 2017 dataset or anything
  derived from it, including the reports in `reports/` and the dashboard's outputs. These
  remain under the 4TU General Terms of Use: non-commercial use only, with acknowledgement
  of the source and its creator. See [section 2](#2-dataset).
