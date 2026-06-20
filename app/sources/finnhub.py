"""Finnhub analyst data — recommendation trends + price targets.

Docs: https://finnhub.io/docs/api/recommendation-trends
      https://finnhub.io/docs/api/price-target

We turn Finnhub's aggregate recommendation counts for the latest period into
three AnalystRecommendation rows (buy / hold / sell buckets), each carrying the
analyst count for that bucket. The price target's mean is attached to the buy
bucket so target-hit validation has something to check.
"""
import logging
from datetime import date
from typing import List, Optional

import httpx
from cachetools import TTLCache

from app.models import AnalystRecommendation

logger = logging.getLogger(__name__)

_BASE = "https://finnhub.io/api/v1"
_REC_CACHE: TTLCache = TTLCache(maxsize=1000, ttl=6 * 3600)
_TARGET_CACHE: TTLCache = TTLCache(maxsize=1000, ttl=6 * 3600)


class FinnhubError(RuntimeError):
    pass


class FinnhubClient:
    def __init__(self, api_key: str):
        if not api_key:
            raise FinnhubError(
                "FINNHUB_API_KEY is not set. Get a free key at "
                "https://finnhub.io/register and add it to your .env."
            )
        self.api_key = api_key
        # Persistent client reuses connections across the ~150-symbol daily run.
        self._client = httpx.Client(timeout=30)

    def __del__(self):
        try:
            self._client.close()
        except Exception:
            pass

    def _get(self, path: str, symbol: str) -> Optional[dict | list]:
        try:
            resp = self._client.get(
                f"{_BASE}{path}",
                params={"symbol": symbol, "token": self.api_key},
            )
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                logger.warning("Finnhub rate-limited on %s %s", path, symbol)
            else:
                logger.warning("Finnhub %s failed for %s: %s", path, symbol, e)
            return None
        except Exception as e:
            logger.warning("Finnhub %s error for %s: %s", path, symbol, e)
            return None

    def get_recommendation_trend(self, symbol: str) -> Optional[dict]:
        """Latest-period recommendation counts, or None.

        Returns {strongBuy, buy, hold, sell, strongSell, period}.
        """
        symbol = symbol.strip().upper()
        if symbol in _REC_CACHE:
            return _REC_CACHE[symbol]
        data = self._get("/stock/recommendation", symbol)
        if not isinstance(data, list) or not data:
            return None
        # Finnhub returns newest period first; be defensive and sort by period.
        latest = max(data, key=lambda d: d.get("period", ""))
        _REC_CACHE[symbol] = latest
        return latest

    def get_price_target(self, symbol: str) -> Optional[dict]:
        """{targetHigh, targetLow, targetMean, targetMedian, lastUpdated} or None."""
        symbol = symbol.strip().upper()
        if symbol in _TARGET_CACHE:
            return _TARGET_CACHE[symbol]
        data = self._get("/stock/price-target", symbol)
        if not isinstance(data, dict) or not data.get("targetMean"):
            return None
        _TARGET_CACHE[symbol] = data
        return data

    def get_recommendations(
        self, symbol: str, entry_date: Optional[str] = None
    ) -> List[AnalystRecommendation]:
        """Build AnalystRecommendation rows for one symbol from Finnhub.

        Empty list when Finnhub has no coverage. Never raises on data issues.
        """
        symbol = symbol.strip().upper()
        entry_date = entry_date or date.today().isoformat()
        trend = self.get_recommendation_trend(symbol)
        if not trend:
            return []

        buy = int(trend.get("strongBuy", 0) or 0) + int(trend.get("buy", 0) or 0)
        hold = int(trend.get("hold", 0) or 0)
        sell = int(trend.get("strongSell", 0) or 0) + int(trend.get("sell", 0) or 0)

        target = self.get_price_target(symbol)
        target_mean = None
        if target:
            target_mean = target.get("targetMedian") or target.get("targetMean")
            target_mean = float(target_mean) if target_mean else None

        recs: List[AnalystRecommendation] = []
        period = trend.get("period", "")
        base_raw = {"period": period, "source_endpoint": "recommendation"}
        for action, n in (("buy", buy), ("hold", hold), ("sell", sell)):
            if n <= 0:
                continue
            recs.append(
                AnalystRecommendation(
                    symbol=symbol,
                    source="finnhub",
                    action=action,
                    count=n,
                    # Only the buy bucket gets a target to validate against.
                    target_price=target_mean if action == "buy" else None,
                    firm=None,
                    analyst=None,
                    entry_date=entry_date,
                    raw=base_raw,
                )
            )
        return recs
