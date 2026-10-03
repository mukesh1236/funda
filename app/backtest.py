"""Hypothetical returns from price history: the "what if I'd bought" calculator
and the scoreboard of how the strongest analyst consensus has performed.

Everything here is a SIMULATION on past prices. Two rules keep it honest:

  * No look-ahead. The scoreboard rebuilds the consensus as it stood on each past
    date from only the recommendations dated on or before it, and buys at the
    NEXT trading day's close, because a signal built from a day's data cannot be
    traded at that same day's close.
  * Every result is compared with simply holding the market index over the same
    dates, because "it went up" says nothing when everything went up.

The calculations are pure functions over (date, close) series so they can be
tested without a network. Fetching and orchestration are at the bottom.
"""
import logging
import threading
from bisect import bisect_left, bisect_right
from datetime import date, datetime, timedelta, timezone
from statistics import mean, median
from typing import Dict, List, Optional, Sequence, Tuple

from cachetools import TTLCache

from app.analytics import compute_consensus
from app.models import (
    AnalystRecommendation,
    ScoreboardHorizon,
    ScoreboardResult,
    WhatIfBenchmark,
    WhatIfPoint,
    WhatIfResult,
)
from app.themes import market_of

logger = logging.getLogger(__name__)

HORIZONS = (30, 60, 90)          # days held after buying
TOP_N = 10                        # stocks picked at each snapshot
MIN_ANALYSTS = 5                  # ignore stocks too thinly covered to call a consensus
STEP_DAYS = 7                     # a snapshot every week
MIN_PICKS = 3                     # a snapshot with fewer priced picks is skipped
MAX_SERIES_POINTS = 80            # what-if chart resolution

# The index each market is compared with. ETFs, not the raw index, so dividends are
# in the benchmark the same way they are in the (adjusted) stock prices.
BENCHMARKS = {
    "us": ("SPY", "S&P 500 (SPY)"),
    "in": ("NIFTYBEES.NS", "Nifty 50 (NIFTYBEES)"),
}

PRICE_NOTE = ("Prices are adjusted for splits and dividends. No fees, taxes or slippage. "
              "Hypothetical: past performance does not predict future results.")


class WhatIfError(ValueError):
    """A request the calculator cannot answer; the message is safe to show a user."""


class Series:
    """Ascending (date, close) pairs with the lookups the calculations need."""

    def __init__(self, closes: Sequence[Tuple[date, float]]):
        pairs = sorted((d, float(p)) for d, p in closes if p and float(p) > 0)
        self.dates = [d for d, _ in pairs]
        self.prices = [p for _, p in pairs]

    def __bool__(self) -> bool:
        return bool(self.dates)

    @property
    def first_date(self) -> Optional[date]:
        return self.dates[0] if self.dates else None

    @property
    def last_date(self) -> Optional[date]:
        return self.dates[-1] if self.dates else None

    @property
    def last(self) -> Optional[Tuple[date, float]]:
        return (self.dates[-1], self.prices[-1]) if self.dates else None

    def on_or_after(self, d: date) -> Optional[Tuple[date, float]]:
        i = bisect_left(self.dates, d)
        return (self.dates[i], self.prices[i]) if i < len(self.dates) else None

    def after(self, d: date) -> Optional[Tuple[date, float]]:
        i = bisect_right(self.dates, d)
        return (self.dates[i], self.prices[i]) if i < len(self.dates) else None

    def on_or_before(self, d: date) -> Optional[Tuple[date, float]]:
        i = bisect_right(self.dates, d) - 1
        return (self.dates[i], self.prices[i]) if i >= 0 else None


def _downsample(items: list, limit: int) -> list:
    if len(items) <= limit:
        return items
    step = (len(items) - 1) / (limit - 1)
    picked = [items[round(i * step)] for i in range(limit)]
    picked[-1] = items[-1]
    return picked


