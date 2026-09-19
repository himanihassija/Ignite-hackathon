"""
PRD Section 11, Stage 1: Profile.
Scans the raw sensor CSVs in data/aqi-recent-10/ and reports device count,
coordinate spread, time coverage, sampling gaps and a basic sanity check
(pm2_5 should not exceed pm10). Run this before writing any cleaning/
zone-assignment code -- it tells you whether the sensor coverage actually
overlaps a usable demo area in Delhi NCR.
"""
import pandas as pd
import glob
import os
from collections import defaultdict

DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "aqi-recent-10")
)
REPORT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "profile_report.txt")

files = sorted(glob.glob(os.path.join(DATA_DIR, "*.csv")))
lines = []


def out(s=""):
    print(s)
    lines.append(s)


out(f"Found {len(files)} CSV files in {DATA_DIR}")
out("")

device_stats = defaultdict(lambda: {
    "count": 0, "min_time": None, "max_time": None,
    "lat_min": None, "lat_max": None, "long_min": None, "long_max": None,
})

overall_lat_min = overall_lat_max = overall_long_min = overall_long_max = None
total_rows = 0
null_rows = 0
pm_violation_rows = 0

for f in files:
    df = pd.read_csv(
        f, usecols=["deviceId", "dateTime", "lat", "long", "pm2_5", "pm10"],
        dtype={"deviceId": str}, low_memory=False
    )
    df["dateTime"] = pd.to_datetime(df["dateTime"], errors="coerce")
    total_rows += len(df)
    null_rows += int(df[["lat", "long", "pm2_5", "pm10"]].isna().any(axis=1).sum())
    pm_violation_rows += int((df["pm2_5"] > df["pm10"]).sum())

    lat_min, lat_max = df["lat"].min(), df["lat"].max()
    long_min, long_max = df["long"].min(), df["long"].max()
    overall_lat_min = lat_min if overall_lat_min is None else min(overall_lat_min, lat_min)
    overall_lat_max = lat_max if overall_lat_max is None else max(overall_lat_max, lat_max)
    overall_long_min = long_min if overall_long_min is None else min(overall_long_min, long_min)
    overall_long_max = long_max if overall_long_max is None else max(overall_long_max, long_max)

    grp = df.groupby("deviceId").agg(
        count=("dateTime", "size"),
        min_time=("dateTime", "min"), max_time=("dateTime", "max"),
        lat_min=("lat", "min"), lat_max=("lat", "max"),
        long_min=("long", "min"), long_max=("long", "max"),
    )
    for dev_id, row in grp.iterrows():
        s = device_stats[dev_id]
        s["count"] += int(row["count"])
        s["min_time"] = row["min_time"] if s["min_time"] is None else min(s["min_time"], row["min_time"])
        s["max_time"] = row["max_time"] if s["max_time"] is None else max(s["max_time"], row["max_time"])
        s["lat_min"] = row["lat_min"] if s["lat_min"] is None else min(s["lat_min"], row["lat_min"])
        s["lat_max"] = row["lat_max"] if s["lat_max"] is None else max(s["lat_max"], row["lat_max"])
        s["long_min"] = row["long_min"] if s["long_min"] is None else min(s["long_min"], row["long_min"])
        s["long_max"] = row["long_max"] if s["long_max"] is None else max(s["long_max"], row["long_max"])

    out(f"  processed {os.path.basename(f)}: {len(df):,} rows, {df['deviceId'].nunique()} devices")

out("")
out("=" * 70)
out(f"TOTAL rows across all files: {total_rows:,}")
out(f"Rows with a null lat/long/pm2_5/pm10: {null_rows:,} ({null_rows/total_rows*100:.2f}%)")
out(f"Rows where pm2_5 > pm10 (data quality flag): {pm_violation_rows:,} ({pm_violation_rows/total_rows*100:.2f}%)")
out(f"Distinct device IDs across all 20 days: {len(device_stats)}")
out(f"Overall bounding box: lat [{overall_lat_min:.5f}, {overall_lat_max:.5f}], "
    f"long [{overall_long_min:.5f}, {overall_long_max:.5f}]")
out("")

out(f"{'deviceId':<16}{'rows':>9}{'days_span':>11}{'avg_gap_min':>13}{'lat_range':>18}{'long_range':>20}")
sorted_devices = sorted(device_stats.items(), key=lambda x: -x[1]["count"])
for dev_id, s in sorted_devices:
    span = s["max_time"] - s["min_time"]
    days_span = round(span.total_seconds() / 86400, 1) if span else 0
    avg_gap = round(span.total_seconds() / 60 / (s["count"] - 1), 1) if s["count"] > 1 and span else 0
    lat_r = f'{s["lat_min"]:.4f}-{s["lat_max"]:.4f}'
    long_r = f'{s["long_min"]:.4f}-{s["long_max"]:.4f}'
    out(f"{dev_id:<16}{s['count']:>9}{days_span:>11}{avg_gap:>13}{lat_r:>18}{long_r:>20}")

out("")
moving = [d for d, s in device_stats.items() if (s["lat_max"] - s["lat_min"]) > 0.001 or (s["long_max"] - s["long_min"]) > 0.001]
out(f"Devices whose coordinates shift >0.001 deg across readings (should be ~0 for fixed sensors): {len(moving)}")
if moving:
    out(f"  -> {moving[:20]}")

with open(REPORT_PATH, "w") as fh:
    fh.write("\n".join(lines))
print(f"\nFull report saved to {REPORT_PATH}")
