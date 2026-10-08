from fastapi import APIRouter
from pydantic import Field

from app.schemas import PropertySearchResponse, QueryRequest
from app.services.intent_recognition import parse_query
from app.services.property_matcher import BackendUnavailableError, fetch_all_properties, match_properties
from app.services.match_scorer import score_matches
...
matches = match_properties(criteria, all_properties, limit=10_000)
scored = score_matches(matches, criteria)      # مرتبة من الأعلى
page = scored[request.offset: request.offset + request.limit]

router = APIRouter(prefix="/api/search", tags=["search"])


class FindPropertiesRequest(QueryRequest):
    limit: int = Field(50, ge=1, le=200)
    offset: int = Field(0, ge=0)


@router.post("/find-properties", response_model=PropertySearchResponse)
def find_properties(request: FindPropertiesRequest) -> PropertySearchResponse:
    """
    Natural language text -> parsed criteria -> real properties
    -> matches scored by match_scorer (hard criteria + budget + vibe)
    -> sorted by match_score and paginated.
    """
    criteria = parse_query(request)

    if criteria.needs_clarification:
        return PropertySearchResponse(parsed_criteria=criteria, matches_found=0, properties=[])

    try:
        all_properties = fetch_all_properties()
    except BackendUnavailableError:
        return PropertySearchResponse(parsed_criteria=criteria, matches_found=0, properties=[])

    # limit كبير: نجيب كل المطابق ثم نرتب ونقسّم نحن
    matches = match_properties(criteria, all_properties, limit=10_000)

    scored = []
    for p in matches:
        item = dict(p)
        item["match_score"], item["match_reasons"] = score_property(item, criteria)
        scored.append(item)
    scored.sort(key=lambda p: p["match_score"], reverse=True)

    page = scored[request.offset: request.offset + request.limit]
    return PropertySearchResponse(
        parsed_criteria=criteria,
        matches_found=len(scored),
        properties=page,
    )