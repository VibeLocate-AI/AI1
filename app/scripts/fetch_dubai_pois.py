"""
Pulls Points-of-Interest (POI) data for Dubai from OpenStreetMap via the
Overpass API.

UPDATED: each POI now carries a `category` (the 4 broad Vibe Report
dimensions — unchanged, still used for safety/quietness/amenities
scoring) AND a new `subcategory` field (e.g. "hospital", "school",
"cafe", "pharmacy") so the frontend can pick a distinct map pin icon per
place type instead of one icon per broad category. This is purely an
additive data change — nothing about the Vibe Report scoring logic
changes; category stays exactly as before.

Usage:
    python -m app.scripts.fetch_dubai_pois

Output:
    data/dubai_pois.json
"""

import json
import time
from pathlib import Path

import requests

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
DUBAI_BBOX = (25.00, 54.90, 25.35, 55.60)

# Maps each OSM (key, value) tag pair to:
#   - the broad Vibe Report category (unchanged from before — safety /
#     quietness_positive / quietness_negative / amenities)
#   - a specific subcategory string for map-pin icon selection
CATEGORY_TAGS: dict[tuple[str, str], dict[str, str]] = {
    ("amenity", "police"):        {"category": "safety", "subcategory": "police"},
    ("amenity", "hospital"):      {"category": "safety", "subcategory": "hospital"},
    ("amenity", "clinic"):        {"category": "safety", "subcategory": "clinic"},

    ("leisure", "park"):              {"category": "quietness_positive", "subcategory": "park"},
    ("landuse", "recreation_ground"): {"category": "quietness_positive", "subcategory": "recreation_ground"},

    ("amenity", "nightclub"): {"category": "quietness_negative", "subcategory": "nightclub"},
    ("amenity", "bar"):       {"category": "quietness_negative", "subcategory": "bar"},

    ("amenity", "restaurant"):      {"category": "amenities", "subcategory": "restaurant"},
    ("amenity", "cafe"):            {"category": "amenities", "subcategory": "cafe"},
    ("amenity", "pharmacy"):        {"category": "amenities", "subcategory": "pharmacy"},
    ("shop", "supermarket"):        {"category": "amenities", "subcategory": "supermarket"},
    ("public_transport", "station"): {"category": "amenities", "subcategory": "transit_station"},
    ("amenity", "school"):          {"category": "amenities", "subcategory": "school"},
}


def build_overpass_query(bbox: tuple[float, float, float, float]) -> str:
    south, west, north, east = bbox
    clauses = [
        f'node["{key}"="{value}"]({south},{west},{north},{east});'
        for (key, value) in CATEGORY_TAGS
    ]
    body = "\n  ".join(clauses)
    return f"""
[out:json][timeout:60];
(
  {body}
);
out body;
"""


def category_for_tags(tags: dict) -> dict | None:
    """Returns {"category": ..., "subcategory": ...} for the first
    matching OSM tag pair, or None if this POI doesn't match any of
    our tracked tag pairs."""
    for (key, value), labels in CATEGORY_TAGS.items():
        if tags.get(key) == value:
            return labels
    return None


def fetch_dubai_pois() -> list[dict]:
    query = build_overpass_query(DUBAI_BBOX)
    headers = {
        "User-Agent": "VibeLocateAI-DataIngestion/0.2 (student project; contact: naji.m.mushtaha@gmail.com)",
        "Accept": "application/json",
    }
    response = requests.post(OVERPASS_URL, data={"data": query}, headers=headers, timeout=90)
    response.raise_for_status()
    elements = response.json().get("elements", [])

    pois = []
    for el in elements:
        tags = el.get("tags", {})
        labels = category_for_tags(tags)
        if labels is None:
            continue
        pois.append({
            "osm_id": el["id"],
            "name": tags.get("name", "Unnamed"),
            "category": labels["category"],
            "subcategory": labels["subcategory"],
            "latitude": el["lat"],
            "longitude": el["lon"],
        })
    return pois


def main():
    print("Fetching Dubai POIs from Overpass API... (can take 20-60s)")
    started = time.time()
    pois = fetch_dubai_pois()
    elapsed = time.time() - started

    out_dir = Path(__file__).resolve().parents[2] / "data"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / "dubai_pois.json"
    out_path.write_text(json.dumps(pois, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Done in {elapsed:.1f}s — {len(pois)} POIs saved to {out_path}")


if __name__ == "__main__":
    main()