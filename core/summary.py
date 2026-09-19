"""
Daily summary for the Slack digest: how many orders were delivered, how bad
the AQI was, how many reroutes had to happen, and what the AQI-aware
routing actually saved riders in exposure.

sim_date is the REPLAYED demo date being summarized (e.g. 2021-01-15), not
real wall-clock "today" -- every Order/ZoneReading/Alert timestamp in this
project lives in that replayed historical window, not the present.
"""
from datetime import datetime, timedelta, timezone
from .neo4j_client import get_driver

IST = timezone(timedelta(hours=5, minutes=30))

PM25_BANDS = [(30, "Good"), (60, "Satisfactory"), (90, "Moderate"), (120, "Poor"), (250, "Very Poor")]


def _pm25_band(pm25):
    for ceiling, label in PM25_BANDS:
        if pm25 <= ceiling:
            return label
    return "Severe"


def get_daily_summary(sim_date):
    """sim_date: a date() for the replayed day to summarize."""
    day_start = datetime(sim_date.year, sim_date.month, sim_date.day, 0, 0, 0, tzinfo=IST)
    day_end = day_start + timedelta(days=1)
    start_iso, end_iso = day_start.isoformat(), day_end.isoformat()

    driver = get_driver()
    with driver.session() as session:
        orders = session.run("MATCH (o:Order) RETURN o.status AS status, count(*) AS n").data()

        reroutes = session.run("""
            MATCH (a:Alert)
            WHERE a.type IN ['reroute', 'spike_no_alternate'] AND a.ts >= datetime($start) AND a.ts < datetime($end)
            RETURN a.type AS type, count(*) AS n
        """, start=start_iso, end=end_iso).data()

        aqi = session.run("""
            MATCH (zr:ZoneReading)
            WHERE zr.ts >= datetime($start) AND zr.ts < datetime($end)
            RETURN avg(zr.pm25) AS avg_pm25, max(zr.pm25) AS max_pm25, count(*) AS n_readings,
                   count(DISTINCT zr.h3) AS n_zones
        """, start=start_iso, end=end_iso).single()

        hotspot = session.run("""
            MATCH (zr:ZoneReading)
            WHERE zr.ts >= datetime($start) AND zr.ts < datetime($end)
            WITH zr.h3 AS zone, avg(zr.pm25) AS avg_pm25
            RETURN zone, avg_pm25
            ORDER BY avg_pm25 DESC LIMIT 1
        """, start=start_iso, end=end_iso).single()

        riders = session.run("""
            MATCH (rd:Rider) WHERE rd.exposure_used > 0
            RETURN count(rd) AS n_active, avg(rd.exposure_used) AS avg_exposure, max(rd.exposure_used) AS max_exposure
        """).single()

        routing = session.run("""
            MATCH (o:Order)-[:HAS_CANDIDATE]->(r:Route)
            WHERE r.dose IS NOT NULL
            WITH o, avg(r.dose) AS avg_dose, count(r) AS n_candidates
            RETURN count(o) AS n_orders_scored, sum(n_candidates) AS n_routes
        """).single()

        savings = session.run("""
            MATCH (o:Order)-[:HAS_CANDIDATE]->(r:Route)
            WHERE r.dose IS NOT NULL
            WITH o, avg(r.dose) AS avg_dose, count(r) AS n_candidates
            WHERE n_candidates > 1
            MATCH (o)-[:HAS_CANDIDATE]->(sel:Route {selected: true})
            RETURN sum(avg_dose - sel.dose) AS total_avoided, count(o) AS n_compared
        """).single()

        grap = session.run("MATCH (gs:GRAPStage {active: true}) RETURN gs.name AS name LIMIT 1").single()

    order_counts = {row["status"]: row["n"] for row in orders}
    reroute_counts = {row["type"]: row["n"] for row in reroutes}

    return {
        "date": sim_date.isoformat(),
        "orders_delivered": order_counts.get("assigned", 0) + order_counts.get("delivered", 0),
        "orders_pending": order_counts.get("pending", 0),
        "reroutes": reroute_counts.get("reroute", 0),
        "spikes_no_alternate": reroute_counts.get("spike_no_alternate", 0),
        "avg_pm25": round(aqi["avg_pm25"], 1) if aqi["avg_pm25"] is not None else None,
        "max_pm25": round(aqi["max_pm25"], 1) if aqi["max_pm25"] is not None else None,
        "n_readings": aqi["n_readings"],
        "n_zones": aqi["n_zones"],
        "hotspot_zone": hotspot["zone"] if hotspot else None,
        "hotspot_pm25": round(hotspot["avg_pm25"], 1) if hotspot and hotspot["avg_pm25"] is not None else None,
        "n_active_riders": riders["n_active"] or 0,
        "avg_rider_exposure": round(riders["avg_exposure"], 1) if riders["avg_exposure"] is not None else None,
        "max_rider_exposure": round(riders["max_exposure"], 1) if riders["max_exposure"] is not None else None,
        "n_orders_scored": routing["n_orders_scored"] or 0,
        "n_routes_evaluated": routing["n_routes"] or 0,
        "total_avoided_dose": round(savings["total_avoided"], 1) if savings and savings["total_avoided"] is not None else None,
        "n_orders_compared": savings["n_compared"] if savings else 0,
        "grap_stage": grap["name"] if grap else None,
    }


def format_summary_message(summary):
    aqi_band = _pm25_band(summary["max_pm25"]) if summary["max_pm25"] is not None else "unknown"

    lines = [f"*:package: Daily Ops Summary — {summary['date']}*", ""]

    lines.append("*Deliveries*")
    lines.append(f"• Delivered/assigned: *{summary['orders_delivered']}*  (pending: {summary['orders_pending']})")
    if summary["n_active_riders"]:
        exposure_bit = f" (avg exposure {summary['avg_rider_exposure']}, peak {summary['max_rider_exposure']})"
        lines.append(f"• Active riders tracked: *{summary['n_active_riders']}*{exposure_bit}")
    lines.append("")

    lines.append("*Air Quality*")
    grap_bit = f" — GRAP: {summary['grap_stage']}" if summary["grap_stage"] else ""
    lines.append(f"• Avg PM2.5: *{summary['avg_pm25']}*  ·  Peak: *{summary['max_pm25']}* ({aqi_band}){grap_bit}")
    lines.append(f"• Coverage: {summary['n_zones']} zones across {summary['n_readings']} sensor readings")
    if summary["hotspot_zone"]:
        lines.append(f"• Hardest-hit zone: `{summary['hotspot_zone']}` (avg PM2.5 {summary['hotspot_pm25']})")
    lines.append("")

    lines.append("*Routing Intelligence*")
    reroute_line = f"• Reroutes triggered: *{summary['reroutes']}*"
    if summary["spikes_no_alternate"]:
        reroute_line += f" (plus {summary['spikes_no_alternate']} spikes with no safe alternate found)"
    lines.append(reroute_line)
    if summary["n_routes_evaluated"]:
        lines.append(f"• Candidate routes evaluated: *{summary['n_routes_evaluated']}* across {summary['n_orders_scored']} orders")
    if summary["total_avoided_dose"] is not None and summary["n_orders_compared"]:
        lines.append(
            f"• Exposure avoided by AQI-aware routing: *{summary['total_avoided_dose']} µg·hr/m³* "
            f"saved vs. average candidate, across {summary['n_orders_compared']} orders"
        )

    return "\n".join(lines)
