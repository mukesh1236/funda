# AlphaFunds

AlphaFunds tracks daily US and Indian stock analyst recommendations, aggregates
a **consensus rating** per stock, and validates over time whether each
**target price was hit**. It layers on mutual-fund/ETF analytics, an LLM-powered
**Ask AI** assistant (on the website and over WhatsApp), user accounts, and an
internal SRE/observability dashboard — all served by one FastAPI app with a
vanilla JS/HTML/CSS frontend and a SQLite store.

Standalone project — no dependency on other apps.

## Contents

- [Features](#features)
- [How it works](#how-it-works)
- [Project layout](#project-layout)
- [Setup](#setup)
- [Run](#run)
- [Configuration (.env)](#configuration-env)
- [API surface](#api-surface)
- [Tests](#tests)
- [Deploy](#deploy)

## Features

### Analyst consensus & target tracking
- **Consensus feed**: every tracked stock by company name + ticker, with
  buy/hold/sell counts, a consensus score, average price target, **1M / 3M /
  6M / 12M price returns**, a **target-hit confidence** badge (Low/Medium/High),
  and hit/miss status. Confidence blends proximity to target, consensus
  strength, 3‑month momentum, and the realized hit-rate from accumulated
  history — a heuristic that sharpens as more targets resolve (`app/analytics.py`).
- **Target validation**: a buy target is "hit" once price ≥ target, a sell
  target once price ≤ target; otherwise it stays "pending" until the outcome
  horizon (default 365 days) elapses, then it's marked "missed"
  (`evaluate_outcome` in `app/analytics.py`).
- **Expand any row** for a "why analysts recommend it" summary (consensus
  stance, target upside, recent raises/cuts, segments, recurring news themes),
  the individual analysts behind it (firm, grade change, target move — e.g.
  "UBS: Buy, PT $275 → $280"), and recent news headlines. `SUMMARY_PROVIDER`
  can swap the deterministic rule summary for an LLM-written narrative.
- **Leaderboard**: rank tracked stocks by consensus score or realized
  target hit-rate.
- **Thematic segments**: browse by theme — AI, Semiconductors, Finance, Green
  Energy, Data Center, EV, Cloud & Software (US) and IT, Banking, FMCG, Auto,
  Pharma, Energy, Infra & Capital Goods, Telecom (India, NSE `.NS` tickers) —
  defined in `app/themes.py`.
- **Highlights** strip: buzzing stocks (widest analyst coverage), strongest
  buy consensus, strongest sell consensus, recomputed per selected segment.
- **Big investors / funds** column: institutional ownership %, number of
  fund/ETF holders, and the institution that most recently increased its
  stake (13F data via yfinance); opening a stock shows recent buyers and top
  holders.
- **Watchlist**: pin any ticker (tracked or not) to a named group; the tab
  shows pinned price, current price, % change since pin, today's move, and a
  daily-variation sparkline.
- **Multi-market**: US and India (NSE) run as parallel universes end-to-end —
  store, analytics, ownership, watchlist all auto-detect market from the
  ticker's `.NS` suffix.

### Mutual funds & ETFs
- **Fund detail & comparison** (`app/funds.py`): metrics and side-by-side
  holdings comparison for any two funds/ETFs.
- **Return drivers (Pareto 80/20)**: pulls a fund's *complete* holdings from
  SEC EDGAR N‑PORT filings (`app/sources/nport.py`), resolves CUSIP → ticker
  via OpenFIGI, and computes which holdings drove 80% of the 3M/6M/1Y return
  (`app/fund_analytics.py::pareto_drivers`). Falls back to yfinance's top-10
  holdings with an honest coverage label when N‑PORT data isn't available.
  Large funds compute in the background (`status: "computing"` while the UI
  polls) and cache the result for 24h.
- **Fund RAG**: fund docs/holdings are chunked, embedded
  (`sentence-transformers`), and indexed with FAISS (`app/fund_rag.py`) so
  Ask AI can answer fund-specific questions grounded in the fund's own data.

### Ask AI (chat)
- **Shared brain** (`app/chat.py::answer_question` / `answer_question_stream`)
  powers both the website chat widget and the WhatsApp bot — one place to fix
  bugs or improve answers for every surface.
- **LLM-first, rule-engine fallback**: tries the configured LLM provider first
  for grounded, reasoned answers; a deterministic rule-based responder
  (feed/leaderboard/symbol lookups) kicks in if the LLM is unavailable, over
  budget, or the question doesn't need one. `ChatResponse.source` tells the UI
  which path answered.
- **Providers** (`app/llm.py`): OpenRouter (free open-source models, default
  provider for `auto`), Google Gemini, xAI Grok, local Ollama — configurable
  via `SUMMARY_PROVIDER`, each degrading gracefully to the next if unreachable
  or unconfigured.
- **Live web grounding**: causal questions ("why is X falling today?") that
  the tracked dataset can't answer are grounded with a live Tavily search
  (`app/sources/tavily.py`) before going to the LLM.
- **Guardrails**: refuses to give personalized investment advice (detects and
  redirects "should I buy X" style questions), stays in-scope to the tracked
  data + fund knowledge, and streams responses (`/api/chat/stream`) for a
  responsive UI.

### WhatsApp assistant
- **Twilio WhatsApp sandbox integration** (`app/whatsapp/`): a one-tap
  "Connect WhatsApp" flow mints a 6‑digit linking code
  (`app/whatsapp/linking.py`) tied to the logged-in account; the user sends it
  from WhatsApp to bind their phone number.
- **Inbound webhook** (`app/whatsapp/webhook.py`) is signature-verified
  (Twilio HMAC-SHA1), rate-limited per phone (abuse guard), and routes:
  linking code → bind account; `STOP`/`UNSUBSCRIBE` → opt out (compliance);
  otherwise → the same `answer_question()` brain as the website, replied over
  TwiML with a disclaimer footer.
- Ask things like *"top buys today"*, *"how's NVDA?"*, or *"which analysts
  are most accurate?"* straight from WhatsApp — no app needed.

### Accounts & access control
- **Auth** (`app/auth.py`): email/password registration and login, bcrypt
  password hashing, signed session cookies (`itsdangerous`), forgot/reset
  password flow (emails the reset link via SMTP, or logs it to console if
  SMTP isn't configured).
- **Roles**: `require_beta` / `require_admin` dependencies gate beta features
  and the admin/SRE views; the account matching `ADMIN_EMAIL` is auto-promoted
  to admin on first startup.

### SRE / AI observability (admin)
- **Request metrics**: an in-memory ring buffer tracks p50/p95 latency and
  hourly volume per endpoint (`app/reqmetrics.py`).
- **AI usage tracking**: token counts, latency, provider, and fallback rate
  per LLM call, surfaced as a burn-rate gauge against `AI_DAILY_CALL_BUDGET` /
  `AI_DAILY_TOKEN_BUDGET`.
- **Threshold alerts** (`app/alerts.py`): data-freshness checks (was the daily
  job run on schedule?), AI budget thresholds, and other SLOs fire to a
  Slack-compatible webhook with a cooldown to avoid spam; alerts also surface
  in the admin dashboard.
- **Sentry** integration (optional): error tracking + performance tracing via
  `SENTRY_DSN`; `/api/admin/sentry-test` sends a synthetic error to verify
  wiring.

## How it works

```
sources (Yahoo · Finnhub · Morningstar · TipRanks · FMP · Polygon · SEC N-PORT)
   │  daily job (scripts/run_daily.py or in-process APScheduler)
   ▼
SQLite (data/recommendations.db)  ──►  consensus + outcome validation (yfinance prices)
   │                                        │
   │                                        ▼
   │                              fund holdings + FAISS RAG index
   ▼
FastAPI (app/main.py)
   ├──► /api/recommendations/... , /api/funds/... , /api/watchlist/... → static dashboard (web/)
   ├──► /api/chat (+ /stream)  ──►  app/chat.py (LLM-first, rule fallback, web-grounded)
   ├──► /api/whatsapp/webhook  ──►  Twilio WhatsApp sandbox (same chat brain)
   ├──► /api/auth/...          ──►  accounts, sessions, password reset
   └──► /api/admin/...         ──►  SRE metrics, AI usage, alerts (admin-only)
```

- **Consensus** = `buy_count − sell_count`, recency- and source-reliability-weighted
  (`app/analytics.py::compute_consensus`).
- Every data source degrades gracefully — if one is blocked or missing a key,
  the others still run. Yahoo (via `yfinance`) needs no key and works out of
  the box.

## Project layout

```
app/
  main.py            FastAPI app, routes, scheduler wiring, middleware
  config.py           Settings (.env-driven)
  store.py             SQLite persistence layer
  service.py            Aggregation/build layer behind the API (feed, detail, digest)
  analytics.py           Consensus scoring, outcome evaluation, confidence
  themes.py                US + India thematic ticker universes
  models.py                 Pydantic request/response models
  chat.py             Ask AI brain (LLM-first + rule fallback), shared by web + WhatsApp
  llm.py                Provider clients: OpenRouter, Gemini, Grok, Ollama
  summarize.py            "Why analysts recommend it" narrative generation
  funds.py            Fund/ETF endpoints: compare, detail, drivers, RAG reindex
  fund_analytics.py     Pareto 80/20 return-driver math
  fund_rag.py              Chunk/embed/FAISS-index fund docs for grounded Q&A
  auth.py              Sessions, password hashing, RBAC dependencies
  whatsapp/            Twilio client, account-linking, inbound webhook
  notifications/       Pluggable notifiers: console, email, whatsapp
  sources/             Per-provider data source clients (Yahoo, Finnhub, FMP,
                        Morningstar, TipRanks, Polygon, SEC N-PORT/insider,
                        Tavily search, market news, fundamentals, profiles)
  alerts.py            Threshold alerting (freshness, AI budget) + Slack webhook
  reqmetrics.py        Request latency/volume ring buffer for the SRE view
  jobs.py              Daily collection + validation job (APScheduler)
web/                  Static dashboard (vanilla JS/HTML/CSS)
scripts/run_daily.py  One-shot CLI to run the daily job
tests/                pytest suite (~25 files; see Tests)
docs/                 Design notes and session context from past work
```

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows (source .venv/bin/activate on macOS/Linux)
pip install -r requirements.txt
copy .env.example .env            # then add FINNHUB_API_KEY (free at finnhub.io)
```

## Run

```bash
# Collect + validate once, print a digest:
python -m scripts.run_daily

# Start the API + dashboard (also runs the daily scheduler):
uvicorn app.main:app --reload --port 8100
#   Dashboard:  http://localhost:8100/
#   API docs:   http://localhost:8100/docs
```

Click **Refresh now** on the dashboard to fetch on demand.

## Configuration (.env)

All settings live in `app/config.py` / `.env.example`. The essentials:

| Key | Default | Meaning |
|-----|---------|---------|
| `YAHOO_ENABLED` | true | Free Yahoo analyst data (no key) |
| `FINNHUB_API_KEY` | — | Free Finnhub key (optional second source) |
| `TIPRANKS_ENABLED` | true | Best-effort TipRanks scrape (often 403) |
| `FMP_API_KEY` | — | Free FMP key for named-firm grades (optional) |
| `POLYGON_API_KEY` | — | Licensed analyst data, free Starter tier (optional) |
| `TRACKED_UNIVERSE` / `TRACKED_UNIVERSE_IN` | union of all themes | Comma-separated tickers to track, per market (overrides themes) |
| `MORNINGSTAR_SCRAPE_ENABLED` | true | Best-effort star-rating enrichment |
| `OUTCOME_HORIZON_DAYS` | 365 | Days a target has to be hit before "missed" |
| `SUMMARY_PROVIDER` | rule | `rule` \| `openrouter` \| `gemini` \| `grok` \| `ollama` \| `auto` — chat + narrative LLM |
| `OPENROUTER_API_KEY` / `OPENROUTER_MODEL` | — | Free key at openrouter.ai; `:free` models cost nothing |
| `GEMINI_API_KEY` / `GROK_API_KEY` | — | Alternate free-tier LLM providers |
| `OLLAMA_BASE_URL` / `OLLAMA_MODEL` | localhost:11434 | Local LLM fallback |
| `TAVILY_API_KEY` | — | Live web search to ground "why is X moving" questions |
| `OPENFIGI_API_KEY` | — | Raises CUSIP→ticker resolution rate limits for fund drivers |
| `EDGAR_USER_AGENT` | — | Required identify-your-app header for SEC N‑PORT fetches |
| `NOTIFIER` | console | `console` \| `email` \| `whatsapp` |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` / `TWILIO_WHATSAPP_FROM` / `WHATSAPP_SANDBOX_JOIN` | — | Twilio WhatsApp sandbox credentials |
| `SESSION_SECRET` | random per-process | Set in production so logins survive restarts |
| `ADMIN_EMAIL` | — | This account is auto-promoted to admin on first startup |
| `SMTP_HOST`/`_PORT`/`_USER`/`_PASSWORD`/`_FROM_EMAIL` | — | Email for password resets; unset → reset link logged to console |
| `SENTRY_DSN` | — | Error tracking + tracing; empty disables Sentry |
| `AI_DAILY_CALL_BUDGET` / `AI_DAILY_TOKEN_BUDGET` | 50 / 0 | LLM usage budgets for the SRE burn gauge |
| `ALERT_WEBHOOK_URL` / `ALERT_COOLDOWN_HOURS` | — / 6 | Slack-compatible webhook for threshold alerts |
| `FRESHNESS_GRACE_HOURS` | 2 | Grace period after the daily job before a "job missed" alert |
| `DAILY_JOB_HOUR` / `DAILY_JOB_MINUTE` | 8 / 0 | When the in-process scheduler runs |
| `ENABLE_SCHEDULER` | true | Run the daily job inside the API process |

See `.env.example` for the full, commented list.

## API surface

Full interactive docs at `/docs` (Swagger UI) once the server is running.
Highlights:

| Area | Endpoints |
|------|-----------|
| Recommendations | `GET /api/recommendations/feed`, `/leaderboard`, `/{symbol}`, `POST /refresh` |
| Themes / search | `GET /api/themes`, `GET /api/search`, `GET /api/stocks/{symbol}` |
| Watchlist | `GET/POST /api/watchlist`, `GET /api/watchlist/groups`, `DELETE /api/watchlist/{symbol}` |
| Funds | `app/funds.py` router — compare, detail, return drivers, RAG reindex |
| Market | `GET /api/market/digest` |
| Chat | `POST /api/chat`, `POST /api/chat/stream` |
| WhatsApp | `POST /api/whatsapp/link-code` (auth), `POST /api/whatsapp/webhook` (Twilio) |
| Auth | `POST /api/auth/register`, `/login`, `/logout`, `GET /me`, `POST /password`, `/forgot-password`, `/reset-password` |
| Admin | `GET /api/admin/users`, `/stats`, `/ai-stats`, `/sre-metrics`, `POST /sentry-test` |
| Health | `GET /api/health` |

## Tests

```bash
pytest tests/
```

~25 test modules covering consensus math, outcome boundaries
(hit/miss/pending/expired), confidence scoring, store dedupe/queries, every
data source's mapping and graceful degradation, fund drivers/RAG, chat
(rule + streaming), auth, WhatsApp linking/webhook, SRE metrics, and India
market handling.

## Deploy

Merging to `main` triggers a Railway redeploy (repo is connected to
Railway). Remember to bump the `?v=` asset version in `web/index.html`
whenever `web/app.js` or `web/styles.css` changes, so browsers don't serve
stale cached assets.
