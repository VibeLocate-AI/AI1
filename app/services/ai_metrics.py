"""
Lightweight metrics logging for the AI service — powers the future
"صحة خدمة الذكاء الاصطناعي" admin page.

Design choice: a simple append-only JSON Lines file (data/ai_metrics.jsonl),
NOT a database table. This is intentional for now:
- Zero new infrastructure/migrations needed to start collecting real data today.
- Easy to inspect/debug by hand (one JSON object per line).
- Easy to swap for a real DB table later — the recording call site
  (record_event() below) is the only thing that would need to change.

Each event captures exactly what the admin page in the wireframe needs:
endpoint, model used, outcome (success/rate_limited/timeout/no_choices/
malformed/other_error), latency, and timestamp.
"""

import json
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_METRICS_PATH = Path(__file__).resolve().parents[2] / "data" / "ai_metrics.jsonl"


def record_event(
    endpoint: str,
    model: str,
    outcome: str,
    latency_seconds: float,
    extra: Optional[dict] = None,
) -> None:
    """
    Appends one line to data/ai_metrics.jsonl. Never raises — a metrics
    write failing must NEVER break the actual request it's measuring
    (NFR3.01-style: observability is best-effort, not load-bearing).
    """
    event = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "endpoint": endpoint,
        "model": model,
        "outcome": outcome,  # "success" | "rate_limited" | "timeout" | "no_choices" | "malformed" | "other_error"
        "latency_seconds": round(latency_seconds, 3),
    }
    if extra:
        event.update(extra)

    try:
        _METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(_METRICS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        pass  # metrics logging must never crash the actual request


@contextmanager
def timed_event(endpoint: str, model: str):
    """
    Usage:
        with timed_event("ai-contextual", settings.deepseek_model) as ev:
            ... do the call ...
            ev["outcome"] = "success"

    If the block raises before setting ev["outcome"], it's recorded as
    "other_error" automatically — no call site can forget to log a failure.
    """
    ev = {"outcome": "other_error"}
    start = time.time()
    try:
        yield ev
    finally:
        record_event(endpoint, model, ev["outcome"], time.time() - start)


def read_recent_events(limit: int = 200) -> list[dict]:
    """Returns the most recent `limit` events, newest first."""
    if not _METRICS_PATH.exists():
        return []
    lines = _METRICS_PATH.read_text(encoding="utf-8").strip().splitlines()
    events = [json.loads(line) for line in lines[-limit:]]
    return list(reversed(events))


def summarize(events: list[dict]) -> dict:
    """
    Aggregates a list of events into exactly the numbers the admin
    wireframe needs: success rate, fallback trigger count, avg latency,
    and a breakdown of failure reasons.
    """
    if not events:
        return {
            "total_requests": 0,
            "success_rate": None,
            "avg_latency_seconds": None,
            "fallback_triggers": 0,
            "outcome_breakdown": {},
        }

    total = len(events)
    successes = sum(1 for e in events if e["outcome"] == "success")
    fallback_triggers = sum(1 for e in events if e.get("used_fallback"))
    avg_latency = sum(e["latency_seconds"] for e in events) / total

    breakdown: dict[str, int] = {}
    for e in events:
        breakdown[e["outcome"]] = breakdown.get(e["outcome"], 0) + 1

    return {
        "total_requests": total,
        "success_rate": round(successes / total, 3),
        "avg_latency_seconds": round(avg_latency, 3),
        "fallback_triggers": fallback_triggers,
        "outcome_breakdown": breakdown,
    }