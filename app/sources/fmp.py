"""Financial Modeling Prep — named-firm analyst grades (free key).

Uses the legacy /v3/grade endpoint, which the free tier covers. Each recent
grade action (e.g. "Morgan Stanley → Overweight") becomes one recommendation
with the firm name attached — a nice complement to the count-only aggregate
sources. Returns [] without a key or on any error.
"""
import logging
from datetime import date
from typing import List, Optional

import httpx
from cachetools import TTLCache

from app.models import AnalystRecommendation
from app.sources._mapping import grade_to_action

logger = logging.getLogger(__name__)

_BASE = "https://financialmodelingprep.com/api/v3"
_CACHE: TTLCache = TTLCache(maxsize=1000, ttl=6 * 3600)


class FMPError(RuntimeError):
    pass


class FMPClient:
    def __init__(self, api_key: str, max_grades: int = 20):
        if not api_key:
            raise FMPError("FMP_API_KEY is not set.")
        self.api_key = api_key
        self.max_grades = max_grades

    def _fetch_grades(self, symbol: str) -> Optional[list]:
        try:
            with httpx.Client(timeout=30) as c:
                resp = c.get(f"{_BASE}/grade/{symbol}", params={"apikey": self.api_key})
                resp.raise_for_status()
                data = resp.json()
                return data if isinstance(data, list) else None
        except Exception as e:
            logger.warning("FMP grades failed for %s: %s", symbol, e)
            return None

    def get_recommendations(
        self, symbol: str, entry_date: Optional[str] = None
    ) -> List[AnalystRecommendation]:
        symbol = symbol.strip().upper()
        entry_date = entry_date or date.today().isoformat()
        if symbol in _CACHE:
            return _CACHE[symbol]

        recs: List[AnalystRecommendation] = []
        grades = self._fetch_grades(symbol)
        for g in (grades or [])[: self.max_grades]:
            action = grade_to_action(g.get("newGrade"))
            if not action:
                continue
            firm = g.get("gradingCompany") or "Unknown firm"
            prev, new = g.get("previousGrade"), g.get("newGrade")
            note = f"{firm}: {prev} -> {new}" if prev and prev != new else f"{firm}: {new}"
            recs.append(AnalystRecommendation(
                symbol=symbol, source="fmp", action=action, count=1,
                firm=firm, note=note, entry_date=g.get("date") or entry_date,
                raw={"newGrade": new, "grade_date": g.get("date")},
            ))
        _CACHE[symbol] = recs
        return recs
