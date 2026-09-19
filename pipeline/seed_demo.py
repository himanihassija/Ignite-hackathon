"""
PRD Section 11, Stage 7 (Seed): knowledge layer + simulated fleet.
Loads Hub, Rider, Order, Place/Alias, VehicleType, MaskType, GRAPStage/Rule
into Neo4j. Must run on a machine that can reach Aura's bolt port (7687) --
same restriction as neo4j_load.py, this sandbox can't do it.
Safe to re-run: everything is MERGE'd.
"""
import os
import random
import pandas as pd
import h3
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
random.seed(42)

# ---- Hub: the best-covered zone from the pipeline run (South Delhi / Nehru Place area) ----
zoned = pd.read_csv(os.path.join(PROJECT_ROOT, "data", "cleaned", "zoned_readings.csv"))
zone_counts = zoned.groupby("h3_zone").size().sort_values(ascending=False)
hub_zone = zone_counts.index[0]
hub_lat, hub_lon = h3.cell_to_latlng(hub_zone)
HUB = {"id": "hub-1", "name": "South Delhi Hub (Nehru Place / South Ext)", "lat": hub_lat, "lon": hub_lon}

# ---- Riders: mix of vehicle types and mask usage (unmasked riders are the whole point of the demo) ----
RIDERS = [{
    "id": f"rider-{i}", "name": f"Rider {i}", "status": "available",
    "lat": hub_lat + random.uniform(-0.003, 0.003), "lon": hub_lon + random.uniform(-0.003, 0.003),
    "exposure_budget": 500.0, "exposure_used": 0.0,
    "activity_factor": round(random.uniform(1.0, 1.3), 2),
    "vehicle": random.choice(["bike", "e_rickshaw", "van"]),
    "mask": random.choice(["none", "none", "surgical", "n95"]),  # weighted toward unmasked, matches the problem framing
} for i in range(1, 9)]

