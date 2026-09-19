"""
Candidate routes via Google's Routes API, converted into the PASSES_THROUGH
zone-hop format scoring.py expects (h3 zone, seconds spent, arrival bucket).

Google gives us a polyline + total duration, not a per-zone breakdown. This
module decodes the polyline, walks it in fixed 30-second steps (assumes
roughly constant speed along the route -- a real simplification, but Google's
free tier doesn't hand back per-segment timing), assigns each step to an H3
zone, and collapses consecutive same-zone steps into hops. Zones a route
passes through that aren't already in Neo4j (routes will often stray outside
the ~189 zones our sensors actually covered) get created on the fly with just
a centroid and no ZoneReading data -- which correctly shows up downstream as
lower `coverage` on that route, rather than silently vanishing.
"""
import base64
from datetime import timedelta
import requests
import h3
from .neo4j_client import env, get_driver

H3_RES = 8
SAMPLE_INTERVAL_S = 30


def _decode_polyline(encoded):
    """Standard Google encoded-polyline decoding, no external dependency."""
    points = []
    index = lat = lng = 0
    while index < len(encoded):
        for is_lat in (True, False):
            shift = result = 0
            while True:
                b = ord(encoded[index]) - 63
                index += 1
                result |= (b & 0x1f) << shift
                shift += 5
                if b < 0x20:
                    break
            delta = ~(result >> 1) if (result & 1) else (result >> 1)
            if is_lat:
                lat += delta
            else:
                lng += delta
        points.append((lat / 1e5, lng / 1e5))
    return points


