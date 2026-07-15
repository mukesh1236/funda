"""SEC EDGAR N-PORT client — COMPLETE fund holdings, free and official.

Every US-registered fund/ETF files Form NPORT-P monthly; each <invstOrSec>
row carries name, CUSIP, sometimes ticker, and pctVal (% of net assets).
Public with roughly a 60-day lag on quarter-end months.

Pipeline:
  fund_identity(ticker)      -> (cik, series_id) via company_tickers_mf.json
  fetch_nport_holdings(t)    -> {"as_of", "holdings": [{name,cusip,ticker,weight}]}
  resolve_tickers(rows, ...) -> fills missing tickers via OpenFIGI (optional key)

Everything degrades gracefully to None/[] — callers fall back to the
yfinance top-10 sample.
"""
import logging
import os
import time
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

import httpx
from cachetools import TTLCache

logger = logging.getLogger(__name__)

# EDGAR etiquette: identify yourself; stay well under 10 req/s.
_UA = os.environ.get("EDGAR_USER_AGENT", "AlphaFunds/1.0 (research; contact via repo)")
_HEADERS = {"User-Agent": _UA, "Accept-Encoding": "gzip"}

_MF_MAP_CACHE: TTLCache = TTLCache(maxsize=1, ttl=24 * 3600)
_HOLDINGS_CACHE: TTLCache = TTLCache(maxsize=64, ttl=24 * 3600)

_MAX_NPORT_DOCS_TO_TRY = 8      # a CIK files one NPORT-P per series per month


def _get(url: str, timeout: float = 30) -> Optional[httpx.Response]:
    try:
        resp = httpx.get(url, headers=_HEADERS, timeout=timeout, follow_redirects=True)
        if resp.status_code == 200:
            return resp
        logger.info("EDGAR %s -> HTTP %s", url, resp.status_code)
    except Exception as e:
        logger.info("EDGAR fetch failed %s: %s", url, e)
    return None


def fund_identity(ticker: str) -> Optional[Tuple[int, str]]:
    """(cik, seriesId) for a fund ticker from SEC's mutual-fund ticker map.
    Covers mutual funds AND ETFs registered as investment companies."""
    sym = ticker.upper().strip()
    mapping = _MF_MAP_CACHE.get("map")
    if mapping is None:
        resp = _get("https://www.sec.gov/files/company_tickers_mf.json")
        if resp is None:
            return None
        try:
            doc = resp.json()
            fields = doc["fields"]                     # [cik, seriesId, classId, symbol]
            i_cik, i_series = fields.index("cik"), fields.index("seriesId")
            i_sym = fields.index("symbol")
            mapping = {}
            for row in doc["data"]:
                mapping[str(row[i_sym]).upper()] = (int(row[i_cik]), str(row[i_series]))
        except Exception as e:
            logger.warning("company_tickers_mf parse failed: %s", e)
            return None
        _MF_MAP_CACHE["map"] = mapping
    return mapping.get(sym)


def _iter_local(root, tag: str):
    """Namespace-agnostic iterator (N-PORT XML uses a default namespace)."""
    for el in root.iter():
        if el.tag.split('}')[-1] == tag:
            yield el


def _text(parent, tag: str) -> Optional[str]:
    for el in parent.iter():
        if el.tag.split('}')[-1] == tag:
            return (el.text or "").strip() or None
    return None


