"""Company name + trailing price returns (1/3/6/12-month) via yfinance.

Fetched during the daily job and cached in the DB so the feed stays fast.
returns_from_closes is a pure helper (no network) for testability.
"""
import bisect
import logging
from datetime import date, timedelta
from typing import Dict, List, Optional

import yfinance as yf
from cachetools import TTLCache

logger = logging.getLogger(__name__)

_HISTORY_CACHE: TTLCache = TTLCache(maxsize=1000, ttl=3600)
_OWNERSHIP_CACHE: TTLCache = TTLCache(maxsize=1000, ttl=12 * 3600)

# (label, lookback in calendar days)
_WINDOWS = (("one_month", 30), ("three_month", 91),
            ("six_month", 182), ("twelve_month", 365))


def returns_from_closes(
    days: List[date], closes: List[float], today: Optional[date] = None
) -> Dict[str, Optional[float]]:
    """Percentage return over each window, using the close on-or-before each
    lookback date. days must be ascending and aligned with closes.
    Returns {label: pct|None}."""
    out: Dict[str, Optional[float]] = {k: None for k, _ in _WINDOWS}
    if not closes or not days or len(days) != len(closes):
        return out
    today = today or days[-1]
    latest = closes[-1]
    if not latest or latest <= 0:
        return out
    for label, lookback in _WINDOWS:
        cutoff = today - timedelta(days=lookback)
        idx_right = bisect.bisect_right(days, cutoff)
        idx = idx_right - 1 if idx_right > 0 else None
        if idx is None:
            continue
        past = closes[idx]
        if past and past > 0:
            out[label] = round((latest - past) / past * 100, 2)
    return out


def fetch_profile(symbol: str) -> Optional[dict]:
    """{company_name, returns} for a symbol, or None on failure."""
    symbol = symbol.strip().upper()
    try:
        ticker = yf.Ticker(symbol)
        name = None
        try:
            info = ticker.info
            name = info.get("longName") or info.get("shortName")
        except Exception:
            name = None
        rets = {k: None for k, _ in _WINDOWS}
        hist = ticker.history(period="1y")
        if hist is not None and len(hist):
            days = [d.date() if hasattr(d, "date") else d for d in hist.index]
            closes = [float(c) for c in hist["Close"].tolist()]
            rets = returns_from_closes(days, closes, today=date.today())
        return {"company_name": name, "returns": rets}
    except Exception as e:
        logger.warning("profile fetch failed for %s: %s", symbol, e)
        return None


def _pct(v) -> Optional[float]:
    """yfinance fractions (0.0779) → percent (7.79). None if unparseable."""
    try:
        if v is None:
            return None
        return round(float(v) * 100, 2)
    except (TypeError, ValueError):
        return None


def _holders_from_df(df, kind: str) -> List[dict]:
    out: List[dict] = []
    if df is None or len(df) == 0:
        return out
    for _, r in df.iterrows():
        holder = r.get("Holder")
        if not holder:
            continue
        d = r.get("Date Reported")
        try:
            d = d.date().isoformat() if hasattr(d, "date") else (str(d)[:10] if d else None)
        except Exception:
            d = None
        out.append({
            "holder": str(holder), "pct_held": _pct(r.get("pctHeld")),
            "change_pct": _pct(r.get("pctChange")), "date": d, "kind": kind,
        })
    return out


def fetch_ownership(symbol: str) -> dict:
    """Institutional + fund ownership for a symbol via yfinance.

    Returns {inst_pct, insider_pct, fund_holders, institutions, funds,
    recent_buyers}. NOTE: pct_held is the % of the COMPANY each holder owns, not
    the stock's weight inside that fund. Empty-ish dict on failure.
    """
    symbol = symbol.strip().upper()
    if symbol in _OWNERSHIP_CACHE:
        return _OWNERSHIP_CACHE[symbol]

    result = {"inst_pct": None, "insider_pct": None, "fund_holders": 0,
              "institutions": [], "funds": [], "recent_buyers": []}
    try:
        t = yf.Ticker(symbol)
        try:
            mh = t.major_holders
            if mh is not None and "Value" in getattr(mh, "columns", []):
                d = mh["Value"].to_dict()
                result["inst_pct"] = _pct(d.get("institutionsPercentHeld"))
                result["insider_pct"] = _pct(d.get("insidersPercentHeld"))
        except Exception:
            pass
        insts = _holders_from_df(t.institutional_holders, "institution")
        funds = _holders_from_df(t.mutualfund_holders, "fund")
        # Recently increased positions (positive 13F change), biggest first.
        buyers = sorted(
            [h for h in insts + funds if (h["change_pct"] or 0) > 0],
            key=lambda h: h["change_pct"], reverse=True)
        result.update({"institutions": insts, "funds": funds,
                       "fund_holders": len(funds), "recent_buyers": buyers})
    except Exception as e:
        logger.warning("ownership fetch failed for %s: %s", symbol, e)

    _OWNERSHIP_CACHE[symbol] = result
    return result


def ownership_summary(own: dict) -> dict:
    """Compact summary (inst_pct, fund_holders, top_buyer, change) for storage."""
    buyers = own.get("recent_buyers") or []
    top = buyers[0] if buyers else None
    return {
        "inst_pct": own.get("inst_pct"),
        "fund_holders": own.get("fund_holders"),
        "top_buyer": top["holder"] if top else None,
        "top_buyer_change": top["change_pct"] if top else None,
    }


def get_price_history(symbol: str, since: Optional[date] = None) -> List[dict]:
    """Daily closes as [{date, close}] ascending. Range covers `since` (the pin
    date) plus a little lead. [] on failure. Cached 1h per (symbol, period)."""
    symbol = symbol.strip().upper()
    days_back = (date.today() - since).days if since else 30
    period = "1y" if days_back > 80 else "3mo"
    key = f"{symbol}:{period}"
    if key in _HISTORY_CACHE:
        return _HISTORY_CACHE[key]

    out: List[dict] = []
    try:
        hist = yf.Ticker(symbol).history(period=period)
        if hist is not None and len(hist):
            for idx, close in zip(hist.index, hist["Close"].tolist()):
                d = idx.date() if hasattr(idx, "date") else idx
                if close and float(close) > 0:
                    out.append({"date": d.isoformat(), "close": round(float(close), 2)})
    except Exception as e:
        logger.warning("price history failed for %s: %s", symbol, e)
        out = []

    _HISTORY_CACHE[key] = out
    return out
