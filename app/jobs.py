"""Daily pipeline: collect → store → validate outcomes → notify.

Designed to be safe to run repeatedly: stored recommendations dedupe on
(symbol, source, firm, action, entry_date), so a second run on the same day
adds nothing new.
"""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from typing import Optional

from app.analytics import evaluate_outcome
from app.config import Settings, get_settings
from app.notifications import get_notifier
from app.service import build_feed
from app.sources.finnhub import FinnhubClient, FinnhubError
from app.sources.fmp import FMPClient, FMPError
from app.sources.morningstar import MorningstarScraper
from app.sources.polygon import PolygonClient, PolygonError
from app.sources.prices import get_current_price
from app.sources.profiles import fetch_ownership, fetch_profile, ownership_summary
from app.sources.tipranks import TipRanksClient
from app.sources.yahoo import YahooClient, YahooUpgradesClient
from app.store import RecommendationStore

logger = logging.getLogger(__name__)


def _build_sources(settings: Settings) -> list:
    """Assemble the enabled source callables. Each exposes
    get_recommendations(symbol, entry_date) -> list[AnalystRecommendation].
    Sources that need missing credentials are skipped with a log line."""
    sources: list = []

    if settings.yahoo_enabled:
        sources.append(("yahoo", YahooClient()))
        # Named per-firm actions (display detail, not counted).
        sources.append(("yahoo_upgrades", YahooUpgradesClient()))

    try:
        sources.append(("finnhub", FinnhubClient(settings.finnhub_api_key)))
    except FinnhubError as e:
        logger.info("Finnhub disabled: %s", e)

    if settings.tipranks_enabled:
        sources.append(("tipranks", TipRanksClient(enabled=True)))

    try:
        sources.append(("fmp", FMPClient(settings.fmp_api_key)))
    except FMPError as e:
        logger.info("FMP disabled: %s", e)

    # Morningstar exposes get_analyst_view (single) — adapt to the list API.
    if settings.morningstar_scrape_enabled:
        scraper = MorningstarScraper(enabled=True)

        class _MorningstarAdapter:
            def get_recommendations(self, symbol, entry_date=None):
                rec = scraper.get_analyst_view(symbol, entry_date=entry_date)
                return [rec] if rec else []

        sources.append(("morningstar", _MorningstarAdapter()))

    try:
        sources.append(("polygon", PolygonClient(settings.polygon_api_key)))
    except PolygonError as e:
        logger.info("Polygon disabled: %s", e)

    return sources


def collect(store: RecommendationStore, settings: Settings) -> int:
    """Fetch today's recommendations for the universe and persist new ones.
    Symbols are processed in parallel (10 workers) — each source already
    fails soft, so worker crashes don't break the rest.
    Returns the number of newly inserted recommendation rows."""
    entry_date = date.today().isoformat()
    universe = settings.universe("us") + settings.universe("in")
    sources = _build_sources(settings)
    logger.info("collect: %d sources, %d symbols", len(sources), len(universe))

    def _fetch_symbol(symbol: str) -> list:
        price = get_current_price(symbol)
        recs = []
        for name, src in sources:
            try:
                recs.extend(src.get_recommendations(symbol, entry_date=entry_date))
            except Exception as e:
                logger.warning("source %s failed for %s: %s", name, symbol, e)
        for rec in recs:
            if rec.entry_price is None:
                rec.entry_price = price
        return recs

    all_recs: list = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futures = {ex.submit(_fetch_symbol, s): s for s in universe}
        for f in as_completed(futures):
            sym = futures[f]
            try:
                all_recs.extend(f.result())
            except Exception as e:
                logger.warning("collect worker failed for %s: %s", sym, e)

    # One transaction for the whole batch — inserting row-by-row opened a
    # connection (and took the write lock) per recommendation.
    inserted = store.add_recommendations(all_recs)

    logger.info("collect: %d new recommendations across %d symbols",
                inserted, len(universe))
    return inserted


def validate(store: RecommendationStore, settings: Settings) -> int:
    """Re-check every pending recommendation against the current price.
    Returns the count whose target is now hit."""
    horizon = settings.outcome_horizon_days
    today = date.today()
    hits = 0
    pending = store.pending_recommendations()

    # Prefetch prices for the distinct symbols in parallel — fetching them one
    # at a time serialized a network round-trip per symbol.
    price_cache: dict[str, Optional[float]] = {}
    symbols = {rec.symbol for rec in pending}
    if symbols:
        with ThreadPoolExecutor(max_workers=min(8, len(symbols))) as ex:
            futures = {ex.submit(get_current_price, s): s for s in symbols}
            for f in as_completed(futures):
                sym = futures[f]
                try:
                    price_cache[sym] = f.result()
                except Exception as e:
                    logger.warning("price prefetch failed for %s: %s", sym, e)
                    price_cache[sym] = None

    for rec in pending:
        price = price_cache.get(rec.symbol)
        if price is None:
            continue
        outcome = evaluate_outcome(rec, price, horizon_days=horizon, today=today)
        store.upsert_outcome(outcome)
        if outcome.status == "hit":
            hits += 1

    logger.info("validate: %d targets currently hit", hits)
    return hits


def refresh_profiles(store: RecommendationStore, settings: Settings) -> int:
    """Fetch + store company name and 1/3/6/12-month returns for the universe.
    Runs in parallel with 8 workers — yfinance calls are I/O-bound."""
    universe = settings.universe("us") + settings.universe("in")

    def _refresh(symbol: str) -> bool:
        prof = fetch_profile(symbol)
        if not prof:
            return False
        own = ownership_summary(fetch_ownership(symbol))
        store.upsert_profile(symbol, prof.get("company_name"),
                             prof.get("returns", {}), ownership=own)
        return True

    updated = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_refresh, s): s for s in universe}
        for f in as_completed(futures):
            try:
                if f.result():
                    updated += 1
            except Exception as e:
                logger.warning("profile refresh failed for %s: %s", futures[f], e)

    logger.info("profiles: refreshed %d symbols", updated)
    return updated


def run_daily(
    store: Optional[RecommendationStore] = None,
    settings: Optional[Settings] = None,
) -> dict:
    """Full daily run. Returns the digest dict that was sent to the notifier.
    Uses a DB lock so multiple processes/workers each running the scheduler
    don't duplicate work — only the first caller on a given day proceeds."""
    settings = settings or get_settings()
    store = store or RecommendationStore(settings.recommendations_db_path)
    today = date.today().isoformat()

    if not store.claim_daily_job(today):
        logger.info("Daily job already ran today (%s) — skipping.", today)
        return {"date": today, "skipped": True}

    inserted = collect(store, settings)
    hits = validate(store, settings)
    refresh_profiles(store, settings)

    feed = build_feed(store, days=1)
    top = feed.stocks[:10]
    digest = {
        "date": date.today().isoformat(),
        "new_recommendations": inserted,
        "targets_hit": hits,
        "top_stocks": [s.model_dump() for s in top],
    }

    try:
        get_notifier(settings).send(digest)
    except NotImplementedError as e:
        logger.warning("Notifier not wired up: %s", e)
    except Exception as e:  # delivery must not fail the whole run
        logger.error("Notifier error: %s", e)

    return digest
