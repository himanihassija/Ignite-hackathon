"""
Grounded natural-language explanations for a scored route, using Gemini.

Per the PRD's sequence diagram (page 13): "C->>M: explain from subgraph" --
the core package retrieves the actual facts behind a route's score FIRST,
then asks Gemini to write about ONLY those facts. Gemini is a writer here,
not a retriever -- it never sees enough to invent a zone or a number that
isn't already in the subgraph.
"""
import requests
from .neo4j_client import get_driver, env

SUBGRAPH_QUERY = """
MATCH (rt:Route {id: $route_id})-[p:PASSES_THROUGH]->(z:Zone)
OPTIONAL MATCH (zr:ZoneReading {h3: z.h3, ts: p.bucket})
RETURN z.h3 AS h3, p.idx AS idx, p.seconds AS seconds, p.bucket AS ts,
       zr.pm25 AS pm25, zr.confidence AS confidence, zr.source AS source
ORDER BY p.idx
"""


def get_route_subgraph(route_id):
    """Pull exactly the facts behind a route's score -- per-zone PM2.5, dwell
    time, and reading confidence. This dict is the ONLY thing Gemini is
    allowed to write about."""
    driver = get_driver()
    with driver.session() as session:
        hops = [dict(r) for r in session.run(SUBGRAPH_QUERY, route_id=route_id)]
        route = session.run("""
            MATCH (rt:Route {id: $route_id})
            RETURN rt.dose AS dose, rt.coverage AS coverage, rt.rank AS rank,
                   rt.duration_s AS duration_s, rt.distance_m AS distance_m
        """, route_id=route_id).single()
    return {"route_id": route_id, "hops": hops, **dict(route)}


def _fallback_explanation(subgraph):
    """No Gemini key configured (or the call failed) -- a plain templated
    explanation from the same subgraph, so the app still works without the LLM."""
    with_pm = [h for h in subgraph["hops"] if h["pm25"] is not None]
    if not with_pm:
        return f"Route {subgraph['route_id']}: no exposure data available for this path."
    worst = max(with_pm, key=lambda h: h["pm25"])
    dose = subgraph["dose"] or 0.0
    coverage = subgraph["coverage"] or 0.0
    return (
        f"Route {subgraph['route_id']} has a total exposure dose of {dose:.1f}, "
        f"covering {coverage*100:.0f}% of zones with real sensor data. "
        f"Worst zone on this route is {worst['h3']} at {worst['pm25']:.0f} ug/m3 PM2.5."
    )


def explain_route(route_id, other_candidates=None):
    """Ask Gemini for a short, grounded explanation of this route's exposure
    score. other_candidates (optional) = the list score_candidates() returns,
    for comparison ("this route vs the other N options")."""
    subgraph = get_route_subgraph(route_id)
    api_key = env("LLM_API_KEY")
    if not api_key:
        return _fallback_explanation(subgraph)

    facts_lines = []
    for hop in subgraph["hops"]:
        pm25 = hop["pm25"] if hop["pm25"] is not None else "no reading"
        facts_lines.append(
            f"- Zone {hop['h3']}: PM2.5 = {pm25}, {hop['seconds']}s spent here, "
            f"confidence = {hop['confidence'] or 'none'}, source = {hop['source'] or 'none'}"
        )
    facts_text = "\n".join(facts_lines)

    comparison_text = ""
    if other_candidates:
        comparison_text = "\nOther candidate routes for this order:\n" + "\n".join(
            f"- {c['route']}: dose {c.get('dose_final', c.get('dose_base'))}"
            for c in other_candidates
        )

    prompt = (
        "You are explaining a delivery route's air-quality exposure score to a dispatcher. "
        "Use ONLY the facts below -- never invent a zone, a PM2.5 value, or a reason that "
        "isn't in this data. Write 2-3 short sentences, plain language, no jargon.\n\n"
        f"Route {subgraph['route_id']}: total dose {subgraph['dose']}, coverage {subgraph['coverage']}, "
        f"duration {subgraph['duration_s']}s, distance {subgraph['distance_m']}m.\n\n"
        f"Per-zone data:\n{facts_text}\n{comparison_text}"
    )

    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    try:
        resp = requests.post(url, params={"key": api_key}, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception as exc:
        return _fallback_explanation(subgraph) + f" (Gemini call failed: {exc})"
