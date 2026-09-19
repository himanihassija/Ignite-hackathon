"""
Route exposure scoring, delivery-window filter, and dispatch. This is the
PRD's "dose scoring and window filter" + "dispatch" MVP items -- one of the
five core-package responsibilities in the architecture table (page 13).

The dose/coverage query is the PRD's own, verbatim (page 18): for each
candidate Route already attached to an Order via HAS_CANDIDATE, it reads
whichever ZoneReading exists for every zone the route passes through, at the
bucket the route would arrive there. Activity and mask factors are applied
here in Python, not in the query -- the PRD is explicit these "are applied
in the service layer." The rider's VehicleType.exposure_factor is folded in
the same way (an enclosed van cuts exposure further than an open bike) --
the PRD's dose formula text doesn't spell this term out, but the property
exists on VehicleType for exactly this, so it's applied identically to the
mask term.

score_candidates() also writes dose/coverage/rank back onto each Route node
(matching the PRD's Route property table) so anything downstream -- the
dispatcher UI, the explanation step -- can just read rt.dose instead of
recomputing it. rt.dose stores the raw, rider-independent dose_base; the
rider-adjusted dose_final is computed per call and never persisted on the
shared Route node, since it depends on who's assigned.
"""
from datetime import timedelta
from .neo4j_client import get_driver

DOSE_QUERY = """
MATCH (o:Order {id: $orderId})-[:HAS_CANDIDATE]->(r:Route)-[p:PASSES_THROUGH]->(z:Zone)
OPTIONAL MATCH (zr:ZoneReading {h3: z.h3, ts: p.bucket})
RETURN r.id AS route,
       sum(coalesce(zr.pm25, 0) * p.seconds / 3600.0) AS dose_base,
       toFloat(count(zr)) / count(p) AS coverage
ORDER BY dose_base
"""

ZONE_BREAKDOWN_QUERY = """
MATCH (rt:Route {id: $route_id})-[p:PASSES_THROUGH]->(z:Zone)
OPTIONAL MATCH (zr:ZoneReading {h3: z.h3, ts: p.bucket})
RETURN z.h3 AS h3, p.bucket AS ts, coalesce(zr.pm25, 0) * p.seconds / 3600.0 AS dose_base_zone
ORDER BY p.idx
"""


def _get_rider_factors(session, rider_id):
    record = session.run("""
        MATCH (r:Rider {id: $rider_id})
        OPTIONAL MATCH (r)-[:USES]->(v:VehicleType)
        OPTIONAL MATCH (r)-[:WEARS]->(m:MaskType)
        RETURN r.activity_factor AS activity_factor,
               coalesce(v.exposure_factor, 1.0) AS vehicle_factor,
               coalesce(m.filtration, 0.0) AS mask_filtration
    """, rider_id=rider_id).single()
    return dict(record)


def apply_rider_factors(dose_base, activity_factor, mask_filtration, vehicle_exposure_factor=1.0):
    """PRD dose formula: D = sum(C_z * dt) * A * (1 - F_mask), plus the vehicle
    exposure factor applied the same way."""
    return dose_base * activity_factor * (1 - mask_filtration) * vehicle_exposure_factor


def score_candidates(order_id, rider_id=None):
    """Score every candidate Route already attached to an Order via HAS_CANDIDATE,
    write dose/coverage/rank back onto each Route node, and return a list of
    dicts, cheapest dose first. Pass rider_id to also get a dose_final adjusted
    for that rider's activity/mask/vehicle -- ranking then sorts by dose_final
    instead of the raw dose_base (the written-back rt.rank always reflects the
    rider-independent dose_base order, since Routes are shared across riders)."""
    driver = get_driver()
    with driver.session() as session:
        rows = [dict(r) for r in session.run(DOSE_QUERY, orderId=order_id)]

        for rank, row in enumerate(rows):
            session.run("""
                MATCH (rt:Route {id: $route_id})
                SET rt.dose = $dose_base, rt.coverage = $coverage, rt.rank = $rank
            """, route_id=row["route"], dose_base=row["dose_base"], coverage=row["coverage"], rank=rank)

        if rider_id:
            factors = _get_rider_factors(session, rider_id)
            for row in rows:
                row["dose_final"] = apply_rider_factors(
                    row["dose_base"], factors["activity_factor"],
                    factors["mask_filtration"], factors["vehicle_factor"],
                )
            rows.sort(key=lambda r: r["dose_final"])
        return rows


def passes_window_filter(route_duration_s, order_window_start, order_window_end, depart_ts):
    """True if departing at depart_ts and taking route_duration_s seconds lands
    the delivery inside the order's [window_start, window_end]."""
    arrival = depart_ts + timedelta(seconds=route_duration_s)
    return order_window_start <= arrival <= order_window_end


def dispatch(order_id, rider_id, route_id):
    """Assign a rider to an order via the chosen route: write ASSIGNED and a
    per-zone EXPOSED_IN exposure ledger, mark the route selected, bump the
    rider's exposure_used, and flip the order to 'assigned'. Returns the
    total dose actually applied (rider-adjusted)."""
    driver = get_driver()
    with driver.session() as session:
        factors = _get_rider_factors(session, rider_id)
        breakdown = [dict(r) for r in session.run(ZONE_BREAKDOWN_QUERY, route_id=route_id)]

        total_final_dose = 0.0
        for row in breakdown:
            zone_dose_final = apply_rider_factors(
                row["dose_base_zone"], factors["activity_factor"],
                factors["mask_filtration"], factors["vehicle_factor"],
            )
            total_final_dose += zone_dose_final
            session.run("""
                MATCH (rd:Rider {id: $rider_id}), (z:Zone {h3: $h3})
                MERGE (rd)-[e:EXPOSED_IN]->(z)
                SET e.ts = $ts, e.dose = $dose
            """, rider_id=rider_id, h3=row["h3"], ts=row["ts"], dose=zone_dose_final)

        # ON CREATE vs ON MATCH: exposure_used only bumps the FIRST time this
        # rider is assigned to this order. Re-calling dispatch() for the same
        # pair (a re-run, a UI double-click) just refreshes the timestamp
        # instead of double-billing the rider for one delivery.
        session.run("""
            MATCH (rd:Rider {id: $rider_id}), (o:Order {id: $order_id}), (rt:Route {id: $route_id})
            MERGE (rd)-[a:ASSIGNED]->(o)
            ON CREATE SET a.ts = datetime(), rd.exposure_used = coalesce(rd.exposure_used, 0) + $total_dose
            ON MATCH SET a.ts = datetime()
            SET rt.selected = true, o.status = 'assigned'
        """, rider_id=rider_id, order_id=order_id, route_id=route_id, total_dose=total_final_dose)

        return total_final_dose
