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
from cachetools import TTLCache

from app.config import Settings

logger = logging.getLogger(__name__)

_URL = "https://api.tavily.com/search"
# Free tier is capped at 1,000 searches/month — cache repeated/near-duplicate
# questions (e.g. several users asking about the same falling sector the same
# hour) instead of burning quota on an identical query.
_CACHE: TTLCache = TTLCache(maxsize=500, ttl=1800)


def search_web(query: str, settings: Settings, max_results: int = 4) -> List[dict]:
    """[{title, url, content}] from a live web search, newest-first (topic=news),
    or [] if unconfigured/failed."""
    if not settings.tavily_api_key:
        return []
    cache_key = query.strip().lower()
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached
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
        results = [
            {"title": r.get("title", ""), "url": r.get("url", ""),
             "content": r.get("content", "")}
            for r in resp.json().get("results", [])
        ]
        if results:
            _CACHE[cache_key] = results   # only cache non-empty — never cache a miss/failure
        return results
    except Exception as e:
        logger.warning("Tavily search error: %s", e)
        return []
