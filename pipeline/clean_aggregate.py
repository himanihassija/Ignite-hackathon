"""
PRD Section 11, Stage 2 (DT-1): Clean + aggregate to 15-minute medians per device.
Input:  data/aqi-recent-10/*.csv  (raw, ~6-second-interval mobile sensor readings)
Output: data/cleaned/aggregated_15min.csv
        one row per (deviceId, 15-min bucket) with median lat/long/pm1_0/pm2_5/pm10
        and how many raw readings fed that bucket.
Devices are mobile (confirmed in profile_aqi.py), so lat/long are aggregated too,
not treated as fixed per device.
"""
import pandas as pd
import glob
import os

DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "aqi-recent-10")
)
OUT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "cleaned")
)
os.makedirs(OUT_DIR, exist_ok=True)
OUT_PATH = os.path.join(OUT_DIR, "aggregated_15min.csv")

files = sorted(glob.glob(os.path.join(DATA_DIR, "*.csv")))
print(f"Found {len(files)} files")

chunks = []
total_raw = 0
total_dropped = 0

for f in files:
    df = pd.read_csv(
        f, usecols=["deviceId", "dateTime", "lat", "long", "pm1_0", "pm2_5", "pm10"],
        dtype={"deviceId": str}, low_memory=False
    )
    df["dateTime"] = pd.to_datetime(df["dateTime"], errors="coerce")
    before = len(df)
    df = df.dropna(subset=["deviceId", "dateTime", "lat", "long", "pm2_5", "pm10"])
    df = df[df["pm2_5"] <= df["pm10"]]  # drop the ~0.01% quality-flagged rows
    dropped = before - len(df)
    total_raw += before
    total_dropped += dropped

    df["bucket"] = df["dateTime"].dt.floor("15min")
    agg = (
        df.groupby(["deviceId", "bucket"])
        .agg(
            lat=("lat", "median"),
            long=("long", "median"),
            pm1_0=("pm1_0", "median"),
            pm2_5=("pm2_5", "median"),
            pm10=("pm10", "median"),
            n_readings=("pm2_5", "size"),
        )
        .reset_index()
    )
    chunks.append(agg)
    print(f"  {os.path.basename(f)}: {before:,} raw -> dropped {dropped:,} -> {len(agg):,} 15-min buckets")

result = pd.concat(chunks, ignore_index=True)
result = result.sort_values(["deviceId", "bucket"]).reset_index(drop=True)
result.to_csv(OUT_PATH, index=False)

print()
print("=" * 70)
print(f"Total raw rows read: {total_raw:,}")
print(f"Total dropped (nulls / pm2_5>pm10): {total_dropped:,}")
print(f"Output: {len(result):,} (device, 15-min bucket) rows -> {OUT_PATH}")
print()
print(result.head(10).to_string(index=False))
print()
print("Readings per bucket (sanity check, should mostly be in the dozens since raw data is ~6s interval):")
print(result["n_readings"].describe())