# ── what if I had bought ─────────────────────────────────────────────────────
def what_if(symbol: str, amount: float, start: date,
            closes: Sequence[Tuple[date, float]],
            bench_closes: Optional[Sequence[Tuple[date, float]]] = None,
            bench_symbol: Optional[str] = None,
            bench_name: Optional[str] = None) -> WhatIfResult:
    """What `amount` put into `symbol` at the first close on or after `start` would
    be worth at the latest close, next to the same amount in the benchmark."""
    if amount <= 0:
        raise WhatIfError("The amount must be above zero.")
    s = Series(closes)
    if not s:
        raise WhatIfError(f"There is no price history for {symbol}.")
    entry = s.on_or_after(start)
    if entry is None or entry[0] >= s.last_date:
        raise WhatIfError(
            "That date is too recent: there needs to be at least one later trading day "
            f"(latest price is {s.last_date.isoformat()}).")
    entry_date, entry_price = entry
    last_date, last_price = s.last
    shares = amount / entry_price
    value_now = shares * last_price

    bench = Series(bench_closes or [])
    bench_entry = bench.on_or_after(entry_date) if bench else None
    bench_ok = bool(bench_entry and bench.last_date and bench.last_date > bench_entry[0])

    points: List[WhatIfPoint] = []
    for d, p in zip(s.dates, s.prices):
        if d < entry_date:
            continue
        b_val = None
        if bench_ok:
            b = bench.on_or_before(d)
            if b and b[0] >= bench_entry[0]:
                b_val = round(amount * b[1] / bench_entry[1], 2)
        points.append(WhatIfPoint(date=d.isoformat(), value=round(shares * p, 2), benchmark=b_val))
    points = _downsample(points, MAX_SERIES_POINTS)

    benchmark = None
    excess = None
    if bench_ok:
        b_now = bench.last[1]
        b_value = amount * b_now / bench_entry[1]
        benchmark = WhatIfBenchmark(
            symbol=bench_symbol or "", name=bench_name or bench_symbol or "",
            value_now=round(b_value, 2), gain_pct=round((b_value / amount - 1) * 100, 2))
        excess = round(((value_now / amount) - (b_value / amount)) * 100, 2)

    return WhatIfResult(
        symbol=symbol, amount=round(amount, 2), start_requested=start.isoformat(),
        entry_date=entry_date.isoformat(), entry_price=round(entry_price, 2),
        last_date=last_date.isoformat(), last_price=round(last_price, 2),
        shares=round(shares, 4), value_now=round(value_now, 2),
        gain=round(value_now - amount, 2), gain_pct=round((value_now / amount - 1) * 100, 2),
        days=(last_date - entry_date).days, benchmark=benchmark, excess_pct=excess,
        series=points, notes=[PRICE_NOTE],
    )


# ── scoreboard ───────────────────────────────────────────────────────────────
def _window_return(series: Series, signal_day: date, horizon: int) -> Optional[float]:
    """% return from the close AFTER `signal_day` to the close on or after
    signal_day + horizon. None when the window has not fully elapsed in the data."""
    entry = series.after(signal_day)
    exit_ = series.on_or_after(signal_day + timedelta(days=horizon))
    if entry is None or exit_ is None or exit_[0] <= entry[0]:
        return None
    return (exit_[1] / entry[1] - 1) * 100


def _snapshot_dates(first: date, last_price_day: date, min_horizon: int, step: int) -> List[date]:
    out: List[date] = []
    d = first
    cutoff = last_price_day - timedelta(days=min_horizon)
    while d <= cutoff:
        out.append(d)
        d += timedelta(days=step)
    return out


def _picks_as_of(recs_by_symbol: Dict[str, List[AnalystRecommendation]], day: date,
                 top_n: int, min_analysts: int) -> List[str]:
    """The strongest-consensus stocks as they stood on `day`, using only
    recommendations dated on or before it."""
    iso = day.isoformat()
    scored = []
    for sym, recs in recs_by_symbol.items():
        known = [r for r in recs if (r.entry_date or "") <= iso]
        c = compute_consensus(known) if known else None
        if c and c.total_count >= min_analysts and c.consensus_score > 0:
            scored.append((c.consensus_score, c.total_count, sym))
    scored.sort(key=lambda t: (-t[0], -t[1], t[2]))
    return [sym for _, _, sym in scored[:top_n]]


def compute_scoreboard(market: str,
                       recs_by_symbol: Dict[str, List[AnalystRecommendation]],
                       closes_by_symbol: Dict[str, Sequence[Tuple[date, float]]],
                       bench_closes: Optional[Sequence[Tuple[date, float]]] = None,
                       horizons: Sequence[int] = HORIZONS, top_n: int = TOP_N,
                       min_analysts: int = MIN_ANALYSTS, step_days: int = STEP_DAYS) -> ScoreboardResult:
    """How the strongest-consensus stocks fared after being picked, week by week."""
    bench_symbol, bench_name = BENCHMARKS.get(market, BENCHMARKS["us"])
    series = {sym: Series(c) for sym, c in closes_by_symbol.items() if c}
    bench = Series(bench_closes or [])
    all_dates = [r.entry_date for recs in recs_by_symbol.values() for r in recs if r.entry_date]
    notes = [
        "Each week the 10 strongest-consensus stocks are picked from the recommendations "
        "known that day and bought equally at the next day's close.",
        "Weekly windows overlap, so the weeks are not independent: read the figures as "
        "indicative, not as proof.",
        "The tracked list of stocks was chosen today, which flatters it (survivorship).",
        PRICE_NOTE,
    ]
    base = dict(market=market, status="ready", top_n=top_n, min_analysts=min_analysts,
                benchmark_symbol=bench_symbol, benchmark_name=bench_name)
    if not all_dates or not series:
        return ScoreboardResult(**base, horizons=[], snapshots=0, notes=notes + [
            "There is not enough recorded history to score anything yet."])

    first = date.fromisoformat(min(all_dates))
    last_price_day = max(s.last_date for s in series.values())
    snaps = _snapshot_dates(first, last_price_day, min(horizons), step_days)
    picks_by_day = {d: _picks_as_of(recs_by_symbol, d, top_n, min_analysts) for d in snaps}

    out: List[ScoreboardHorizon] = []
    for h in horizons:
        snap_returns, bench_returns, excesses, all_picks = [], [], [], []
        for d in snaps:
            rets = [r for sym in picks_by_day[d] if sym in series
                    for r in [_window_return(series[sym], d, h)] if r is not None]
            if len(rets) < MIN_PICKS:
                continue
            avg = mean(rets)
            snap_returns.append(avg)
            all_picks.extend(rets)
            b = _window_return(bench, d, h) if bench else None
            if b is not None:
                bench_returns.append(b)
                excesses.append(avg - b)
        if not snap_returns:
            out.append(ScoreboardHorizon(days=h, snapshots=0, picks=0))
            continue
        out.append(ScoreboardHorizon(
            days=h, snapshots=len(snap_returns), picks=len(all_picks),
            avg_return_pct=round(mean(snap_returns), 2),
            median_pick_return_pct=round(median(all_picks), 2),
            pct_picks_up=round(sum(1 for r in all_picks if r > 0) / len(all_picks) * 100, 1),
            benchmark_avg_return_pct=round(mean(bench_returns), 2) if bench_returns else None,
            avg_excess_pct=round(mean(excesses), 2) if excesses else None,
            pct_snapshots_beating_benchmark=(
                round(sum(1 for e in excesses if e > 0) / len(excesses) * 100, 1) if excesses else None),
        ))
    return ScoreboardResult(
        **base, since=first.isoformat(), until=last_price_day.isoformat(),
        snapshots=len(snaps), horizons=out, notes=notes)


