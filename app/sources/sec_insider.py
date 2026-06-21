"""SEC Form 4 insider trading data via yfinance.

Form 4 is filed with the SEC whenever a company insider (CEO, CFO, director,
10%+ shareholder) buys or sells shares. It's a leading indicator — insiders
buying their own stock is historically a bullish signal.
"""
import logging
from typing import List

logger = logging.getLogger(__name__)


def fetch_insider_trades(symbol: str) -> List[dict]:
    """Return recent insider transactions for a symbol (up to 10).

    Each dict has: insider, role, action, shares, value, date.
    Returns [] on any error — fail-soft so it never breaks the detail view.
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        df = ticker.insider_transactions
        if df is None or df.empty:
            return []

        trades = []
        for _, row in df.head(10).iterrows():
            # yfinance column names vary slightly across versions — use .get safely
            raw = row.to_dict()
            action_raw = str(raw.get("Transaction", raw.get("transaction", "")) or "")
            action = "Buy" if "purchase" in action_raw.lower() else "Sale"

            date_val = raw.get("Start Date", raw.get("startDate", raw.get("date", "")))
            date_str = str(date_val)[:10] if date_val is not None else None

            shares_raw = raw.get("Shares", raw.get("shares"))
            value_raw = raw.get("Value", raw.get("value"))

            trades.append({
                "insider": str(raw.get("Insider", raw.get("insider", "Unknown"))),
                "role": str(raw.get("Relationship", raw.get("position", ""))),
                "action": action,
                "shares": int(shares_raw) if shares_raw and str(shares_raw) != "nan" else None,
                "value": float(value_raw) if value_raw and str(value_raw) != "nan" else None,
                "date": date_str,
            })
        return trades
    except Exception as e:
        logger.debug("Insider trades fetch failed for %s: %s", symbol, e)
        return []
