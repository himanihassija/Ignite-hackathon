"""
Place resolution: typed text -> {name, lat, lon, source}.

Order, per the PRD's own sequence diagram (page 13):
  1. Alias cache in Neo4j (fast, free, exact match on normalized text)
  2. Gemini normalize -- ONLY on a cache miss. Gemini cleans up typos/
     abbreviations into geocodable text; it is never trusted for coordinates.
  3. Google Geocoding, bounded to the NCR box, is the actual source of truth
     for lat/lon.
A successful Gemini+Google resolution is cached back as a new Alias, so the
cache gets better as the demo runs (this write-back isn't explicitly spelled
out in the PRD text but matches what the Alias node's "resolution cache"
purpose implies).
"""
import re
import requests
from .neo4j_client import get_driver, env

NCR_BOUNDS = {"lat_min": 28.35, "lat_max": 28.90, "lon_min": 76.80, "lon_max": 77.55}


def _normalize_text(text):
    return re.sub(r"\s+", " ", text.strip().lower())


def _lookup_alias(text):
    driver = get_driver()
    with driver.session() as session:
        record = session.run("""
            MATCH (a:Alias {text: $text})-[:REFERS_TO]->(p:Place)
            RETURN p.id AS id, p.name AS name, p.location AS location
            ORDER BY p.name LIMIT 1
        """, text=text).single()
        if record:
            loc = record["location"]
            return {"id": record["id"], "name": record["name"], "lat": loc.latitude, "lon": loc.longitude, "source": "alias_cache"}
    return None


def _gemini_normalize(raw_text):
    api_key = env("LLM_API_KEY")
    if not api_key:
        return raw_text  # no key configured -- fall back to the raw text, Google still gets a shot
    url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
    prompt = (
        "Rewrite this as a short, clean place name or address for geocoding in Delhi NCR, India. "
        "Fix typos and expand abbreviations. Reply with ONLY the place text, nothing else.\n\n"
        f"Input: {raw_text}"
    )
    resp = requests.post(url, params={"key": api_key}, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"].strip()


def _google_geocode(text):
    api_key = env("GOOGLE_MAPS_API_KEY")
    if not api_key:
        raise RuntimeError("GOOGLE_MAPS_API_KEY not set in .env -- add one with Geocoding API + Routes API enabled")
    resp = requests.get(
        "https://maps.googleapis.com/maps/api/geocode/json",
        params={
            "address": f"{text}, Delhi NCR, India",
            "bounds": f"{NCR_BOUNDS['lat_min']},{NCR_BOUNDS['lon_min']}|{NCR_BOUNDS['lat_max']},{NCR_BOUNDS['lon_max']}",
            "region": "in",
            "key": api_key,
        },
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data["status"] != "OK" or not data["results"]:
        return None
    top = data["results"][0]
    loc = top["geometry"]["location"]
    return {"name": top["formatted_address"], "lat": loc["lat"], "lon": loc["lng"], "source": "google_geocode"}


def _cache_alias(raw_text, resolved):
    driver = get_driver()
    place_id = "place-" + re.sub(r"[^a-z0-9]+", "-", resolved["name"].lower()).strip("-")[:60]
    with driver.session() as session:
        session.run("""
            MERGE (pl:Place {id: $id})
            ON CREATE SET pl.name = $name, pl.type = 'geocoded', pl.city = 'Delhi',
                          pl.location = point({latitude: $lat, longitude: $lon})
            WITH pl
            MERGE (a:Alias {text: $alias_text})
            SET a.votes = coalesce(a.votes, 0) + 1
            MERGE (a)-[rel:REFERS_TO]->(pl)
            SET rel.votes = coalesce(rel.votes, 0) + 1, rel.confidence = 0.7
        """, id=place_id, name=resolved["name"], lat=resolved["lat"], lon=resolved["lon"], alias_text=raw_text)


def resolve_place(raw_text):
    """Resolve free-text input to a location dict, or None if nothing was found."""
    text = _normalize_text(raw_text)
    if not text:
        return None

    hit = _lookup_alias(text)
    if hit:
        return hit

    cleaned = _gemini_normalize(raw_text)
    resolved = _google_geocode(cleaned)
    if resolved is None:
        return None

    _cache_alias(text, resolved)
    return resolved


if __name__ == "__main__":
    import sys
    query = " ".join(sys.argv[1:]) or "nehru place"
    print(f"Resolving: {query!r}")
    print(resolve_place(query))
