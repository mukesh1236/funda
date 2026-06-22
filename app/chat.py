"""Grounded Q&A over the live feed, leaderboard, and individual stocks.

Builds a compact text snapshot of current data and asks the configured LLM
(Gemini / Ollama) to answer using ONLY that data — so replies are grounded in
real consensus numbers rather than the model's training memory.
"""
import logging
from typing import Optional, Tuple

from app.analytics import compute_consensus
from app.config import Settings
from app.llm import generate_narrative
from app.service import build_feed, build_leaderboard
from app.store import RecommendationStore

logger = logging.getLogger(__name__)

_MAX_FEED = 40        # cap context size to stay well within free-tier limits
_MAX_LB = 20
_MAX_NAMED = 12
_LLM_PROVIDERS = ("gemini", "ollama", "auto")


def _fmt_feed(store: RecommendationStore, market: str) -> str:
    feed = build_feed(store, days=30, market=market)
    lines = []
    for s in feed.stocks[:_MAX_FEED]:
        conf = f"{s.confidence.label}({round(s.confidence.score)})" if s.confidence else "—"
        conv = f"{round(s.conviction_score * 100)}%" if s.conviction_score is not None else "—"
        tgt = f"${s.avg_target}" if s.avg_target else "—"
        status = s.outcome.status if s.outcome else "—"
        segs = ", ".join(s.themes) if s.themes else "—"
        lines.append(
            f"{s.symbol} ({s.company_name or ''}): "
            f"{s.buy_count}B/{s.hold_count}H/{s.sell_count}S, score {s.consensus_score:+d}, "
            f"conviction {conv}, avg_target {tgt}, confidence {conf}, "
            f"target_status {status}, segments [{segs}]"
        )
    return "\n".join(lines) or "(no stocks in the feed)"


def _fmt_leaderboard(store: RecommendationStore, market: str) -> str:
    lb = build_leaderboard(store, metric="hit_rate", limit=_MAX_LB, market=market)
    lines = []
    for i, e in enumerate(lb.entries, 1):
        hr = f"{round(e.hit_rate * 100)}%" if e.hit_rate is not None else "n/a"
        lines.append(
            f"#{i} {e.symbol}: score {e.consensus_score:+d}, "
            f"{e.total_count} analysts, hit_rate {hr} ({e.resolved_count} resolved)"
        )
    return "\n".join(lines) or "(leaderboard empty)"


def _fmt_symbol(store: RecommendationStore, symbol: str) -> str:
    """Lightweight per-stock context (avoids the heavy build_detail LLM path)."""
    recs = store.list_for_symbol(symbol)
    if not recs:
        return ""
    c = compute_consensus(recs)
    if not c:
        return ""
    named = [r for r in recs if r.firm][:_MAX_NAMED]
    firm_lines = [
        f"  {r.firm}: {r.action.upper()}"
        + (f" target ${r.target_price:g}" if r.target_price else "")
        + (f" — {r.note}" if r.note else "")
        for r in named
    ]
    head = (
        f"FOCUS STOCK {symbol} ({c.company_name or ''}): "
        f"{c.buy_count}B/{c.hold_count}H/{c.sell_count}S, score {c.consensus_score:+d}, "
        f"avg_target {('$' + str(c.avg_target)) if c.avg_target else '—'}, "
        f"sources [{', '.join(c.sources)}]"
    )
    firms = "\n".join(firm_lines) if firm_lines else "  (no named-firm detail)"
    return f"{head}\nNamed firm ratings:\n{firms}"


def _prompt(question: str, market: str, feed: str, lb: str, sym_ctx: str) -> str:
    region = "Indian (NSE)" if market == "in" else "US"
    focus = f"\n{sym_ctx}\n" if sym_ctx else ""
    return (
        "You are AlphaFunds' equity-research assistant. Answer the user's question "
        "using ONLY the data below. If the data doesn't contain the answer, say so "
        "plainly — never invent tickers, prices, ratings, or figures. Be concise and "
        "specific; cite tickers and numbers from the data. Do not give personalized "
        "investment advice or tell the user what to buy/sell.\n\n"
        f"MARKET: {region}\n\n"
        f"FEED — analyst consensus per stock (last 30 days):\n{feed}\n\n"
        f"LEADERBOARD — ranked by realized target hit rate:\n{lb}\n"
        f"{focus}\n"
        f"USER QUESTION: {question}\n\nANSWER:"
    )


def answer_question(
    store: RecommendationStore,
    settings: Settings,
    question: str,
    market: str = "us",
    symbol: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Return (answer, error). error is set when the LLM isn't configured/usable."""
    if settings.summary_provider not in _LLM_PROVIDERS:
        return None, (
            "AI chat isn't enabled. Set SUMMARY_PROVIDER to 'gemini' and add a "
            "GEMINI_API_KEY (free at aistudio.google.com/apikey)."
        )

    feed = _fmt_feed(store, market)
    lb = _fmt_leaderboard(store, market)
    sym_ctx = _fmt_symbol(store, symbol) if symbol else ""
    prompt = _prompt(question, market, feed, lb, sym_ctx)

    answer = generate_narrative(prompt, settings, timeout=30)
    if not answer:
        from app import llm
        reason = llm.last_gemini_error
        detail = "The AI is temporarily unavailable. Try again in a moment."
        if reason:
            detail += f" (reason: {reason})"
        return None, detail
    return answer, None
