"""Daily closing prices back to a chosen start date, for the what-if calculator
and the scoreboard. Adjusted for splits and dividends. Empty on any failure.

Separate from prices.py (latest price) and profiles.py (a 3-month or 1-year window
for the watchlist) because these need an arbitrary start date, and the scoreboard
needs many symbols at once.
"""
import logging
from datetime import date
from typing import Dict, List, Tuple

from cachetools import TTLCache

logger = logging.getLogger(__name__)

# Bounded: the oldest series is dropped first (ADR 0010). Each entry is a few
# hundred (date, float) pairs, so 512 of them is a few MB at most.
_CLOSES_CACHE: TTLCache = TTLCache(maxsize=512, ttl=3600)

_CHUNK = 50


def _period_for(start: date) -> str:
    days = (date.today() - start).days
    if days <= 85:
        return "3mo"
    if days <= 180:
        return "6mo"
    if days <= 370:
        return "1y"
    if days <= 1800:
        return "5y"
    return "10y"


def _to_pairs(close_series) -> List[Tuple[date, float]]:
    out: List[Tuple[date, float]] = []
    for idx, close in close_series.dropna().items():
        d = idx.date() if hasattr(idx, "date") else idx
        if close and float(close) > 0:
            out.append((d, round(float(close), 4)))
    return out


def get_closes(symbol: str, start: date) -> List[Tuple[date, float]]:
    """[(date, close)] ascending, covering at least `start` to today."""
    symbol = symbol.strip().upper()
    period = _period_for(start)
    key = (symbol, period)
    if key in _CLOSES_CACHE:
        return _CLOSES_CACHE[key]
    out: List[Tuple[date, float]] = []
    try:
        import yfinance as yf
        hist = yf.Ticker(symbol).history(period=period, auto_adjust=True)
        if hist is not None and len(hist) and "Close" in hist.columns:
            out = _to_pairs(hist["Close"])
    except Exception as e:
        logger.warning("closes failed for %s: %s", symbol, e)
        out = []
    if out:                       # never cache a failure: it would stick for an hour
        _CLOSES_CACHE[key] = out
    return out


def get_closes_many(symbols: List[str], start: date) -> Dict[str, List[Tuple[date, float]]]:
    """Closes for many symbols in a few chunked downloads, not one request each.
    Symbols with no data are simply absent from the result."""
    period = _period_for(start)
    wanted = sorted({s.strip().upper() for s in symbols if s and s.strip()})
    out: Dict[str, List[Tuple[date, float]]] = {}
    missing: List[str] = []
    for sym in wanted:
        if (sym, period) in _CLOSES_CACHE:
            out[sym] = _CLOSES_CACHE[(sym, period)]
        else:
            missing.append(sym)
    if not missing:
        return out
    try:
        import yfinance as yf
    except ImportError:
        return out
    for i in range(0, len(missing), _CHUNK):
        chunk = missing[i:i + _CHUNK]
        try:
            raw = yf.download(" ".join(chunk), period=period, interval="1d", auto_adjust=True,
                              progress=False, group_by="ticker", threads=True)
        except Exception as e:
            logger.warning("closes chunk failed (%d symbols): %s", len(chunk), e)
            continue
        for sym in chunk:
            try:
                col = raw["Close"] if len(chunk) == 1 else raw[sym]["Close"]
                pairs = _to_pairs(col)
            except Exception:
                continue
            if pairs:
                out[sym] = pairs
                _CLOSES_CACHE[(sym, period)] = pairs
        del raw                   # release the frame before the next chunk
    return out
