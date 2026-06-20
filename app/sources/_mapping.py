"""Shared mapping of free-text analyst grades/ratings onto buy/hold/sell.

Used by sources that report textual ratings (FMP grades, TipRanks). Kept in one
place so every source classifies the same vocabulary identically (DRY).
"""
from typing import Optional

# Order matters: check sell/hold phrases before bare "perform" etc. Matched as
# substrings against a lowercased rating string.
_BUY = (
    "strong buy", "conviction buy", "top pick", "outperform", "overweight",
    "accumulate", "buy", "add", "positive", "bullish", "market outperform",
    "sector outperform",
)
_SELL = (
    "strong sell", "underperform", "underweight", "reduce", "sell",
    "negative", "bearish", "market underperform", "sector underperform",
)
_HOLD = (
    "hold", "neutral", "market perform", "sector perform", "peer perform",
    "equal-weight", "equalweight", "equal weight", "in-line", "in line",
    "inline",
)


def grade_to_action(rating: Optional[str]) -> Optional[str]:
    """Map a textual rating to 'buy' | 'hold' | 'sell', or None if unrecognised.

    Sell and hold are checked before buy so phrases like "market underperform"
    or "market perform" aren't captured by a loose "perform"/"buy" match.
    """
    if not rating:
        return None
    r = rating.strip().lower()
    for kw in _SELL:
        if kw in r:
            return "sell"
    for kw in _HOLD:
        if kw in r:
            return "hold"
    for kw in _BUY:
        if kw in r:
            return "buy"
    return None