# ---- Orders: pickup at the hub, drop scattered across a spread of covered zones (dense to thin) ----
sorted_zones = zone_counts.index.tolist()
drop_zones = sorted_zones[::max(1, len(sorted_zones) // 12)][:12]
ORDERS = []
for i, z in enumerate(drop_zones, start=1):
    dlat, dlon = h3.cell_to_latlng(z)
    ORDERS.append({
        "id": f"order-{i}",
        "pickup_lat": hub_lat, "pickup_lon": hub_lon,
        "drop_lat": dlat, "drop_lon": dlon,
        "window_start": "2021-01-15T10:00:00+05:30", "window_end": "2021-01-15T10:45:00+05:30",  # IST -- must match ZoneReading.ts's offset
        "status": "pending",
    })

# ---- Places / Aliases: real landmarks inside the covered area, for the place-search step ----
PLACES = [
    {"id": "place-nehru-place", "name": "Nehru Place", "type": "market", "lat": 28.5487, "lon": 77.2519, "aliases": ["nehru place", "np"]},
    {"id": "place-south-ex", "name": "South Extension", "type": "market", "lat": 28.5730, "lon": 77.2230, "aliases": ["south extension", "south ex", "se"]},
    {"id": "place-lajpat-nagar", "name": "Lajpat Nagar", "type": "market", "lat": 28.5677, "lon": 77.2431, "aliases": ["lajpat nagar", "ln"]},
    {"id": "place-kalkaji", "name": "Kalkaji", "type": "sector", "lat": 28.5355, "lon": 77.2585, "aliases": ["kalkaji"]},
    {"id": "place-okhla", "name": "Okhla", "type": "sector", "lat": 28.5355, "lon": 77.2750, "aliases": ["okhla", "okhla industrial area"]},
]

VEHICLE_TYPES = [
    {"name": "bike", "exposure_factor": 1.0},
    {"name": "e_rickshaw", "exposure_factor": 0.85},
    {"name": "van", "exposure_factor": 0.4},
]
MASK_TYPES = [
    {"name": "none", "filtration": 0.0},
    {"name": "surgical", "filtration": 0.3},
    {"name": "n95", "filtration": 0.85},
]
GRAP_STAGE = {"level": 3, "name": "Severe"}
RULES = [
    {"id": "rule-bs3-bs4-ban", "description": "BS-III petrol and BS-IV diesel light vehicles barred from plying in NCR", "source_url": "https://caqm.gov.in/", "effective_from": "2021-01-01"},
    {"id": "rule-truck-entry", "description": "Non-essential truck entry into Delhi restricted; only essential-goods carriers allowed", "source_url": "https://caqm.gov.in/", "effective_from": "2021-01-01"},
]


def load_hub(tx, hub):
    tx.run("MERGE (h:Hub {id: $id}) SET h.name = $name, h.location = point({latitude: $lat, longitude: $lon})", **hub)


def load_riders(tx, rows):
    tx.run("""
        UNWIND $rows AS row
        MERGE (r:Rider {id: row.id})
        SET r.name = row.name, r.status = row.status,
            r.location = point({latitude: row.lat, longitude: row.lon}),
            r.exposure_budget = row.exposure_budget, r.exposure_used = row.exposure_used,
            r.activity_factor = row.activity_factor
        WITH r, row
        MATCH (v:VehicleType {name: row.vehicle})
        MERGE (r)-[:USES]->(v)
        WITH r, row
        MATCH (m:MaskType {name: row.mask})
        MERGE (r)-[:WEARS]->(m)
    """, rows=rows)


def load_orders(tx, rows):
    tx.run("""
        UNWIND $rows AS row
        MERGE (o:Order {id: row.id})
        SET o.pickup = point({latitude: row.pickup_lat, longitude: row.pickup_lon}),
            o.drop = point({latitude: row.drop_lat, longitude: row.drop_lon}),
            o.window_start = datetime(row.window_start), o.window_end = datetime(row.window_end),
            o.status = row.status
    """, rows=rows)


def load_places(tx, places):
    for p in places:
        tx.run("""
            MERGE (pl:Place {id: $id})
            SET pl.name = $name, pl.type = $type, pl.city = 'Delhi',
                pl.location = point({latitude: $lat, longitude: $lon})
        """, id=p["id"], name=p["name"], type=p["type"], lat=p["lat"], lon=p["lon"])
        for alias in p["aliases"]:
            tx.run("""
                MATCH (pl:Place {id: $pid})
                MERGE (a:Alias {text: $alias})
                SET a.votes = coalesce(a.votes, 0) + 1
                MERGE (a)-[rel:REFERS_TO]->(pl)
                SET rel.votes = coalesce(rel.votes, 0) + 1, rel.confidence = 1.0
            """, pid=p["id"], alias=alias)


def load_vehicle_types(tx, rows):
    tx.run("UNWIND $rows AS row MERGE (v:VehicleType {name: row.name}) SET v.exposure_factor = row.exposure_factor", rows=rows)


def load_mask_types(tx, rows):
    tx.run("UNWIND $rows AS row MERGE (m:MaskType {name: row.name}) SET m.filtration = row.filtration", rows=rows)


def load_grap(tx, stage, rules, restrict_vehicle, apply_zone):
    tx.run("MERGE (gs:GRAPStage {level: $level}) SET gs.name = $name, gs.active = true", **stage)
    for r in rules:
        tx.run("""
            MATCH (gs:GRAPStage {level: $level})
            MERGE (ru:Rule {id: $id})
            SET ru.description = $description, ru.source_url = $source_url, ru.effective_from = date($effective_from)
            MERGE (gs)-[:TRIGGERS]->(ru)
            WITH ru
            MATCH (v:VehicleType {name: $restrict_vehicle})
            MERGE (ru)-[:RESTRICTS]->(v)
            WITH ru
            MATCH (z:Zone {h3: $apply_zone})
            MERGE (ru)-[:APPLIES_TO]->(z)
        """, level=stage["level"], id=r["id"], description=r["description"], source_url=r["source_url"],
             effective_from=r["effective_from"], restrict_vehicle=restrict_vehicle, apply_zone=apply_zone)


with driver.session() as session:
    print("Vehicle types + mask types...")
    session.execute_write(load_vehicle_types, VEHICLE_TYPES)
    session.execute_write(load_mask_types, MASK_TYPES)
    print("  done")

    print(f"Hub at zone {hub_zone} ({hub_lat:.5f}, {hub_lon:.5f})...")
    session.execute_write(load_hub, HUB)
    print("  done")

    print(f"{len(RIDERS)} riders...")
    session.execute_write(load_riders, RIDERS)
    print("  done")

    print(f"{len(ORDERS)} orders across {len(drop_zones)} zones...")
    session.execute_write(load_orders, ORDERS)
    print("  done")

    print(f"{len(PLACES)} places + aliases...")
    session.execute_write(load_places, PLACES)
    print("  done")

    print("GRAP stage + rules...")
    session.execute_write(load_grap, GRAP_STAGE, RULES, "van", hub_zone)
    print("  done")

driver.close()
print("SEED_COMPLETE")
