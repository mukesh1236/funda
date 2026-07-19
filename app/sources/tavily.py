"""Tavily web search — live grounding for causal/news questions the tracked
analyst dataset can't answer (e.g. "why are semiconductor stocks falling").

Docs: https://docs.tavily.com/documentation/api-reference/endpoint/search

Never raises; returns [] on any failure (no key, network error, bad response)
so a search hiccup never breaks the chat answer — the caller just proceeds
without web context.
"""
import logging
from typing import List, Optional

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

_URL = "https://api.tavily.com/search"


def search_web(query: str, settings: Settings, max_results: int = 4) -> List[dict]:
    """[{title, url, content}] from a live web search, newest-first (topic=news),
    or [] if unconfigured/failed."""
    if not settings.tavily_api_key:
        return []
    try:
        resp = httpx.post(
            _URL,
            json={
                "api_key": settings.tavily_api_key,
                "query": query,
                "topic": "news",
                "search_depth": "basic",
                "max_results": max_results,
            },
            timeout=10,
        )
        if resp.status_code != 200:
            logger.warning("Tavily search failed (%s): %s", resp.status_code, resp.text[:200])
            return []
        return [
            {"title": r.get("title", ""), "url": r.get("url", ""),
             "content": r.get("content", "")}
            for r in resp.json().get("results", [])
        ]
    except Exception as e:
        logger.warning("Tavily search error: %s", e)
        return []
