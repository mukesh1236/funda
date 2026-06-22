"""Macro/market headlines from Yahoo Finance indices + CNBC + MarketWatch RSS.
All fetches are best-effort — failures return [] so the digest degrades
gracefully. Results are cached 30 min (suitable for an on-demand UX)."""
import logging
import xml.etree.ElementTree as ET
from typing import List

import httpx
import yfinance as yf
from cachetools import TTLCache

logger = logging.getLogger(__name__)

_CACHE: TTLCache = TTLCache(maxsize=16, ttl=1800)

# RSS feeds per market.
_RSS_FEEDS = {
    "us": {
        "CNBC Markets": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "MarketWatch": "https://feeds.marketwatch.com/marketwatch/topstories/",
    },
    "in": {
        "Economic Times": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
        "Moneycontrol": "https://www.moneycontrol.com/rss/marketreports.xml",
        "Business Standard": "https://www.business-standard.com/rss/markets-106.rss",
    },
}

# Index tickers whose news feeds proxy "the market" headlines, per market.
_MARKET_SYMBOLS = {
    "us": ["^GSPC", "^DJI", "^IXIC"],          # S&P 500, Dow, Nasdaq
    "in": ["^NSEI", "^BSESN"],                  # Nifty 50, Sensex
}


def _parse_rss(xml_text: str, source: str, limit: int = 8) -> List[dict]:
    items = []
    try:
        root = ET.fromstring(xml_text)
        channel = root.find("channel")
        if channel is None:
            return []
        for item in channel.findall("item")[:limit]:
            title = (item.findtext("title") or "").strip()
            url = (item.findtext("link") or "").strip()
            pub = (item.findtext("pubDate") or "").strip()
            if title:
                items.append({"title": title, "url": url or None,
                              "published": pub or None, "source": source})
    except ET.ParseError as e:
        logger.debug("RSS parse error (%s): %s", source, e)
    return items


def _fetch_rss(url: str, source: str, limit: int = 8) -> List[dict]:
    try:
        with httpx.Client(timeout=10, headers={"User-Agent": "Mozilla/5.0"}) as client:
            resp = client.get(url)
            resp.raise_for_status()
            return _parse_rss(resp.text, source, limit)
    except Exception as e:
        logger.info("RSS fetch failed (%s): %s", source, e)
        return []


def _fetch_yahoo_market_news(market: str = "us", limit: int = 10) -> List[dict]:
    """News items from the market's index tickers on Yahoo Finance."""
    items: List[dict] = []
    seen: set = set()
    for sym in _MARKET_SYMBOLS.get(market, _MARKET_SYMBOLS["us"]):
        try:
            raw = yf.Ticker(sym).news or []
            for it in raw[:limit]:
                c = it.get("content", it) if isinstance(it, dict) else {}
                title = (c.get("title") or it.get("title") or "").strip()
                if not title or title in seen:
                    continue
                seen.add(title)
                provider = c.get("provider") or {}
                publisher = (
                    provider.get("displayName") if isinstance(provider, dict) else None
                ) or it.get("publisher") or "Yahoo Finance"
                url = None
                for k in ("canonicalUrl", "clickThroughUrl"):
                    v = c.get(k)
                    if isinstance(v, dict) and v.get("url"):
                        url = v["url"]
                        break
                url = url or it.get("link")
                items.append({
                    "title": title, "url": url,
                    "published": c.get("pubDate") or it.get("providerPublishTime"),
                    "source": publisher,
                })
        except Exception as e:
            logger.info("Yahoo market news failed (%s): %s", sym, e)
    return items[:limit]


def fetch_macro_headlines(market: str = "us", limit_per_source: int = 8) -> List[dict]:
    """Merge Yahoo + RSS headlines for one market, deduped by title. Cached 30 min."""
    market = market if market in _RSS_FEEDS else "us"
    cache_key = f"macro_headlines:{market}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    items: List[dict] = []
    seen: set = set()

    for item in _fetch_yahoo_market_news(market=market, limit=limit_per_source):
        key = item["title"].lower()
        if key not in seen:
            seen.add(key)
            items.append(item)

    for source_name, url in _RSS_FEEDS[market].items():
        for item in _fetch_rss(url, source_name, limit=limit_per_source):
            key = item["title"].lower()
            if key not in seen:
                seen.add(key)
                items.append(item)

    _CACHE[cache_key] = items
    return items
