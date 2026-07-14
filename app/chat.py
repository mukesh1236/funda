"""Q&A over the live feed, leaderboard, and individual stocks.

Two layers:
  1. A deterministic answer engine (_rule_answer) handles the common, well-shaped
     questions directly from the data — instant, free, always available.
  2. For open-ended questions, falls back to the configured LLM (Gemini/Ollama),
     and if that's unavailable it returns a data overview rather than an error.
"""
import logging
import re
from typing import List, Optional, Tuple

from app.analytics import compute_consensus
from app.config import Settings
from app.llm import generate_narrative
from app.service import build_feed, build_leaderboard
from app.store import RecommendationStore

logger = logging.getLogger(__name__)

_MAX_FEED = 40        # cap LLM context size to stay within free-tier limits
_MAX_LB = 20
_MAX_NAMED = 12
_LLM_PROVIDERS = ("gemini", "grok", "ollama", "auto")


# ── shared formatting ─────────────────────────────────────────────────────────
def _line(s) -> str:
    tgt = f", avg target ${s.avg_target}" if s.avg_target else ""
    return (f"{s.symbol} ({s.company_name or s.symbol}): "
            f"{s.buy_count}B/{s.hold_count}H/{s.sell_count}S, score {s.consensus_score:+d}{tgt}")


# ── deterministic answer engine ───────────────────────────────────────────────
_COMMON_WORDS = {
    "NOW", "IT", "ALL", "ON", "OR", "ARE", "BE", "UP", "DO", "GO", "SO",
    "AI", "ME", "MY", "BY", "IN", "AT", "TO", "OF", "IS", "HI", "AN",
    "RE", "AS", "IF", "NO", "US", "WE", "HE", "SHE", "THE", "AND", "FOR",
    "NOT", "BUT", "OUT", "CAN", "MAY", "HAD", "HAS", "WAS", "DID", "GET",
    "BUY", "TOP", "HOW", "WHY", "WHO", "ANY", "NEW", "OLD", "BIG", "LOW",
    "HIGH", "BEST", "MORE", "MOST", "SOME", "JUST", "ALSO", "THEN", "THAN",
    "INTO", "OVER", "WELL", "ONLY", "LAST", "NEXT", "GOOD", "VERY", "REAL",
}


def _detect_symbol(question: str, stocks: list) -> Optional[str]:
    """Find a ticker the user mentioned (by symbol or company name)."""
    toks = {t.upper() for t in re.findall(r"[A-Za-z.\-]{2,}", question)}
    by_sym = {s.symbol.upper(): s.symbol for s in stocks}
    for t in toks:
        if t in by_sym and t not in _COMMON_WORDS:
            return by_sym[t]
    # company-name match (need a distinctive word, len >= 4)
    ql = question.lower()
    for s in stocks:
        name = (s.company_name or "").lower()
        for word in re.findall(r"[a-z]{4,}", name):
            if word in ("inc", "corp", "ltd", "limited", "company", "group", "holdings") :
                continue
            if word in ql:
                return s.symbol
    return None


def _stock_answer(store: RecommendationStore, symbol: str, stocks: list) -> str:
    s = next((x for x in stocks if x.symbol.upper() == symbol.upper()), None)
    recs = store.list_for_symbol(symbol)
    c = s or compute_consensus(recs)
    if not c:
        return f"I don't have analyst data for {symbol} in this market yet."
    named = [r for r in recs if r.firm][:6]
    parts = [
        f"{c.symbol} ({getattr(c, 'company_name', None) or c.symbol}): "
        f"{c.buy_count} Buy / {c.hold_count} Hold / {c.sell_count} Sell "
        f"(net {c.consensus_score:+d})."
    ]
    if c.avg_target:
        cur = c.outcome.current_price if (s and s.outcome) else None
        if cur:
            up = (c.avg_target - cur) / cur * 100
            parts.append(f"Avg price target ${c.avg_target:.2f} vs ${cur:.2f} ({up:+.0f}%).")
        else:
            parts.append(f"Avg price target ${c.avg_target:.2f}.")
    if getattr(c, "conviction_score", None) is not None:
        parts.append(f"Analyst conviction (agreement): {round(c.conviction_score * 100)}%.")
    if named:
        parts.append("Recent named calls: " + "; ".join(
            f"{r.firm} {r.action.upper()}" + (f" PT ${r.target_price:g}" if r.target_price else "")
            for r in named))
    return " ".join(parts)


