"""TipRanks consensus — best-effort.

TipRanks has no official free API, but its site is backed by a public JSON
endpoint that returns the analyst consensus (buy/hold/sell counts) and a price
target. We read that endpoint defensively: any failure (block, shape change,
timeout) returns [] and logs — it must never crash the daily job. This is
ToS-grey scraping; disable via TIPRANKS_ENABLED=false if it misbehaves.
"""
import logging
from datetime import date
from typing import List, Optional

import httpx
from cachetools import TTLCache

from app.models import AnalystRecommendation

logger = logging.getLogger(__name__)

_CACHE: TTLCache = TTLCache(maxsize=1000, ttl=12 * 3600)
_URL = "https://www.tipranks.com/api/stocks/getData/"
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


class TipRanksClient:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def _fetch(self, symbol: str) -> Optional[dict]:
        try:
            with httpx.Client(timeout=20, headers=_HEADERS, follow_redirects=True) as c:
                resp = c.get(_URL, params={"name": symbol})
                if resp.status_code != 200:
                    logger.info("TipRanks %s returned %s", symbol, resp.status_code)
                    return None
                return resp.json()
        except Exception as e:
            logger.warning("TipRanks fetch failed for %s: %s", symbol, e)
            return None

    @staticmethod
    def _parse(data: dict) -> tuple[int, int, int, Optional[float]]:
        """Extract (buy, hold, sell, target) from the TipRanks payload.

        The consensus lives under the latest entry of "consensuses"; the price
        target under "ptConsensus" or "priceTarget". All lookups are guarded.
        """
        buy = hold = sell = 0
        consensuses = data.get("consensuses") or []
        if isinstance(consensuses, list) and consensuses:
            latest = consensuses[0]
            buy = int(latest.get("nB", 0) or 0)   # number of Buy ratings
            hold = int(latest.get("nH", 0) or 0)  # number of Hold ratings
            sell = int(latest.get("nS", 0) or 0)  # number of Sell ratings

        target: Optional[float] = None
        pt = data.get("ptConsensus") or []
        if isinstance(pt, list) and pt:
            val = pt[0].get("priceTarget")
            if val and float(val) > 0:
                target = float(val)
        if target is None:
            val = data.get("priceTarget")
            if val and float(val) > 0:
                target = float(val)
        return buy, hold, sell, target

    def get_recommendations(
        self, symbol: str, entry_date: Optional[str] = None
    ) -> List[AnalystRecommendation]:
        if not self.enabled:
            return []
        symbol = symbol.strip().upper()
        entry_date = entry_date or date.today().isoformat()
        if symbol in _CACHE:
            return _CACHE[symbol]

        recs: List[AnalystRecommendation] = []
        data = self._fetch(symbol)
        if isinstance(data, dict):
            try:
                buy, hold, sell, target = self._parse(data)
                for action, n in (("buy", buy), ("hold", hold), ("sell", sell)):
                    if n <= 0:
                        continue
                    recs.append(AnalystRecommendation(
                        symbol=symbol, source="tipranks", action=action, count=n,
                        target_price=target if action == "buy" else None,
                        firm=None, entry_date=entry_date,
                    ))
            except Exception as e:
                logger.warning("TipRanks parse failed for %s: %s", symbol, e)
                recs = []

        _CACHE[symbol] = recs
        return recs
