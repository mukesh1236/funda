"""Company name → ticker lookup via Yahoo Finance search (no API key).
Returns up to `limit` matches ordered by relevance. Cached 1h per query."""
import logging
import re
from typing import List, Optional

import httpx
from cachetools import TTLCache

logger = logging.getLogger(__name__)

_CACHE: TTLCache = TTLCache(maxsize=500, ttl=3600)

_URL = "https://query1.finance.yahoo.com/v1/finance/search"
_HEADERS = {"User-Agent": "Mozilla/5.0"}

# Static name → NSE ticker for common Indian companies that Yahoo search
# often misses (returns US ADR instead of NSE listing).
_INDIA_STATIC: List[tuple] = [
    # keywords (lowercase)         ticker          display name
    (["hdfc bank", "hdfcbank"],    "HDFCBANK.NS",  "HDFC Bank Ltd"),
    (["hdfc"],                     "HDFC.NS",      "HDFC Ltd"),
    (["sbi", "state bank"],        "SBIN.NS",      "State Bank of India"),
    (["icici bank", "icicibank"],  "ICICIBANK.NS", "ICICI Bank Ltd"),
    (["icici"],                    "ICICIBANK.NS", "ICICI Bank Ltd"),
    (["kotak"],                    "KOTAKBANK.NS", "Kotak Mahindra Bank"),
    (["axis bank", "axisbank"],    "AXISBANK.NS",  "Axis Bank Ltd"),
    (["indusin", "indusind"],      "INDUSINDBK.NS","IndusInd Bank"),
    (["tcs", "tata consult"],      "TCS.NS",       "Tata Consultancy Services"),
    (["infosys", "infy"],          "INFY.NS",      "Infosys Ltd"),
    (["wipro"],                    "WIPRO.NS",     "Wipro Ltd"),
    (["hcl tech", "hcltech"],      "HCLTECH.NS",   "HCL Technologies"),
    (["tech mahindra", "techm"],   "TECHM.NS",     "Tech Mahindra"),
    (["ltimindtree", "ltim"],      "LTIM.NS",      "LTIMindtree"),
    (["hindustan unilever", "hul", "hindunilvr"], "HINDUNILVR.NS", "Hindustan Unilever"),
    (["itc"],                      "ITC.NS",       "ITC Ltd"),
    (["nestle"],                   "NESTLEIND.NS", "Nestle India"),
    (["britannia"],                "BRITANNIA.NS", "Britannia Industries"),
    (["dabur"],                    "DABUR.NS",     "Dabur India"),
    (["tata consumer", "tataconsum"], "TATACONSUM.NS", "Tata Consumer Products"),
    (["maruti"],                   "MARUTI.NS",    "Maruti Suzuki"),
    (["tata motors", "tatamotors"],"TATAMOTORS.NS","Tata Motors"),
    (["mahindra", "m&m"],          "M&M.NS",       "Mahindra & Mahindra"),
    (["bajaj auto", "bajaj-auto"], "BAJAJ-AUTO.NS","Bajaj Auto"),
    (["eicher"],                   "EICHERMOT.NS", "Eicher Motors"),
    (["hero moto", "heromotoco"],  "HEROMOTOCO.NS","Hero MotoCorp"),
    (["sun pharma", "sunpharma"],  "SUNPHARMA.NS", "Sun Pharmaceutical"),
    (["dr reddy", "drreddy"],      "DRREDDY.NS",   "Dr. Reddy's Laboratories"),
    (["cipla"],                    "CIPLA.NS",     "Cipla Ltd"),
    (["divi", "divislab"],         "DIVISLAB.NS",  "Divi's Laboratories"),
    (["lupin"],                    "LUPIN.NS",     "Lupin Ltd"),
    (["reliance"],                 "RELIANCE.NS",  "Reliance Industries"),
    (["ongc"],                     "ONGC.NS",      "Oil & Natural Gas Corp"),
    (["ntpc"],                     "NTPC.NS",      "NTPC Ltd"),
    (["power grid", "powergrid"],  "POWERGRID.NS", "Power Grid Corp"),
    (["adani green", "adanigreen"],"ADANIGREEN.NS","Adani Green Energy"),
    (["tata power", "tatapower"],  "TATAPOWER.NS", "Tata Power"),
    (["larsen", "l&t", " lt "],    "LT.NS",        "Larsen & Toubro"),
    (["adani port", "adaniport"],  "ADANIPORTS.NS","Adani Ports"),
    (["ultratech", "ultracemco"],  "ULTRACEMCO.NS","UltraTech Cement"),
    (["siemens"],                  "SIEMENS.NS",   "Siemens India"),
    (["grasim"],                   "GRASIM.NS",    "Grasim Industries"),
    (["bharti airtel", "airtel"],  "BHARTIARTL.NS","Bharti Airtel"),
    (["vodafone idea", "idea"],    "IDEA.NS",      "Vodafone Idea"),
]


