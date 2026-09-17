"""
Intent Recognition service — implements US-07 (Natural Language AI Search).

REBUILT to match the NEW backend schema (database_final.sql) — see
schemas.py's module docstring for the full history of why this changed.
Key difference from the previous version: property_type is now matched
as free text against KNOWN_PROPERTY_TYPES, not converted to a numeric ID.
"""

from app.deepseek_client import DeepSeekUnavailableError, call_json
from app.schemas import KNOWN_PROPERTY_TYPES, ParsedCriteria, QueryRequest

_TYPE_LIST_STR = ", ".join(f'"{t}"' for t in KNOWN_PROPERTY_TYPES)

SYSTEM_PROMPT = f"""You are an intent-extraction engine for a real estate search app.
The user writes in Arabic or English, describing what kind of property they want.
Extract structured search criteria from their text.

Return ONLY a JSON object with exactly these fields:
{{
  "property_type": string or null,        // MUST be one of: {_TYPE_LIST_STR}, or null if not mentioned/unclear
  "max_budget": number or null,            // numeric only, no currency symbol
  "budget_currency": string or null,       // e.g. "AED" — whatever currency the user actually stated. If they said a plain number with no currency, use null.
  "min_bedrooms": integer or null,
  "vibe_tags": array of short lowercase English tags (e.g. ["quiet", "modern", "near_cafes"]),
  "required_amenities": array of short lowercase English tags for anything the user wants that ISN'T the property type/budget/bedrooms/location (e.g. ["pool", "sea_view"]) — free-form, since there is no fixed amenities list to match against,
  "location_hint": string or null,         // any neighborhood/community/city/emirate mentioned, verbatim
  "confidence": number 0.0-1.0,            // how confident you are in this extraction
  "needs_clarification": boolean           // true if the text is too short/vague to search on
}}

Rules:
- If the text is a single vague word (e.g. "nice", "حلو") set needs_clarification=true
  and confidence below 0.3.
- Never invent a budget, currency, or bedroom count that isn't stated or clearly implied.
- vibe_tags and required_amenities must always be arrays, even if empty.
- property_type must match the fixed list exactly (capitalized) or be null — never invent a new type.
"""


def parse_query(request: QueryRequest) -> ParsedCriteria:
    """
    Scenario 1 (Happy Path): normal query -> full structured criteria.
    Scenario 2 (Unclear/short prompt): returns needs_clarification=True
    instead of raising, so the API can prompt the user (per US-07 spec).
    """
    try:
        raw = call_json(SYSTEM_PROMPT, request.raw_text)
    except DeepSeekUnavailableError:
        # NFR3.01: degrade gracefully rather than 500ing the client.
        return ParsedCriteria(confidence=0.0, needs_clarification=True)

    # Case-insensitive match against the known type vocabulary. Since
    # there's no authoritative enum table anymore (see schemas.py), we
    # normalize to the canonical capitalization from KNOWN_PROPERTY_TYPES
    # rather than trusting whatever casing the LLM produced.
    property_type = raw.get("property_type")
    normalized_type = None
    if property_type:
        for known in KNOWN_PROPERTY_TYPES:
            if known.lower() == property_type.strip().lower():
                normalized_type = known
                break

    return ParsedCriteria(
        property_type=normalized_type,
        max_budget=raw.get("max_budget"),
        budget_currency=raw.get("budget_currency"),
        min_bedrooms=raw.get("min_bedrooms"),
        vibe_tags=raw.get("vibe_tags") or [],
        required_amenities=raw.get("required_amenities") or [],
        location_hint=raw.get("location_hint"),
        confidence=float(raw.get("confidence", 0.0)),
        needs_clarification=bool(raw.get("needs_clarification", False)),
    )