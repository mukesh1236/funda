"""Shared data models — dataclasses for internal use, Pydantic for the API."""
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from pydantic import BaseModel, field_validator

# Tickers: 1–20 chars, letters/digits/dots/hyphens (e.g. BRK.B, BF-B)
_SYMBOL_RE = re.compile(r"^[A-Z0-9.\-]{1,20}$")

# Recommendations are normalised onto three buckets. Sources map their own
# vocabulary ("Strong Buy", "outperform", "overweight" …) onto these.
RECOMMENDATION_ACTIONS = ("buy", "hold", "sell")

# Outcome lifecycle for a recommendation's target price:
#   "pending" — within horizon, target not yet reached
#   "hit"     — price reached the target in the recommended direction
#   "missed"  — horizon elapsed without hitting a directional target
#   "expired" — past horizon with no target price to evaluate against
OUTCOME_STATUSES = ("pending", "hit", "missed", "expired")


def normalize_symbol(symbol: str) -> str:
    """Uppercase + validate a ticker. Raises ValueError if malformed."""
    s = symbol.strip().upper()
    if not _SYMBOL_RE.match(s):
        raise ValueError(
            "symbol must be 1–20 chars (letters, digits, '.', '-')"
        )
    return s


# ── Dataclasses (internal computation) ────────────────────────────────────────

@dataclass
class AnalystRecommendation:
    """A single dated analyst call on one stock, from one source."""
    symbol: str
    source: str                          # "finnhub" | "morningstar"
    action: str                          # one of RECOMMENDATION_ACTIONS
    entry_date: str                      # "YYYY-MM-DD" — when recorded
    entry_price: Optional[float] = None  # stock price snapshot at entry
    target_price: Optional[float] = None
    firm: Optional[str] = None           # research firm / publisher
    analyst: Optional[str] = None        # named analyst, if known
    # How many analysts this row represents. Finnhub gives aggregate counts per
    # action bucket (e.g. 25 buys), so one row can stand for many analysts.
    # Single named calls (e.g. Morningstar) use count=1.
    count: int = 1
    note: Optional[str] = None           # rationale/context (e.g. grade change)
    url: Optional[str] = None            # link to source, if any
    raw: Dict = field(default_factory=dict)
    rec_id: Optional[int] = None         # set once persisted


@dataclass
class RecommendationOutcome:
    """Result of validating a recommendation's target vs. current price."""
    rec_id: Optional[int]
    symbol: str
    current_price: float
    target_price: Optional[float]
    pct_to_target: Optional[float]       # signed % distance to target
    status: str                          # one of OUTCOME_STATUSES
    days_held: int
    last_checked: str                    # "YYYY-MM-DD"


# ── Pydantic API models ───────────────────────────────────────────────────────

class RecommendationOut(BaseModel):
    rec_id: int
    symbol: str
    source: str
    action: str
    count: int = 1
    firm: Optional[str] = None
    analyst: Optional[str] = None
    note: Optional[str] = None
    url: Optional[str] = None
    target_price: Optional[float] = None
    entry_price: Optional[float] = None
    entry_date: str


class OutcomeOut(BaseModel):
    symbol: str
    current_price: Optional[float] = None
    target_price: Optional[float] = None
    pct_to_target: Optional[float] = None
    status: str
    days_held: int = 0
    last_checked: Optional[str] = None


class Returns(BaseModel):
    one_month: Optional[float] = None
    three_month: Optional[float] = None
    six_month: Optional[float] = None
    twelve_month: Optional[float] = None


class Confidence(BaseModel):
    """Estimated likelihood the recommendation's target is reached. A heuristic,
    not a guarantee — see analytics.estimate_confidence."""
    score: float                 # 0-100
    label: str                   # "Low" | "Medium" | "High"
    rationale: str
    components: Dict[str, float] = {}


class OwnershipSummary(BaseModel):
    """Compact ownership snapshot shown in the feed."""
    inst_pct: Optional[float] = None        # % of company held by institutions
    fund_holders: Optional[int] = None      # # of fund/ETF holders (top list)
    top_buyer: Optional[str] = None         # holder that most recently added
    top_buyer_change: Optional[float] = None  # their position change %


