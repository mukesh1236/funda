"""Fund return attribution — the Pareto (80/20) driver analysis.

pareto_drivers() is pure math (no I/O) so it's directly unit-testable;
batch_period_returns() fetches period returns for many tickers in a few
chunked yf.download calls instead of one call per holding.
"""
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

PERIOD_DAYS = {"3mo": 91, "6mo": 182, "1y": 365}


def pareto_drivers(
    holdings: List[dict],
    returns: Dict[str, float],
    threshold: float = 0.80,
) -> dict:
    """Attribution over a fund's holdings.

    holdings: [{ticker, name, weight}] — weight as % of fund net assets.
    returns:  {TICKER: period_return_pct}
    Each holding's contribution to the fund's period return (in percentage
    points) = weight% x return% / 100. Holdings are ranked by contribution;
    the smallest prefix reaching `threshold` of the total POSITIVE
    contribution is flagged as the Pareto set.
    """
    items: List[dict] = []
    skipped = 0
    for h in holdings:
        ticker = (h.get("ticker") or "").upper().strip()
        weight = h.get("weight") or 0.0
        ret = returns.get(ticker)
        if not ticker or ret is None or weight <= 0:
            skipped += 1
            continue
        items.append({
            "ticker": ticker,
            "name": h.get("name") or ticker,
            "weight": round(weight, 3),
            "ret_pct": round(ret, 2),
            "contribution": round(weight * ret / 100.0, 3),   # pp of fund return
        })

    items.sort(key=lambda x: x["contribution"], reverse=True)

    total_positive = sum(i["contribution"] for i in items if i["contribution"] > 0)
    total_contribution = sum(i["contribution"] for i in items)
    coverage_pct = sum(i["weight"] for i in items)

    cum = 0.0
    pareto_count = 0
    pareto_weight = 0.0
    pareto_open = total_positive > 0
    for i in items:
        if i["contribution"] > 0 and total_positive > 0:
            cum += i["contribution"]
            i["cum_pct"] = round(cum / total_positive * 100.0, 1)
        else:
            i["cum_pct"] = None
        if pareto_open and i["contribution"] > 0:
            i["pareto"] = True
            pareto_count += 1
            pareto_weight += i["weight"]
            if i["cum_pct"] is not None and i["cum_pct"] >= threshold * 100.0:
                pareto_open = False   # threshold reached — this row closes the set
        else:
            i["pareto"] = False

    return {
        "items": items,
        "skipped": skipped,
        "coverage_pct": round(coverage_pct, 1),
        "total_contribution_pct": round(total_contribution, 2),
        "pareto_count": pareto_count,
        "pareto_weight_pct": round(pareto_weight, 1),
    }


def build_headline(symbol: str, period: str, result: dict, holdings_count: int) -> str:
    """One shared sentence for the UI and the chat context."""
    if result["pareto_count"] == 0:
        return (f"No positive return drivers found for {symbol} over {period} "
                f"(fund contribution {result['total_contribution_pct']:+.1f}pp).")
    return (
        f"{result['pareto_count']} of {holdings_count} holdings "
        f"({result['pareto_weight_pct']:.0f}% of fund weight) drove 80% of "
        f"{symbol}'s positive {period} return "
        f"(total contribution {result['total_contribution_pct']:+.1f}pp from "
        f"{result['coverage_pct']:.0f}% disclosed weight)."
    )


def batch_period_returns(tickers: List[str], period: str = "1y",
                          chunk_size: int = 100) -> Dict[str, float]:
    """Period % return per ticker from first/last close in the window —
    a few chunked yf.download calls, not one request per holding."""
    days = PERIOD_DAYS.get(period, 365)
    out: Dict[str, float] = {}
    tickers = sorted({t.upper().strip() for t in tickers if t and t.strip()})
    if not tickers:
        return out

    try:
        import yfinance as yf
    except ImportError:
        return out

    for start in range(0, len(tickers), chunk_size):
        chunk = tickers[start:start + chunk_size]
        try:
            raw = yf.download(
                " ".join(chunk), period=f"{days}d", interval="1d",
                auto_adjust=True, progress=False, group_by="ticker", threads=True,
            )
            for sym in chunk:
                try:
                    closes = (raw["Close"] if len(chunk) == 1 else raw[sym]["Close"]).dropna()
                    if len(closes) >= 2:
                        first, last = float(closes.iloc[0]), float(closes.iloc[-1])
                        if first > 0:
                            out[sym] = round((last - first) / first * 100.0, 2)
                except Exception:
                    pass
        except Exception as e:
            logger.warning("batch returns chunk failed (%d tickers): %s", len(chunk), e)
    return out