def _rule_answer(
    store: RecommendationStore, market: str, question: str, symbol: Optional[str],
    feed,
) -> Optional[str]:
    q = question.lower()
    rated = [s for s in feed.stocks if s.total_count > 0]

    # Specific stock (passed in, or mentioned in the question)
    target = symbol or _detect_symbol(question, feed.stocks)
    if target:
        return _stock_answer(store, target, feed.stocks)

    if not rated:
        return "No analyst-rated stocks are in the feed yet for this market. Try ↻ Refresh now."

    def top(key, n=5):
        return sorted(rated, key=key, reverse=True)[:n]

    # Sell / bearish
    if any(w in q for w in ("sell", "bearish", "avoid", "short ", "downgrade", "worst")):
        picks = sorted([s for s in rated if s.sell_count > 0],
                       key=lambda s: (s.sell_count, -s.consensus_score), reverse=True)[:5]
        if not picks:
            return "No stocks currently have a sell-leaning consensus in this market."
        return "Most sell-rated right now:\n" + "\n".join("• " + _line(s) for s in picks)

    # Conviction / agreement
    if any(w in q for w in ("conviction", "agree", "unanimous", "consensus stronges")):
        picks = top(lambda s: (s.conviction_score or 0, s.total_count))
        return "Highest analyst conviction (agreement level):\n" + "\n".join(
            f"• {s.symbol}: {round((s.conviction_score or 0) * 100)}% aligned, "
            f"{s.buy_count}B/{s.hold_count}H/{s.sell_count}S" for s in picks)

    # Hit rate / accuracy / track record
    if any(w in q for w in ("hit rate", "hit-rate", "accurate", "track record",
                            "reliable", "best perform", "performing")):
        lb = build_leaderboard(store, metric="hit_rate", limit=10, market=market)
        picks = [e for e in lb.entries if e.resolved_count > 0][:5]
        if not picks:
            return "Not enough resolved target outcomes yet to rank by hit rate."
        return "Best target hit rates so far:\n" + "\n".join(
            f"• {e.symbol}: {round(e.hit_rate * 100)}% of {e.resolved_count} resolved calls hit"
            for e in picks)

    # Buzz / coverage
    if any(w in q for w in ("buzz", "coverage", "most analyst", "popular", "talked", "covered")):
        picks = top(lambda s: (s.total_count, len(s.sources)))
        return "Most analyst coverage today:\n" + "\n".join(
            f"• {s.symbol}: {s.total_count} analysts, {s.buy_count}B/{s.hold_count}H/{s.sell_count}S"
            for s in picks)

    # Upside / target potential
    if any(w in q for w in ("upside", "potential", "highest target", "most room", "undervalued")):
        cand = []
        for s in rated:
            cur = s.outcome.current_price if s.outcome else None
            if s.avg_target and cur and cur > 0:
                cand.append(((s.avg_target - cur) / cur * 100, s))
        cand.sort(key=lambda t: t[0], reverse=True)
        if not cand:
            return "No price-target upside data is available yet."
        return "Highest upside to the average analyst target:\n" + "\n".join(
            f"• {s.symbol}: {up:+.0f}% (${s.outcome.current_price:.2f} → ${s.avg_target:.2f})"
            for up, s in cand[:5])

    # Buy / bullish / best / strongest (broad)
    if any(w in q for w in ("buy", "bullish", "strong", "best", "top", "recommend", "pick")):
        picks = top(lambda s: (s.consensus_score, s.total_count))
        return "Strongest buy consensus right now:\n" + "\n".join("• " + _line(s) for s in picks)

    return None   # open-ended → let the LLM (or overview) handle it


