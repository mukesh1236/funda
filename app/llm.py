"""Shared best-effort LLM calls for the per-stock "why analysts recommend it"
narrative and the daily macro digest. Supports a local Ollama model or Google
Gemini's free API. Always degrades gracefully — None on any failure/timeout."""
import logging
import time
from typing import List, Optional

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

# Last failure reason from any LLM call — surfaced in /api/health for
# diagnosis. (Name kept for backwards compatibility; it covers all providers.)
last_gemini_error: Optional[str] = None

_metrics_store = None


def _record(provider: str, model: Optional[str], ok: bool, started: float,
            prompt_tokens: Optional[int] = None,
            completion_tokens: Optional[int] = None,
            error: Optional[str] = None) -> None:
    """Best-effort AI-usage telemetry (tokens, latency, outcome) into the
    llm_calls table — must never break or slow a user-facing request."""
    global _metrics_store
    try:
        if _metrics_store is None:
            from app.config import get_settings
            from app.store import RecommendationStore
            _metrics_store = RecommendationStore(get_settings().recommendations_db_path)
        _metrics_store.add_llm_call(
            provider=provider, model=model, ok=ok,
            latency_ms=round((time.perf_counter() - started) * 1000, 1),
            prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
            error=(error or "")[:300] or None,
        )
    except Exception as e:
        logger.debug("llm metrics skipped: %s", e)


# Ranked fallback chain of free open-source models. The configured model is
# always tried first; these cover model renames/retirements/rate limits so a
# single stale slug can never silently kill the whole AI feature.
_OPENROUTER_FREE_FALLBACKS = [
    "deepseek/deepseek-chat-v3.1:free",
    "deepseek/deepseek-chat-v3-0324:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "qwen/qwen-2.5-72b-instruct:free",
    "mistralai/mistral-small-3.2-24b-instruct:free",
]
_openrouter_catalog: dict = {"ts": 0.0, "free": None}   # live :free slugs, 24h cache


def _openrouter_candidates(settings: Settings) -> List[str]:
    """Configured model first, then known-good free models — filtered against
    OpenRouter's live catalog when reachable so we never retry dead slugs."""
    candidates = [settings.openrouter_model] + [
        m for m in _OPENROUTER_FREE_FALLBACKS if m != settings.openrouter_model
    ]
    now = time.time()
    if _openrouter_catalog["free"] is None or now - _openrouter_catalog["ts"] > 24 * 3600:
        try:
            resp = httpx.get("https://openrouter.ai/api/v1/models", timeout=10)
            if resp.status_code == 200:
                _openrouter_catalog["free"] = {
                    m["id"] for m in resp.json().get("data", []) if m["id"].endswith(":free")
                }
                _openrouter_catalog["ts"] = now
        except Exception as e:
            logger.debug("OpenRouter catalog fetch failed: %s", e)
    live = _openrouter_catalog["free"]
    if live:
        filtered = [m for m in candidates if m in live or not m.endswith(":free")]
        if filtered:
            return filtered
        # None of our hardcoded fallbacks are still live (OpenRouter renamed/
        # retired them) — better to try real current free slugs than to keep
        # retrying ones we already know are dead.
        return sorted(live)
    return candidates


def generate_narrative(prompt: str, settings: Settings, timeout: float = 20) -> Optional[str]:
    """Route to the configured LLM provider. None if disabled or unreachable.

    Provider selection (settings.summary_provider):
      "gemini" → Gemini API
      "grok"   → xAI Grok API (OpenAI-compatible, free tier)
      "ollama" → local Ollama
      "auto"   → Grok if GROK_API_KEY set, else Gemini if GEMINI_API_KEY set, else Ollama
      anything else (e.g. "rule") → None (rule summary only)
    """
    provider = settings.summary_provider
    if provider == "gemini":
        return gemini_generate(prompt, settings, timeout)
    if provider == "grok":
        return grok_generate(prompt, settings, timeout)
    if provider == "openrouter":
        return openrouter_generate(prompt, settings, timeout)
    if provider == "ollama":
        return ollama_generate(prompt, settings, timeout)
    if provider == "auto":
        # Cheapest-first: OpenRouter free open-source models cost nothing.
        if settings.openrouter_api_key:
            answer = openrouter_generate(prompt, settings, timeout)
            if answer:
                return answer
        if settings.grok_api_key:
            return grok_generate(prompt, settings, timeout)
        if settings.gemini_api_key:
            return gemini_generate(prompt, settings, timeout)
        return ollama_generate(prompt, settings, timeout)
    return None


def gemini_generate(prompt: str, settings: Settings, timeout: float = 20) -> Optional[str]:
    """Prose from Google Gemini's free API, or None on any failure.
    Sets module-level last_gemini_error with the reason on failure."""
    global last_gemini_error
    last_gemini_error = None
    if not settings.gemini_api_key:
        last_gemini_error = "GEMINI_API_KEY not set"
        logger.info("Gemini selected but GEMINI_API_KEY is not set.")
        return None
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{settings.gemini_model}:generateContent"
    )
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                url,
                headers={"x-goog-api-key": settings.gemini_api_key},
                json={"contents": [{"parts": [{"text": prompt}]}]},
            )
            if resp.status_code != 200:
                # Surface the real cause (bad key, wrong model, quota).
                last_gemini_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                logger.warning(
                    "Gemini HTTP %s for model %s: %s",
                    resp.status_code, settings.gemini_model, resp.text[:300],
                )
                _record("gemini", settings.gemini_model, False, started,
                        error=f"HTTP {resp.status_code}")
                return None
            data = resp.json()
            text = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            ).strip()
            usage = data.get("usageMetadata", {})
            if not text:
                last_gemini_error = f"empty response: {str(data)[:200]}"
                logger.warning("Gemini returned no text: %s", str(data)[:300])
            _record("gemini", settings.gemini_model, bool(text), started,
                    prompt_tokens=usage.get("promptTokenCount"),
                    completion_tokens=usage.get("candidatesTokenCount"),
                    error=None if text else "empty response")
            return text or None
    except Exception as e:
        last_gemini_error = f"{type(e).__name__}: {e}"
        logger.warning("Gemini call failed: %s", e)
        _record("gemini", settings.gemini_model, False, started, error=type(e).__name__)
        return None


