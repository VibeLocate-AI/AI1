"""
Connects to real property data and matches it against AI-parsed search
criteria.

TEMPORARY DATA SOURCE SWITCH (read this before touching fetch logic):
/api/home was confirmed (via diagnostic logging) to return only ~12
"featured" properties out of the real 708 in the database — it's a
homepage endpoint, not a full catalog endpoint. Until the backend team
confirms a dedicated full-listing/search endpoint, this file reads
ALL properties from a local JSON snapshot (data/all_properties.json)
extracted directly from the backend team's own database_final.sql dump
instead of calling /api/home.

TO SWITCH BACK to a live backend call once a real endpoint exists:
replace the body of fetch_all_properties() with an httpx.get() call
(see git history / earlier version of this file for the HTTP-based
implementation) — match_properties() and everything else below is
unaffected by where the data comes from.
"""

import json
import logging
from pathlib import Path

from app.schemas import ParsedCriteria

logger = logging.getLogger("vibelocate.property_matcher")

_LOCAL_PROPERTIES_PATH = Path(__file__).resolve().parents[2] / "data" / "all_properties.json"


class BackendUnavailableError(Exception):
    """Raised when property data can't be loaded (kept for API compatibility
    with the live-backend version of this function)."""


def fetch_all_properties() -> list[dict]:
    """
    TEMPORARY: reads the full local snapshot (706 properties, extracted
    from database_final.sql) instead of calling /api/home live — see
    module docstring.
    """
    if not _LOCAL_PROPERTIES_PATH.exists():
        raise BackendUnavailableError(
            f"Local properties snapshot not found at {_LOCAL_PROPERTIES_PATH}. "
            f"Make sure data/all_properties.json was extracted from database_final.sql."
        )

    try:
        properties = json.loads(_LOCAL_PROPERTIES_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.error("Failed to load local properties snapshot: %s", exc)
        raise BackendUnavailableError(str(exc)) from exc

    logger.info("fetch_all_properties(): loaded %d properties from local snapshot", len(properties))
    return properties


def _text_matches(needle: str, haystack) -> bool:
    """Case-insensitive substring match; tolerant of None/non-string fields."""
    if not haystack:
        return False
    return needle.strip().lower() in str(haystack).strip().lower()


def match_properties(
    criteria: ParsedCriteria,
    properties: list[dict],
    limit: int = 10,
) -> list[dict]:
    """
    Matching logic for the new schema (database_final.sql) — no numeric
    type_id, no amenities table. See schemas.py's module docstring for
    the full schema history.

    - property_type: case-insensitive exact match against
      property_type_en (or property_type_ar).
    - max_budget: price <= max_budget.
    - min_bedrooms: bedrooms >= min_bedrooms.
    - location_hint: loose substring match against community_en,
      neighborhood_en, or city_en (and their _ar equivalents).
    - required_amenities: NOT enforced — no amenities data exists in
      this schema.
    """
    results = []

    for prop in properties:
        if criteria.property_type:
            type_en = prop.get("property_type_en", "")
            type_ar = prop.get("property_type_ar", "")
            if not (_text_matches(criteria.property_type, type_en)
                    or _text_matches(criteria.property_type, type_ar)):
                continue

        if criteria.max_budget is not None:
            price = prop.get("price")
            try:
                if price is not None and float(price) > criteria.max_budget:
                    continue
            except (TypeError, ValueError):
                pass

        if criteria.min_bedrooms is not None:
            bedrooms = prop.get("bedrooms")
            if isinstance(bedrooms, (int, float)) and bedrooms < criteria.min_bedrooms:
                continue

        if criteria.location_hint:
            location_fields = [
                prop.get("community_en"), prop.get("neighborhood_en"),
                prop.get("city_en"), prop.get("community_ar"),
                prop.get("neighborhood_ar"), prop.get("city_ar"),
            ]
            if not any(_text_matches(criteria.location_hint, f) for f in location_fields):
                continue

        results.append(prop)

    return results[:limit]