"""Central configuration — loaded from .env (see .env.example)."""
from functools import lru_cache

from pydantic import ConfigDict
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = ConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Data sources
    finnhub_api_key: str = ""
    morningstar_scrape_enabled: bool = True

    # Yahoo Finance analyst data via yfinance — free, no key. On by default.
    yahoo_enabled: bool = True

    # TipRanks consensus — best-effort scrape of their public JSON, graceful on
    # failure (no official free API). On by default; disable if it gets blocked.
    tipranks_enabled: bool = True

    # Financial Modeling Prep — optional named-firm analyst grades. Needs a free
    # key from https://site.financialmodelingprep.com/ ; off when unset.
    fmp_api_key: str = ""

    # What to track. Empty → union of all US themes (app/themes.py).
    tracked_universe: str = ""

    # Indian (NSE) universe override. Empty → union of all INDIA_THEMES.
    tracked_universe_in: str = ""

    # Storage
    recommendations_db_path: str = "./data/recommendations.db"

    # Outcome validation horizon (days a target has to be hit)
    outcome_horizon_days: int = 365

    # "Why analysts recommend" summary provider:
    #   "rule"   = instant deterministic summary only (no deps, default)
    #   "ollama" = also try a local Ollama model for a prose narrative
    #   "gemini" = use Google Gemini's free API (set GEMINI_API_KEY)
    #   "auto"   = try Gemini if a key is set, else Ollama
    # Any LLM path falls back to the rule summary if it's unreachable.
    summary_provider: str = "rule"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"

    # Google Gemini (free tier) — get a key at https://aistudio.google.com/apikey
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash-lite"   # highest free-tier quota

    # xAI Grok (free tier) — get a key at https://console.x.ai
    grok_api_key: str = ""
    grok_model: str = "grok-3-mini"

    # OpenRouter — one key, many models, including free open-source ones.
    # Get a key at https://openrouter.ai/keys ; models ending in ":free" cost
    # nothing. Default is DeepSeek V3.1 free — the strongest free open-source
    # model for grounded reasoning at the time of writing.
    openrouter_api_key: str = ""
    openrouter_model: str = "deepseek/deepseek-chat-v3.1:free"

    # Live web search (Tavily) — grounds causal/"why is X falling" questions the
    # tracked dataset can't answer with real recent context. Free tier at
    # https://tavily.com ; leave blank to skip web search entirely.
    tavily_api_key: str = ""

    # Notifications: "console" | "whatsapp"
    notifier: str = "console"
    whatsapp_to: str = ""  # destination number when whatsapp is wired up

    # WhatsApp assistant (Phase 1 — Twilio sandbox). Get these from the Twilio
    # console; TWILIO_WHATSAPP_FROM is like "whatsapp:+14155238886" (sandbox).
    # whatsapp_sandbox_join is the sandbox opt-in phrase shown to users.
    twilio_account_sid: str = ""
    twilio_auth_token: str = ""
    twilio_whatsapp_from: str = ""
    whatsapp_sandbox_join: str = ""   # e.g. "join <two-words>"

    # Scheduler
    enable_scheduler: bool = True
    daily_job_hour: int = 8
    daily_job_minute: int = 0

    # FastAPI
    api_host: str = "0.0.0.0"
    api_port: int = 8100

    # Auth — HMAC key for signing session cookies. Empty → a random per-process
    # key is generated (sessions drop on restart); set SESSION_SECRET in .env for
    # production so logins survive restarts.
    session_secret: str = ""
    session_max_age_days: int = 30

    # Observability — Sentry error tracking + tracing. Empty → disabled.
    # Set SENTRY_DSN in .env / Railway Variables to enable.
    sentry_dsn: str = ""
    # Share of requests traced for performance monitoring (0.0–1.0). 0.1 = 10%,
    # a sane default that keeps Sentry's transaction quota from filling up.
    sentry_traces_sample_rate: float = 0.1

    # Polygon.io licensed analyst data. Free Starter plan (5 req/min).
    # Sign up at https://polygon.io — set POLYGON_API_KEY in .env to enable.
    polygon_api_key: str = ""

    # SMTP email (for password-reset links). Empty → link is logged to console.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from_email: str = ""   # defaults to smtp_user when unset

    # App public base URL (for reset-link generation).
    app_base_url: str = "http://localhost:8100"

    # Admin bootstrap — this email is promoted to role="admin" on first startup.
    admin_email: str = ""

    # ── SRE / AI observability ────────────────────────────────────────────────
    # Daily AI budgets for the burn gauge + preemptive alerts. Defaults match
    # OpenRouter's free tier without credits (~50 free-model requests/day).
    # Set 0 to disable a budget.
    ai_daily_call_budget: int = 50
    ai_daily_token_budget: int = 0

    # Where threshold alerts go: a Slack-compatible webhook URL receiving
    # {"text": "..."}. Empty → alerts are logged at WARNING only.
    alert_webhook_url: str = ""

    # Hours before the same alert may fire again (spam guard).
    alert_cooldown_hours: int = 6

    # Grace period after the scheduled daily job before "job missed" alerts.
    freshness_grace_hours: int = 2

    def universe(self, market: str = "us") -> list[str]:
        """Resolve the ticker watchlist for a market ("us" | "in"): env
        override, else the union of all thematic segments for that market
        (see app/themes.py)."""
        from app.themes import all_tickers

        raw = (self.tracked_universe_in if market == "in" else self.tracked_universe).strip()
        if raw:
            return [t.strip().upper() for t in raw.split(",") if t.strip()]
        return all_tickers(market=market)


@lru_cache()
def get_settings() -> Settings:
    return Settings()
