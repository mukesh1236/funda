"""Best-effort Morningstar enrichment.

Morningstar has no public API and actively changes its markup / blocks bots, so
this is intentionally fault-tolerant: any failure (block, timeout, layout
change, parse error) returns None and logs a warning — it must never crash the
daily job. When it does succeed it contributes a single named 'morningstar'
recommendation derived from the star rating.
"""
import logging
import re
from datetime import date
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from cachetools import TTLCache

from app.models import AnalystRecommendation

logger = logging.getLogger(__name__)

_CACHE: TTLCache = TTLCache(maxsize=500, ttl=12 * 3600)
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}


class MorningstarScraper:
    def __init__(self, enabled: bool = True):
        self.enabled = enabled

    def _fetch_html(self, symbol: str) -> Optional[str]:
        # Morningstar quote pages are keyed by exchange; the US quote redirect
        # endpoint resolves most common tickers.
        url = f"https://www.morningstar.com/stocks/xnas/{symbol.lower()}/quote"
        try:
            with httpx.Client(timeout=20, headers=_HEADERS, follow_redirects=True) as c:
                resp = c.get(url)
                if resp.status_code != 200:
                    logger.info("Morningstar %s returned %s", symbol, resp.status_code)
                    return None
                return resp.text
        except Exception as e:
            logger.warning("Morningstar fetch failed for %s: %s", symbol, e)
            return None

    @staticmethod
    def _parse_star_rating(html: str) -> Optional[int]:
        """Extract a 1–5 star rating if present. Returns None when not found."""
        # Morningstar exposes the rating in a few shapes; try a couple of cheap
        # heuristics rather than depending on one brittle selector.
        m = re.search(r'"starRating"\s*:\s*"?([1-5])"?', html)
        if m:
            return int(m.group(1))
        soup = BeautifulSoup(html, "html.parser")
        el = soup.find(attrs={"aria-label": re.compile(r"([1-5])\s*star", re.I)})
        if el:
            lm = re.search(r"([1-5])", el.get("aria-label", ""))
            if lm:
                return int(lm.group(1))
        return None

    def get_analyst_view(
        self, symbol: str, entry_date: Optional[str] = None
    ) -> Optional[AnalystRecommendation]:
        """A single Morningstar-derived call, or None on any failure.

        Star → action mapping: 4–5 → buy (undervalued), 3 → hold,
        1–2 → sell (overvalued).
        """
        if not self.enabled:
            return None
        symbol = symbol.strip().upper()
        if symbol in _CACHE:
            return _CACHE[symbol]

        html = self._fetch_html(symbol)
        if not html:
            return None
        stars = self._parse_star_rating(html)
        if stars is None:
            logger.info("Morningstar: no star rating parsed for %s", symbol)
            return None

        action = "buy" if stars >= 4 else "sell" if stars <= 2 else "hold"
        rec = AnalystRecommendation(
            symbol=symbol,
            source="morningstar",
            action=action,
            count=1,
            firm="Morningstar",
            analyst=None,
            note=f"Morningstar: {stars}-star rating ({'undervalued' if stars >= 4 else 'overvalued' if stars <= 2 else 'fairly valued'})",
            entry_date=entry_date or date.today().isoformat(),
            raw={"star_rating": stars},
        )
        _CACHE[symbol] = rec
        return rec
