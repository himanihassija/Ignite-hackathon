"""
Tests core/reroute.py against a real severe-pollution event found in the
actual data: on 2021-01-23 20:00 IST, zone 883da115a9fffff hit PM2.5=269
(Severe) while its neighbor 883da115abfffff was at 159 (not severe) --
a genuine spike-vs-safe-alternative scenario to route around.
"""
import sys
import os

sys.path.insert(0, os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")))

from core.neo4j_client import get_driver
from core.reroute import advance_rider_position, check_and_reroute, find_avoidance_path

ORDER_ID = "order-2"
ROUTE_ID = "route-test-2"

SAFE_START = "883da115abfffff"   # pm25=159 at the bucket below
SEVERE_ZONE = "883da115a9fffff"  # pm25=269 -- the spike
DESTINATION = "883da11585fffff"  # pm25=201 at the bucket below -- not severe

HOPS = [
    {"h3": SAFE_START, "ts": "2021-01-23T19:45:00+05:30", "seconds": 300},
    {"h3": SEVERE_ZONE, "ts": "2021-01-23T20:00:00+05:30", "seconds": 300},
    {"h3": DESTINATION, "ts": "2021-01-23T20:15:00+05:30", "seconds": 300},
]

driver = get_driver()
with driver.session() as session:
    # test-harness reset, same reasoning as test_scoring.py
    session.run("MATCH (rt:Route {id: $id})-[p:PASSES_THROUGH]->() DELETE p", id=ROUTE_ID)

    session.run("""
        MATCH (o:Order {id: $order_id})
        MERGE (rt:Route {id: $route_id})
        SET rt.provider = 'test', rt.duration_s = $duration, rt.distance_m = 3000
        MERGE (o)-[:HAS_CANDIDATE]->(rt)
    """, order_id=ORDER_ID, route_id=ROUTE_ID, duration=sum(h["seconds"] for h in HOPS))

    for idx, hop in enumerate(HOPS):
        session.run("""
            MATCH (rt:Route {id: $route_id}), (z:Zone {h3: $h3})
            MERGE (rt)-[p:PASSES_THROUGH]->(z)
            SET p.idx = $idx, p.seconds = $seconds, p.bucket = datetime($ts)
        """, route_id=ROUTE_ID, h3=hop["h3"], idx=idx, seconds=hop["seconds"], ts=hop["ts"])

print(f"Built test route {ROUTE_ID}: safe -> SEVERE (pm25=269) -> destination")
print()

print("advance_rider_position at elapsed=100s (should be hop 0, the safe start):")
print(" ", advance_rider_position(ROUTE_ID, 100))
print()

print("advance_rider_position at elapsed=350s (should be hop 1, the SEVERE zone):")
print(" ", advance_rider_position(ROUTE_ID, 350))
print()

print(f"check_and_reroute at elapsed=100s, destination={DESTINATION}:")
result = check_and_reroute(ROUTE_ID, 100, DESTINATION)
print(" ", result)
print()

print(f"find_avoidance_path directly: {SAFE_START} -> {DESTINATION} at the spike's bucket:")
path = find_avoidance_path(SAFE_START, DESTINATION, "2021-01-23T20:00:00+05:30")
print(" ", path)
if path:
    avoided = SEVERE_ZONE not in path["path"]
    print(f"  avoided the severe zone entirely: {avoided}")

print()
print("TEST_REROUTE_COMPLETE")