def _yahoo_quotes(query: str, count: int = 12, fuzzy: bool = False) -> List[dict]:
    """Raw Yahoo Finance search quotes list."""
    try:
        with httpx.Client(timeout=8, headers=_HEADERS) as client:
            params = {"q": query, "quotesCount": count, "newsCount": 0}
            if fuzzy:
                params["enableFuzzyQuery"] = "true"
            else:
                params["enableFuzzyQuery"] = "false"
                params["quotesQueryId"] = "tss_match_phrase_query"
            resp = client.get(_URL, params=params)
            resp.raise_for_status()
            return resp.json().get("quotes") or []
    except Exception as e:
        logger.info("Yahoo search failed for %r (fuzzy=%s): %s", query, fuzzy, e)
        return []


def _yahoo_quotes_with_fallback(query: str, count: int = 12) -> List[dict]:
    """Strict phrase-match first (Yahoo's default, precise for exact names);
    if that finds nothing, retry with fuzzy matching enabled. Plain
    phrase-match can miss real companies over small wording differences —
    e.g. "coca cola" vs Yahoo's canonical "The Coca-Cola Company" — that a
    fuzzy/typo-tolerant search resolves fine."""
    quotes = _yahoo_quotes(query, count, fuzzy=False)
    if quotes:
        return quotes
    return _yahoo_quotes(query, count, fuzzy=True)


# Yahoo quoteType -> the type the frontend routes on. Without this the UI can't
# tell a fund from a stock and sends both to the stock detail view.
_RESULT_TYPES = {
    "EQUITY": "stock",
    "ETF": "etf",
    "MUTUALFUND": "fund",
    "INDEX": "index",
    "CURRENCY": "other",
    "CRYPTOCURRENCY": "other",
}
_TYPE_RANK = {"stock": 0, "etf": 1, "index": 2, "fund": 3, "other": 4}


def _parse_quotes(quotes: List[dict], market: str, limit: int,
                  include_funds: bool = True) -> List[dict]:
    results = []
    seen = set()
    for item in quotes:
        sym = (item.get("symbol") or "").strip().upper()
        if not sym or sym in seen:
            continue
        is_india = sym.endswith(".NS") or sym.endswith(".BO")
        if market == "in" and not is_india:
            continue
        if market == "us" and is_india:
            continue
        kind = (item.get("quoteType") or "").upper()
        if kind in ("FUTURE", "OPTION"):
            continue
        if kind == "MUTUALFUND" and not include_funds:
            continue
        seen.add(sym)
        name = item.get("shortname") or item.get("longname") or sym
        exchange = item.get("exchange") or item.get("fullExchangeName") or ""
        results.append({"symbol": sym, "name": name, "exchange": exchange,
                        "type": _RESULT_TYPES.get(kind, "stock")})
        if len(results) >= limit * 3:
            # Over-collect so ranking has something to work with, then trim.
            break

    # Stocks and ETFs first. A query like "fidelity" otherwise returns a dozen
    # share classes of the same mutual fund and buries everything else.
    results.sort(key=lambda r: _TYPE_RANK.get(r["type"], 3))
    return results[:limit]


def _static_india_matches(q_lower: str, limit: int) -> List[dict]:
    """Fast static lookup for common Indian company names/abbreviations."""
    results = []
    seen = set()
    for keywords, sym, name in _INDIA_STATIC:
        if any(kw in q_lower for kw in keywords):
            if sym not in seen:
                seen.add(sym)
                results.append({"symbol": sym, "name": name, "exchange": "NSE",
                                "type": "stock"})
                if len(results) >= limit:
                    break
    return results


def search_tickers(query: str, market: str = "us", limit: int = 6,
                   include_funds: bool = True) -> List[dict]:
    """Search by company name or partial ticker.

    Returns [{symbol, name, exchange, type}] where type is
    stock | etf | fund | index | other.

    `include_funds=False` for callers that can't act on a mutual fund — the
    watchlist pins an entry price and tracks daily moves, which a fund with one
    NAV per day can't support.

    India market: tries static name mapping first, then Yahoo Finance search.
    """
    q = query.strip()
    if len(q) < 1:
        return []
    cache_key = f"{q.lower()}:{market}:{int(include_funds)}"
    if cache_key in _CACHE:
        return _CACHE[cache_key]

    results: List[dict] = []

    if market == "in":
        # Static lookup covers abbreviations Yahoo often misses (SBI, HDFC, etc.)
        results = _static_india_matches(q.lower(), limit)

    if len(results) < limit:
        # Fill remaining slots from Yahoo search (strict phrase-match, then
        # fuzzy fallback — see _yahoo_quotes_with_fallback).
        quotes = _yahoo_quotes_with_fallback(q)
        yahoo = _parse_quotes(quotes, market, limit - len(results), include_funds)
        seen = {r["symbol"] for r in results}
        results += [r for r in yahoo if r["symbol"] not in seen]

    # India final fallback: retry Yahoo with " NSE" for short queries.
    if market == "in" and not results and len(q) <= 20:
        quotes2 = _yahoo_quotes_with_fallback(q + " NSE")
        results = _parse_quotes(quotes2, market, limit, include_funds)

    # Only cache HITS. Caching an empty result for the full hour would
    # memorize a transient failure (timeout, rate limit) or an over-strict
    # match as "this company doesn't exist" for everyone, for an hour.
    if results:
        _CACHE[cache_key] = results
    return results
