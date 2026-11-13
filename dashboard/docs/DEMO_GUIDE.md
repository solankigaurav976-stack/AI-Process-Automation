# Portfolio Demonstration Guide

How to present the Loan Application Process Intelligence Dashboard in an interview or
portfolio review. All numbers are for the full dataset with no filters applied.

**Before you start**

* Run `../.venv/bin/streamlit run app.py` from `dashboard/` and open every page once, so
  the first-load calculations (a few seconds each) are cached before the audience arrives.
* Reset filters (sidebar → **Reset filters**). Keep the browser at about 1400 px wide.
* Have one application ID ready: `Application_1000334415` (A_Denied, 40 events, one offer).

---

## 1. Recommended demonstration order

| # | Page | Purpose in the story |
|---|---|---|
| 1 | Executive Overview | The outcome and timing picture - and the first two traps (A_Pending, censoring) |
| 2 | Cancellation Intelligence | The core business finding: two kinds of cancellation and a likely expiry rule |
| 3 | Process Analysis | Where time passes - and why the longest waits are probably timers, not staff |
| 4 | Application Explorer | Proof at the level of one application |
| 5 | Data Quality & Reconciliation | Why the numbers can be trusted |

## 2. KPIs and findings to explain

| KPI / finding | Value | What to say |
|---|---|---|
| Applications / events | 31,509 / 1,202,267 | Jan 2016 - 1 Feb 2017, three interleaved event streams |
| A_Pending (offer accepted) | 17,228 (54.7 %) | Offer accepted - the log has no approval or payout, so I never call it "approved" or "funded" |
| A_Cancelled | 10,431 (33.1 %) | One in three applications is cancelled |
| A_Denied | 3,752 (11.9 %) | |
| No final status recorded | 98 (0.3 %) | Still open when the data ends; kept visible, never dropped |
| Median duration | 19.1 days (P90 35.1) | First to last recorded event |
| Offer return rate | 55.8 % | 23,305 of 41,733 sent offers returned |
| O_Cancelled | 20,898 events in 15,682 applications | Offer withdrawn - 5,050 of these applications still reached A_Pending |
| A_Cancelled before any O_Cancelled | 9,855 (94.5 %) | The application is usually cancelled first, then its offers |
| Likely automatic expiry (exploratory) | 7,932 of 10,431 | User_1 (likely, but not confirmed, a system account), ≥ 30 days after the last offer was sent |
| Offer-first cancellations | 576 | All still ended A_Cancelled - not customer recovery |
| Longest median waits | 736 h and 641 h, both ending in A_Cancelled | Likely timer-driven (exploratory) - not staff performance |
| Reconciliation | 85 PASS · 0 FAIL · 7 INFO | Every figure agrees with the existing reports |

## 3. Interactive features to show

1. **Executive** - click the *A_Cancelled* bar: every page is now filtered; the chip at the
   top removes it. Click a month bar (e.g. Jun 2016) to filter by start month; click again
   to clear. Point out the faded, labelled Oct-Dec 2016 bars.
2. **Cancellation** - click *Replacement offer created* in the patterns chart: the gap chart
   and case table follow. Click a point, then **Open in Application Explorer** - the
   global filters stay in place.
3. **Process** - switch the matrix between *Median wait* and *Transition count*; click the
   W_Call after offers → A_Cancelled cell to open its drill-down (count, median, P90 and the
   histogram of every individual wait). Toggle activity frequency between *All lifecycle
   stages* and *Completed only*.
4. **Explorer** - search `Application_1000334415`; hover events on the timeline, drag to zoom,
   click an event to select its row in the event table; download the events.
5. **Data Quality** - tick *Show only failed checks* (none appear), then show the side-by-side
   comparison of final status versus last recorded activity.
6. **Hover any KPI** - the tooltip shows the definition, calculation, source and caveats
   from the central registry.

## 4. Suggested 3-5 minute presentation

**0:00 - Problem (30 s).** "This is a Dutch bank's loan-application log - 31,500
applications and 1.2 million events from three systems recorded in parallel. The business
wants to know outcomes, timing and why so many applications are cancelled. The challenge
is that the obvious analysis gives wrong answers."

