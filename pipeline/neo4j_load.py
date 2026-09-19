"""
PRD Section 11, Stage 6 (Load) + Section 12 constraints/indexes (verbatim from PRD).
Loads Zone, Sensor, ADJACENT zone-pairs, and ZoneReading(-OF->Zone) into Neo4j
AuraDB from the offline pipeline outputs. Safe to re-run (everything is MERGE'd).
"""
import pandas as pd
import h3
import os
import sys
from neo4j import GraphDatabase

BASE = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.normpath(os.path.join(BASE, ".."))

env = {}
with open(os.path.join(PROJECT_ROOT, ".env")) as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        env[k.strip()] = v.strip()

driver = GraphDatabase.driver(env["NEO4J_URI"], auth=(env["NEO4J_USERNAME"], env["NEO4J_PASSWORD"]))

CONSTRAINTS = [
    "CREATE CONSTRAINT zone_h3 IF NOT EXISTS FOR (z:Zone) REQUIRE z.h3 IS UNIQUE",
    "CREATE CONSTRAINT sensor_id IF NOT EXISTS FOR (s:Sensor) REQUIRE s.deviceId IS UNIQUE",
    "CREATE CONSTRAINT rider_id IF NOT EXISTS FOR (r:Rider) REQUIRE r.id IS UNIQUE",
    "CREATE CONSTRAINT order_id IF NOT EXISTS FOR (o:Order) REQUIRE o.id IS UNIQUE",
    "CREATE CONSTRAINT route_id IF NOT EXISTS FOR (r:Route) REQUIRE r.id IS UNIQUE",
    "CREATE CONSTRAINT alias_text IF NOT EXISTS FOR (a:Alias) REQUIRE a.text IS UNIQUE",
    "CREATE CONSTRAINT place_id IF NOT EXISTS FOR (p:Place) REQUIRE p.id IS UNIQUE",
    "CREATE INDEX zone_reading_lookup IF NOT EXISTS FOR (zr:ZoneReading) ON (zr.h3, zr.ts)",
    "CREATE POINT INDEX place_location IF NOT EXISTS FOR (p:Place) ON (p.location)",
]

STAGE = sys.argv[1] if len(sys.argv) > 1 else "setup"

if STAGE == "setup":
    print("Constraints/indexes...")
    with driver.session() as session:
        for stmt in CONSTRAINTS:
            session.run(stmt)
    print("  done")

    zoned = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "cleaned", "zoned_readings.csv"), parse_dates=["bucket"])
    zones = sorted(zoned["h3_zone"].unique())
    zone_rows = []
    for z in zones:
        lat, lon = h3.cell_to_latlng(z)
        zone_rows.append({"h3": z, "res": 8, "lat": lat, "lon": lon})

    def load_zones(tx, rows):
        tx.run("""
            UNWIND $rows AS row
            MERGE (z:Zone {h3: row.h3})
            SET z.res = row.res, z.centroid = point({latitude: row.lat, longitude: row.lon})
        """, rows=rows)

    print(f"Loading {len(zone_rows)} Zone nodes...")
    with driver.session() as session:
        session.execute_write(load_zones, zone_rows)
    print("  done")

    sensor_summary = zoned.groupby("deviceId").agg(
        lat=("lat", "mean"), long=("long", "mean"),
        first_ts=("bucket", "min"), last_ts=("bucket", "max"),
    ).reset_index()
    sensor_rows = [
        {"deviceId": r.deviceId, "lat": r.lat, "lon": r.long,
         "first_ts": r.first_ts.isoformat(), "last_ts": r.last_ts.isoformat()}
        for r in sensor_summary.itertuples()
    ]

    def load_sensors(tx, rows):
        tx.run("""
            UNWIND $rows AS row
            MERGE (s:Sensor {deviceId: row.deviceId})
            SET s.location = point({latitude: row.lat, longitude: row.lon}),
                s.first_ts = datetime(row.first_ts), s.last_ts = datetime(row.last_ts),
                s.quality = 'unverified', s.mobile = true
        """, rows=rows)

    print(f"Loading {len(sensor_rows)} Sensor nodes (mobile=true; location = mean position over the window)...")
    with driver.session() as session:
        session.execute_write(load_sensors, sensor_rows)
    print("  done")

    zone_set = set(zones)
    adj_pairs = set()
    for z in zones:
        for nb in h3.grid_ring(z, 1):
            if nb in zone_set:
                adj_pairs.add(tuple(sorted((z, nb))))
    adj_rows = [{"a": a, "b": b} for a, b in adj_pairs]

    def load_adjacency(tx, rows):
        tx.run("""
            UNWIND $rows AS row
            MATCH (za:Zone {h3: row.a}), (zb:Zone {h3: row.b})
            MERGE (za)-[:ADJACENT]->(zb)
            MERGE (zb)-[:ADJACENT]->(za)
        """, rows=rows)

    print(f"Loading {len(adj_rows)} ADJACENT zone pairs...")
    with driver.session() as session:
        session.execute_write(load_adjacency, adj_rows)
    print("  done")
    print("SETUP_COMPLETE")

elif STAGE == "readings":
    zr = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "cleaned", "zone_readings_interpolated.csv"), parse_dates=["ts"])
    records = zr.to_dict("records")
    for rec in records:
        rec["ts"] = rec["ts"].isoformat()
    total = len(records)
    start = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    BATCH = 5000

    def load_zone_readings(tx, rows):
        tx.run("""
            UNWIND $rows AS row
            MATCH (z:Zone {h3: row.h3})
            MERGE (zr:ZoneReading {h3: row.h3, ts: datetime(row.ts), source: row.source})
            SET zr.pm25 = row.pm25, zr.confidence = row.confidence, zr.n_sensors = row.n_sensors
            MERGE (zr)-[:OF]->(z)
        """, rows=rows)

    print(f"Loading ZoneReading rows {start:,} to {total:,} (batches of {BATCH})...")
    with driver.session() as session:
        for i in range(start, total, BATCH):
            batch = records[i:i + BATCH]
            session.execute_write(load_zone_readings, batch)
            print(f"  {min(i+BATCH, total):,}/{total:,} done  (resume with: python3 neo4j_load.py readings {min(i+BATCH, total)})")
    print("READINGS_COMPLETE")

driver.close()
