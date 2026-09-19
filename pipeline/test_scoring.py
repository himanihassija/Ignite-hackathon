"""
Validates core/scoring.py end-to-end against REAL ZoneReading data, without
needing Google Routes yet (that's still blocked on the Maps API key). Builds
one synthetic Route with two hops through zones/buckets we know have actual
sensor-derived data, scores it, dispatches it to a seeded rider, and confirms
the rider's exposure_used actually updated.

Safe to re-run (MERGE'd route/relationships); creates route-test-1 only.
"""
import sys
import os

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from core.neo4j_client import get_driver
from core.scoring import score_candidates, dispatch, ZONE_BREAKDOWN_QUERY

ORDER_ID = "order-1"
RIDER_ID = "rider-1"
ROUTE_ID = "route-test-1"

# two real zones with High-confidence ZoneReading data at the same known bucket
HOPS = [
    {"h3": "883da11513fffff", "ts": "2021-01-10T00:00:00+05:30", "seconds": 400},
    {"h3": "883da11511fffff", "ts": "2021-01-10T00:00:00+05:30", "seconds": 400},
]

driver = get_driver()
with driver.session() as session:
    # Test-harness reset: seed_demo.py resets Rider/Order PROPERTIES but not
    # relationships another script created, so without this, re-running this
    # test after a reseed looks like "already assigned" and silently skips
    # the exposure bump -- confusing when you're expecting a clean re-run.
    # This wipes just this test's own prior ASSIGNED/EXPOSED_IN so every run
    # of this file starts from the same state. Real dispatch code never does
    # this -- an actual delivery assignment should never be deleted.
    session.run("""
        MATCH (rd:Rider {id: $rider_id})-[a:ASSIGNED]->(o:Order {id: $order_id})
        DELETE a
        SET o.status = 'pending'
    """, rider_id=RIDER_ID, order_id=ORDER_ID)
    session.run("""
        MATCH (:Rider {id: $rider_id})-[e:EXPOSED_IN]->(:Zone)
        DELETE e
    """, rider_id=RIDER_ID)

    session.run("""
        MATCH (o:Order {id: $order_id})
        MERGE (rt:Route {id: $route_id})
        SET rt.provider = 'test', rt.duration_s = $duration, rt.distance_m = 2000
        MERGE (o)-[:HAS_CANDIDATE]->(rt)
    """, order_id=ORDER_ID, route_id=ROUTE_ID, duration=sum(h["seconds"] for h in HOPS))

    for idx, hop in enumerate(HOPS):
        session.run("""
            MATCH (rt:Route {id: $route_id}), (z:Zone {h3: $h3})
            MERGE (rt)-[p:PASSES_THROUGH]->(z)
            SET p.idx = $idx, p.seconds = $seconds, p.bucket = datetime($ts)
        """, route_id=ROUTE_ID, h3=hop["h3"], idx=idx, seconds=hop["seconds"], ts=hop["ts"])

print(f"Built test route {ROUTE_ID} with {len(HOPS)} hops through real zone data.")
print()

print(f"Scoring candidates for {ORDER_ID} (rider {RIDER_ID})...")
scored = score_candidates(ORDER_ID, rider_id=RIDER_ID)
for row in scored:
    print(f"  {row}")
print()

print(f"Dispatching {RIDER_ID} -> {ORDER_ID} via {ROUTE_ID}...")
total_dose = dispatch(ORDER_ID, RIDER_ID, ROUTE_ID)
print(f"  total dose applied: {total_dose:.2f}")
print()

with driver.session() as session:
    record = session.run("MATCH (r:Rider {id: $id}) RETURN r.exposure_used AS used, r.status AS status", id=RIDER_ID).single()
    order_record = session.run("MATCH (o:Order {id: $id}) RETURN o.status AS status", id=ORDER_ID).single()
    exposed = session.run("MATCH (:Rider {id: $id})-[e:EXPOSED_IN]->(z:Zone) RETURN z.h3 AS h3, e.dose AS dose", id=RIDER_ID).data()

print(f"rider-1.exposure_used is now: {record['used']}")
print(f"order-1.status is now: {order_record['status']}")
print("EXPOSED_IN edges written:")
for row in exposed:
    print(f"  {row}")

print()
print("TEST_SCORING_COMPLETE")
