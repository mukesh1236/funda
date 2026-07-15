"""Read-side helpers shared by the API and the daily job: build consensus
feeds, per-symbol detail, and the leaderboard from stored data."""
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from app.analytics import compute_consensus, estimate_confidence
from app.models import (
    ConsensusOut,
    DailyPoint,
    FeedHighlights,
    TodayCall,
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
from app.sources.profiles import fetch_ownership, fetch_profile, get_price_history
from app.sources.yahoo import get_news
from app.store import RecommendationStore
from app.themes import INDIA_THEMES, THEMES, market_of, themes_for, tickers_for




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


# Keyed by the actual symbol set, not just the market: keying by market alone
# let a theme-filtered request poison the cache with a subset of symbols for
# every other request's 5 minutes.
_DAY_CHANGE_CACHE: Dict[frozenset, Tuple[float, Dict[str, float]]] = {}
_DAY_CHANGE_TTL = 300  # 5 minutes — fast enough for intraday, cheap on yfinance


def _batch_day_changes(symbols: List[str], market: str = "us") -> Dict[str, float]:
    """Fetch today's % price change for a list of symbols in one yfinance call.
    Cached 5 minutes per symbol-set so rapid user refreshes don't hammer
    yfinance. Returns {SYMBOL: pct_change}. Silently returns {} on any failure."""
    if not symbols:
        return {}
    key = frozenset(s.upper() for s in symbols)
    now = time.time()
    cached = _DAY_CHANGE_CACHE.get(key)
    if cached and now - cached[0] < _DAY_CHANGE_TTL:
        return cached[1]
    try:
        import yfinance as yf
        raw = yf.download(
            " ".join(symbols), period="2d", auto_adjust=True,
            progress=False, group_by="ticker", threads=True,
        )
        result: Dict[str, float] = {}
        for sym in symbols:
            try:
                close_col = raw["Close"] if len(symbols) == 1 else raw[sym]["Close"]
                closes = close_col.dropna()
                if len(closes) >= 2:
                    prev, today_c = float(closes.iloc[-2]), float(closes.iloc[-1])
                    if prev > 0:
                        result[sym.upper()] = round((today_c - prev) / prev * 100, 2)
            except Exception:
                pass
        _DAY_CHANGE_CACHE[key] = (now, result)
        return result
    except Exception as e:
        logger.info("batch day-change fetch failed: %s", e)
        return {}


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
    market_symbols: set = set()   # every symbol in this market, pre-theme-filter
    for r in recs:
        if market_of(r.symbol) != market:
            continue
        market_symbols.add(r.symbol)
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

    # Enrich with live today's price change (one batch yfinance call).
    # Fetch for the whole market's symbols — not just the theme-filtered subset —
    # so every theme view within the TTL shares one warm cache entry.
    day_changes = _batch_day_changes(sorted(market_symbols), market=market)
    for s in stocks:
        s.day_change_pct = day_changes.get(s.symbol.upper())

    return RecommendationFeedResult(
        generated_at=_now_iso(), days=days,
        highlights=_highlights(stocks, store, market), stocks=stocks,
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


def _highlights(
    stocks: List[ConsensusOut],
    store: "RecommendationStore",
    market: str = "us",
) -> FeedHighlights:
    """Most-buzzed, strongest buy/sell consensus, today's movers, and today's catalysts."""
    rated = [s for s in stocks if s.total_count > 0]
    if not rated:
        return FeedHighlights()
    by_buzz = sorted(rated, key=lambda s: (s.total_count, len(s.sources)), reverse=True)
    top_buy = max(rated, key=lambda s: s.consensus_score)
    top_sell = min(rated, key=lambda s: s.consensus_score)

    # Today's movers: stocks that actually gained today, sorted by % change.
    movers = sorted(
        [s for s in rated if s.day_change_pct is not None and s.day_change_pct > 0],
        key=lambda s: s.day_change_pct,
        reverse=True,
    )[:5]

    # Today's catalysts: analyst calls published today (the "why" behind moves).
    today = date.today().isoformat()
    # Build lookup: symbol → ConsensusOut for day_change_pct + company_name
    stock_map = {s.symbol.upper(): s for s in rated}
    today_recs = [
        r for r in store.list_recent(days=1)
        if r.entry_date == today
        and market_of(r.symbol) == market
        and r.action.lower() in ("buy", "strong buy", "outperform", "overweight",
                                  "upgrade", "initiate", "positive", "accumulate")
    ]
    # Dedupe: one call per symbol (most bullish / most specific firm first)
    seen: set = set()
    catalysts: List[TodayCall] = []
    for r in sorted(today_recs, key=lambda r: (r.firm is not None, r.target_price is not None), reverse=True):
        sym = r.symbol.upper()
        if sym in seen:
            continue
        seen.add(sym)
        stock = stock_map.get(sym)
        catalysts.append(TodayCall(
            symbol=r.symbol,
            company_name=stock.company_name if stock else None,
            firm=r.firm,
            action=r.action,
            target_price=r.target_price,
            day_change_pct=stock.day_change_pct if stock else None,
        ))
        if len(catalysts) >= 8:
            break

    # Sort: moving stocks with a named firm call first
    catalysts.sort(key=lambda c: (
        c.day_change_pct is not None and c.day_change_pct > 0,
        c.firm is not None,
        c.day_change_pct or 0,
    ), reverse=True)

    return FeedHighlights(
        most_buzzed=by_buzz[0],
        top_buzzed=by_buzz[:5],
        top_buy=top_buy if top_buy.consensus_score > 0 else None,
        top_sell=top_sell if top_sell.consensus_score < 0 else None,
        top_movers=movers,
        today_catalysts=catalysts[:6],
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
    # The four enrichment fetches are independent network calls — run them
    # concurrently instead of stacking their latencies end to end.
    from app.sources.sec_insider import fetch_insider_trades
    with ThreadPoolExecutor(max_workers=4) as ex:
        f_news = ex.submit(get_news, symbol)
        f_own = ex.submit(fetch_ownership, symbol)
        f_fund = ex.submit(fetch_fundamentals, symbol)
        f_trades = ex.submit(fetch_insider_trades, symbol)
        news_raw, own, fmap, raw_trades = (
            f_news.result(), f_own.result(), f_fund.result(), f_trades.result()
        )

    news = [NewsItem(**n) for n in news_raw]
    ownership = Ownership(
        inst_pct=own.get("inst_pct"), insider_pct=own.get("insider_pct"),
        fund_holders=own.get("fund_holders"),
        institutions=[Holder(**h) for h in own.get("institutions", [])[:8]],
        funds=[Holder(**h) for h in own.get("funds", [])[:8]],
        recent_buyers=[Holder(**h) for h in own.get("recent_buyers", [])[:6]],
    )
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


_OVERVIEW_CACHE: Dict[str, Tuple[float, "StockOverview"]] = {}
_OVERVIEW_TTL = 600   # 10 min — generic data, doesn't need to be tick-fresh


def build_stock_overview(symbol: str):
    """Generic profile for ANY ticker — the finance-site basics (price,
    company, fundamentals, ownership, news, insider trades) — independent of
    whether we track analyst recommendations for it. Returns None only when
    every source comes back empty (i.e. the ticker doesn't resolve)."""
    from app.models import InsiderTrade, StockOverview
    from app.sources.sec_insider import fetch_insider_trades

    sym = symbol.upper().strip()
    now = time.time()
    cached = _OVERVIEW_CACHE.get(sym)
    if cached and now - cached[0] < _OVERVIEW_TTL:
        return cached[1]

    with ThreadPoolExecutor(max_workers=6) as ex:
        f_prof = ex.submit(fetch_profile, sym)
        f_price = ex.submit(get_current_price, sym)
        f_fund = ex.submit(fetch_fundamentals, sym)
        f_news = ex.submit(get_news, sym)
        f_own = ex.submit(fetch_ownership, sym)
        f_ins = ex.submit(fetch_insider_trades, sym)
        prof, price, fmap, news_raw, own, raw_trades = (
            f_prof.result(), f_price.result(), f_fund.result(),
            f_news.result(), f_own.result(), f_ins.result(),
        )

    name = (prof or {}).get("company_name")
    if name is None and price is None and not fmap:
        return None   # nothing resolves — likely not a real ticker

    rets = (prof or {}).get("returns") or {}
    returns = Returns(
        one_month=rets.get("one_month"), three_month=rets.get("three_month"),
        six_month=rets.get("six_month"), twelve_month=rets.get("twelve_month"),
    )
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
    ownership = Ownership(
        inst_pct=own.get("inst_pct"), insider_pct=own.get("insider_pct"),
        fund_holders=own.get("fund_holders"),
        institutions=[Holder(**h) for h in own.get("institutions", [])[:8]],
        funds=[Holder(**h) for h in own.get("funds", [])[:8]],
        recent_buyers=[Holder(**h) for h in own.get("recent_buyers", [])[:6]],
    )

    overview = StockOverview(
        symbol=sym, company_name=name,
        price=round(price, 2) if price is not None else None,
        returns=returns, fundamentals=fundamentals, ownership=ownership,
        news=[NewsItem(**n) for n in news_raw],
        insider_trades=[InsiderTrade(**t) for t in raw_trades],
    )
    _OVERVIEW_CACHE[sym] = (now, overview)
    return overview


def build_market_digest(settings, market: str = "us") -> MarketDigest:
    """Aggregate macro headlines for one market (US or India) from Yahoo Finance
    index news + that market's RSS feeds, with an optional LLM briefing."""
    from app.llm import generate_narrative
    from app.sources.market_news import fetch_macro_headlines

    raw = fetch_macro_headlines(market=market)
    narrative = None
    if raw:
        region = "Indian" if market == "in" else "US"
        bullets = "\n".join(f"- {h['title']}" for h in raw[:15])
        prompt = (
            f"You are a senior equity analyst preparing a morning macro briefing on the "
            f"{region} equity market. In 3–4 concise sentences summarize the key themes "
            "from today's headlines and what they imply for equities (rate expectations, "
            "sector rotation, risk-on/risk-off sentiment, etc.).\n\n"
            f"Headlines:\n{bullets}\n\nBriefing:"
        )
        narrative = generate_narrative(prompt, settings, timeout=30)

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
