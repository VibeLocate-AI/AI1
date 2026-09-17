
"""
Pydantic schemas.

REBUILT FROM SCRATCH following a MAJOR schema change on the backend side.

TIMELINE OF SCHEMA CHANGES (kept here so future-you understands why the
matching logic looks the way it does):

1. First assumption (from live /api/home): 4 property types incl.
   "Townhouse", generic amenity names ("Swimming Pool", "Gym"...).
2. Corrected against an initial SQL dump: 3 types only (Apartment/
   Villa/Penthouse), specific feature names (Infinity Pool, Full Sea
   View, Smart Home, Covered Parking) — turned out to be incomplete
   seed data.
3. Corrected again after the backend team sent the FULL property_types
   table: 16 numeric type IDs (1=Apartment ... 16=Other), confirmed via
   a live property with type_id=12 ("Commercial Shop").
4. **THIS VERSION**: the backend team replaced their entire database
   with a NEW schema (database_final.sql, confirmed as the new official
   source of truth, superseding all of the above). Key differences:
   - NO numeric type_id / property_types lookup table anymore.
     Property type is now a plain string: `property_type_en` /
     `property_type_ar` (e.g. "Apartment", "Villa"), stored directly on
     each property row.
   - NO property_features / amenities table exists in this schema at
     all — there is nothing equivalent to "Infinity Pool" to match
     against anymore. required_amenities is kept in ParsedCriteria for
     forward-compatibility (and because the LLM naturally extracts this
     kind of thing from user text) but is NOT currently matchable
     against real backend data — see property_matcher.py for how this
     is handled (kept as an unused/advisory filter for now).
   - Currency is a real column (`currency_code`), confirmed as "AED"
     for every current row (no other currency seen yet, and no rentals
     — every row's `purpose_en` is "Sale").
   - `latitude`/`longitude` are present directly on each property row
     (DECIMAL, not nullable) — good news for Vibe Report integration,
     no separate geocoding step needed.

Property type vocabulary (property_type_en), confirmed directly from
the actual INSERT data in database_final.sql (708 properties):
  Apartment, Building, Café, Clinic, Commercial Shop, Hotel, House,
  Land, Office, Restaurant, School, Showroom, Townhouse, Villa,
  Warehouse

NOTE: this list came from inspecting the actual data (a sample of
values that appear at least once), not a separate lookup/enum table —
there's no guarantee this is exhaustive if more property types get
added later with different text.  Matching should be done
case-insensitively and treated as advisory/best-effort rather than a
hard enum, since there's no authoritative type table to validate
against anymore.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Backend-confirmed vocab — from database_final.sql's actual property data
# (NOT a lookup table — see module docstring for why this is advisory).
# ---------------------------------------------------------------------------

KNOWN_PROPERTY_TYPES: list[str] = [
    "Apartment", "Building", "Café", "Clinic", "Commercial Shop",
    "Hotel", "House", "Land", "Office", "Restaurant", "School",
    "Showroom", "Townhouse", "Villa", "Warehouse",
]

# No property_features table exists in the new schema. Kept empty (not
# deleted) so intent_recognition.py's amenity-filtering logic degrades
# to "no valid amenities possible" instead of crashing on a missing name.
KNOWN_FEATURE_NAMES: list[str] = []


# ---------------------------------------------------------------------------
# US-07 — Natural Language AI Search / R2.02
# ---------------------------------------------------------------------------

class Language(str, Enum):
    ar = "ar"
    en = "en"


class QueryRequest(BaseModel):
    """What the Flutter client sends to POST /api/search/ai-contextual."""

    raw_text: str = Field(..., min_length=1, examples=[
        "quiet apartment near modern cafes under 150000 AED",
        "شقة هادية بـ 145000 درهم",
    ])
    language: Optional[Language] = None  # auto-detected if not provided


class ParsedCriteria(BaseModel):
    """
    Structured output of Query.parseWithLLM().
    Shaped to match the NEW backend schema (database_final.sql):
    property_type is now free text (matched case-insensitively against
    KNOWN_PROPERTY_TYPES), not a numeric ID.
    """

    property_type: Optional[str] = None            # e.g. "Apartment" — matched case-insensitively against KNOWN_PROPERTY_TYPES
    max_budget: Optional[float] = None
    budget_currency: Optional[str] = None           # "AED" is the only currency seen in the data so far
    min_bedrooms: Optional[int] = None
    vibe_tags: list[str] = Field(default_factory=list)        # free-form, e.g. ["quiet", "near_cafes"] — not matched to any backend field
    required_amenities: list[str] = Field(default_factory=list)  # advisory only — no amenities table exists in the new schema (see module docstring)
    location_hint: Optional[str] = None             # matched loosely against community/neighborhood/city fields
    confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    needs_clarification: bool = False               # Scenario 2 (US-07): too short / unclear


class Query(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    raw_text: str
    parsed_criteria: Optional[ParsedCriteria] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class PropertySearchResponse(BaseModel):
    """
    The actual end-to-end result: what the AI understood from the user's
    text, PLUS the real matching properties fetched live from the
    backend. `properties` are raw dicts (not strongly typed) since we
    don't own the backend's Property schema — we just pass through
    whatever fields it returns.
    """

    parsed_criteria: ParsedCriteria
    matches_found: int
    properties: list[dict]


# ---------------------------------------------------------------------------
# US-08 / US-10 — Sentiment Analysis & Vibe Report
# ---------------------------------------------------------------------------

class ReviewIn(BaseModel):
    """A single review to be scored, e.g. pulled from the seeded/synthetic dataset."""

    text: str = Field(..., min_length=1)
    source: str = "synthetic_deepseek_v1"


class SentimentResult(BaseModel):
    sentiment_score: float = Field(ge=-1.0, le=1.0)  # -1 negative ... +1 positive
    label: str                                        # "negative" | "neutral" | "positive"
    safety_mentioned: bool = False
    quietness_mentioned: bool = False
    amenities_mentioned: bool = False


class VibeReport(BaseModel):
    property_id: Optional[str] = None                # matches backend's properties.property_id (e.g. "PROP_10001"), NOT a bigint anymore
    safety_score: float = Field(ge=0.0, le=10.0)
    quietness_score: float = Field(ge=0.0, le=10.0)
    amenities_score: float = Field(ge=0.0, le=10.0)
    reviews_analyzed: int = 0
    data_confidence: str = "sufficient"  # "sufficient" | "pending_more_data" (US-08 Scenario 2)
    generated_at: datetime = Field(default_factory=datetime.utcnow)
class PropertyVibeRequest(BaseModel):
    """Request body for POST /api/properties/vibe-report — a real
    property's id + coordinates, used to build its Neighborhood Vibe
    Report from nearby real POIs and their generated reviews."""
    property_id: str = Field(..., examples=["PROP_10211"])
    latitude: float = Field(..., examples=[25.21227])
    longitude: float = Field(..., examples=[55.27946])