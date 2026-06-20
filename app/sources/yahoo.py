"""Yahoo Finance analyst data via yfinance — free, no API key.

Yahoo exposes the same shape as Finnhub: a recommendation trend with
strongBuy/buy/hold/sell/strongSell counts, plus mean/median price targets. We
turn the latest period into buy/hold/sell bucket rows (source="yahoo"), with the
target attached to the buy bucket. Never raises — returns [] on any failure.
"""
import logging
from datetime import date
from typing import List, Optional

import yfinance as yf
from cachetools import TTLCache

from app.models import AnalystRecommendation
from app.sources._mapping import grade_to_action

logger = logging.getLogger(__name__)

_CACHE: TTLCache = TTLCache(maxsize=1000, ttl=6 * 3600)
_UPGRADES_CACHE: TTLCache = TTLCache(maxsize=1000, ttl=6 * 3600)
_NEWS_CACHE: TTLCache = TTLCache(maxsize=1000, ttl=3 * 3600)


def _latest_trend(ticker: "yf.Ticker") -> Optional[dict]:
    """Latest-period recommendation counts as a dict, or None."""
    try:
        df = ticker.recommendations
    except Exception as e:
        logger.warning("yahoo recommendations error: %s", e)
        return None
    if df is None or len(df) == 0:
        return None
    try:
        # yfinance returns rows for periods 0m, -1m, -2m, -3m; 0m is current.
        row = df.iloc[0].to_dict()
    except Exception:
        return None
    return row


def _price_target(ticker: "yf.Ticker") -> Optional[float]:
    """Median (preferred) or mean analyst price target, or None."""
    try:
        pt = ticker.analyst_price_targets  # newer yfinance: dict
        if isinstance(pt, dict):
            val = pt.get("median") or pt.get("mean")
            if val and float(val) > 0:
                return float(val)
    except Exception:
        pass
    try:
        info = ticker.info
        val = info.get("targetMedianPrice") or info.get("targetMeanPrice")
        if val and float(val) > 0:
            return float(val)
    except Exception:
        pass
    return None


class YahooClient:
    def get_recommendations(
        self, symbol: str, entry_date: Optional[str] = None
    ) -> List[AnalystRecommendation]:
        symbol = symbol.strip().upper()
        entry_date = entry_date or date.today().isoformat()
        cache_key = f"{symbol}:{entry_date}"
        if cache_key in _CACHE:
            return _CACHE[cache_key]

        recs: List[AnalystRecommendation] = []
        try:
            ticker = yf.Ticker(symbol)
            trend = _latest_trend(ticker)
            if not trend:
                _CACHE[cache_key] = recs
                return recs

            def _n(*keys) -> int:
                return sum(int(trend.get(k, 0) or 0) for k in keys)

            buy = _n("strongBuy", "buy")
            hold = _n("hold")
            sell = _n("strongSell", "sell")
            target = _price_target(ticker)

            for action, n in (("buy", buy), ("hold", hold), ("sell", sell)):
                if n <= 0:
                    continue
                recs.append(AnalystRecommendation(
                    symbol=symbol, source="yahoo", action=action, count=n,
                    target_price=target if action == "buy" else None,
                    firm=None, entry_date=entry_date,
                    raw={"period": str(trend.get("period", ""))},
                ))
        except Exception as e:  # any yfinance hiccup must not crash the job
            logger.warning("yahoo source failed for %s: %s", symbol, e)
            recs = []

        _CACHE[cache_key] = recs
        return recs


def _compose_note(firm: str, to_grade, from_grade, pt_action, cur_pt, prior_pt) -> str:
    """Human-readable rationale from a Yahoo upgrade/downgrade row."""
    parts = []
    if to_grade and from_grade and to_grade != from_grade:
        parts.append(f"{from_grade} -> {to_grade}")
    elif to_grade:
        parts.append(f"reiterated {to_grade}")
    if pt_action and cur_pt:
        if prior_pt and prior_pt != cur_pt:
            parts.append(f"PT {pt_action.lower()} ${prior_pt:g} -> ${cur_pt:g}")
        else:
            parts.append(f"PT ${cur_pt:g}")
    return f"{firm}: " + ", ".join(parts) if parts else firm


class YahooUpgradesClient:
    """Individual named analyst actions from Yahoo's upgrade/downgrade feed.

    These are display-only detail (firm + grade change + price-target move) — see
    analytics.COUNTING_SOURCES; they are NOT summed into the consensus counts.
    """

    def __init__(self, max_actions: int = 15):
        self.max_actions = max_actions

    def get_recommendations(
        self, symbol: str, entry_date: Optional[str] = None
    ) -> List[AnalystRecommendation]:
        symbol = symbol.strip().upper()
        entry_date = entry_date or date.today().isoformat()
        cache_key = f"{symbol}:{entry_date}"
        if cache_key in _UPGRADES_CACHE:
            return _UPGRADES_CACHE[cache_key]

        recs: List[AnalystRecommendation] = []
        try:
            df = yf.Ticker(symbol).upgrades_downgrades
            if df is not None and len(df):
                for idx, row in df.head(self.max_actions).iterrows():
                    firm = str(row.get("Firm") or "").strip()
                    to_grade = row.get("ToGrade")
                    action = grade_to_action(to_grade)
                    if not firm or not action:
                        continue
                    cur_pt = row.get("currentPriceTarget")
                    cur_pt = float(cur_pt) if cur_pt and float(cur_pt) > 0 else None
                    prior_pt = row.get("priorPriceTarget")
                    prior_pt = float(prior_pt) if prior_pt and float(prior_pt) > 0 else None
                    # The DataFrame index is the grade date.
                    try:
                        gdate = idx.date().isoformat()
                    except Exception:
                        gdate = entry_date or date.today().isoformat()
                    recs.append(AnalystRecommendation(
                        symbol=symbol, source="yahoo_upgrades", action=action,
                        count=1, firm=firm,
                        note=_compose_note(firm, to_grade, row.get("FromGrade"),
                                           row.get("priceTargetAction"), cur_pt, prior_pt),
                        target_price=cur_pt, entry_date=gdate,
                    ))
        except Exception as e:
            logger.warning("yahoo upgrades failed for %s: %s", symbol, e)
            recs = []

        _UPGRADES_CACHE[cache_key] = recs
        return recs


def get_news(symbol: str, limit: int = 5) -> List[dict]:
    """Recent news headlines for context. [] on failure. Each dict has
    title / publisher / url / published."""
    symbol = symbol.strip().upper()
    if symbol in _NEWS_CACHE:
        return _NEWS_CACHE[symbol]

    items: List[dict] = []
    try:
        raw = yf.Ticker(symbol).news or []
        for it in raw[:limit]:
            # Newer yfinance nests fields under "content".
            c = it.get("content", it) if isinstance(it, dict) else {}
            title = c.get("title") or it.get("title")
            if not title:
                continue
            provider = c.get("provider") or {}
            publisher = (provider.get("displayName") if isinstance(provider, dict)
                         else None) or it.get("publisher")
            url = None
            for k in ("canonicalUrl", "clickThroughUrl"):
                v = c.get(k)
                if isinstance(v, dict) and v.get("url"):
                    url = v["url"]
                    break
            url = url or it.get("link")
            items.append({
                "title": title, "publisher": publisher, "url": url,
                "published": c.get("pubDate") or it.get("providerPublishTime"),
            })
    except Exception as e:
        logger.warning("yahoo news failed for %s: %s", symbol, e)
        items = []

    _NEWS_CACHE[symbol] = items
    return items
