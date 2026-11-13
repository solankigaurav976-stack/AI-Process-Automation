
from pathlib import Path
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent

df = pd.read_parquet(
    ROOT / "data/processed/loan_application_events.parquet"
)

df = df.sort_values(
    ["case:concept:name", "time:timestamp"]
).copy()

# Identify the next activity within each application
grouped = df.groupby("case:concept:name")

df["next_activity"] = grouped["concept:name"].shift(-1)
df["next_timestamp"] = grouped["time:timestamp"].shift(-1)

# Calculate time between consecutive activities
df["waiting_hours"] = (
    df["next_timestamp"] - df["time:timestamp"]
).dt.total_seconds() / 3600

transitions = df.dropna(
    subset=["next_activity", "waiting_hours"]
).copy()

# Summarise process transitions
results = transitions.groupby(
    ["concept:name", "next_activity"]
).agg(
    transition_count=("waiting_hours", "size"),
    average_wait_hours=("waiting_hours", "mean"),
    median_wait_hours=("waiting_hours", "median"),
    p90_wait_hours=("waiting_hours", lambda x: x.quantile(0.90))
).reset_index()

# Focus on frequently occurring transitions
results = results[
    results["transition_count"] >= 100
].sort_values(
    "median_wait_hours", ascending=False
)

output = ROOT / "reports"
output.mkdir(exist_ok=True)

results.to_csv(
    output / "process_bottlenecks.csv",
    index=False
)

print("\nTOP 15 LONGEST MEDIAN TRANSITIONS")
print(results.head(15).to_string(index=False))

print("\nBottleneck analysis saved successfully.")