def _overview(feed) -> str:
    rated = [s for s in feed.stocks if s.total_count > 0][:5]
    if not rated:
        return "No analyst-rated stocks are in the feed yet for this market."
    return (
        "Here are today's strongest-consensus stocks:\n"
        + "\n".join("• " + _line(s) for s in rated)
        + "\n\nYou can ask about a specific stock, or about conviction, hit rate, "
        "upside, coverage, or sells."
    )


# ── LLM context (open-ended questions) ────────────────────────────────────────
def _fmt_feed(feed) -> str:
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
        "You are AlphaFunds' equity research assistant. Reason over the analyst "
        "dataset below and answer the user's question directly.\n\n"
        "GUIDELINES:\n"
        "- First think about what the question is actually asking, then answer THAT "
        "specifically — never reply with a generic list when a specific question was asked.\n"
        "- Ground every claim in the dataset: cite tickers and the numbers behind your reasoning.\n"
        "- You are encouraged to reason: compare stocks, compute upside vs. targets, weigh "
        "conviction against coverage breadth, use hit rates to judge reliability, and explain "
        "the WHY behind a consensus using the named firm actions and notes.\n"
        "- If the dataset cannot answer the question, say so and name exactly what's missing — "
        "do not invent facts about companies beyond this dataset.\n"
        "- No investment advice; you analyse, the user decides.\n"
        "- Answer in 3-8 sentences of clear prose.\n\n"
        f"MARKET: {region}\n\n"
        f"ANALYST FEED (last 30 days):\n{feed}\n\n"
        f"LEADERBOARD (by hit rate):\n{lb}\n"
        f"{focus}\n"
        f"USER QUESTION: {question}\n\nANSWER:"
    )


# ── fund context injection ────────────────────────────────────────────────────

_FUND_KEYWORDS = frozenset({
    "etf", "fund", "expense ratio", "expense", "holdings", "sector",
    "cagr", "inception", "return", "performance", "fees", "nav",
    "vanguard", "ishares", "invesco", "schwab", "fidelity", "blackrock",
})

_KNOWN_FUND_SYMBOLS = frozenset({
    "SPY", "VOO", "QQQ", "VTI", "IVV", "VUG", "SCHD", "GLD", "TLT",
    "FXAIX", "VFIAX", "FCNTX", "VTSAX", "SWTSX", "FSKAX",
    "AGG", "BND", "VNQ", "XLF", "XLK", "XLE", "XLV", "XLI",
})


def _detect_fund_ticker(question: str) -> Optional[str]:
    """Return a fund symbol if the question seems to be about a fund."""
    q = question.upper()
    ql = question.lower()

    # Exact fund symbol match (case-insensitive — the whitelist is safe).
    for sym in _KNOWN_FUND_SYMBOLS:
        if re.search(r"\b" + re.escape(sym) + r"\b", q):
            return sym

    # Generic fund keyword: extract a ticker token from the ORIGINAL question.
    # Only words the user actually typed in uppercase count — matching against
    # the uppercased question would turn every word ("WHAT", "BEST") into a
    # ticker candidate and hijack ordinary questions into the fund path.
    if any(kw in ql for kw in _FUND_KEYWORDS):
        toks = re.findall(r"\b([A-Z]{2,6})\b", question)
        for tok in toks:
            if tok not in _COMMON_WORDS:
                return tok

    return None


def _build_fund_context(symbol: str) -> str:
    """Build a structured context block from live yfinance data."""
    try:
        from app.fund_data import get_fund_info, get_fund_performance

        info = get_fund_info(symbol)
        if not info:
            return ""
        perf = get_fund_performance(symbol) or {}

        parts = [f"FUND DATA for {symbol} ({info.get('name', symbol)}):"]
        if info.get("category"):
            parts.append(f"Category: {info['category']}")
        if info.get("expense_ratio") is not None:
            parts.append(f"Expense ratio: {info['expense_ratio']}% per year")
        if perf.get("inception_date"):
            parts.append(f"Inception: {perf['inception_date']}")
        if perf.get("since_inception_cagr") is not None:
            parts.append(f"CAGR since inception: {perf['since_inception_cagr']}%")
        if perf.get("total_return_pct") is not None:
            parts.append(f"Total return since inception: {perf['total_return_pct']}%")
        for label, key in [("1Y CAGR", "cagr_1y"), ("3Y CAGR", "cagr_3y"), ("5Y CAGR", "cagr_5y")]:
            if perf.get(key) is not None:
                parts.append(f"{label}: {perf[key]}%")
        holdings = info.get("holdings", [])
        if holdings:
            h_text = ", ".join(
                f"{h.get('name') or h.get('ticker', '?')} {h.get('weight', 0):.1f}%"
                for h in holdings[:5]
            )
            parts.append(f"Top 5 holdings: {h_text}")
        sectors = info.get("sector_weights", {})
        if sectors:
            top_s = sorted(sectors.items(), key=lambda x: x[1], reverse=True)[:3]
            s_text = ", ".join(f"{k} {v:.1f}%" for k, v in top_s)
            parts.append(f"Top sectors: {s_text}")
        return "\n".join(parts)
    except Exception as e:
        logger.debug("_build_fund_context(%s): %s", symbol, e)
        return ""


