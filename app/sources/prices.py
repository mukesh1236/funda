"""Current stock price via yfinance. Cached 1h; returns None on any failure."""
import logging
from typing import Optional

import yfinance as yf
from cachetools import TTLCache

logger = logging.getLogger(__name__)

_PRICE_CACHE: TTLCache = TTLCache(maxsize=2000, ttl=3600)


def get_current_price(symbol: str) -> Optional[float]:
    """Latest close for `symbol`. None if yfinance has no data / errors."""
    symbol = symbol.strip().upper()
    if symbol in _PRICE_CACHE:
        return _PRICE_CACHE[symbol]

    price: Optional[float] = None
    try:
        ticker = yf.Ticker(symbol)
        # fast_info is the cheapest path; fall back to recent history.
        try:
            fi = ticker.fast_info
            # FastInfo supports attribute access (last_price) reliably; its
            # .get() uses camelCase keys ("lastPrice"), so try attribute
            # access first and only fall back to .get() for older versions.
            last = getattr(fi, "last_price", None)
            if last is None and hasattr(fi, "get"):
                last = fi.get("lastPrice")
            if last and last > 0:
                price = float(last)
        except Exception:
            price = None
        if price is None:
            hist = ticker.history(period="5d")
            if hist is not None and not hist.empty and "Close" in hist.columns:
                close = float(hist["Close"].iloc[-1])
                price = close if close > 0 else None
    except Exception as e:  # network / symbol errors must not crash the job
        logger.warning("price fetch failed for %s: %s", symbol, e)
        price = None

    if price is not None:
        _PRICE_CACHE[symbol] = price
    return price
