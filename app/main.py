"""FastAPI app: serves the recommendation API + the static dashboard, and runs
the in-process daily scheduler.

Start with:
    uvicorn app.main:app --reload --port 8100
"""
import logging
import os
import secrets
import sys
from contextlib import asynccontextmanager
from datetime import date
from typing import Optional

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# UTF-8 stdout on Windows so logging never hits charmap errors.
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from app.config import get_settings
from app.jobs import run_daily
from app.models import (
    ChangePasswordRequest,
    ChatRequest,
    ChatResponse,
    ForgotPasswordRequest,
    LeaderboardResult,
    LoginRequest,
    MarketDigest,
    RecommendationFeedResult,
    RefreshResult,
    RegisterRequest,
    ResetPasswordRequest,
    SetRoleRequest,
    StockDetailResult,
    ThemesResult,
    UserOut,
    WatchlistAddRequest,
    WatchlistGroups,
    WatchlistResult,
    normalize_symbol,
)
from app.service import (
    build_detail,
    build_feed,
    build_leaderboard,
    build_market_digest,
    build_themes,
    build_watchlist,
)
from app.auth import (
    clear_session_cookie,
    get_current_user,
    hash_password,
    require_admin,
    set_session_cookie,
    verify_password,
)
from app.store import RecommendationStore

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

settings = get_settings()

if settings.sentry_dsn:
    import sentry_sdk
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        traces_sample_rate=0.1,
        profiles_sample_rate=0.1,
        send_default_pii=False,
    )
    logger.info("Sentry enabled.")

store = RecommendationStore(settings.recommendations_db_path)
_scheduler = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scheduler
    # Promote the admin seed email on every startup — idempotent.
    if settings.admin_email:
        store.ensure_admin(settings.admin_email)
        logger.info("Admin email ensured: %s", settings.admin_email)
    if settings.enable_scheduler:
        try:
            from apscheduler.schedulers.background import BackgroundScheduler
            from apscheduler.triggers.cron import CronTrigger

            _scheduler = BackgroundScheduler(daemon=True)
            _scheduler.add_job(
                lambda: run_daily(store, settings),
                CronTrigger(hour=settings.daily_job_hour, minute=settings.daily_job_minute),
                id="daily_recommendations",
                replace_existing=True,
            )
            _scheduler.start()
            logger.info(
                "Scheduler started — daily run at %02d:%02d local time.",
                settings.daily_job_hour, settings.daily_job_minute,
            )
        except Exception as e:
            logger.error("Could not start scheduler: %s", e)
    yield
    if _scheduler:
        _scheduler.shutdown(wait=False)