class Holder(BaseModel):
    holder: str
    pct_held: Optional[float] = None         # % of the COMPANY's shares it owns
    change_pct: Optional[float] = None       # change in that position (13F)
    date: Optional[str] = None               # report date
    kind: str = "institution"               # "institution" | "fund"


class Ownership(BaseModel):
    """Full ownership detail shown when a stock is opened. NOTE: pct_held is the
    share of the COMPANY each holder owns — not the stock's weight inside that
    fund (that needs full fund portfolios, which aren't available for free)."""
    inst_pct: Optional[float] = None
    insider_pct: Optional[float] = None
    fund_holders: Optional[int] = None
    institutions: List[Holder] = []
    funds: List[Holder] = []
    recent_buyers: List[Holder] = []         # holders that increased their stake


class Fundamentals(BaseModel):
    """Core valuation/profitability/leverage fundamentals used to judge a buy
    decision, plus plain-English notes on what each number implies."""
    pe_ratio: Optional[float] = None
    forward_pe: Optional[float] = None
    peg_ratio: Optional[float] = None
    eps: Optional[float] = None
    market_cap: Optional[float] = None
    revenue_growth: Optional[float] = None   # % YoY
    profit_margin: Optional[float] = None    # %
    roe: Optional[float] = None              # % return on equity
    debt_to_equity: Optional[float] = None
    dividend_yield: Optional[float] = None   # %
    beta: Optional[float] = None
    price_to_book: Optional[float] = None
    week52_low: Optional[float] = None
    week52_high: Optional[float] = None
    sector: Optional[str] = None
    industry: Optional[str] = None
    notes: List[str] = []                    # what each number implies


class InsiderTrade(BaseModel):
    """One SEC Form 4 insider transaction."""
    insider: str
    role: Optional[str] = None
    action: str                        # "Buy" | "Sale"
    shares: Optional[int] = None
    value: Optional[float] = None
    date: Optional[str] = None


class ConsensusOut(BaseModel):
    """Aggregate rating for one stock — the 'count the recommendations' view."""
    symbol: str
    company_name: Optional[str] = None
    buy_count: int
    hold_count: int
    sell_count: int
    total_count: int
    consensus_score: int                 # buy_count - sell_count
    weighted_score: Optional[float] = None   # hit-rate × recency weighted score
    conviction_score: Optional[float] = None # analyst agreement level 0-1
    avg_target: Optional[float] = None
    latest_entry_date: Optional[str] = None
    sources: List[str] = []
    firms: List[str] = []
    themes: List[str] = []                # thematic segments this stock is in
    returns: Optional[Returns] = None     # 1/3/6/12-month price returns
    confidence: Optional[Confidence] = None
    ownership: Optional[OwnershipSummary] = None
    outcome: Optional[OutcomeOut] = None  # filled when validation data exists


class FeedHighlights(BaseModel):
    most_buzzed: Optional[ConsensusOut] = None   # most analyst coverage
    top_buzzed: List[ConsensusOut] = []          # 5 most-buzzing stocks of the day
    top_buy: Optional[ConsensusOut] = None       # strongest buy consensus
    top_sell: Optional[ConsensusOut] = None      # strongest sell consensus


class RecommendationFeedResult(BaseModel):
    generated_at: str
    days: int
    highlights: FeedHighlights = FeedHighlights()
    stocks: List[ConsensusOut]


class NewsItem(BaseModel):
    title: str
    publisher: Optional[str] = None
    url: Optional[str] = None
    published: Optional[str] = None


class AnalystSummary(BaseModel):
    """Synthesized 'why analysts recommend this' view."""
    headline: str                  # one-line takeaway
    reasons: List[str] = []        # bullet reasons behind the consensus
    narrative: Optional[str] = None  # LLM prose, when a model is configured
    source: str = "rule"           # "rule" | "ollama"