def _parse_nport_xml(xml_bytes: bytes, want_series: Optional[str]) -> Optional[dict]:
    """Parse one NPORT-P primary doc. Returns None if it's for another series."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError as e:
        logger.info("NPORT XML parse error: %s", e)
        return None

    series = None
    for el in _iter_local(root, "seriesId"):
        series = (el.text or "").strip()
        break
    if want_series and series and series != want_series:
        return None

    as_of = None
    for el in _iter_local(root, "repPdDate"):
        as_of = (el.text or "").strip()
        break

    holdings: List[dict] = []
    for sec in _iter_local(root, "invstOrSec"):
        try:
            pct = _text(sec, "pctVal")
            weight = float(pct) if pct not in (None, "") else 0.0
        except ValueError:
            weight = 0.0
        if weight <= 0:
            continue
        tick = None
        for t in _iter_local(sec, "ticker"):
            tick = (t.get("value") or t.text or "").strip() or None
            break
        cusip = _text(sec, "cusip")
        if cusip in ("N/A", "000000000"):
            cusip = None
        holdings.append({
            "name": _text(sec, "name") or _text(sec, "title") or "?",
            "cusip": cusip,
            "ticker": tick,
            "weight": round(weight, 4),
        })

    if not holdings:
        return None
    holdings.sort(key=lambda h: h["weight"], reverse=True)
    return {"as_of": as_of, "series": series, "holdings": holdings}


def fetch_nport_holdings(ticker: str) -> Optional[dict]:
    """Latest complete portfolio for `ticker` from its newest NPORT-P filing.
    {"as_of", "holdings": [{name, cusip, ticker, weight}]} or None."""
    sym = ticker.upper().strip()
    cached = _HOLDINGS_CACHE.get(sym)
    if cached is not None:
        return cached

    ident = fund_identity(sym)
    if ident is None:
        logger.info("N-PORT: %s not in SEC fund ticker map", sym)
        return None
    cik, series_id = ident

    resp = _get(f"https://data.sec.gov/submissions/CIK{cik:010d}.json")
    if resp is None:
        return None
    try:
        recent = resp.json()["filings"]["recent"]
        forms = recent["form"]
        accessions = recent["accessionNumber"]
        docs = recent["primaryDocument"]
    except Exception as e:
        logger.warning("EDGAR submissions parse failed for CIK %s: %s", cik, e)
        return None

    tried = 0
    for i, form in enumerate(forms):
        if form != "NPORT-P" or tried >= _MAX_NPORT_DOCS_TO_TRY:
            continue
        tried += 1
        acc = accessions[i].replace("-", "")
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{docs[i]}"
        doc = _get(url, timeout=60)
        time.sleep(0.15)   # stay far under EDGAR's rate ceiling
        if doc is None:
            continue
        parsed = _parse_nport_xml(doc.content, want_series=series_id)
        if parsed:
            result = {"as_of": parsed["as_of"], "holdings": parsed["holdings"]}
            _HOLDINGS_CACHE[sym] = result
            logger.info("N-PORT: %s -> %d holdings as of %s",
                        sym, len(result["holdings"]), result["as_of"])
            return result
    logger.info("N-PORT: no matching filing found for %s (tried %d docs)", sym, tried)
    return None


# ── CUSIP → ticker resolution (OpenFIGI, optional free key) ──────────────────

def resolve_cusips(cusips: List[str]) -> Dict[str, str]:
    """Best-effort CUSIP→ticker via OpenFIGI. {} without network/key limits.
    Free tier: 100 jobs/request with a key, 10 without."""
    cusips = [c for c in dict.fromkeys(cusips) if c]
    if not cusips:
        return {}
    key = os.environ.get("OPENFIGI_API_KEY", "")
    headers = {"Content-Type": "application/json"}
    if key:
        headers["X-OPENFIGI-APIKEY"] = key
    batch = 100 if key else 10
    out: Dict[str, str] = {}
    for start in range(0, len(cusips), batch):
        chunk = cusips[start:start + batch]
        jobs = [{"idType": "ID_CUSIP", "idValue": c} for c in chunk]
        try:
            resp = httpx.post("https://api.openfigi.com/v3/mapping",
                               json=jobs, headers=headers, timeout=20)
            if resp.status_code != 200:
                logger.info("OpenFIGI HTTP %s — stopping resolution", resp.status_code)
                break
            for cusip, result in zip(chunk, resp.json()):
                data = (result or {}).get("data") or []
                tick = data[0].get("ticker") if data else None
                if tick:
                    out[cusip] = tick.upper()
            time.sleep(0.3 if key else 6)   # respect unauthenticated rate limit
        except Exception as e:
            logger.info("OpenFIGI failed: %s", e)
            break
    return out
