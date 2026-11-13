
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]

DATA_FILE = ROOT / "data/processed/loan_application_events.parquet"
REPORT_DIR = ROOT / "reports"
REPORT_DIR.mkdir(exist_ok=True)

print("Loading loan application data...")

df = pd.read_parquet(DATA_FILE)

# Sort events chronologically
df = df.sort_values(
    ["case:concept:name", "time:timestamp"]
)

# Get the final recorded event for each application
final_events = df.groupby(
    "case:concept:name"
).tail(1).copy()

# Count final recorded activities
outcomes = (
    final_events["concept:name"]
    .value_counts()
    .rename_axis("final_activity")
    .reset_index(name="applications")
)

outcomes["percentage"] = (
    outcomes["applications"] / len(final_events) * 100
).round(2)

# Save report
outcomes.to_csv(
    REPORT_DIR / "application_outcomes.csv",
    index=False
)

print("\nAPPLICATION OUTCOME ANALYSIS")
print(f"Total applications: {len(final_events):,}")
print(outcomes.to_string(index=False))

print("\nOutcome report saved successfully.")