# ── entry point ───────────────────────────────────────────────────────────────
def answer_question(
    store: RecommendationStore,
    settings: Settings,
    question: str,
    market: str = "us",
    symbol: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str], str]:
    """Return (answer, error, source).

    LLM-FIRST: when an LLM provider is configured, every question goes to the
    LLM with the full data context so answers are reasoned, not canned. The
    deterministic rule engine and the overview are FALLBACKS for when the LLM
    is off or unreachable — never the first choice. (It used to be the other
    way around, which made the bot parrot dashboard lists.)

    source ∈ {"llm", "fund-data", "rule", "overview"} — surfaced in the UI so
    a fallback answer is visibly a fallback.
    """
    llm_on = settings.summary_provider in _LLM_PROVIDERS

    # 0) Fund-specific question — inject live fund data + RAG context.
    fund_sym = _detect_fund_ticker(question)
    if fund_sym:
        fund_context = _build_fund_context(fund_sym)
        if fund_context and llm_on:
            # Pull extra context from the RAG index if available.
            try:
                from app.fund_rag import query_fund_docs
                rag_ctx = query_fund_docs(fund_sym, question)
                if rag_ctx:
                    fund_context += "\n\nFund document context:\n" + rag_ctx
            except Exception as e:
                logger.debug("RAG query skipped: %s", e)

            fund_prompt = (
                "You are a fund research assistant. Reason over the data below to "
                "answer the question directly — cite the numbers behind your answer, "
                "and say what's missing if the data can't answer it.\n"
                "Do not give investment advice. 3-6 sentences.\n\n"
                f"{fund_context}\n\n"
                f"QUESTION: {question}\n\nANSWER:"
            )
            answer = generate_narrative(fund_prompt, settings, timeout=30)
            if answer:
                return answer, None, "llm"

        # LLM off/unreachable → structured fund data beats no answer.
        if fund_context:
            return fund_context, None, "fund-data"

    # Build the feed ONCE per question — priciest input, shared by every path.
    feed = build_feed(store, days=30, market=market)

    # 1) LLM with full context — the primary answer path when configured.
    if llm_on:
        feed_ctx = _fmt_feed(feed)
        lb = _fmt_leaderboard(store, market)
        # include stock context even when the symbol comes from question text
        detected = symbol or _detect_symbol(question, feed.stocks)
        sym_ctx = _fmt_symbol(store, detected) if detected else ""
        prompt = _prompt(question, market, feed_ctx, lb, sym_ctx)
        answer = generate_narrative(prompt, settings, timeout=30)
        if answer:
            return answer, None, "llm"
        from app import llm
        logger.info("Chat LLM unavailable (%s) — falling back to rule engine.",
                    llm.last_gemini_error)

    # 2) Fallback: deterministic answer for common, structured questions.
    rule = _rule_answer(store, market, question, symbol, feed)
    if rule:
        return rule, None, "rule"

    # 3) Last resort: data overview (never a raw error).
    overview = _overview(feed)
    if not llm_on:
        overview += ("\n\n💡 AI reasoning is off. Set SUMMARY_PROVIDER "
                     "(gemini / grok / ollama) and the matching API key to get "
                     "reasoned answers instead of quick data lookups.")
    return overview, None, "overview"
