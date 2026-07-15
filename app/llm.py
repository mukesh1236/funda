"""Shared best-effort LLM calls for the per-stock "why analysts recommend it"
narrative and the daily macro digest. Supports a local Ollama model or Google
Gemini's free API. Always degrades gracefully — None on any failure/timeout."""
import logging
from typing import Optional

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

# Last failure reason from a Gemini call — surfaced by the chat for diagnosis.
last_gemini_error: Optional[str] = None


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
    """Prose from OpenRouter (OpenAI-compatible). Models ending in ':free'
    (e.g. deepseek/deepseek-chat-v3.1:free) cost nothing — the affordable
    default for this app. None on any failure."""
    global last_gemini_error
    if not settings.openrouter_api_key:
        last_gemini_error = "OPENROUTER_API_KEY not set"
        logger.info("OpenRouter selected but OPENROUTER_API_KEY is not set.")
        return None
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {settings.openrouter_api_key}",
                    # Optional attribution headers OpenRouter recommends:
                    "HTTP-Referer": settings.app_base_url,
                    "X-Title": "AlphaFunds Analyst Tracker",
                },
                json={
                    "model": settings.openrouter_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 700,
                },
            )
            if resp.status_code != 200:
                last_gemini_error = f"OpenRouter HTTP {resp.status_code}: {resp.text[:200]}"
                logger.warning("OpenRouter HTTP %s for model %s: %s",
                               resp.status_code, settings.openrouter_model, resp.text[:300])
                return None
            text = (
                resp.json()
                .get("choices", [{}])[0]
                .get("message", {})
                .get("content", "")
                or ""
            ).strip()
            if not text:
                last_gemini_error = f"OpenRouter empty response: {resp.text[:200]}"
            return text or None
    except Exception as e:
        last_gemini_error = f"OpenRouter {type(e).__name__}: {e}"
        logger.warning("OpenRouter call failed: %s", e)
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
