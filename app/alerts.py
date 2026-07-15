"""Threshold alerts — the dashboard informs, alerts protect.

check_alerts() runs on the scheduler every 15 minutes and evaluates:
  - AI success rate collapsed (last 24h, needs >=5 calls)
  - AI daily budget burn >= 80% (calls and/or tokens, configurable)
  - Daily collection job missed its schedule (+ grace period)
  - 5xx error rate elevated (last 24h, needs >=20 requests)

Each alert fires at most once per cooldown window (default 6h). Delivery:
Slack-compatible webhook when ALERT_WEBHOOK_URL is set; always logged at
WARNING; and kept in an in-memory history the SRE dashboard renders as its
(real) alerts feed.
"""
import logging
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Deque, Dict, List, Optional

import httpx

from app import reqmetrics
from app.config import Settings
from app.store import RecommendationStore

logger = logging.getLogger(__name__)

_last_fired: Dict[str, float] = {}
_history: Deque[dict] = deque(maxlen=100)
_lock = threading.Lock()

AI_SUCCESS_RATE_THRESHOLD = 0.90
AI_SUCCESS_MIN_CALLS = 5
BUDGET_BURN_THRESHOLD = 0.80
ERROR_RATE_5XX_THRESHOLD = 0.05
ERROR_RATE_MIN_REQUESTS = 20


def data_freshness(store: RecommendationStore, settings: Settings) -> dict:
    """Freshness SLO: the daily collection job must have run by its scheduled
    time (+ grace). Shared by the alert check and the SRE dashboard tile."""
    last_run = store.last_daily_run()   # "YYYY-MM-DD" or None
    now = datetime.now(timezone.utc)
    due = now.replace(hour=settings.daily_job_hour, minute=settings.daily_job_minute,
                       second=0, microsecond=0) + timedelta(hours=settings.freshness_grace_hours)
    ran_today = last_run == now.date().isoformat()
    breach = (not ran_today) and now >= due
    return {
        "last_run": last_run,
        "scheduled": f"{settings.daily_job_hour:02d}:{settings.daily_job_minute:02d} UTC",
        "grace_hours": settings.freshness_grace_hours,
        "ran_today": ran_today,
        "breach": breach,
    }


def ai_budget(store: RecommendationStore, settings: Settings) -> dict:
    """Today's burn against the configured daily AI budgets."""
    stats = store.llm_stats(days=1)
    calls_today, tokens_today = stats["calls_today"], stats["tokens_today"]
    call_pct = (round(calls_today / settings.ai_daily_call_budget, 3)
                if settings.ai_daily_call_budget else None)
    token_pct = (round(tokens_today / settings.ai_daily_token_budget, 3)
                 if settings.ai_daily_token_budget else None)
    return {
        "calls_today": calls_today,
        "call_budget": settings.ai_daily_call_budget or None,
        "call_burn_pct": call_pct,
        "tokens_today": tokens_today,
        "token_budget": settings.ai_daily_token_budget or None,
        "token_burn_pct": token_pct,
    }


def _notify(settings: Settings, key: str, message: str) -> None:
    logger.warning("ALERT [%s] %s", key, message)
    entry = {"ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "key": key, "message": message}
    with _lock:
        _history.appendleft(entry)
    if settings.alert_webhook_url:
        try:
            httpx.post(settings.alert_webhook_url,
                        json={"text": f"🚨 [{key}] {message}"}, timeout=10)
        except Exception as e:
            logger.warning("alert webhook delivery failed: %s", e)


def _fire(settings: Settings, key: str, message: str) -> bool:
    """Fire unless the same alert fired within the cooldown window."""
    now = time.time()
    cooldown = settings.alert_cooldown_hours * 3600
    with _lock:
        if now - _last_fired.get(key, 0) < cooldown:
            return False
        _last_fired[key] = now
    _notify(settings, key, message)
    return True


def check_alerts(store: RecommendationStore, settings: Settings) -> List[str]:
    """Evaluate all thresholds; returns the alert keys fired this run."""
    fired: List[str] = []

    try:
        stats = store.llm_stats(days=1)
        if (stats["calls"] >= AI_SUCCESS_MIN_CALLS
                and stats["success_rate"] is not None
                and stats["success_rate"] < AI_SUCCESS_RATE_THRESHOLD):
            if _fire(settings, "ai_success_rate",
                     f"AI success rate {stats['success_rate']:.0%} over the last 24h "
                     f"({stats['calls']} calls) — below {AI_SUCCESS_RATE_THRESHOLD:.0%}. "
                     f"Check /api/health llm.last_error."):
                fired.append("ai_success_rate")
    except Exception as e:
        logger.debug("ai_success_rate check skipped: %s", e)

    try:
        budget = ai_budget(store, settings)
        for kind in ("call", "token"):
            pct = budget[f"{kind}_burn_pct"]
            if pct is not None and pct >= BUDGET_BURN_THRESHOLD:
                if _fire(settings, f"ai_{kind}_budget",
                         f"AI {kind} budget {pct:.0%} burned today "
                         f"({budget[f'{kind}s_today']} of {budget[f'{kind}_budget']}). "
                         f"Answers will degrade to rule fallbacks at 100%."):
                    fired.append(f"ai_{kind}_budget")
    except Exception as e:
        logger.debug("ai_budget check skipped: %s", e)

    try:
        fresh = data_freshness(store, settings)
        if fresh["breach"]:
            if _fire(settings, "daily_job_missed",
                     f"Daily collection job has NOT run today (last run: "
                     f"{fresh['last_run'] or 'never'}; scheduled {fresh['scheduled']} "
                     f"+{fresh['grace_hours']}h grace). Feed data is stale."):
                fired.append("daily_job_missed")
    except Exception as e:
        logger.debug("freshness check skipped: %s", e)

    try:
        req = reqmetrics.summary()
        if (req["requests"] >= ERROR_RATE_MIN_REQUESTS
                and req["error_rate_5xx"] is not None
                and req["error_rate_5xx"] > ERROR_RATE_5XX_THRESHOLD):
            if _fire(settings, "error_rate_5xx",
                     f"5xx error rate {req['error_rate_5xx']:.1%} over the last 24h "
                     f"({req['requests']} requests) — above {ERROR_RATE_5XX_THRESHOLD:.0%}."):
                fired.append("error_rate_5xx")
    except Exception as e:
        logger.debug("error_rate check skipped: %s", e)

    return fired


def recent_alerts(limit: int = 20) -> List[dict]:
    with _lock:
        return list(_history)[:limit]


def reset() -> None:
    """Test helper."""
    with _lock:
        _last_fired.clear()
        _history.clear()
