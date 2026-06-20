"""Build a 'why analysts recommend this' summary for a stock.

Two layers:
  - build_rule_summary: deterministic, instant, no dependencies. Distils the
    consensus stance, target upside, recent target raises/cuts, segments, and
    recurring news themes into a headline + bullet reasons.
  - maybe_llm_narrative: optional prose from a local Ollama model, best-effort
    (short timeout, graceful fallback). Enabled via SUMMARY_PROVIDER.

build_summary combines them and caches per symbol so repeated opens are fast.
"""
import logging
import re
from datetime import date
from typing import List, Optional

from cachetools import TTLCache

from app.config import Settings
from app.llm import ollama_generate
from app.models import AnalystSummary, StockDetailResult

logger = logging.getLogger(__name__)

_SUMMARY_CACHE: TTLCache = TTLCache(maxsize=2000, ttl=6 * 3600)

# Recurring themes we look for in news headlines as a free proxy for "why".
_NEWS_KEYWORDS = {
    "AI": ["ai", "artificial intelligence", "chatbot", "copilot"],
    "data center": ["data center", "datacenter"],
    "chips": ["chip", "semiconductor", "gpu"],
    "earnings": ["earnings", "results", "beat", "miss"],
    "revenue growth": ["revenue", "sales growth", "guidance", "forecast"],
    "cloud": ["cloud", "azure", "aws"],
    "demand": ["demand", "orders", "backlog"],
    "buyback/dividend": ["buyback", "dividend", "repurchase"],
    "regulation": ["antitrust", "regulation", "lawsuit", "probe"],
    "EV/autos": ["ev ", "electric vehicle", "deliveries"],
}


def extract_news_themes(titles: List[str], limit: int = 3) -> List[str]:
    """Recurring themes present across headlines, most frequent first."""
    blob = " ".join(t.lower() for t in titles if t)
    scored = []
    for theme, kws in _NEWS_KEYWORDS.items():
        hits = sum(blob.count(kw) for kw in kws)
        if hits:
            scored.append((hits, theme))
    scored.sort(reverse=True)
    return [t for _, t in scored[:limit]]


def _stance(buy: int, hold: int, sell: int) -> str:
    total = buy + hold + sell
    if total == 0:
        return "No analyst coverage yet"
    score = buy - sell
    if score <= 0 and sell > buy:
        return "Bearish lean" if sell < buy * 2 else "Strongly bearish"
    if score == 0:
        return "Mixed / hold"
    if buy >= sell * 3 and buy >= total * 0.6:
        return "Strongly bullish"
    return "Bullish lean"


def build_rule_summary(detail: StockDetailResult) -> AnalystSummary:
    c = detail.consensus
    named = [r for r in detail.recommendations if r.firm]
    reasons: List[str] = []

    stance = _stance(c.buy_count, c.hold_count, c.sell_count)
    reasons.append(
        f"{c.buy_count} of {c.total_count} analysts rate it Buy, "
        f"{c.hold_count} Hold, {c.sell_count} Sell (net {c.consensus_score:+d})."
    )

    # Target upside vs current price (from the validated outcome, if any).
    cur = detail.outcome.current_price if detail.outcome else None
    if c.avg_target:
        if cur and cur > 0:
            upside = (c.avg_target - cur) / cur * 100
            reasons.append(
                f"Average price target ${c.avg_target:g} vs current ${cur:g} "
                f"→ {upside:+.0f}% {'upside' if upside >= 0 else 'downside'}."
            )
        else:
            reasons.append(f"Average price target ${c.avg_target:g}.")

    # Recent target revisions among named firms (momentum signal).
    raises = sum(1 for r in named if r.note and "raise" in r.note.lower())
    cuts = sum(1 for r in named if r.note and ("lower" in r.note.lower() or "cut" in r.note.lower()))
    if raises or cuts:
        reasons.append(f"{raises} recent price-target raise(s) and {cuts} cut(s) among named firms.")
    example = next((r for r in named if r.note and "raise" in r.note.lower()), None)
    if example:
        reasons.append(f"e.g., {example.note}.")

    if c.themes:
        reasons.append(f"Part of these segments: {', '.join(c.themes)}.")

    news_themes = extract_news_themes([n.title for n in detail.news])
    if news_themes:
        reasons.append(f"Recent headlines center on: {', '.join(news_themes)}.")

    if detail.outcome and detail.outcome.status == "hit":
        reasons.append("Its price target has already been reached.")

    if c.confidence:
        reasons.append(
            f"Confidence target is hit: {c.confidence.label} "
            f"({c.confidence.score:g}/100) — {c.confidence.rationale}"
        )

    # Headline
    bits = [stance]
    if c.total_count:
        bits.append(f"{c.buy_count}/{c.total_count} rate Buy")
    if c.avg_target and cur and cur > 0:
        up = (c.avg_target - cur) / cur * 100
        bits.append(f"~{up:+.0f}% to target")
    headline = " · ".join(bits)

    return AnalystSummary(headline=headline, reasons=reasons, source="rule")


def _llm_prompt(detail: StockDetailResult) -> str:
    notes = [r.note for r in detail.recommendations if r.firm and r.note][:10]
    headlines = [n.title for n in detail.news][:6]
    c = detail.consensus
    return (
        f"You are a concise equity research assistant. In 2-3 factual sentences, "
        f"summarize WHY Wall Street analysts currently rate {detail.symbol} the way "
        f"they do. Consensus: {c.buy_count} buy, {c.hold_count} hold, {c.sell_count} "
        f"sell; average target ${c.avg_target}.\n\n"
        f"Recent analyst actions:\n- " + "\n- ".join(notes or ["(none)"]) + "\n\n"
        f"Recent headlines:\n- " + "\n- ".join(headlines or ["(none)"]) + "\n\n"
        f"Do not invent specifics; base it only on the above. Summary:"
    )


def maybe_llm_narrative(detail: StockDetailResult, settings: Settings) -> Optional[str]:
    """Best-effort prose narrative from Ollama. None on any failure."""
    return ollama_generate(_llm_prompt(detail), settings, timeout=20)


def build_summary(detail: StockDetailResult, settings: Settings) -> AnalystSummary:
    """Rule summary, optionally enriched with an LLM narrative. Cached per day."""
    key = f"{detail.symbol}:{date.today().isoformat()}:{settings.summary_provider}"
    if key in _SUMMARY_CACHE:
        return _SUMMARY_CACHE[key]

    summary = build_rule_summary(detail)
    narrative = maybe_llm_narrative(detail, settings)
    if narrative:
        summary.narrative = narrative
        summary.source = "ollama"

    _SUMMARY_CACHE[key] = summary
    return summary
