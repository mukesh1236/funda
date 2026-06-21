"""Polygon.io analyst recommendations — licensed data source.

Free Starter plan gives 5 requests/minute. Requires POLYGON_API_KEY in .env.
Sign up at https://polygon.io (free tier is enough for this source).
"""
import logging
from datetime import date
from typing import List, Optional

import httpx
from cachetools import TTLCache

from app.models import AnalystRecommendation

logger = logging.getLogger(__name__)
_cache: TTLCache = TTLCache(maxsize=200, ttl=3600)
_BASE = "https://api.polygon.io"

# Polygon uses varied rating strings — map to our 3-bucket system.
_ACTION_MAP = {
    "buy": "buy", "strong buy": "buy", "overweight": "buy",
    "outperform": "buy", "market outperform": "buy", "positive": "buy",
    "hold": "hold", "neutral": "hold", "equal-weight": "hold",
    "equal weight": "hold", "market perform": "hold", "in-line": "hold",
    "sell": "sell", "underweight": "sell", "underperform": "sell",
    "market underperform": "sell", "negative": "sell",
}


class PolygonError(Exception):
    pass


class PolygonClient:
    def __init__(self, api_key: str):
        if not api_key:
            raise PolygonError("POLYGON_API_KEY is not set")
        self.api_key = api_key
        self._client = httpx.Client(timeout=15)

    def __del__(self):
        try:
            self._client.close()
        except Exception:
            pass

    def get_recommendations(
        self, symbol: str, entry_date: Optional[str] = None
    ) -> List[AnalystRecommendation]:
        if symbol in _cache:
            return _cache[symbol]
        try:
            resp = self._client.get(
                f"{_BASE}/vX/analysts/ratings/{symbol}",
                params={"apiKey": self.api_key, "limit": 10, "order": "desc"},
            )
            if resp.status_code == 403:
                logger.info("Polygon: access denied for %s (plan limit?)", symbol)
                return []
            if resp.status_code != 200:
                logger.debug("Polygon: %s returned %d", symbol, resp.status_code)
                return []

            recs = []
            for item in resp.json().get("results", []):
                raw_action = (item.get("rating") or "").lower().strip()
                action = _ACTION_MAP.get(raw_action, "hold")
                firm = (item.get("analyst_details") or {}).get("name")
                recs.append(AnalystRecommendation(
                    symbol=symbol,
                    source="polygon",
                    action=action,
                    count=1,
                    firm=firm,
                    target_price=item.get("target_price"),
                    entry_date=entry_date or date.today().isoformat(),
                    note=f"Polygon: {item.get('rating', '')}",
                ))
            _cache[symbol] = recs
            return recs
        except Exception as e:
            logger.debug("Polygon fetch failed for %s: %s", symbol, e)
            return []
