"""
PRD Section 11, Stage 5 (Interpolate) + coverage rule (Section 12 preamble):
For each (zone, bucket), inverse-distance-weight PM2.5 from sensor readings
within R=3km of the zone's H3 centroid.

Coverage rule (from PRD, verbatim):
  >=2 sensors within R -> IDW blend            -> confidence "High"
  1 sensor within R     -> use it directly      -> confidence "Medium"
  0 sensors within R    -> no reading           -> zone excluded for that bucket

All rows here get source="sensor" (they're derived from real CSV sensor data,
regardless of 1 vs 2+ contributors). source="simulated" is reserved for demo
spike injection and source="model" for the deferred live-API fallback -- this
script never writes those.

Input:  data/cleaned/zoned_readings.csv
Output: data/cleaned/zone_readings_interpolated.csv
        columns: h3, ts, pm25, source, confidence, n_sensors
"""
import pandas as pd
import numpy as np
import h3
import os

BASE = os.path.dirname(os.path.abspath(__file__))
IN_PATH = os.path.normpath(os.path.join(BASE, "..", "data", "cleaned", "zoned_readings.csv"))
OUT_PATH = os.path.normpath(os.path.join(BASE, "..", "data", "cleaned", "zone_readings_interpolated.csv"))

R_KM = 3.0
IDW_POWER = 2

df = pd.read_csv(IN_PATH, parse_dates=["bucket"])
print(f"Loaded {len(df):,} zoned readings")

zones = sorted(df["h3_zone"].unique())
zone_centroids = {z: h3.cell_to_latlng(z) for z in zones}
print(f"Target zone universe: {len(zones)} zones")

mean_lat = df["lat"].mean()
KM_PER_DEG_LAT = 111.0
KM_PER_DEG_LON = 111.0 * np.cos(np.radians(mean_lat))


def flat_dist_km(lat1, lon1, lat2, lon2):
    dlat = (lat1 - lat2) * KM_PER_DEG_LAT
    dlon = (lon1 - lon2) * KM_PER_DEG_LON
    return np.sqrt(dlat ** 2 + dlon ** 2)


zone_ids = np.array(zones)
zone_lat = np.array([zone_centroids[z][0] for z in zones])
zone_lon = np.array([zone_centroids[z][1] for z in zones])

rows = []
buckets = df["bucket"].sort_values().unique()
print(f"Processing {len(buckets)} time buckets...")

for i, b in enumerate(buckets):
    bdf = df[df["bucket"] == b]
    r_lat = bdf["lat"].to_numpy()
    r_lon = bdf["long"].to_numpy()
    r_pm = bdf["pm2_5"].to_numpy()

    for zi, z in enumerate(zone_ids):
        d = flat_dist_km(zone_lat[zi], zone_lon[zi], r_lat, r_lon)
        within_r = d <= R_KM
        n = int(within_r.sum())
        if n == 0:
            continue
        elif n == 1:
            pm_val = float(r_pm[within_r][0])
            confidence = "Medium"
        else:
            w = 1.0 / np.maximum(d[within_r], 0.05) ** IDW_POWER
            pm_val = float(np.sum(w * r_pm[within_r]) / np.sum(w))
            confidence = "High"
        rows.append((z, b, round(pm_val, 1), "sensor", confidence, n))

    if (i + 1) % 300 == 0:
        print(f"  {i+1}/{len(buckets)} buckets done, {len(rows):,} zone-readings so far")

out = pd.DataFrame(rows, columns=["h3", "ts", "pm25", "source", "confidence", "n_sensors"])
out.to_csv(OUT_PATH, index=False)

print()
print("=" * 70)
print(f"Total (zone, bucket) rows produced: {len(out):,}")
print(f"  High confidence (>=2 sensors within {R_KM}km):   {(out['confidence']=='High').sum():,}")
print(f"  Medium confidence (1 sensor within {R_KM}km):    {(out['confidence']=='Medium').sum():,}")
possible = len(zones) * len(buckets)
print(f"Coverage: {len(out):,} / {possible:,} possible (zone x bucket) combos = {len(out)/possible*100:.1f}%")
print()
print("Sample rows:")
print(out.head(10).to_string(index=False))
