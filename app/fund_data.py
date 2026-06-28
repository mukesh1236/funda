"""Fund data layer — yfinance wrappers with TTL caching.

Three public functions, each cached 6 hours:
  get_fund_info(symbol)        -> dict  name, category, expense_ratio, sectors, holdings
  get_fund_performance(symbol) -> dict  CAGR metrics (inception, 1y, 3y, 5y)
  get_fund_holdings(symbol)    -> list  [{ticker, name, weight}]

Fee field handling (learned empirically):
  annualReportExpenseRatio  is a FRACTION (0.0003 = 0.03%)  -> multiply x100
  netExpenseRatio / expenseRatio  are already PERCENT (0.18 = 0.18%)  -> use as-is
"""
import logging
import threading
from datetime import datetime
from typing import Any, Dict, List, Optional

import yfinance as yf
from cachetools import TTLCache

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_INFO_CACHE: TTLCache = TTLCache(maxsize=256, ttl=6 * 3600)
_PERF_CACHE: TTLCache = TTLCache(maxsize=256, ttl=6 * 3600)


# ── helpers ───────────────────────────────────────────────────────────────────

def _cagr(start: float, end: float, years: float) -> Optional[float]:
    if start <= 0 or end <= 0 or years < 0.5:
        return None
    return round(((end / start) ** (1.0 / years) - 1) * 100, 2)


def _pct(v: Optional[float]) -> Optional[float]:
    """Normalise a value that may be a fraction (<2) or already a percent (>=2)."""
    if v is None:
        return None
    f = float(v)
    return round(f * 100, 4) if abs(f) < 2 else round(f, 4)


def _parse_inception(raw) -> Optional[str]:
    if raw is None:
        return None
    try:
        ts = int(raw)
        return datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return str(raw)[:10] if raw else None


# ── public API ────────────────────────────────────────────────────────────────

def get_fund_info(symbol: str) -> Optional[Dict[str, Any]]:
    """Return {symbol, name, category, expense_ratio, inception_date, sector_weights, holdings}."""
    sym = symbol.upper().strip()
    with _LOCK:
        cached = _INFO_CACHE.get(sym)
    if cached is not None:
        return cached

    try:
        t = yf.Ticker(sym)
        info = t.info or {}

        # Expense ratio
        expense_ratio: Optional[float] = None
        if info.get("annualReportExpenseRatio") is not None:
            expense_ratio = round(float(info["annualReportExpenseRatio"]) * 100, 4)
        elif info.get("netExpenseRatio") is not None:
            expense_ratio = round(float(info["netExpenseRatio"]), 4)
        elif info.get("expenseRatio") is not None:
            expense_ratio = round(float(info["expenseRatio"]), 4)

        sector_weights: Dict[str, float] = {}
        holdings: List[Dict] = []

        try:
            fd = t.funds_data
            if fd is not None:
                # sector_weightings
                sw = getattr(fd, "sector_weightings", None)
                if sw is not None:
                    raw_sw = sw.to_dict() if hasattr(sw, "to_dict") else (sw if isinstance(sw, dict) else {})
                    for k, v in raw_sw.items():
                        if v is not None:
                            sector_weights[str(k)] = round(_pct(float(v)) or 0, 2)

                # top_holdings — DataFrame with index=ticker
                th = getattr(fd, "top_holdings", None)
                if th is not None and hasattr(th, "iterrows"):
                    for ticker_idx, row in th.iterrows():
                        name = row.get("Name") or row.get("name") or str(ticker_idx)
                        weight_raw = row.get("Holding Percent") or row.get("holdingPercent") or 0
                        weight = round(_pct(float(weight_raw)) or 0, 2)
                        ticker = str(ticker_idx) if ticker_idx and str(ticker_idx) not in ("nan", "") else None
                        holdings.append({"ticker": ticker, "name": str(name), "weight": weight})
        except Exception as e:
            logger.debug("funds_data for %s: %s", sym, e)

        result: Dict[str, Any] = {
            "symbol": sym,
            "name": info.get("longName") or info.get("shortName") or sym,
            "category": info.get("category") or info.get("fundFamily") or None,
            "expense_ratio": expense_ratio,
            "inception_date": _parse_inception(info.get("fundInceptionDate")),
            "sector_weights": sector_weights,
            "holdings": holdings,
        }
        with _LOCK:
            _INFO_CACHE[sym] = result
        return result
    except Exception as e:
        logger.warning("get_fund_info(%s): %s", sym, e)
        return None


def get_fund_performance(symbol: str) -> Optional[Dict[str, Any]]:
    """Compute CAGR metrics from full price history. Returns None on error."""
    sym = symbol.upper().strip()
    with _LOCK:
        cached = _PERF_CACHE.get(sym)
    if cached is not None:
        return cached

    try:
        import pandas as pd
        hist = yf.Ticker(sym).history(period="max", auto_adjust=True)
        if hist is None or hist.empty or len(hist) < 2:
            return None
        closes = hist["Close"].dropna()
        if len(closes) < 2:
            return None

        end_price = float(closes.iloc[-1])
        start_price = float(closes.iloc[0])
        start_date = closes.index[0]
        end_date = closes.index[-1]
        total_days = (end_date - start_date).days
        years = total_days / 365.25

        def cagr_for(n_years: int) -> Optional[float]:
            cutoff = end_date - pd.DateOffset(years=n_years)
            sub = closes[closes.index >= cutoff]
            if len(sub) < 2:
                return None
            return _cagr(float(sub.iloc[0]), end_price, n_years)

        result: Dict[str, Any] = {
            "inception_date": start_date.strftime("%Y-%m-%d"),
            "as_of": end_date.strftime("%Y-%m-%d"),
            "years_since_inception": round(years, 1),
            "since_inception_cagr": _cagr(start_price, end_price, years),
            "total_return_pct": round((end_price / start_price - 1) * 100, 2) if start_price > 0 else None,
            "cagr_1y": cagr_for(1),
            "cagr_3y": cagr_for(3),
            "cagr_5y": cagr_for(5),
        }
        with _LOCK:
            _PERF_CACHE[sym] = result
        return result
    except Exception as e:
        logger.warning("get_fund_performance(%s): %s", sym, e)
        return None


def get_fund_holdings(symbol: str) -> List[Dict]:
    """Return top holdings list (from get_fund_info cache). Empty list on error."""
    info = get_fund_info(symbol)
    return (info or {}).get("holdings", [])
