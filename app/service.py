"""Read-side helpers shared by the API and the daily job: build consensus
feeds, per-symbol detail, and the leaderboard from stored data."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from typing import List, Optional

from app.analytics import compute_consensus, estimate_confidence
from app.models import (
    ConsensusOut,
    DailyPoint,
    FeedHighlights,
    Fundamentals,
    Holder,
    LeaderboardEntry,
    LeaderboardResult,
    MacroHeadline,
    MarketDigest,
    NewsItem,
    OutcomeOut,
    Ownership,
    OwnershipSummary,
    RecommendationFeedResult,
    RecommendationOut,
    Returns,
    StockDetailResult,
    ThemeInfo,
    ThemesResult,
    WatchlistItem,
    WatchlistResult,
)
from app.sources.fundamentals import build_fundamentals_notes, fetch_fundamentals
from app.sources.prices import get_current_price
from app.sources.profiles import fetch_ownership, get_price_history
from app.sources.yahoo import get_news
from app.store import RecommendationStore
from app.themes import INDIA_THEMES, THEMES, market_of, themes_for, tickers_for

import logging
logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _enrich(
    store: RecommendationStore,
    c: ConsensusOut,
    *,
    outcomes: Optional[dict] = None,
    profiles: Optional[dict] = None,
    counts: Optional[dict] = None,
) -> ConsensusOut:
    """Attach outcome, themes, company name + returns, and target-hit confidence.
    Pass pre-loaded bulk maps (feed path) to avoid N+1 per-symbol DB queries;
    omit them (detail path) to fall back to individual queries."""
    c.themes = themes_for(c.symbol)

    o = outcomes.get(c.symbol) if outcomes is not None else store.latest_outcome(c.symbol)
    if o:
        c.outcome = OutcomeOut(
            symbol=c.symbol,
            current_price=o.get("current_price"),
            target_price=o.get("target_price"),
            pct_to_target=o.get("pct_to_target"),
            status=o.get("status", "pending"),
            days_held=o.get("days_held", 0),
            last_checked=o.get("last_checked"),
        )

    prof = profiles.get(c.symbol) if profiles is not None else store.get_profile(c.symbol)
    if prof:
        c.company_name = prof.get("company_name")
        c.returns = Returns(
            one_month=prof.get("ret_1m"), three_month=prof.get("ret_3m"),
            six_month=prof.get("ret_6m"), twelve_month=prof.get("ret_12m"),
        )
        if prof.get("inst_pct") is not None or prof.get("top_buyer"):
            c.ownership = OwnershipSummary(
                inst_pct=prof.get("inst_pct"), fund_holders=prof.get("fund_holders"),
                top_buyer=prof.get("top_buyer"), top_buyer_change=prof.get("top_buyer_change"),
            )

    symbol_counts = (counts.get(c.symbol, {}) if counts is not None
                     else store.outcome_counts(c.symbol))
    resolved = symbol_counts.get("hit", 0) + symbol_counts.get("missed", 0)
    hit_rate = (symbol_counts.get("hit", 0) / resolved) if resolved else None
    current_price = c.outcome.current_price if c.outcome else None
    ret_3m = c.returns.three_month if c.returns else None
    c.confidence = estimate_confidence(c, current_price, ret_3m, hit_rate, resolved)
    return c


def build_feed(
    store: RecommendationStore, days: int = 1, theme: Optional[str] = None,
    market: str = "us",
) -> RecommendationFeedResult:
    """Recommendations from the last `days`, grouped per stock with consensus.
    If `theme` is given, only stocks in that segment are returned. `market`
    ("us" | "in") restricts to that market's tickers."""
    recs = store.list_recent(days=days)
    theme_filter = {t.upper() for t in tickers_for(theme, market=market)} if theme else None

    by_symbol: dict[str, list] = {}
    for r in recs:
        if market_of(r.symbol) != market:
            continue
        if theme_filter is not None and r.symbol.upper() not in theme_filter:
            continue
        by_symbol.setdefault(r.symbol, []).append(r)

    # Bulk-load enrichment data once — avoids 3×N per-symbol DB queries (N+1).
    all_outcomes = store.latest_outcomes_all()
    all_profiles_map = store.all_profiles()
    all_counts = store.outcome_counts_all()

    stocks: List[ConsensusOut] = []
    for symbol, group in by_symbol.items():
        consensus = compute_consensus(group)
        if consensus:
            stocks.append(_enrich(store, consensus,
                                  outcomes=all_outcomes,
                                  profiles=all_profiles_map,
                                  counts=all_counts))

    # Strongest consensus first, then by volume of coverage.
    stocks.sort(key=lambda c: (c.consensus_score, c.total_count), reverse=True)
    return RecommendationFeedResult(
        generated_at=_now_iso(), days=days,
        highlights=_highlights(stocks), stocks=stocks,
    )


def compute_daily_points(points: List[dict]) -> List[DailyPoint]:
    """Turn ascending [{date, close}] into DailyPoints with day-over-day %."""
    out: List[DailyPoint] = []
    prev = None
    for p in points:
        close = p["close"]
        change = round((close - prev) / prev * 100, 2) if prev else None
        out.append(DailyPoint(date=p["date"], close=close, change_pct=change))
        prev = close
    return out


