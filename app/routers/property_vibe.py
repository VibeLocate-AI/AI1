from fastapi import APIRouter

from app.schemas import PropertyVibeRequest, VibeReport
from app.services.vibe_report_builder import build_vibe_report_for_property

router = APIRouter(prefix="/api/properties", tags=["properties"])


@router.post("/vibe-report", response_model=VibeReport)
def property_vibe_report(request: PropertyVibeRequest) -> VibeReport:
    """
    THE final piece: given a REAL property's id + coordinates (as
    returned by /api/search/find-properties), builds its Neighborhood
    Vibe Report from real nearby POIs (data/dubai_pois.json) and their
    generated reviews (data/synthetic_reviews_pois.json).

    This closes the full loop: user text -> parsed criteria -> matched
    real property -> real Vibe Report for that specific property's
    actual location.
    """
    return build_vibe_report_for_property(
        property_id=request.property_id,
        latitude=request.latitude,
        longitude=request.longitude,
    )