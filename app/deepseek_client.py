"""
Thin wrapper around the LLM provider's OpenAI-compatible chat completions
API. UPDATED: every call now records a metrics event (app/services/ai_metrics.py)
so the admin "AI service health" page can show real numbers instead of
placeholders.
"""

import json
import logging
import time

from openai import APIError, APITimeoutError, OpenAI, RateLimitError

from app.config import settings
from app.services.ai_metrics import record_event

logger = logging.getLogger("vibelocate.llm")

_client = OpenAI(
    api_key=settings.deepseek_api_key,
    base_url=settings.deepseek_base_url,
    timeout=settings.llm_timeout_seconds,
)

MAX_RATE_LIMIT_RETRIES = 5
BASE_BACKOFF_SECONDS = 3.0
MAX_BACKOFF_SECONDS = 15.0


class DeepSeekUnavailableError(Exception):
    pass


def _call_once(model: str, system_prompt: str, user_prompt: str) -> dict:
    last_error = None
    for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
        try:
            response = _client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system_prompt},
                          {"role": "user", "content": user_prompt}],
                response_format={"type": "json_object"}, temperature=0.1,
            )
            if not response.choices:
                raise DeepSeekUnavailableError(f"Empty response from {model} (no_choices)")
            content = response.choices[0].message.content
            if not content:
                raise DeepSeekUnavailableError(f"Empty content from {model}")
            return json.loads(content)

        except RateLimitError as exc:
            last_error = ("rate_limited", exc)
            if attempt < MAX_RATE_LIMIT_RETRIES:
                delay = min(BASE_BACKOFF_SECONDS * (2 ** attempt), MAX_BACKOFF_SECONDS)
                logger.warning("Model %s rate-limited (attempt %d/%d), retrying in %.1fs",
                                model, attempt + 1, MAX_RATE_LIMIT_RETRIES, delay)
                time.sleep(delay)
                continue
            logger.error("Model %s still rate-limited after %d retries", model, MAX_RATE_LIMIT_RETRIES)

        except APITimeoutError as exc:
            last_error = ("timeout", exc)
            logger.error("Model %s timed out: %s", model, exc)
            raise DeepSeekUnavailableError(str(exc)) from exc

        except APIError as exc:
            last_error = ("other_error", exc)
            logger.error("Model %s call failed: %s", model, exc)
            raise DeepSeekUnavailableError(str(exc)) from exc

        except (json.JSONDecodeError, IndexError, AttributeError, TypeError) as exc:
            last_error = ("malformed", exc)
            logger.error("Model %s returned malformed response: %s", model, exc)
            raise DeepSeekUnavailableError("Malformed LLM response") from exc

    outcome, err = last_error
    raise DeepSeekUnavailableError(str(err))


def call_json(system_prompt: str, user_prompt: str, endpoint: str = "unknown") -> dict:
    """
    Tries the primary model, then the fallback if configured. Records
    ONE metrics event for the whole call (endpoint, which model actually
    served it, outcome, total latency, and whether fallback was used) —
    this is exactly what the admin health page reads.
    """
    start = time.time()
    used_fallback = False
    final_model = settings.deepseek_model
    outcome = "other_error"

    try:
        result = _call_once(settings.deepseek_model, system_prompt, user_prompt)
        outcome = "success"
        return result

    except DeepSeekUnavailableError as primary_error:
        primary_outcome = ("rate_limited" if "rate" in str(primary_error).lower()
                            else "timeout" if "time" in str(primary_error).lower()
                            else "no_choices" if "no_choices" in str(primary_error).lower()
                            else "other_error")
        if not settings.deepseek_fallback_model:
            outcome = primary_outcome
            raise

        logger.warning("Primary model (%s) failed: %s — trying fallback (%s)",
                        settings.deepseek_model, primary_error, settings.deepseek_fallback_model)
        used_fallback = True
        final_model = settings.deepseek_fallback_model
        try:
            result = _call_once(settings.deepseek_fallback_model, system_prompt, user_prompt)
            outcome = "success"
            return result
        except DeepSeekUnavailableError as fallback_error:
            outcome = "other_error"
            raise DeepSeekUnavailableError(
                f"Both primary and fallback models failed. "
                f"Primary: {primary_error} | Fallback: {fallback_error}"
            ) from fallback_error

    finally:
        record_event(
            endpoint=endpoint,
            model=final_model,
            outcome=outcome,
            latency_seconds=time.time() - start,
            extra={"used_fallback": used_fallback},
        )