def _process_watchlist_entry(e: dict, store: RecommendationStore) -> WatchlistItem:
    """Fetch price history + compute variation for one watchlist entry.
    Extracted so it can run in a thread pool."""
    sym, pin_date, pin_price = e["symbol"], e["pin_date"], e["pin_price"]
    try:
        since = date.fromisoformat(pin_date)
    except (ValueError, TypeError):
        since = None
    hist = get_price_history(sym, since=since)
    pin_pts = [p for p in hist if p["date"] >= pin_date] if hist else []
    daily = compute_daily_points(pin_pts)
    current = (pin_pts[-1]["close"] if pin_pts
               else hist[-1]["close"] if hist else get_current_price(sym))
    if current is not None:
        current = round(current, 2)
    # P0: use explicit None checks — pin_price=0.0 is falsy but valid
    since_pin = (
        round((current - pin_price) / pin_price * 100, 2)
        if current is not None and pin_price is not None and pin_price != 0
        else None
    )
    company = e.get("company_name") or (store.get_profile(sym) or {}).get("company_name")
    return WatchlistItem(
        symbol=sym, company_name=company, group=e["grp"], pin_date=pin_date,
        pin_price=pin_price, current_price=current,
        change_since_pin_pct=since_pin,
        day_change_pct=daily[-1].change_pct if daily else None,
        daily=daily,
    )


def build_watchlist(
    store: RecommendationStore, user_id: int, group: Optional[str] = None,
    market: Optional[str] = None,
) -> WatchlistResult:
    """Watchlist entries with pinned price + daily variation since the pin day,
    scoped to one user. `market` ("us"|"in") restricts to that market's tickers.
    Price histories are fetched in parallel (one thread per item)."""
    entries = [
        e for e in store.list_watchlist(user_id, group)
        if not market or market_of(e["symbol"]) == market
    ]
    if not entries:
        return WatchlistResult(group=group, items=[])

    items: List[WatchlistItem] = []
    with ThreadPoolExecutor(max_workers=min(8, len(entries))) as ex:
        futures = {ex.submit(_process_watchlist_entry, e, store): e for e in entries}
        for f in as_completed(futures):
            try:
                items.append(f.result())
            except Exception as exc:
                sym = futures[f].get("symbol", "?")
                logger.warning("watchlist item failed for %s: %s", sym, exc)

    items.sort(key=lambda i: i.symbol)
    return WatchlistResult(group=group, items=items)


def build_themes(market: str = "us") -> ThemesResult:
    """Thematic segments with their tickers for one market — drives the UI filter."""
    themes = INDIA_THEMES if market == "in" else THEMES
    return ThemesResult(themes=[
        ThemeInfo(name=name, ticker_count=len(tickers), tickers=list(tickers))
        for name, tickers in themes.items()
    ])


def _highlights(stocks: List[ConsensusOut]) -> FeedHighlights:
    """Most-buzzed (widest coverage) + strongest buy / sell consensus."""
    rated = [s for s in stocks if s.total_count > 0]
    if not rated:
        return FeedHighlights()
    by_buzz = sorted(rated, key=lambda s: (s.total_count, len(s.sources)), reverse=True)
    top_buy = max(rated, key=lambda s: s.consensus_score)
    top_sell = min(rated, key=lambda s: s.consensus_score)
    return FeedHighlights(
        most_buzzed=by_buzz[0],
        top_buzzed=by_buzz[:5],
        top_buy=top_buy if top_buy.consensus_score > 0 else None,
        top_sell=top_sell if top_sell.consensus_score < 0 else None,
    )


