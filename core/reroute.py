"""
Live rerouting through the ZONE graph when AQI along a route spikes -- this
runs entirely against Neo4j's own ADJACENT zone graph, no Google Routes call
needed. That means it works right now even without the Maps key, and stays
useful afterward as a fast in-graph reroute check between full re-requests
to Google.

Two pieces, matching the PRD's "replay clock with spike injector" + the
reroute-on-spike alert item:

  - advance_rider_position(): given a route and elapsed time since departure,
    which zone is the rider in right now. Computed on demand from the route's
    PASSES_THROUGH hops, never written continuously -- matches the PRD's
    "all queries read the zone reading for the clock's current bucket"
    approach (page 15), so there's nothing to keep in sync as the clock ticks.

  - check_and_reroute(): looks at the rider's REMAINING hops; if any upcoming
    zone is Severe at its scheduled arrival bucket, searches the ADJACENT
    zone graph for an avoidance path from the rider's current position to
    the final destination, weighted to route around (not through) severe
    zones. Call it again on a later tick -- if the new path also develops a
    severe zone, it reroutes again. That's the "shifts, then shifts again"
    behavior.
"""
import heapq
from .neo4j_client import get_driver

SEVERE_PM25_THRESHOLD = 250.0  # CPCB "Severe" band floor
SEVERE_PENALTY = 50.0          # cost added for routing a step INTO a severe zone


def advance_rider_position(route_id, elapsed_seconds):
    """Which zone the rider is in after elapsed_seconds since departing on
    route_id. Returns None once elapsed_seconds is past the route's end."""
    driver = get_driver()
    with driver.session() as session:
        hops = [dict(r) for r in session.run("""
            MATCH (rt:Route {id: $route_id})-[p:PASSES_THROUGH]->(z:Zone)
            RETURN z.h3 AS h3, p.idx AS idx, p.seconds AS seconds, p.bucket AS ts
            ORDER BY p.idx
        """, route_id=route_id)]

    cursor = 0.0
    for hop in hops:
        cursor += hop["seconds"]
        if elapsed_seconds <= cursor:
            return {"h3": hop["h3"], "idx": hop["idx"], "ts": hop["ts"]}
    return None


def _current_pm25(session, h3_id, bucket_ts):
    record = session.run(
        "MATCH (zr:ZoneReading {h3: $h3, ts: $ts}) RETURN zr.pm25 AS pm25",
        h3=h3_id, ts=bucket_ts,
    ).single()
    return record["pm25"] if record else None


def _zone_neighbors(session, h3_id):
    return [r["nb"] for r in session.run(
        "MATCH (:Zone {h3: $h3})-[:ADJACENT]->(nb:Zone) RETURN nb.h3 AS nb", h3=h3_id
    )]


def find_avoidance_path(from_zone, to_zone, at_ts, severe_threshold=SEVERE_PM25_THRESHOLD):
    """Dijkstra over the ADJACENT zone graph. Entering a zone costs 1, plus a
    heavy penalty if that zone's PM2.5 at `at_ts` is above severe_threshold --
    so the search prefers a longer path around a severe zone over a shorter
    one straight through it, but will still go through one if there's no
    other way to reach the destination in the loaded zone graph. Returns
    {"path": [...], "cost": ...} or None if from_zone/to_zone aren't connected
    within it at all."""
    driver = get_driver()
    with driver.session() as session:
        visited = set()
        heap = [(0.0, from_zone, [from_zone])]
        while heap:
            cost, zone, path = heapq.heappop(heap)
            if zone == to_zone:
                return {"path": path, "cost": cost}
            if zone in visited:
                continue
            visited.add(zone)
            for nb in _zone_neighbors(session, zone):
                if nb in visited:
                    continue
                pm25 = _current_pm25(session, nb, at_ts)
                step_cost = 1.0 + (SEVERE_PENALTY if (pm25 is not None and pm25 > severe_threshold) else 0.0)
                heapq.heappush(heap, (cost + step_cost, nb, path + [nb]))
        return None


def check_and_reroute(route_id, elapsed_seconds, destination_zone, severe_threshold=SEVERE_PM25_THRESHOLD):
    """Check the rider's remaining hops on route_id for a severe zone; if one
    is found, find an avoidance path from the rider's current position to
    destination_zone. Returns a status dict -- call again on a later tick to
    get chained rerouting if the new path spikes too."""
    position = advance_rider_position(route_id, elapsed_seconds)
    if position is None:
        return {"status": "route_complete"}

    driver = get_driver()
    with driver.session() as session:
        remaining_hops = [dict(r) for r in session.run("""
            MATCH (rt:Route {id: $route_id})-[p:PASSES_THROUGH]->(z:Zone)
            WHERE p.idx >= $idx
            RETURN z.h3 AS h3, p.bucket AS ts
            ORDER BY p.idx
        """, route_id=route_id, idx=position["idx"])]

        spiked = None
        for hop in remaining_hops:
            pm25 = _current_pm25(session, hop["h3"], hop["ts"])
            if pm25 is not None and pm25 > severe_threshold:
                spiked = hop
                break

    if spiked is None:
        return {"status": "on_track", "current_zone": position["h3"]}

    reroute = find_avoidance_path(position["h3"], destination_zone, spiked["ts"], severe_threshold)
    if reroute is None:
        return {
            "status": "spike_detected_no_alternate",
            "spiked_zone": spiked["h3"],
            "current_zone": position["h3"],
            # Track B (rider app / push / voice via Web Speech API) reads this
            # string as-is -- Track A owns the wording so it's grounded in the
            # real data, Track B just decides how to deliver it (toast, push,
            # spoken aloud).
            "message": "Warning: high AQI area ahead, no alternate route available. Proceed with a mask if you can.",
        }

    return {
        "status": "rerouted",
        "current_zone": position["h3"],
        "spiked_zone": spiked["h3"],
        "new_path": reroute["path"],
        "message": "Caution: high AQI area approaching. Rerouting now to avoid it.",
    }


def check_reroute_and_log(route_id, elapsed_seconds, destination_zone, order_id=None, rider_id=None,
                           severe_threshold=SEVERE_PM25_THRESHOLD):
    """Same as check_and_reroute(), but also writes an Alert (core.alerts.log_alert)
    exactly once per call when a spike is found -- this is what the Slack daily
    summary's reroute count comes from. Use this from the replay-clock loop;
    use check_and_reroute() directly for a read-only check with no side effects."""
    from .alerts import log_alert
    result = check_and_reroute(route_id, elapsed_seconds, destination_zone, severe_threshold)
    if result["status"] == "rerouted":
        log_alert("reroute", "high", order_id=order_id, rider_id=rider_id)
    elif result["status"] == "spike_detected_no_alternate":
        log_alert("spike_no_alternate", "high", order_id=order_id, rider_id=rider_id)
    return result
