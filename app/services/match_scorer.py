"""
Server-side Match Score for /api/search/find-properties.

Usage in the router:
    from app.services.match_scorer import score_matches
    scored = score_matches(matches, criteria)   # sorted, each item has
                                                # match_score / match_reasons / match_breakdown

How it works (no LLM call, deterministic, explainable):
  Components are only counted when they apply, and the final score is
  earned / applicable * 100, so asking for fewer things does NOT inflate it.

  type      15  property type matches the request            (if asked)
  location  15  area / community matches the hint            (if asked)
  budget    15  closeness to budget, over budget = 0         (if asked)
  bedrooms   5  exact = full, more = 80%, fewer = 0          (if asked)
  vibe      30  requested vibe tags vs real POIs in 500 m    (always; general
                livability if no vibe was requested)
  quality   20  area safety (6) + amenities (6) + value for
                money vs other results (5) + verified (3)    (always)

Final score is clamped to 45..99. POIs come from data/dubai_pois.json;
if missing, POI-based parts become neutral (not a penalty).
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from statistics import median
from typing import Any

POIS_PATH = Path(__file__).resolve().parents[2] / "data" / "dubai_pois.json"
RADIUS_M = 500
CELL = 0.005  # ~550 m grid cell, a 3x3 lookup always covers 500 m

W = {"type": 15, "location": 15, "budget": 15, "bedrooms": 5, "vibe": 30, "quality": 20}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def _get(obj: Any, *names: str, default=None):
    for n in names:
        v = obj.get(n) if isinstance(obj, dict) else getattr(obj, n, None)
        if v not in (None, ""):
            return v
    return default


def _num(v) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _haversine_m(lat1, lon1, lat2, lon2) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


@lru_cache(maxsize=1)
def _poi_grid() -> dict:
    grid: dict = defaultdict(list)
    try:
        pois = json.loads(POIS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    for p in pois:
        lat, lon = _num(p.get("latitude")), _num(p.get("longitude"))
        if lat is None or lon is None:
            continue
        key = (int(lat // CELL), int(lon // CELL))
        grid[key].append((lat, lon, p.get("category") or "", p.get("subcategory") or ""))
    return grid


def _nearby_counts(lat: float, lon: float) -> dict[str, int]:
    grid = _poi_grid()
    counts: dict[str, int] = defaultdict(int)
    ci, cj = int(lat // CELL), int(lon // CELL)
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            for plat, plon, cat, sub in grid.get((ci + di, cj + dj), ()):
                if _haversine_m(lat, lon, plat, plon) <= RADIUS_M:
                    counts[cat] += 1
                    if sub:
                        counts[sub] += 1
    return counts


# --------------------------------------------------------------------------
# vibe fit: requested vibe tags vs what is really around the property
# --------------------------------------------------------------------------
def _vibe_fit(tags: list[str], amenities: list[str], c: dict[str, int]) -> tuple[float, list[str]]:
    wanted = [t.lower() for t in (tags or [])] + [a.lower() for a in (amenities or [])]
    reasons: list[str] = []
    if not wanted:  # no vibe asked: general livability
        n = c.get("amenities", 0)
        return min(1.0, n / 6), ([f"{n} amenities within 500 m"] if n else [])

    scores = []
    for t in wanted:
        if any(k in t for k in ("quiet", "calm", "peace")):
            noisy, parks = c.get("quietness_negative", 0), c.get("quietness_positive", 0)
            scores.append((1.0 if noisy == 0 else max(0.0, 1 - noisy / 4)) * 0.8 + min(parks, 2) / 2 * 0.2)
            reasons.append("no bars or clubs nearby" if noisy == 0
                           else f"{noisy} nightlife venue(s) within 500 m (less quiet)")
        elif any(k in t for k in ("safe", "secur")):
            n = c.get("safety", 0)
            scores.append(min(1.0, n / 3))
            if n:
                reasons.append(f"{n} police/hospital/clinic within 500 m")
        elif any(k in t for k in ("cafe", "coffee")):
            n = c.get("cafe", 0)
            scores.append(min(1.0, n / 3))
            if n:
                reasons.append(f"{n} cafes within 500 m")
        elif any(k in t for k in ("restaurant", "food")):
            n = c.get("restaurant", 0)
            scores.append(min(1.0, n / 5))
            if n:
                reasons.append(f"{n} restaurants within 500 m")
        elif any(k in t for k in ("park", "green")):
            n = c.get("park", 0) or c.get("quietness_positive", 0)
            scores.append(min(1.0, n / 2))
            if n:
                reasons.append("park nearby")
        elif any(k in t for k in ("metro", "transit", "station")):
            n = c.get("public_transport", 0) or c.get("station", 0)
            scores.append(min(1.0, n / 2))
            if n:
                reasons.append("public transport nearby")
        else:
            scores.append(0.5)  # unknown tag: neutral, never punish
    return (sum(scores) / len(scores) if scores else 0.5), reasons[:3]


# --------------------------------------------------------------------------
# main scoring
# --------------------------------------------------------------------------
def _ppsf(prop) -> float | None:
    price, area = _num(_get(prop, "price", "price_aed", "amount")), _num(_get(prop, "area_sqft", "area"))
    return price / area if price and area else None


def score_one(prop: Any, criteria: Any, median_ppsf: float | None = None) -> dict:
    earned: dict[str, float] = {}
    applicable: dict[str, float] = {}
    reasons: list[str] = []

    def add(name: str, fit: float):
        applicable[name] = W[name]
        earned[name] = W[name] * max(0.0, min(1.0, fit))

    # ---- type -----------------------------------------------------------
    ptype = getattr(criteria, "property_type", None)
    if ptype:
        have = " ".join(str(_get(prop, k, default="")) for k in
                        ("property_type_en", "property_type_ar", "property_subtype_en",
                         "property_subtype_ar", "category_en", "category_ar")).lower()
        ok = str(ptype).lower() in have
        add("type", 1.0 if ok else 0.0)
        if ok:
            reasons.append(f"type: {ptype}")

    # ---- location -------------------------------------------------------
    loc = getattr(criteria, "location_hint", None)
    if loc:
        hay = " ".join(str(_get(prop, k, default="")) for k in
                       ("community_en", "community_ar", "neighborhood_en", "neighborhood_ar",
                        "city_en", "address_en", "address_ar", "title_en")).lower()
        ok = str(loc).lower() in hay
        add("location", 1.0 if ok else 0.0)
        if ok:
            reasons.append(f"in {loc}")

    # ---- budget ---------------------------------------------------------
    price = _num(_get(prop, "price", "price_aed", "amount"))
    max_b, min_b = getattr(criteria, "max_budget", None), getattr(criteria, "min_budget", None)
    if price and max_b:
        ratio = price / max_b
        add("budget", (0.6 + 0.4 * ratio) if ratio <= 1 else 0.0)
        reasons.append(f"{round(ratio * 100)}% of your budget" if ratio <= 1 else "over budget")
    elif price and min_b:
        add("budget", min(1.0, 0.6 + 0.4 * (min_b / price)))

    # ---- bedrooms -------------------------------------------------------
    beds_req = getattr(criteria, "min_bedrooms", None)
    beds = _num(_get(prop, "bedrooms", "beds"))
    if beds_req is not None and beds is not None:
        add("bedrooms", 1.0 if beds == beds_req else 0.8 if beds > beds_req else 0.0)
        if beds >= beds_req:
            reasons.append(f"{int(beds)} bedroom(s)")

    # ---- POI-based parts (vibe + area quality) --------------------------
    lat, lon = _num(_get(prop, "latitude", "lat")), _num(_get(prop, "longitude", "lng", "lon"))
    have_pois = lat is not None and lon is not None and bool(_poi_grid())
    counts = _nearby_counts(lat, lon) if have_pois else {}

    if have_pois:
        fit, vr = _vibe_fit(getattr(criteria, "vibe_tags", []),
                            getattr(criteria, "required_amenities", []), counts)
        reasons += vr
    else:
        fit = 0.5
    add("vibe", fit)

    # ---- quality: always on (area safety, amenities, value, verified) ---
    q_safety = min(1.0, counts.get("safety", 0) / 3) if have_pois else 0.5
    q_amen = min(1.0, counts.get("amenities", 0) / 6) if have_pois else 0.5
    pp = _ppsf(prop)
    if pp and median_ppsf:
        r = pp / median_ppsf
        q_value = 1.0 if r <= 0.8 else 0.0 if r >= 1.5 else 1 - (r - 0.8) / 0.7
        if r <= 0.9:
            reasons.append("good value per sq ft vs similar results")
    else:
        q_value = 0.5
    q_ver = 1.0 if str(_get(prop, "verified", default="0")) in ("1", "True", "true") else 0.0
    if q_ver:
        reasons.append("verified listing")
    add("quality", (q_safety * 6 + q_amen * 6 + q_value * 5 + q_ver * 3) / 20)

    total_applicable = sum(applicable.values())
    raw = 100 * sum(earned.values()) / total_applicable if total_applicable else 50
    score = max(45, min(99, round(raw)))
    breakdown = {k: round(100 * earned[k] / applicable[k]) for k in applicable}
    return {"score": score, "reasons": reasons, "breakdown": breakdown}


def score_matches(matches: list, criteria: Any) -> list[dict]:
    """Scores every match, adds match_score / match_reasons / match_breakdown,
    returns new dicts sorted from best to worst."""
    ppsfs = [x for x in (_ppsf(p) for p in matches) if x]
    med = median(ppsfs) if ppsfs else None
    out = []
    for p in matches:
        r = score_one(p, criteria, med)
        item = dict(p)
        item["match_score"] = r["score"]
        item["match_reasons"] = r["reasons"]
        item["match_breakdown"] = r["breakdown"]
        out.append(item)
    out.sort(key=lambda x: x["match_score"], reverse=True)
    return out


def score_property(prop: Any, criteria: Any) -> tuple[int, list[str]]:
    """Backwards-compatible single-property helper."""
    r = score_one(prop, criteria)
    return r["score"], r["reasons"]