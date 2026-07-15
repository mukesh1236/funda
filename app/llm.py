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
                return None
            data = resp.json()
            text = (
                data.get("candidates", [{}])[0]
                .get("content", {})
                .get("parts", [{}])[0]
                .get("text", "")
            ).strip()
            if not text:
                last_gemini_error = f"empty response: {str(data)[:200]}"
                logger.warning("Gemini returned no text: %s", str(data)[:300])
            return text or None
    except Exception as e:
        last_gemini_error = f"{type(e).__name__}: {e}"
        logger.warning("Gemini call failed: %s", e)
        return None


def grok_generate(prompt: str, settings: Settings, timeout: float = 20) -> Optional[str]:
    """Prose from xAI Grok's free API (OpenAI-compatible), or None on failure."""
    global last_gemini_error
    if not settings.grok_api_key:
        logger.info("Grok selected but GROK_API_KEY is not set.")
        return None
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
                return None
            text = (
                resp.json()
                .get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                or ""
            ).strip()
            return text or None
    except Exception as e:
        last_gemini_error = f"Grok {type(e).__name__}: {e}"
        logger.warning("Grok call failed: %s", e)
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
                    return None
                if resp.status_code != 200:
                    errors.append(f"{model}: HTTP {resp.status_code}")
                    logger.warning("OpenRouter HTTP %s for model %s: %s",
                                   resp.status_code, model, resp.text[:200])
                    continue   # rate limit / bad slug / provider error → next model
                text = (
                    resp.json()
                    .get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    or ""
                ).strip()
                if text:
                    if model != settings.openrouter_model:
                        logger.info("OpenRouter answered via fallback model %s", model)
                    last_gemini_error = None
                    return text
                errors.append(f"{model}: empty response")
            except Exception as e:
                errors.append(f"{model}: {type(e).__name__}")
                logger.warning("OpenRouter call failed for %s: %s", model, e)

    last_gemini_error = "OpenRouter exhausted all models — " + "; ".join(errors[:4])
    return None


def ollama_generate(prompt: str, settings: Settings, timeout: float = 20) -> Optional[str]:
    """Prose from a local Ollama model, or None if unreachable/misconfigured.

    Kept callable directly for backwards compatibility; prefer generate_narrative.
    """
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                f"{settings.ollama_base_url}/api/generate",
                json={"model": settings.ollama_model, "prompt": prompt, "stream": False},
            )
            resp.raise_for_status()
            text = (resp.json().get("response") or "").strip()
            return text or None
    except Exception as e:
        logger.info("LLM narrative unavailable (%s).", e)
        return None
