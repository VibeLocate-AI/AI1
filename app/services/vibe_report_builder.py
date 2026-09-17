"""
Builds a real Neighborhood Vibe Report (US-08) for an ACTUAL property,
using its real latitude/longitude (from all_properties.json /
database_final.sql) against the real Dubai POI dataset
(data/dubai_pois.json, 6,551 points from OpenStreetMap) and the
synthetic reviews generated for those POIs
(data/synthetic_reviews_pois.json).

This is the final piece connecting everything built so far:
  real property (lat/lng)
    -> nearby real POIs within 500m (Haversine distance)
    -> their generated reviews
    -> sentiment analysis on each review
    -> aggregated VibeReport (safety/quietness/amenities scores)
"""

import json
import logging
import math
from pathlib import Path

from app.schemas import ReviewIn, VibeReport
from app.services.sentiment_analysis import aggregate_vibe_report, analyze_review

logger = logging.getLogger("vibelocate.vibe_report_builder")

_DATA_DIR = Path(__file__).resolve().parents[2] / "data"
_POIS_PATH = _DATA_DIR / "dubai_pois.json"
_REVIEWS_PATH = _DATA_DIR / "synthetic_reviews_pois.json"

RADIUS_METERS = 500.0

_EARTH_RADIUS_M = 6_371_000.0


def haversine_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance between two lat/lng points, in meters."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = math.radians(lat2 - lat1)
    d_lambda = math.radians(lon2 - lon1)

    a = (math.sin(d_phi / 2) ** 2
         + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return _EARTH_RADIUS_M * c


def _load_pois() -> list[dict]:
    if not _POIS_PATH.exists():
        logger.error("POIs file not found at %s", _POIS_PATH)
        return []
    return json.loads(_POIS_PATH.read_text(encoding="utf-8"))


def _load_reviews() -> list[dict]:
    if not _REVIEWS_PATH.exists():
        logger.error("Reviews file not found at %s", _REVIEWS_PATH)
        return []
    return json.loads(_REVIEWS_PATH.read_text(encoding="utf-8"))


def find_nearby_pois(
    latitude: float,
    longitude: float,
    pois: list[dict],
    radius_m: float = RADIUS_METERS,
) -> list[dict]:
    """Returns POIs within `radius_m` meters of the given point."""
    nearby = []
    for poi in pois:
        poi_lat, poi_lng = poi.get("latitude"), poi.get("longitude")
        if poi_lat is None or poi_lng is None:
            continue
        distance = haversine_distance_m(latitude, longitude, poi_lat, poi_lng)
        if distance <= radius_m:
            nearby.append(poi)
    return nearby


def build_vibe_report_for_property(
    property_id: str,
    latitude: float,
    longitude: float,
) -> VibeReport:
    """
    Full pipeline: real property location -> nearby real POIs -> their
    generated reviews -> sentiment analysis -> aggregated VibeReport.

    Degrades gracefully (US-08 Scenario 2 / NFR3.01): if no POIs are
    nearby, or none of them have reviews yet, returns a
    "pending_more_data" report instead of erroring out — this is a
    legitimate outcome for properties in newly-covered or sparse areas.
    """
    pois = _load_pois()
    all_reviews = _load_reviews()

    nearby_pois = find_nearby_pois(latitude, longitude, pois)
    nearby_names = {poi["name"] for poi in nearby_pois}

    matched_reviews = [r for r in all_reviews if r.get("place_name") in nearby_names]

    logger.info(
        "Vibe report for property %s: %d POIs within %.0fm, %d reviews matched",
        property_id, len(nearby_pois), RADIUS_METERS, len(matched_reviews),
    )

    sentiment_results = [
        analyze_review(ReviewIn(text=r["text"], source=r.get("source", "synthetic_deepseek_v1")))
        for r in matched_reviews
    ]

    report = aggregate_vibe_report(sentiment_results)
    # Rebuild with property_id included rather than mutating after
    # construction — more robust across Pydantic versions/configs.
    return report.model_copy(update={"property_id": property_id})