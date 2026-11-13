
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

DATA_FILE = ROOT / "data/processed/loan_application_events.parquet"
REPORT_DIR = ROOT / "reports"
REPORT_DIR.mkdir(exist_ok=True)

print("Loading processed event data...")

df = pd.read_parquet(DATA_FILE)

# Sort events chronologically within each application
df = df.sort_values(
    ["case:concept:name", "time:timestamp"]
)

# Analyse individual application journeys
cases = df.groupby("case:concept:name").agg(
    start_time=("time:timestamp", "min"),
    end_time=("time:timestamp", "max"),
    total_events=("concept:name", "count"),
    unique_activities=("concept:name", "nunique")
)

cases["duration_days"] = (
    cases["end_time"] - cases["start_time"]
).dt.total_seconds() / 86400

# Activity frequency
activities = (
    df.groupby("concept:name")
    .agg(
        total_events=("concept:name", "count"),
        applications=("case:concept:name", "nunique")
    )
    .sort_values("total_events", ascending=False)
)

# Save reports
cases.to_csv(REPORT_DIR / "application_performance.csv")
activities.to_csv(REPORT_DIR / "activity_frequency.csv")

print("\nAPPLICATION PERFORMANCE")
print(cases["duration_days"].describe())

print("\nTOP 10 ACTIVITIES")
print(activities.head(10).to_string())

print("\nReports saved successfully.")
