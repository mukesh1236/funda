"""Core fundamentals used to judge whether a stock is worth buying — valuation,
profitability, leverage, growth, and dividend — fetched free via yfinance.

`fetch_fundamentals` is the I/O boundary (cached, fails soft to {}).
`build_fundamentals_notes` is a pure rule-based function that turns the raw
numbers into plain-English "what this means" bullets, the same way
app/summarize.py turns consensus data into the "why analysts recommend it"
reasons.
"""
import logging
from typing import List, Optional

import yfinance as yf
from cachetools import TTLCache

logger = logging.getLogger(__name__)

_FUNDAMENTALS_CACHE: TTLCache = TTLCache(maxsize=1000, ttl=12 * 3600)

_FIELDS = {
    "pe_ratio": "trailingPE",
    "forward_pe": "forwardPE",
    "peg_ratio": "trailingPegRatio",
    "eps": "trailingEps",
    "market_cap": "marketCap",
    "revenue_growth": "revenueGrowth",
    "profit_margin": "profitMargins",
    "roe": "returnOnEquity",
    "debt_to_equity": "debtToEquity",
    "dividend_yield": "dividendYield",
    "beta": "beta",
    "week52_low": "fiftyTwoWeekLow",
    "week52_high": "fiftyTwoWeekHigh",
    "price_to_book": "priceToBook",
    "current_price": "currentPrice",
    "sector": "sector",
    "industry": "industry",
}


def fetch_fundamentals(symbol: str) -> dict:
    """Raw fundamentals dict (see _FIELDS keys), or {} on failure. Cached 12h."""
    symbol = symbol.strip().upper()
    if symbol in _FUNDAMENTALS_CACHE:
        return _FUNDAMENTALS_CACHE[symbol]

    out: dict = {}
    try:
        info = yf.Ticker(symbol).info or {}
        for key, src_key in _FIELDS.items():
            out[key] = info.get(src_key)
        # Percent-style fields come back as fractions (0.18 → 18%) — except
        # dividendYield, which yfinance already returns as a percent value.
        for pct_key in ("revenue_growth", "profit_margin", "roe"):
            v = out.get(pct_key)
            if v is not None:
                out[pct_key] = round(float(v) * 100, 2)
        if out.get("dividend_yield") is not None:
            out["dividend_yield"] = round(float(out["dividend_yield"]), 2)
        if out.get("pe_ratio") is not None:
            out["pe_ratio"] = round(float(out["pe_ratio"]), 2)
        if out.get("forward_pe") is not None:
            out["forward_pe"] = round(float(out["forward_pe"]), 2)
    except Exception as e:
        logger.warning("fundamentals fetch failed for %s: %s", symbol, e)
        out = {}

    _FUNDAMENTALS_CACHE[symbol] = out
    return out


def build_fundamentals_notes(f: dict) -> List[str]:
    """Plain-English bullets on what each fundamental implies for a buy
    decision. Pure function — no I/O, easy to unit test."""
    notes: List[str] = []
    if not f:
        return notes

    pe = f.get("pe_ratio")
    if pe is not None:
        if pe <= 0:
            notes.append(f"P/E is negative ({pe:g}) — the company is currently unprofitable.")
        elif pe < 15:
            notes.append(f"P/E of {pe:g} is low — the stock looks cheap relative to earnings (or the market expects slow growth).")
        elif pe <= 25:
            notes.append(f"P/E of {pe:g} is in a fair/moderate valuation range.")
        else:
            notes.append(f"P/E of {pe:g} is high — priced for strong future growth; more downside risk if growth disappoints.")

    peg = f.get("peg_ratio")
    if peg is not None:
        if peg < 1:
            notes.append(f"PEG ratio {peg:g} (<1) — earnings growth looks attractive relative to its valuation.")
        elif peg <= 2:
            notes.append(f"PEG ratio {peg:g} is reasonable given its growth rate.")
        else:
            notes.append(f"PEG ratio {peg:g} (>2) — pricey relative to its growth rate.")

    rg = f.get("revenue_growth")
    if rg is not None:
        notes.append(f"Revenue growth {rg:+.1f}% year-over-year — {'expanding' if rg > 0 else 'shrinking'} top line.")

    pm = f.get("profit_margin")
    if pm is not None:
        if pm < 0:
            notes.append(f"Profit margin {pm:.1f}% — currently losing money on each dollar of sales.")
        elif pm < 10:
            notes.append(f"Profit margin {pm:.1f}% — thin profitability.")
        else:
            notes.append(f"Profit margin {pm:.1f}% — healthy profitability.")

    roe = f.get("roe")
    if roe is not None:
        if roe >= 15:
            notes.append(f"Return on equity {roe:.1f}% — efficient at turning shareholder capital into profit.")
        elif roe >= 0:
            notes.append(f"Return on equity {roe:.1f}% — modest capital efficiency.")
        else:
            notes.append(f"Return on equity {roe:.1f}% — negative, capital is being eroded.")

    dte = f.get("debt_to_equity")
    if dte is not None:
        if dte > 150:
            notes.append(f"Debt/Equity {dte:.0f} — highly leveraged, higher financial risk.")
        elif dte > 50:
            notes.append(f"Debt/Equity {dte:.0f} — moderate leverage.")
        else:
            notes.append(f"Debt/Equity {dte:.0f} — low leverage, conservative balance sheet.")

    dy = f.get("dividend_yield")
    if dy is not None and dy > 0:
        notes.append(f"Dividend yield {dy:.2f}% — pays income while you hold it.")

    beta = f.get("beta")
    if beta is not None:
        if beta > 1.3:
            notes.append(f"Beta {beta:.2f} — swings more than the market; expect bigger ups and downs.")
        elif beta < 0.8:
            notes.append(f"Beta {beta:.2f} — less volatile than the market.")

    price, lo, hi = f.get("current_price"), f.get("week52_low"), f.get("week52_high")
    if price is not None and lo is not None and hi is not None and hi > lo:
        pos = (price - lo) / (hi - lo) * 100
        notes.append(f"Trading at {pos:.0f}% of its 52-week range (${lo:g}–${hi:g}) — "
                     f"{'near the highs' if pos > 80 else 'near the lows' if pos < 20 else 'mid-range'}.")

    pb = f.get("price_to_book")
    if pb is not None:
        notes.append(f"Price/Book {pb:.2f} — {'trading below book value' if pb < 1 else 'trading above book value'}.")

    return notes
