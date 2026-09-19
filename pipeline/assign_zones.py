"""
PRD Section 11, Stage 3 (DT-2, part 1): Assign each aggregated reading to an
H3 zone (resolution 8, ~0.74 km^2/hex, per PRD default).
Input:  data/cleaned/aggregated_15min.csv
Output: data/cleaned/zoned_readings.csv  (same rows + h3_zone column)
Also reports per-zone reading counts and time-bucket coverage, so we know
which zones have enough data to be usable in the demo.
"""
import pandas as pd
import h3
import os

BASE = os.path.dirname(os.path.abspath(__file__))
IN_PATH = os.path.normpath(os.path.join(BASE, "..", "data", "cleaned", "aggregated_15min.csv"))
OUT_PATH = os.path.normpath(os.path.join(BASE, "..", "data", "cleaned", "zoned_readings.csv"))
H3_RES = 8

df = pd.read_csv(IN_PATH, parse_dates=["bucket"])
print(f"Loaded {len(df):,} (device, 15-min bucket) rows")

df["h3_zone"] = df.apply(lambda r: h3.latlng_to_cell(r["lat"], r["long"], H3_RES), axis=1)
df.to_csv(OUT_PATH, index=False)
print(f"Saved -> {OUT_PATH}")
print()

n_zones = df["h3_zone"].nunique()
total_buckets = df["bucket"].nunique()
print(f"Distinct H3 zones touched (res {H3_RES}): {n_zones}")
print(f"Distinct 15-min time buckets in the data: {total_buckets} "
      f"(spanning {df['bucket'].min()} to {df['bucket'].max()})")
print()

zone_summary = (
    df.groupby("h3_zone")
    .agg(readings=("pm2_5", "size"), buckets=("bucket", "nunique"),
         devices=("deviceId", "nunique"), avg_pm2_5=("pm2_5", "mean"))
    .sort_values("readings", ascending=False)
)
print(f"Coverage: {total_buckets} total buckets exist; top zones cover this many of them:")
print(zone_summary.head(20).to_string())
print()
print("Zone reading-count distribution (how many zones have how much data):")
print(zone_summary["readings"].describe())
print()
under5 = (zone_summary["readings"] < 5).sum()
print(f"Zones with fewer than 5 readings total across 20 days (too sparse to trust): {under5} of {n_zones}")