# ── fetching and orchestration ───────────────────────────────────────────────
_SCORE_CACHE: TTLCache = TTLCache(maxsize=4, ttl=6 * 3600)     # one finished scoreboard per market
_SCORE_FAILED: TTLCache = TTLCache(maxsize=4, ttl=600)         # back off after a failed build
_SCORE_PENDING: set = set()
_SCORE_LOCK = threading.Lock()


def build_what_if(symbol: str, amount: float, start: date) -> WhatIfResult:
    from app.sources.history import get_closes
    if start < date(2000, 1, 1):
        raise WhatIfError("Pick a date after 2000.")
    if start >= date.today():
        raise WhatIfError("Pick a date in the past.")
    closes = get_closes(symbol, start)
    bench_symbol, bench_name = BENCHMARKS[market_of(symbol)]
    bench_closes = get_closes(bench_symbol, start) if bench_symbol != symbol else []
    return what_if(symbol, amount, start, closes, bench_closes, bench_symbol, bench_name)


def _build_scoreboard(store, market: str) -> ScoreboardResult:
    from app.sources.history import get_closes_many
    recs = [r for r in store.all_recommendations() if market_of(r.symbol) == market]
    by_symbol: Dict[str, List[AnalystRecommendation]] = {}
    for r in recs:
        by_symbol.setdefault(r.symbol, []).append(r)
    if not by_symbol:
        return compute_scoreboard(market, {}, {})
    first = date.fromisoformat(min(r.entry_date for r in recs if r.entry_date))
    bench_symbol, _ = BENCHMARKS[market]
    closes = get_closes_many(list(by_symbol) + [bench_symbol], first)
    bench = closes.pop(bench_symbol, [])
    return compute_scoreboard(market, by_symbol, closes, bench)


def scoreboard_ready(market: str) -> Optional[ScoreboardResult]:
    return _SCORE_CACHE.get(market)


def start_scoreboard_build(store, market: str) -> ScoreboardResult:
    """Return the cached scoreboard, or a 'computing' placeholder after making sure
    exactly one build is running. The build downloads prices for the whole tracked
    list, so it runs in the background and the page polls."""
    cached = _SCORE_CACHE.get(market)
    if cached is not None:
        return cached
    if market in _SCORE_FAILED:
        return ScoreboardResult(market=market, status="unavailable", top_n=TOP_N, min_analysts=MIN_ANALYSTS,
                                horizons=[], snapshots=0,
                                notes=["The price data could not be loaded just now. Try again in a few minutes."])
    with _SCORE_LOCK:
        if market not in _SCORE_PENDING:
            _SCORE_PENDING.add(market)
            threading.Thread(target=_run_build, args=(store, market),
                             name=f"scoreboard-{market}", daemon=True).start()
    return ScoreboardResult(market=market, status="computing", top_n=TOP_N, min_analysts=MIN_ANALYSTS,
                            horizons=[], snapshots=0,
                            notes=["Pricing the tracked stocks. This takes up to a minute the first time."])


def _run_build(store, market: str) -> None:
    try:
        result = _build_scoreboard(store, market)
        if not any(h.snapshots for h in result.horizons) and not result.since:
            raise RuntimeError("no result")
        _SCORE_CACHE[market] = result
    except Exception as e:
        logger.warning("scoreboard build failed for %s: %s", market, e)
        _SCORE_FAILED[market] = True
    finally:
        with _SCORE_LOCK:
            _SCORE_PENDING.discard(market)
