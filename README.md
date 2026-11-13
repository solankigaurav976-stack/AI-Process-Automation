# AI-Process-Automation: Loan Application Process Intelligence

Process-mining analysis of a real bank's loan-application event log (BPI Challenge 2017),
delivered as an interactive Streamlit dashboard.

> **The dashboard is in [`dashboard/`](dashboard/).**
> Its full documentation is [`dashboard/README.md`](dashboard/README.md). That file covers
> the business questions, key findings, metric definitions, right-censoring, architecture
> and test results.

| | |
|---|---|
| **Data** | 31,509 loan applications · 1,202,267 events · 1 Jan 2016 - 1 Feb 2017 |
| **Dashboard pages** | Executive Overview · Process Analysis · Cancellation Intelligence · Application Explorer · Data Quality & Reconciliation |
| **Stack** | Python · pandas · Plotly · Streamlit · PyArrow · pytest |
| **Quality** | 566 automated tests · 98 % branch coverage · 85 reconciliation checks pass, 0 fail |
| **Author** | Gaurav Solanki |

## What the project does

The event log interleaves three streams: application states, offers and workflow tasks.
Many of its events are written automatically. The dashboard answers several questions
about these applications:

* where they end up,
* how long they take,
* why so many are cancelled,
* where time passes between recorded events.

It also avoids the usual traps:

* **Final status vs last event.** The final status is not confused with the last recorded
  event.
* **A_Pending (offer accepted)** is not presented as an approved or funded loan.
* **Two kinds of cancellation.** Offer cancellations (O_Cancelled) are kept apart from
  application cancellations (A_Cancelled).
* **Likely automatic timers** are labelled as exploratory rather than shown as staff delays.
* **Incomplete recent months.** The data ends on 1 February 2017, so October to December
  2016 are flagged as incomplete.

Every KPI has one definition in a central registry. Every figure is reconciled against
the analytical reports in `reports/`.

## Documentation

| Document | Purpose |
|---|---|
| [dashboard/README.md](dashboard/README.md) | Full project documentation: findings, definitions, limitations, architecture, setup, testing |
| [dashboard/docs/DEMO_GUIDE.md](dashboard/docs/DEMO_GUIDE.md) | How to present the dashboard: page order, KPIs, a 3-5 minute script, interview questions |
| [dashboard/docs/GITHUB_READINESS.md](dashboard/docs/GITHUB_READINESS.md) | Repository audit: secrets, large files, what is and is not committed |
| [LICENSE](LICENSE) | MIT licence for the project's own code, and its scope |

## Repository structure

```
AI-Process-Automation/
├── dashboard/              # the Streamlit application, its tests and documentation  <- start here
├── reports/                # seven analytical reports (CSV) derived from the dataset
├── src/                    # original data-preparation and analysis scripts
├── data/                   # local only, not in the repository (see "Dataset" below)
│   ├── raw/BPI Challenge 2017.xes
│   └── processed/loan_application_events.parquet
├── requirements-data.txt   # data-preparation dependencies (dashboard pins + pm4py)
├── LICENSE
└── README.md
```

## Quick start

Run these commands from the project root (`AI-Process-Automation/`) on macOS or Linux. On
Windows, use `.venv\Scripts\python` instead of `.venv/bin/python`. Python 3.14 is tested.
More detail is in [dashboard/README.md, sections 10-12](dashboard/README.md#10-installation-and-setup).

**1. Set up the environment** (dashboard and tests)

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r dashboard/requirements-dev.txt
```

**2. Prepare the data.** The repository does not contain the data. The dashboard reads
`data/processed/loan_application_events.parquet`, which you build from the raw log:

```bash
# Download the log from the DOI below and save it as "data/raw/BPI Challenge 2017.xes", then:
.venv/bin/python -m pip install -r requirements-data.txt   # adds pm4py, the XES reader
.venv/bin/python src/ingest_data.py
```

[`requirements-data.txt`](requirements-data.txt) includes the dashboard's pinned packages
and pins `pm4py==2.7.23.12`. It is needed only for this step.

**3. Run the dashboard**

```bash
cd dashboard
../.venv/bin/streamlit run app.py
```

The app opens at <http://localhost:8501> and is bound to this machine only.

**4. Run the tests** (from `dashboard/`)

```bash
../.venv/bin/python -m pytest -W error                       # complete suite
../.venv/bin/python -m pytest -W error -m "not integration"  # fast unit tests only
```

The integration and UI tests need the processed Parquet file and the reports in `reports/`.

## Dataset and terms of use

**BPI Challenge 2017**: the loan-application event log of a Dutch financial institute.
The dataset was created by Boudewijn van Dongen and is published by 4TU.ResearchData. This
project does not own it.

* **Citation:** van Dongen, Boudewijn (2017): BPI Challenge 2017. 4TU.ResearchData. Dataset.
  <https://doi.org/10.4121/uuid:5f3067df-f10b-45da-b98b-86ae4c7a310b>
* **Terms:** the dataset is published under the
  [4TU General Terms of Use](https://doi.org/10.4121/resource:terms_of_use) (August 2016).
  In summary:
  * acknowledge the source and its rights-holder (the creator named above);
  * the data may be remixed, transformed or built upon **for non-commercial purposes
    only**;
  * anyone who distributes the data must also require onward distributors to keep this
    acknowledgement.

  Read the full terms before reusing the data.
* **What this repository contains:**
  * The raw and processed event logs are **not** included. Download them from the DOI
    above.
  * The seven CSV files in `reports/` and the dashboard's outputs (charts, tables, CSV
    downloads) are **derived** from the dataset. They are covered by the 4TU General
    Terms of Use, not by this project's MIT licence: use them for **non-commercial
    purposes only**, and keep the acknowledgement of the source and its creator
    (the citation above) whenever you share them.

## Known repository limitations

* **Unusual folder names in `src/`** (kept as-is, because `src/` is treated as read-only):
  * `src/ src/` has a leading space in its name. It holds `analyze_outcomes.py`.
  * `src/reports/` and the nested `src/src/` folders are empty. Git does not track empty
    folders, so they will not appear in the repository.
* **Reports with no generating script:** three reports in `reports/` have no generating
  script in `src/`. The dashboard reconciles them but cannot regenerate them:
  * `cancellation_completion_delays.csv`
  * `offer_cancellation_followup_patterns.csv`
  * `offer_cancellation_pattern_summary.csv`
* **Data download:** reproducing the processed data requires downloading the raw log
  (about 552 MB) separately.

## Author

**Gaurav Solanki**

<!-- TODO (owner): add the profile links you want to show (LinkedIn / GitHub / portfolio). -->
*Profile links to be added.*

## Licence

* **Code:** the project's original source code and documentation are released under the
  [MIT License](LICENSE). This covers the dashboard, its tests and documentation, and the
  scripts in `src/`.
* **Data:** the MIT licence **does not cover** the BPI Challenge 2017 dataset or anything
  derived from it, including the reports in `reports/`. That data remains under the
  4TU General Terms of Use described above.
