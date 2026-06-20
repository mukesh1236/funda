"""Thematic / segment groupings of US and Indian stocks.

Curated maps of theme -> tickers so the user can browse recommendations by
segment (AI, semiconductors, finance, green energy, data centers, ...). Tickers
intentionally appear in multiple themes where they genuinely belong (e.g. NVDA
is AI + Semiconductors + Data Center). The tracked universe defaults to the
union of every theme below.

India tickers use yfinance's ".NS" (NSE) suffix, which also makes the market
auto-detectable from the symbol itself — no separate "market" column needed
anywhere else in the pipeline (store, analytics, ownership, watchlist, ...).
"""
from typing import Dict, List

THEMES: Dict[str, List[str]] = {
    "AI": [
        "NVDA", "MSFT", "GOOGL", "META", "AMZN", "PLTR", "AMD", "SNOW",
        "AI", "CRM", "NOW",
    ],
    "Semiconductors": [
        "NVDA", "AMD", "AVGO", "TSM", "INTC", "MU", "QCOM", "ASML",
        "AMAT", "LRCX", "TXN", "ARM",
    ],
    "Finance": [
        "JPM", "BAC", "WFC", "GS", "MS", "C", "BLK", "V", "MA", "AXP",
    ],
    "Green Energy": [
        "ENPH", "FSLR", "SEDG", "NEE", "PLUG", "RUN", "BE", "TSLA", "FLNC",
    ],
    "Data Center": [
        "NVDA", "AVGO", "DLR", "EQIX", "VRT", "ANET", "SMCI", "MSFT",
    ],
    "EV": [
        "TSLA", "RIVN", "LCID", "NIO", "F", "GM",
    ],
    "Cloud & Software": [
        "MSFT", "CRM", "NOW", "SNOW", "ADBE", "ORCL", "DDOG", "NET",
    ],
}

# NSE-listed Indian stocks (".NS" suffix — yfinance's convention).
INDIA_THEMES: Dict[str, List[str]] = {
    "IT": [
        "TCS.NS", "INFY.NS", "WIPRO.NS", "HCLTECH.NS", "TECHM.NS", "LTIM.NS",
    ],
    "Banking": [
        "HDFCBANK.NS", "ICICIBANK.NS", "SBIN.NS", "KOTAKBANK.NS",
        "AXISBANK.NS", "INDUSINDBK.NS",
    ],
    "FMCG": [
        "HINDUNILVR.NS", "ITC.NS", "NESTLEIND.NS", "BRITANNIA.NS", "DABUR.NS",
        "TATACONSUM.NS",
    ],
    "Auto": [
        "MARUTI.NS", "TATAMOTORS.NS", "M&M.NS", "BAJAJ-AUTO.NS",
        "EICHERMOT.NS", "HEROMOTOCO.NS",
    ],
    "Pharma": [
        "SUNPHARMA.NS", "DRREDDY.NS", "CIPLA.NS", "DIVISLAB.NS", "LUPIN.NS",
    ],
    "Energy": [
        "RELIANCE.NS", "ONGC.NS", "NTPC.NS", "POWERGRID.NS", "ADANIGREEN.NS",
        "TATAPOWER.NS",
    ],
    "Infra & Capital Goods": [
        "LT.NS", "ADANIPORTS.NS", "ULTRACEMCO.NS", "SIEMENS.NS", "GRASIM.NS",
    ],
    "Telecom": [
        "BHARTIARTL.NS", "IDEA.NS",
    ],
}


def _themes_for_market(market: str) -> Dict[str, List[str]]:
    return INDIA_THEMES if (market or "us").strip().lower() == "in" else THEMES


def market_of(symbol: str) -> str:
    """Auto-detect market from the ticker suffix: ".NS"/".BO" → "in", else "us"."""
    s = (symbol or "").strip().upper()
    return "in" if s.endswith(".NS") or s.endswith(".BO") else "us"


def all_tickers(market: str = "us") -> List[str]:
    """Sorted union of every ticker across all themes for one market."""
    seen = set()
    for tickers in _themes_for_market(market).values():
        seen.update(tickers)
    return sorted(seen)


def tickers_for(theme: str, market: str = "us") -> List[str]:
    """Tickers in a theme (case-insensitive name match), or [] if unknown."""
    for name, tickers in _themes_for_market(market).items():
        if name.lower() == (theme or "").strip().lower():
            return list(tickers)
    return []


def themes_for(symbol: str) -> List[str]:
    """Theme names a symbol belongs to (market auto-detected from the symbol)."""
    s = (symbol or "").strip().upper()
    return [name for name, tickers in _themes_for_market(market_of(s)).items() if s in tickers]