def build_detail(
    store: RecommendationStore, symbol: str, settings=None
) -> Optional[StockDetailResult]:
    recs = store.list_for_symbol(symbol)
    if not recs:
        return None

    # Compute per-source hit rates so consensus weighting is informed by history.
    counts = store.outcome_counts(symbol)
    resolved_total = counts.get("hit", 0) + counts.get("missed", 0)
    source_hit_rates: Optional[dict] = None
    source_resolved: Optional[dict] = None
    if resolved_total > 0:
        # We use the symbol-level hit rate as a proxy for each source here;
        # per-source tracking requires more resolved rows than typical usage gives.
        rate = counts.get("hit", 0) / resolved_total
        source_hit_rates = {r.source: rate for r in recs}
        source_resolved = {r.source: resolved_total for r in recs}

    consensus = compute_consensus(recs, source_hit_rates=source_hit_rates,
                                  source_resolved=source_resolved)
    # P0: compute_consensus returns None when recs is empty; guard before _enrich.
    if consensus is None:
        return None
    consensus = _enrich(store, consensus)

    # Named analyst calls first (these carry the firm + rationale the user wants
    # on expand), then newest first.
    recs.sort(key=lambda r: (r.firm is not None, r.entry_date or ""), reverse=True)
    rec_out = [
        RecommendationOut(
            rec_id=r.rec_id, symbol=r.symbol, source=r.source, action=r.action,
            count=r.count, firm=r.firm, analyst=r.analyst, note=r.note, url=r.url,
            target_price=r.target_price, entry_price=r.entry_price,
            entry_date=r.entry_date,
        )
        for r in recs
    ]
    news = [NewsItem(**n) for n in get_news(symbol)]
    own = fetch_ownership(symbol)
    ownership = Ownership(
        inst_pct=own.get("inst_pct"), insider_pct=own.get("insider_pct"),
        fund_holders=own.get("fund_holders"),
        institutions=[Holder(**h) for h in own.get("institutions", [])[:8]],
        funds=[Holder(**h) for h in own.get("funds", [])[:8]],
        recent_buyers=[Holder(**h) for h in own.get("recent_buyers", [])[:6]],
    )
    fmap = fetch_fundamentals(symbol)
    fundamentals = Fundamentals(
        pe_ratio=fmap.get("pe_ratio"), forward_pe=fmap.get("forward_pe"),
        peg_ratio=fmap.get("peg_ratio"), eps=fmap.get("eps"),
        market_cap=fmap.get("market_cap"), revenue_growth=fmap.get("revenue_growth"),
        profit_margin=fmap.get("profit_margin"), roe=fmap.get("roe"),
        debt_to_equity=fmap.get("debt_to_equity"), dividend_yield=fmap.get("dividend_yield"),
        beta=fmap.get("beta"), price_to_book=fmap.get("price_to_book"),
        week52_low=fmap.get("week52_low"), week52_high=fmap.get("week52_high"),
        sector=fmap.get("sector"), industry=fmap.get("industry"),
        notes=build_fundamentals_notes(fmap),
    ) if fmap else None

    from app.models import InsiderTrade
    from app.sources.sec_insider import fetch_insider_trades
    raw_trades = fetch_insider_trades(symbol)
    insider_trades = [InsiderTrade(**t) for t in raw_trades]

    detail = StockDetailResult(
        symbol=symbol, consensus=consensus, ownership=ownership,
        fundamentals=fundamentals,
        recommendations=rec_out, outcome=consensus.outcome, news=news,
        insider_trades=insider_trades,
    )
    # "Why analysts recommend" summary (rule-based, optional LLM narrative).
    from app.config import get_settings
    from app.summarize import build_summary
    detail.summary = build_summary(detail, settings or get_settings())
    return detail


def build_market_digest(settings) -> MarketDigest:
    """Aggregate macro headlines from Yahoo Finance + CNBC + MarketWatch RSS,
    with an optional LLM briefing synthesised from the top headlines."""
    from app.llm import ollama_generate
    from app.sources.market_news import fetch_macro_headlines

    raw = fetch_macro_headlines()
    narrative = None
    if raw:
        bullets = "\n".join(f"- {h['title']}" for h in raw[:15])
        prompt = (
            "You are a senior equity analyst preparing a morning macro briefing. "
            "In 3–4 concise sentences summarize the key themes from today's headlines "
            "and what they imply for equity markets (rate expectations, sector rotation, "
            "risk-on/risk-off sentiment, etc.).\n\n"
            f"Headlines:\n{bullets}\n\nBriefing:"
        )
        narrative = ollama_generate(prompt, settings, timeout=30)

    return MarketDigest(
        generated_at=_now_iso(),
        headline_count=len(raw),
        headlines=[MacroHeadline(**h) for h in raw],
        narrative=narrative,
    )


def build_leaderboard(
    store: RecommendationStore, metric: str = "consensus", limit: int = 25,
    market: str = "us",
) -> LeaderboardResult:
    """Rank all tracked stocks (in one market) by consensus score or realized
    target hit rate. Uses 2 bulk queries instead of N+1 per-symbol queries."""
    # P1: bulk fetch avoids N+1 query pattern (was 2×N DB round-trips before).
    all_recs = store.all_recommendations_by_symbol()
    all_counts = store.outcome_counts_all()

    entries: List[LeaderboardEntry] = []
    for symbol, recs in all_recs.items():
        if market_of(symbol) != market:
            continue
        consensus = compute_consensus(recs)
        if not consensus:
            continue
        counts = all_counts.get(symbol, {})
        resolved = counts.get("hit", 0) + counts.get("missed", 0)
        hit_rate = (counts.get("hit", 0) / resolved) if resolved else None
        entries.append(LeaderboardEntry(
            symbol=symbol,
            consensus_score=consensus.consensus_score,
            total_count=consensus.total_count,
            hit_rate=round(hit_rate, 3) if hit_rate is not None else None,
            resolved_count=resolved,
        ))

    if metric == "hit_rate":
        entries.sort(
            key=lambda e: (e.hit_rate if e.hit_rate is not None else -1, e.resolved_count),
            reverse=True,
        )
    else:
        metric = "consensus"
        entries.sort(key=lambda e: (e.consensus_score, e.total_count), reverse=True)

    return LeaderboardResult(metric=metric, entries=entries[:limit])
