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
    gemini_model: str = "gemini-2.0-flash"

    # Notifications: "console" | "whatsapp"
    notifier: str = "console"
    whatsapp_to: str = ""  # destination number when whatsapp is wired up

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