def get_candidate_routes(origin_lat, origin_lon, dest_lat, dest_lon):
    """Call Google's Routes API for alternatives between two points. Returns
    a list of {duration_s, distance_m, points}."""
    api_key = env("GOOGLE_MAPS_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_MAPS_API_KEY not set in .env")

    url = "https://routes.googleapis.com/directions/v2:computeRoutes"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": "routes.duration,routes.distanceMeters,routes.polyline.encodedPolyline",
    }
    body = {
        "origin": {"location": {"latLng": {"latitude": origin_lat, "longitude": origin_lon}}},
        "destination": {"location": {"latLng": {"latitude": dest_lat, "longitude": dest_lon}}},
        "travelMode": "TWO_WHEELER",
        "computeAlternativeRoutes": True,
        "routingPreference": "TRAFFIC_AWARE",
    }
    resp = requests.post(url, headers=headers, json=body, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    routes = []
    for r in data.get("routes", []):
        duration_s = int(str(r["duration"]).rstrip("s"))
        distance_m = r["distanceMeters"]
        points = _decode_polyline(r["polyline"]["encodedPolyline"])
        routes.append({"duration_s": duration_s, "distance_m": distance_m, "points": points})
    return routes


def _floor_15min(dt):
    return dt.replace(minute=(dt.minute // 15) * 15, second=0, microsecond=0)


def _route_to_hops(points, duration_s, depart_ts):
    """Walk the decoded polyline in fixed time steps, assign each step to an
    H3 zone, collapse consecutive same-zone steps into hops with idx/seconds/
    bucket -- matching PASSES_THROUGH's schema."""
    if not points or duration_s <= 0:
        return []

    n_steps = max(1, int(duration_s / SAMPLE_INTERVAL_S))
    raw_hops = []
    current_zone = None
    current_start_s = 0.0

    for step in range(n_steps + 1):
        t = min(step * SAMPLE_INTERVAL_S, duration_s)
        frac = t / duration_s
        point_idx = min(int(frac * (len(points) - 1)), len(points) - 1)
        lat, lon = points[point_idx]
        zone = h3.latlng_to_cell(lat, lon, H3_RES)

        if zone != current_zone:
            if current_zone is not None:
                raw_hops.append({"h3": current_zone, "seconds": t - current_start_s, "start_s": current_start_s})
            current_zone = zone
            current_start_s = t

    raw_hops.append({"h3": current_zone, "seconds": duration_s - current_start_s, "start_s": current_start_s})

    hops = []
    for idx, hop in enumerate(raw_hops):
        if hop["seconds"] <= 0:
            continue
        arrival_time = depart_ts + timedelta(seconds=hop["start_s"] + hop["seconds"])
        hops.append({"h3": hop["h3"], "idx": idx, "seconds": hop["seconds"], "bucket": _floor_15min(arrival_time)})
    return hops


def create_route_candidates(order_id, origin, destination, depart_ts):
    """Fetch alternatives from Google, convert each to zone hops, and write
    each as a Route node with PASSES_THROUGH edges + HAS_CANDIDATE from the
    order. Returns the list of created route ids.

    depart_ts MUST be timezone-aware (IST, +05:30) -- every ZoneReading.ts in
    Neo4j is IST. A naive datetime gets silently treated as UTC by Neo4j's
    datetime(), 5.5 hours off from IST, so every dose/coverage lookup below
    would quietly return nothing -- no error, just coverage=0 everywhere."""
    if depart_ts.tzinfo is None:
        raise ValueError(
            "depart_ts must be timezone-aware (IST, +05:30). A naive datetime "
            "silently mismatches ZoneReading timestamps and produces coverage=0 "
            "with no error. Use: from datetime import timezone, timedelta; "
            "datetime(..., tzinfo=timezone(timedelta(hours=5, minutes=30)))"
        )
    routes = get_candidate_routes(origin[0], origin[1], destination[0], destination[1])
    driver = get_driver()
    route_ids = []
    with driver.session() as session:
        # A fresh route search REPLACES this order's existing candidates rather
        # than accumulating alongside them -- old ones were scored against a
        # different departure time and would otherwise silently pollute
        # score_candidates() with stale (often zero-dose) phantom entries that
        # can outrank the real, current routes.
        session.run("""
            MATCH (o:Order {id: $order_id})-[:HAS_CANDIDATE]->(rt:Route)
            DETACH DELETE rt
        """, order_id=order_id)

        for i, route in enumerate(routes):
            route_id = f"{order_id}-route-{i}"
            hops = _route_to_hops(route["points"], route["duration_s"], depart_ts)
            session.run("""
                MATCH (o:Order {id: $order_id})
                MERGE (rt:Route {id: $route_id})
                SET rt.provider = 'google', rt.duration_s = $duration_s, rt.distance_m = $distance_m
                MERGE (o)-[:HAS_CANDIDATE]->(rt)
            """, order_id=order_id, route_id=route_id, duration_s=route["duration_s"], distance_m=route["distance_m"])

            for hop in hops:
                lat, lon = h3.cell_to_latlng(hop["h3"])
                session.run("""
                    MATCH (rt:Route {id: $route_id})
                    MERGE (z:Zone {h3: $h3})
                    ON CREATE SET z.res = 8, z.centroid = point({latitude: $lat, longitude: $lon})
                    MERGE (rt)-[p:PASSES_THROUGH]->(z)
                    SET p.idx = $idx, p.seconds = $seconds, p.bucket = datetime($bucket)
                """, route_id=route_id, h3=hop["h3"], lat=lat, lon=lon,
                     idx=hop["idx"], seconds=hop["seconds"], bucket=hop["bucket"].isoformat())
            route_ids.append(route_id)
    return route_ids


def create_candidates_for_order(order_id, depart_ts):
    """Convenience wrapper: reads an Order's pickup/drop points straight from
    Neo4j and calls create_route_candidates with them."""
    driver = get_driver()
    with driver.session() as session:
        record = session.run(
            "MATCH (o:Order {id: $order_id}) RETURN o.pickup AS pickup, o.drop AS drop",
            order_id=order_id,
        ).single()
    pickup, drop = record["pickup"], record["drop"]
    return create_route_candidates(
        order_id, (pickup.latitude, pickup.longitude), (drop.latitude, drop.longitude), depart_ts
    )