**0:30 - Executive (60 s).** "54.7 % reach A_Pending - which means the customer accepted
an offer, not that the loan was approved or paid. A third are cancelled. The median
application takes 19 days. Note these last three months: the data ends on 1 February 2017,
so they still contain open applications and look faster than they really are - the
dashboard fades and labels them instead of letting them distort the trend." *(Click
A_Cancelled to show that it filters everything.)*

**1:30 - Cancellation (75 s).** "There are two different cancellations. O_Cancelled
withdraws one offer - half of all applications have one, and 5,050 of those still went on
to accept an offer. A_Cancelled cancels the whole application. In 94.5 % of cases the
application is cancelled first. About three quarters of application cancellations are
recorded by User_1 at least 30 days after the last offer was sent, which is consistent
with an automatic expiry rule - I label that as likely and exploratory, because User_1 is
not confirmed to be a system account. For the 576 applications where an offer was
cancelled first, even those that received a replacement offer still ended cancelled - so
this is not recovery."

**2:45 - Process (45 s).** "The matrix shows the waiting time between consecutive recorded
events. The two longest waits - around 27 and 31 days - both end in A_Cancelled and match
the expiry pattern, so they are likely timers, not staff bottlenecks. The waits that remain
are about 3-4 days, mostly around the 'call after offers' follow-up step. These are gaps
between recorded events in parallel streams, so they show where time passes, not proven
staff bottlenecks."

**3:30 - Explorer and trust (60 s).** "Here is one application end to end - every event in
its lane, the offer's life from creation to outcome, and the final-status event. Workflow
tasks are shown as individual events, because their lifecycle stages can't be paired into
reliable durations. Finally, every number reconciles: 85 checks against the existing
reports, all passing, backed by 566 automated tests."

## 5. Questions an interviewer may ask

**Business**

| Question | Answer |
|---|---|
| Why not call A_Pending "approved"? | The log records offer acceptance only; approval and payout are not recorded, so that label would overstate the outcome. |
| What would you recommend to the business? | Confirm the expiry rule with the process owner; if the likely expiry rule (76 % of A_Cancelled, exploratory) is confirmed, the lever is customer follow-up before day 30, not faster staff handling. Investigate why 9,594 applications had offers sent but none returned. |
| Why are recent months different? | Right-censoring: they were observed for less time and still contain open applications. |
| Does a replacement offer mean the application was saved? | No - all 576 offer-first applications still ended A_Cancelled, so this is not recovery. |
| What is the difference between the last activity and the final status? | The last recorded event is usually a workflow or offer event written after the decision; the final status is the last A_Pending / A_Cancelled / A_Denied event. |

**Technical**

| Question | Answer |
|---|---|
| How do you keep KPI definitions consistent? | One registry (`core/definitions.py`); KPI cards, tooltips and tests read from it, and a test fails if a page hard-codes a label. |
| How do you know the numbers are right? | Independent reconciliation against seven existing reports (85 checks), plus unit tests on hand-built logs with known answers. |
| How does cross-filtering work in Streamlit? | Chart clicks run callbacks that write the sidebar filter keys - one source of truth. Chart keys embed the current selection so stale selections cannot linger. |
| How do you handle 1.2 million rows? | Load once with `st.cache_resource` (categorical IDs, sorted once), never expose that frame to pages, cache per-filter results with `st.cache_data` (copies), aggregate on the server, defer CSV generation until a download is clicked. |
| How did you test a UI? | Streamlit AppTest runs every page; clicks are simulated by injecting the selection payload and calling the page's real callback. |
| How do you protect the source data? | Read-only loaders with path and symlink checks; tests fingerprint `data/`, `reports/` and `src/` before and after. |
| How did you define "likely timer-driven"? | ≥ 80 % of the target events recorded by User_1 and a median wait ≥ 24 h - deliberately labelled exploratory. |
| Why no workflow task durations? | Lifecycle stages (schedule, start, suspend, resume, complete) repeat and cannot be paired reliably per task, so durations would be invented. |
| What would you do next? | Survival analysis for censored durations, conformance checking against a process model, and validation of the exploratory rules with the business. |
