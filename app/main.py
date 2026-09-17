from fastapi import FastAPI

from app.routers import property_search, reviews, search

app = FastAPI(
    title="VibeLocate AI — Inference Service",
    description="AI & NLP Processing Service (Chapter 4.1.1, tier 4 of the architecture).",
    version="0.1.0",
)

app.include_router(search.router)
app.include_router(reviews.router)
app.include_router(property_search.router)


@app.get("/health")
def health():
    return {"status": "ok"}
from app.routers import property_vibe  # ضيف هالاستيراد

app.include_router(property_vibe.router)  # ضيف هالسطر بعد باقي include_router