class StockDetailResult(BaseModel):
    symbol: str
    consensus: ConsensusOut
    summary: Optional[AnalystSummary] = None
    ownership: Optional[Ownership] = None
    fundamentals: Optional[Fundamentals] = None
    recommendations: List[RecommendationOut]
    outcome: Optional[OutcomeOut] = None
    news: List[NewsItem] = []
    insider_trades: List[InsiderTrade] = []


class LeaderboardEntry(BaseModel):
    symbol: str
    consensus_score: int
    total_count: int
    hit_rate: Optional[float] = None     # fraction of resolved recs that hit
    resolved_count: int = 0


class LeaderboardResult(BaseModel):
    metric: str
    entries: List[LeaderboardEntry]


class RefreshResult(BaseModel):
    status: str
    message: str


class ThemeInfo(BaseModel):
    name: str
    ticker_count: int
    tickers: List[str]


class ThemesResult(BaseModel):
    themes: List[ThemeInfo]


# ── Watchlist ─────────────────────────────────────────────────────────────────

class WatchlistAddRequest(BaseModel):
    symbol: str
    group: str = "My Watchlist"

    @field_validator("symbol")
    @classmethod
    def _sym(cls, v: str) -> str:
        return normalize_symbol(v)

    @field_validator("group")
    @classmethod
    def _grp(cls, v: str) -> str:
        v = (v or "").strip() or "My Watchlist"
        return v[:60]


class DailyPoint(BaseModel):
    date: str
    close: float
    change_pct: Optional[float] = None   # day-over-day % change


class WatchlistItem(BaseModel):
    symbol: str
    company_name: Optional[str] = None
    group: str
    pin_date: str
    pin_price: Optional[float] = None
    current_price: Optional[float] = None
    change_since_pin_pct: Optional[float] = None
    day_change_pct: Optional[float] = None
    daily: List[DailyPoint] = []         # daily closes since the pin date


class WatchlistResult(BaseModel):
    group: Optional[str] = None
    items: List[WatchlistItem]


class WatchlistGroups(BaseModel):
    groups: List[str]


# ── Market Digest (macro/micro economics daily briefing) ──────────────────────

class MacroHeadline(BaseModel):
    title: str
    url: Optional[str] = None
    published: Optional[str] = None
    source: Optional[str] = None


class MarketDigest(BaseModel):
    """Daily macro briefing: aggregated headlines + optional LLM synthesis."""
    generated_at: str
    headline_count: int
    headlines: List[MacroHeadline]
    narrative: Optional[str] = None  # LLM prose when Ollama is configured


# ── Auth / accounts ───────────────────────────────────────────────────────────

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_MIN_PASSWORD_LEN = 8


def _validate_email(v: str) -> str:
    v = (v or "").strip().lower()
    if not _EMAIL_RE.match(v):
        raise ValueError("Enter a valid email address.")
    return v[:255]


def _validate_password(v: str) -> str:
    if not v or len(v) < _MIN_PASSWORD_LEN:
        raise ValueError(f"Password must be at least {_MIN_PASSWORD_LEN} characters.")
    return v


class RegisterRequest(BaseModel):
    email: str
    password: str
    display_name: Optional[str] = None

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return _validate_email(v)

    @field_validator("password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return _validate_password(v)

    @field_validator("display_name")
    @classmethod
    def _name(cls, v: Optional[str]) -> Optional[str]:
        v = (v or "").strip()
        return v[:60] or None


class LoginRequest(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return (v or "").strip().lower()[:255]


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return _validate_password(v)


class UserOut(BaseModel):
    id: int
    email: str
    display_name: Optional[str] = None
    role: str = "user"   # "user" | "beta" | "admin"


class ForgotPasswordRequest(BaseModel):
    email: str

    @field_validator("email")
    @classmethod
    def _email(cls, v: str) -> str:
        return (v or "").strip().lower()[:255]


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def _pw(cls, v: str) -> str:
        return _validate_password(v)


class SetRoleRequest(BaseModel):
    role: str

    @field_validator("role")
    @classmethod
    def _role(cls, v: str) -> str:
        if v not in ("user", "beta", "admin"):
            raise ValueError("role must be user, beta, or admin")
        return v