app = FastAPI(title="Analyst Recommendation Tracker", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local personal tool
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def track_traffic(request: Request, call_next):
    """Count app page loads (and first-time visitors via a cookie) for the admin
    dashboard. Best-effort — never blocks or fails a request."""
    response = await call_next(request)
    try:
        if request.method == "GET" and request.url.path in ("/", "/index.html"):
            new_visitor = "visitor" not in request.cookies
            store.bump_metric(date.today().isoformat(), new_visitor)
            if new_visitor:
                response.set_cookie(
                    "visitor", secrets.token_hex(8),
                    max_age=365 * 24 * 3600, httponly=True, samesite="lax", path="/",
                )
    except Exception as e:
        logger.debug("traffic metric skipped: %s", e)
    return response


# ── API ───────────────────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    sources = []
    if settings.yahoo_enabled:
        sources.append("yahoo")
    if settings.finnhub_api_key:
        sources.append("finnhub")
    if settings.tipranks_enabled:
        sources.append("tipranks")
    if settings.fmp_api_key:
        sources.append("fmp")
    if settings.morningstar_scrape_enabled:
        sources.append("morningstar")
    if settings.polygon_api_key:
        sources.append("polygon")
    return {
        "status": "ok",
        "db": settings.recommendations_db_path,
        "universe_size": len(settings.universe("us")),
        "universe_size_in": len(settings.universe("in")),
        "sources": sources,
        "scheduler": settings.enable_scheduler,
        "last_updated": store.last_daily_run(),
        "daily_run_time": f"{settings.daily_job_hour:02d}:{settings.daily_job_minute:02d}",
    }


# ── Auth ──────────────────────────────────────────────────────────────────────
def _user_out(user: dict) -> UserOut:
    return UserOut(id=user["id"], email=user["email"],
                   display_name=user.get("display_name"),
                   role=user.get("role", "user"))


@app.post("/api/auth/register", response_model=UserOut)
def register(req: RegisterRequest, response: Response):
    try:
        uid = store.create_user(req.email, hash_password(req.password), req.display_name)
    except ValueError as e:
        raise HTTPException(409, detail=str(e))
    set_session_cookie(response, uid, settings)
    return _user_out(store.get_user_by_id(uid))


@app.post("/api/auth/login", response_model=UserOut)
def login(req: LoginRequest, response: Response):
    user = store.get_user_by_email(req.email)
    if not user or not verify_password(req.password, user["password_hash"]):
        raise HTTPException(401, detail="Incorrect email or password.")
    set_session_cookie(response, user["id"], settings)
    return _user_out(user)


@app.post("/api/auth/logout")
def logout(response: Response):
    clear_session_cookie(response)
    return {"status": "ok"}


@app.get("/api/auth/me", response_model=UserOut)
def me(user: dict = Depends(get_current_user)):
    return _user_out(user)


@app.post("/api/auth/password")
def change_password(
    req: ChangePasswordRequest, user: dict = Depends(get_current_user)
):
    if not verify_password(req.old_password, user["password_hash"]):
        raise HTTPException(401, detail="Current password is incorrect.")
    store.set_password_hash(user["id"], hash_password(req.new_password))
    return {"status": "ok"}


@app.post("/api/auth/forgot-password")
def forgot_password(req: ForgotPasswordRequest):
    """Send a password reset link. Always returns 200 to avoid user enumeration."""
    from app.notifications.email import send_reset_email
    user = store.get_user_by_email(req.email)
    if user:
        token = store.create_reset_token(user["id"])
        link = f"{settings.app_base_url}/reset-password?token={token}"
        send_reset_email(req.email, link, settings)
    return {"status": "ok", "detail": "If that email is registered, a reset link was sent."}


@app.post("/api/auth/reset-password")
def reset_password(req: ResetPasswordRequest):
    """Consume a reset token and update the password."""
    uid = store.consume_reset_token(req.token)
    if uid is None:
        raise HTTPException(400, detail="Reset link is invalid or has expired.")
    store.set_password_hash(uid, hash_password(req.new_password))
    return {"status": "ok"}


# ── Admin ─────────────────────────────────────────────────────────────────────
@app.get("/api/admin/users", response_model=list[UserOut])
def admin_list_users(_: dict = Depends(require_admin)):
    """List all users — admin only."""
    return [_user_out(u) for u in store.list_users()]


@app.patch("/api/admin/users/{uid}/role")
def admin_set_role(uid: int, req: SetRoleRequest, _: dict = Depends(require_admin)):
    """Promote/demote a user's role — admin only."""
    if store.get_user_by_id(uid) is None:
        raise HTTPException(404, detail="User not found.")
    store.set_user_role(uid, req.role)
    return {"status": "ok", "user_id": uid, "role": req.role}


@app.get("/api/admin/stats")
def admin_stats(_: dict = Depends(require_admin)):
    """Aggregate usage metrics for the admin dashboard — admin only."""
    return store.admin_stats()


@app.get("/api/themes", response_model=ThemesResult)
def themes(market: str = Query("us", pattern="^(us|in)$")):
    """Available thematic segments for a market: US (AI, Semiconductors, ...)
    or India (IT, Banking, FMCG, ...)."""
    return build_themes(market=market)


@app.get("/api/recommendations/feed", response_model=RecommendationFeedResult)
def feed(
    days: int = Query(1, ge=1, le=90), theme: Optional[str] = None,
    market: str = Query("us", pattern="^(us|in)$"),
):
    """Recent recommendations grouped per stock with consensus counts.
    Optional ?theme=AI filters to one segment. ?market=in for NSE stocks."""
    return build_feed(store, days=days, theme=theme, market=market)


@app.get("/api/recommendations/leaderboard", response_model=LeaderboardResult)
def leaderboard(
    metric: str = Query("consensus", pattern="^(consensus|hit_rate)$"),
    limit: int = Query(25, ge=1, le=100),
    market: str = Query("us", pattern="^(us|in)$"),
):
    return build_leaderboard(store, metric=metric, limit=limit, market=market)


@app.get("/api/recommendations/{symbol}", response_model=StockDetailResult)
def detail(symbol: str):
    try:
        sym = normalize_symbol(symbol)
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    result = build_detail(store, sym, settings)
    if not result:
        raise HTTPException(404, detail=f"No recommendations tracked for {sym}.")
    return result


@app.get("/api/watchlist", response_model=WatchlistResult)
def watchlist(
    group: Optional[str] = None,
    market: str = Query("us", pattern="^(us|in)$"),
    user: dict = Depends(get_current_user),
):
    """Watchlist items for one market, with pinned price and daily variation."""
    return build_watchlist(store, user["id"], group=group, market=market)


@app.get("/api/watchlist/groups", response_model=WatchlistGroups)
def watchlist_groups(user: dict = Depends(get_current_user)):
    return WatchlistGroups(groups=store.watchlist_groups(user["id"]))


@app.post("/api/watchlist", response_model=WatchlistResult)
def watchlist_add(req: WatchlistAddRequest, user: dict = Depends(get_current_user)):
    """Pin a stock: records the analyst average target as the reference point."""
    from datetime import date as _date

    from app.analytics import compute_consensus
    from app.sources.prices import get_current_price
    from app.sources.profiles import fetch_profile

    profile = store.get_profile(req.symbol) or {}
    name = profile.get("company_name")
    if not name:  # not in the tracked universe — fetch + cache its profile
        fetched = fetch_profile(req.symbol)
        if fetched:
            name = fetched.get("company_name")
            store.upsert_profile(req.symbol, name, fetched.get("returns", {}))

    # Pin to the analysts' average price target (the consensus the user is
    # tracking). Fall back to today's market price when no target is available
    # (e.g. no analyst coverage / no price-target data).
    consensus = compute_consensus(store.list_for_symbol(req.symbol))
    pin_price = consensus.avg_target if consensus else None
    if pin_price is None:
        pin_price = get_current_price(req.symbol)

    # Reject tickers we can't resolve at all — no company name, no price, and no
    # analyst coverage means it isn't a real symbol (likely a typo).
    if name is None and pin_price is None and consensus is None:
        raise HTTPException(
            404, detail=f"'{req.symbol}' not found. Check the ticker and try again.")

    if pin_price is not None:
        pin_price = round(pin_price, 2)
    store.add_watchlist(
        user["id"], req.symbol, req.group, _date.today().isoformat(), pin_price, name,
    )
    return build_watchlist(store, user["id"], group=req.group)


@app.delete("/api/watchlist/{symbol}", response_model=WatchlistResult)
def watchlist_remove(
    symbol: str, group: str = "My Watchlist",
    user: dict = Depends(get_current_user),
):
    try:
        sym = normalize_symbol(symbol)
    except ValueError as e:
        raise HTTPException(422, detail=str(e))
    store.remove_watchlist(user["id"], sym, group)
    return build_watchlist(store, user["id"], group=group)


@app.get("/api/search")
def ticker_search(q: str = Query("", min_length=1), market: str = Query("us", pattern="^(us|in)$")):
    """Company name / partial ticker → [{symbol, name, exchange}] from Yahoo Finance."""
    from app.sources.search import search_tickers
    return {"results": search_tickers(q, market=market)}


@app.get("/api/market/digest", response_model=MarketDigest)
def market_digest(market: str = Query("us", pattern="^(us|in)$")):
    """Daily macro briefing for a market (US or India): top headlines from
    Yahoo Finance index news + that market's RSS feeds, with an optional LLM
    synthesis paragraph (requires Ollama)."""
    return build_market_digest(settings, market=market)


@app.post("/api/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    """Grounded Q&A over the live feed, leaderboard, and (optionally) one stock.
    Answers come from the configured LLM using only the current data."""
    from app.chat import answer_question

    answer, error = answer_question(
        store, settings, req.question, market=req.market, symbol=req.symbol
    )
    if error:
        raise HTTPException(503, detail=error)
    return ChatResponse(answer=answer)


@app.post("/api/recommendations/refresh", response_model=RefreshResult)
def refresh(background_tasks: BackgroundTasks):
    """Trigger a collection + validation run now, in the background."""
    background_tasks.add_task(run_daily, store, settings)
    return RefreshResult(
        status="started",
        message="Fetching latest recommendations in the background. "
                "Refresh the feed in ~30–60s.",
    )


# ── Static dashboard ──────────────────────────────────────────────────────────
# Mounted last so /api routes take precedence. Serves web/index.html at "/".
_WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")
if os.path.isdir(_WEB_DIR):
    app.mount("/", StaticFiles(directory=_WEB_DIR, html=True), name="web")
