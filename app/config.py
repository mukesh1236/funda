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

    # "Why analysts recommend" summary. "rule" = instant deterministic summary
    # (no deps). "ollama" / "auto" = also try a local Ollama model for a prose
    # narrative, falling back to the rule summary if it's unreachable.
    summary_provider: str = "rule"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "llama3.1:8b"

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
