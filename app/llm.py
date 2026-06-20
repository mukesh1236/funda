"""Shared best-effort call to a local Ollama model. Used by both the
per-stock "why analysts recommend it" narrative and the daily macro digest
narrative. Always degrades gracefully — None on any failure or timeout."""
import logging
from typing import Optional

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)


def ollama_generate(prompt: str, settings: Settings, timeout: float = 20) -> Optional[str]:
    """Prose from the configured local Ollama model, or None if unreachable,
    misconfigured, or disabled (SUMMARY_PROVIDER not in ("ollama", "auto"))."""
    if settings.summary_provider not in ("ollama", "auto"):
        return None
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