def grok_generate(prompt: str, settings: Settings, timeout: float = 20) -> Optional[str]:
    """Prose from xAI Grok's free API (OpenAI-compatible), or None on failure."""
    global last_gemini_error
    if not settings.grok_api_key:
        logger.info("Grok selected but GROK_API_KEY is not set.")
        return None
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                "https://api.x.ai/v1/chat/completions",
                headers={"Authorization": f"Bearer {settings.grok_api_key}"},
                json={
                    "model": settings.grok_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 512,
                },
            )
            if resp.status_code != 200:
                last_gemini_error = f"Grok HTTP {resp.status_code}: {resp.text[:200]}"
                logger.warning("Grok HTTP %s: %s", resp.status_code, resp.text[:300])
                _record("grok", settings.grok_model, False, started,
                        error=f"HTTP {resp.status_code}")
                return None
            data = resp.json()
            text = (
                data.get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                or ""
            ).strip()
            usage = data.get("usage", {})
            _record("grok", settings.grok_model, bool(text), started,
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    error=None if text else "empty response")
            return text or None
    except Exception as e:
        last_gemini_error = f"Grok {type(e).__name__}: {e}"
        logger.warning("Grok call failed: %s", e)
        _record("grok", settings.grok_model, False, started, error=type(e).__name__)
        return None


def openrouter_generate(prompt: str, settings: Settings, timeout: float = 30) -> Optional[str]:
    """Prose from OpenRouter (OpenAI-compatible), with a fallback CHAIN of
    free models: the configured model is tried first, and model-level
    failures (bad slug, rate limit, provider outage) roll over to the next
    known-good ':free' model instead of killing the AI feature. None only
    when every candidate fails."""
    global last_gemini_error
    if not settings.openrouter_api_key:
        last_gemini_error = "OPENROUTER_API_KEY not set"
        logger.info("OpenRouter selected but OPENROUTER_API_KEY is not set.")
        return None

    errors: List[str] = []
    deadline = time.time() + max(timeout, 45)   # total budget across the chain
    with httpx.Client(timeout=timeout) as client:
        for model in _openrouter_candidates(settings)[:4]:
            if time.time() > deadline:
                errors.append("chain deadline reached")
                break
            attempt_started = time.perf_counter()
            try:
                resp = client.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={
                        "Authorization": f"Bearer {settings.openrouter_api_key}",
                        # Optional attribution headers OpenRouter recommends:
                        "HTTP-Referer": settings.app_base_url,
                        "X-Title": "AlphaFunds Analyst Tracker",
                    },
                    json={
                        "model": model,
                        "messages": [{"role": "user", "content": prompt}],
                        "max_tokens": 700,
                    },
                )
                if resp.status_code in (401, 403):
                    # Key problem — no other model will fix this; stop early.
                    last_gemini_error = f"OpenRouter auth failed (HTTP {resp.status_code}) — check OPENROUTER_API_KEY"
                    logger.warning(last_gemini_error)
                    _record("openrouter", model, False, attempt_started,
                            error=f"auth HTTP {resp.status_code}")
                    return None
                if resp.status_code != 200:
                    errors.append(f"{model}: HTTP {resp.status_code}")
                    logger.warning("OpenRouter HTTP %s for model %s: %s",
                                   resp.status_code, model, resp.text[:200])
                    _record("openrouter", model, False, attempt_started,
                            error=f"HTTP {resp.status_code}")
                    continue   # rate limit / bad slug / provider error → next model
                data = resp.json()
                text = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    or ""
                ).strip()
                usage = data.get("usage", {})
                _record("openrouter", model, bool(text), attempt_started,
                        prompt_tokens=usage.get("prompt_tokens"),
                        completion_tokens=usage.get("completion_tokens"),
                        error=None if text else "empty response")
                if text:
                    if model != settings.openrouter_model:
                        logger.info("OpenRouter answered via fallback model %s", model)
                    last_gemini_error = None
                    return text
                errors.append(f"{model}: empty response")
            except Exception as e:
                errors.append(f"{model}: {type(e).__name__}")
                logger.warning("OpenRouter call failed for %s: %s", model, e)
                _record("openrouter", model, False, attempt_started, error=type(e).__name__)

    last_gemini_error = "OpenRouter exhausted all models — " + "; ".join(errors[:4])
    return None


def ollama_generate(prompt: str, settings: Settings, timeout: float = 20) -> Optional[str]:
    """Prose from a local Ollama model, or None if unreachable/misconfigured.

    Kept callable directly for backwards compatibility; prefer generate_narrative.
    """
    started = time.perf_counter()
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={"model": settings.ollama_model, "prompt": prompt, "stream": False},
            )
            resp.raise_for_status()
            data = resp.json()
            text = (data.get("response") or "").strip()
            _record("ollama", settings.ollama_model, bool(text), started,
                    prompt_tokens=data.get("prompt_eval_count"),
                    completion_tokens=data.get("eval_count"),
                    error=None if text else "empty response")
            return text or None
    except Exception as e:
        logger.info("LLM narrative unavailable (%s).", e)
        _record("ollama", settings.ollama_model, False, started, error=type(e).__name__)
        return None
