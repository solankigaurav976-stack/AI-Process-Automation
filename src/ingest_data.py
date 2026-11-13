
from pathlib import Path

import pandas as pd
import pm4py

# Project paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_FILE = PROJECT_ROOT / "data/raw/BPI Challenge 2017.xes"
OUTPUT_FILE = PROJECT_ROOT / "data/processed/loan_application_events.parquet"

def main():
    print("Loading original loan application event log...")

    df = pm4py.read_xes(str(RAW_FILE))

    print(f"Loaded {len(df):,} events")

    # Validate required columns
    required_columns = [
        "case:concept:name",
        "concept:name",
        "time:timestamp",
    ]

    missing = [col for col in required_columns if col not in df.columns]

    if missing:
        raise ValueError(f"Missing required columns: {missing}")

    # Standardise timestamps
    df["time:timestamp"] = pd.to_datetime(
        df["time:timestamp"], utc=True, errors="coerce"
    )

    # Validate records
    print("Applications:", df["case:concept:name"].nunique())
    print("Unique activities:", df["concept:name"].nunique())
    print("Missing timestamps:", df["time:timestamp"].isna().sum())

    # Save reusable analytical dataset
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(OUTPUT_FILE, index=False)

    print(f"Parquet file saved: {OUTPUT_FILE}")
    print(f"Final dataset shape: {df.shape}")

if __name__ == "__main__":
    main()
