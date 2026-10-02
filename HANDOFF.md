# AlphaFunds — Engineering Handoff

*Written 30 September 2026. Reorganised around the `docs/` knowledge base on 2 October 2026.*

This is the single entry point for anyone taking over this codebase — human or
AI. Read this first, then `CLAUDE.md` (the working agreement that applies to
every change) and `BACKLOG.md` (what is known-broken and what is planned).

**Detail lives in [`docs/`](docs/README.md).** This document is the narrative and the
overview; for the rules of a specific feature, the schema, the API, decision records,
runbooks or the security policy, go to the matching folder there. Where this document and
`docs/` disagree, `docs/` is the one that is kept current.

**What the product is.** AlphaFunds tracks daily US and Indian analyst stock
recommendations, records them append-only, and later scores whether the calls
actually hit their targets. Around that core it adds fund tooling: SEC-filing
fact sheets, a Portfolio X-Ray, return-driver attribution, and a grounded
"Ask AI" assistant reachable from both the website and WhatsApp.

**Live:** <https://alphafunds.up.railway.app>

---

## 1. Architecture

### Overall shape

A **single stateful node**. One FastAPI process serves the API *and* the static
frontend, owns a SQLite database and FAISS indexes on one mounted volume, and
runs its own scheduler in-process via APScheduler.

That is a deliberate choice, not an accident — see §6. It is what makes the app
cheap and simple to reason about, and it is exactly what rules out
scale-to-zero/serverless hosting without a rewrite.

```
Browser ─┐
         ├─► FastAPI (uvicorn, 1 worker) ─┬─► SQLite  (/data/recommendations.db)
WhatsApp ┘        │                       ├─► FAISS   (/data/fund_index)
  (Twilio)        │                       └─► ONNX embedding weights (/data/fastembed)
                  │
                  ├─► APScheduler (in-process): daily collection, cache warm, alerts
                  └─► External: yfinance, Finnhub, FMP, SEC EDGAR, Tavily, LLM providers
```

### Components

| Module | Responsibility |
|---|---|
| `app/main.py` | FastAPI app, 28 routes, lifespan + scheduler wiring, static file serving |
| `app/store.py` | All persistence. Raw `sqlite3`, no ORM. 18 tables, hand-rolled `_migrate()` |
| `app/service.py` | Read-side: builds the feed, per-symbol detail, leaderboard, stock overview |
| `app/funds.py` | `/api/funds` router — portfolio, compare, drivers, fact sheets, X-Ray |
| `app/chat.py` | The Ask AI brain. Shared by website chat and the WhatsApp bot |
| `app/llm.py` | Multi-provider LLM with fallback + per-call telemetry |
| `app/fund_rag.py` | FAISS retrieval over real SEC filings (ONNX embeddings) |
| `app/factsheet.py` | Fact-sheet generation with enforced numeric provenance |
| `app/portfolio.py` | Portfolio X-Ray maths — pure, network-free, exactly testable |
| `app/jobs.py` | Daily collection, outcome validation, WhatsApp briefs |
| `app/docs/` | SEC EDGAR document fetching and Form N-1A section parsing |
| `app/sources/` | 15 modules — external data adapters plus `_mapping.py`, each adapter with its own bounded cache |
| `app/whatsapp/` | Twilio webhook, HMAC signature verification, account linking |
| `app/notifications/` | Pluggable notifier (console / email / WhatsApp) |
| `web/` | Vanilla JS/HTML/CSS. No build step, no framework, three files |

### APIs

**42 routes.** 28 directly on `app/main.py`, 14 across two routers:
`/api/funds` (`app/funds.py`) and `/api/whatsapp` (`app/whatsapp/webhook.py`).

Grouped:

- **Public read** — `/api/health`, `/api/search`, `/api/recommendations/feed`,
  `/api/recommendations/leaderboard`, `/api/recommendations/{symbol}`,
  `/api/stocks/{symbol}`, `/api/themes`, `/api/market/digest`
- **Auth** — register, login, logout, me, password, forgot-password,
  reset-password
- **Watchlist** (auth) — list, add, delete, groups
- **Funds** (`/api/funds`, mostly auth) — portfolio CRUD, `/compare`,
  `/{symbol}/drivers`, `/{symbol}/factsheet`, `/{symbol}/factsheet/ask`,
  `/{symbol}/factsheet/refresh`, `/portfolio/xray`, `/{symbol}/amount`
- **Chat** — `POST /api/chat`, `POST /api/chat/stream`
- **Admin** (role-gated) — `users`, `stats`, `ai-stats`, `sre-metrics`,
  `sentry-test`, role patch
- **WhatsApp** — `POST /api/whatsapp/webhook`, `POST /api/whatsapp/link-code`

Long operations return **`status: "computing"` and are polled**, rather than
holding a request open. That pattern appears in drivers, fact sheets and X-Ray;
reuse it rather than inventing another.

### Database

**SQLite**, accessed through raw `sqlite3` in `app/store.py`. 18 tables,
7 indexes:

`users`, `watchlist`, `recommendations`, `outcomes`, `profiles`,
`fund_portfolio`, `fund_holdings`, `fund_documents`, `fund_factsheets`,
`fund_factsheet_status`, `whatsapp_links`, `whatsapp_link_codes`,
`password_reset_tokens`, `security_ids`, `metrics_daily`, `llm_calls`,
`chat_answers`, `job_lock`.

**Migrations are hand-rolled.** `RecommendationStore._migrate()` inspects
`PRAGMA table_info` and issues `ALTER TABLE ... ADD COLUMN` when a column is
missing. There is no Alembic. Follow the existing pattern when adding a column;
replacing this with a real migration tool is `BACKLOG.md` P2-A.

`recommendations` is **append-only** — that history is the product's only
genuinely defensible asset (§6), so never rewrite rows in it.

### Hosting & infrastructure — Railway, *not* AWS

There are **no AWS resources**. The app runs on **Railway** from the
`Dockerfile`.

| | |
|---|---|
| Project | `loving-flexibility` |
| Service | `funda` |
| Domain | `alphafunds.up.railway.app` |
| Volume | mounted at `/data` — SQLite DB, FAISS indexes, ONNX weights |
| Plan | Hobby ($5/mo including $5 of usage) |

Billing is dominated by **resident memory** (~$10.24/GB-month). On the
Jul–Aug invoice, memory was $24.01 of $24.45 of usage; vCPU was $0.18.

AWS and Azure were evaluated in detail. Conclusion: migrating saves roughly
**$0–3/month and costs 1–2 days plus ongoing ops**. The cheap serverless tiers
(Lambda, Container Apps scale-to-zero) require replacing SQLite with a managed
database that costs more than the entire current bill. A small VM or Lightsail
fits the architecture but is not cheaper. **Stay on Railway** unless the driver
is compliance or scale, not cost.

### External integrations

| Service | Used for | Required? |
|---|---|---|
| yfinance | prices, day changes, fund info, search | yes (free) |
| SEC EDGAR | fund filings (497K/485BPOS/N-CSR), N-PORT holdings | yes (free) |
| Finnhub / FMP / Polygon / TipRanks / Morningstar | analyst ratings & targets | optional, per key |
| Tavily | web search for chat context | optional |
| OpenRouter / Gemini / Grok / Ollama | LLM narrative + chat | optional; degrades gracefully |
| Twilio | WhatsApp bot (sandbox) | optional |
| Sentry | error reporting | optional |

Every integration is **fail-soft**: a missing key or an unreachable service
degrades the feature, it never breaks a request.

---

## 2. Repository structure

```
app/            backend (see the component table above)
  docs/         SEC EDGAR fetch + Form N-1A parsing
  sources/      15 modules — external data adapters + ticker mapping
  notifications/  console | email | whatsapp notifier
  whatsapp/     Twilio webhook, linking, client
web/            frontend — index.html, app.js, styles.css (no build step)
tests/          31 test modules + conftest.py
scripts/        run_daily.py, seed_demo.py, compare_models.py
docs/           knowledge base: design, architecture, schemas, api, security, adr, workflows
CLAUDE.md       working agreement — READ THIS
BACKLOG.md      security findings + roadmap
Dockerfile      production image
```

### Entry points

- **Web app** — `app/main.py:167`, `app = FastAPI(...)`.
  Run locally: `uvicorn app.main:app --reload`
- **Daily job** — `scripts/run_daily.py`, or automatically via the in-process
  scheduler when `ENABLE_SCHEDULER=true`
- **Demo data** — `scripts/seed_demo.py`
- **Container** — `Dockerfile` `CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}`

### Configuration

All settings live in `app/config.py` (~50 pydantic-settings fields, env-var
overridable). `.env.example` documents 38 keys. Nothing is required to boot —
the app starts with no keys at all and simply offers fewer features.

The settings that most change behaviour:

| Setting | Effect |
|---|---|
| `SUMMARY_PROVIDER` | `auto` / `openrouter` / `gemini` / `grok` / `ollama` / `rule`. Anything else disables the LLM |
| `ENABLE_SCHEDULER` | Whether background jobs run in this process |
| `AI_DAILY_CALL_BUDGET` | Default 50. Enforced in the fact-sheet path **only** — see §3 |
| `WARM_FEED_INTERVAL_MINUTES` | Default 30. `0` disables cache warming |
| `RECOMMENDATIONS_DB_PATH`, `FUND_INDEX_DIR`, `FASTEMBED_CACHE_PATH` | Must point at the mounted volume in production |

---

## 3. Business logic

### Key workflows

**Daily collection** (`app/jobs.py::run_daily`) — fetch analyst
recommendations from every configured source, write them append-only, refresh
profiles, validate outcomes of older calls, send WhatsApp briefs.
`job_lock` + `claim_daily_job()` prevent a double run if more than one process
starts.

**Outcome scoring** — a recommendation gets `OUTCOME_HORIZON_DAYS` (default 365)
for its price target to be hit; the result lands in `outcomes` as
hit / missed / pending. This accumulating record is the product's real moat.

**Fact sheets** (`app/factsheet.py`, `app/docs/`) — locate the fund's filing on
SEC EDGAR, verify it belongs to *that* fund, parse Form N-1A sections, chunk
with provenance, embed into FAISS, then generate a summary against a fixed
rubric. Runs in the background behind a polled status.

**Portfolio X-Ray** (`app/portfolio.py`) — collapse the user's funds into the
securities underneath them: overlap, blended fee, fee paid on duplicated
exposure, concentration. The maths is pure and network-free so it is exactly
testable; all I/O happens in the caller.

**Ask AI** (`app/chat.py::answer_question`) — guardrails first, then fund
context or feed context, then a single LLM call, falling back to a
deterministic rule engine and finally a data overview. The answer's `source`
is returned so the UI can show when an answer was *not* reasoned.

### Rules and assumptions that must not change

These are not style preferences. Each exists because the opposite shipped once
and caused a real problem.

1. **Never invent a number.** Every figure shown must come from data. A
   fabricated fee estimate once shipped in `fund_rag.py` and had to be deleted.
   `app/factsheet.py` enforces this in code — model output is checked against a
   FACTS block numerically — not by asking the prompt nicely.
2. **Money figures only when the user supplied amounts.** `build_xray` emits no
   currency at all unless every fund has a real amount; otherwise it
   equal-weights and says so.
3. **State partial data as partial.** When a fund resolves only to its top-10
   holdings, the reported overlap is a floor and must say so. When a fund can't
   be read at all, it is named, not silently dropped from the denominator.
4. **Degrade, never break.** No LLM, no API key, no network → a lesser answer,
   never a 500.
5. **`recommendations` is append-only.**
6. **Never give personal investment advice.** `_is_personal_advice()` declines
   before the model is ever called.
7. **UI values come from the token scales** in `web/styles.css` — see
   `CLAUDE.md` for the closed sets and the audit command.

### Edge cases worth knowing

- **N-PORT weights don't sum to 100.** Rounding, cash and excluded asset
  classes. `_normalised_holdings()` normalises per fund before combining.
- **Holdings match by ticker, else normalised name.** `funds.holding_key()` is
  shared by the two-fund compare and the N-way X-Ray so both agree.
- **Combined filings.** One document can cover several funds; `locate_fund_span`
  fails closed rather than summarising the wrong fund.
- **Partial amounts.** If only some funds have amounts, X-Ray falls back to
  equal weighting — treating unpriced funds as worthless is worse than not
  weighting.
- **`localStorage` throws** in private windows. Every access in `web/app.js` is
  wrapped; a preference lookup must never break a loading state.
- **An empty cached result is a hit, not a miss.** `_DAY_CHANGE_CACHE` stores
  `{}` when yfinance returns nothing; the read guard is `is not None`.

---

## 4. Production

### Deployment

**Merging to `main` triggers a Railway redeploy.** There is no separate deploy
step, no Railway CLI in use. Typical build is 35–90 seconds.

After deploying, check: Railway → service `funda` → Deployments for `SUCCESS`,
then hard-refresh the site (asset version is currently `?v=39`).

### CI/CD

`.github/workflows/ci.yml` — on push and PR to `main`, Python 3.12,
`pip install -r requirements.txt`, `pytest tests/ -q`. That is the whole
pipeline: no linting, no type checking, no security scanning, no deploy gate.
**CI does not block the Railway deploy** — Railway watches the branch
independently, so a red build still ships.

### Monitoring and logging

- `GET /api/health` — provider status, universe size, last LLM error
- `GET /api/admin/ai-stats` — LLM calls, tokens, latency, per-model breakdown,
  recent failures
- `GET /api/admin/sre-metrics` — request metrics, data-freshness SLO, AI budget
  burn, chat answer-source mix, alert history
- `app/alerts.py` — threshold alerts to a Slack-compatible webhook
- Sentry when `SENTRY_DSN` is set
- `llm_calls` and `chat_answers` tables hold the raw telemetry

**Railway logs everything as `severity: error`** because the app writes to
stderr. Almost all of it is `INFO`. Don't be alarmed by a wall of red.

### Known production issues

- **Memory growth: fixed, with one confirmation outstanding.** RSS used to reset on
  deploy then climb (~32 MB/day: 0.44 GB → 1.18 GB → 1.97 GB over a month). Three
  unbounded caches were bounded in `c605b9d` along with `MALLOC_ARENA_MAX=2`
  ([ADR 0010](docs/adr/0010-bounded-caches.md)). At +24h the 6h and 12h windows had
  identical floors and ceilings (0.3844–0.3994 GB), which a leak would contradict; a
  +72h confirmation was scheduled. Watch the slope, not a single reading
  ([runbook](docs/workflows/memory-investigation.md)).
- **Cost.** Was ~$24/month, almost entirely memory. At ~0.39 GB resident the implied
  figure is ~$5/month, but billing is in arrears so the invoice lags
  ([how to read it](docs/workflows/cost-check.md)).
- **Security.** `BACKLOG.md` P1 lists S1–S12, several still open, including one
  rated Critical. **Read that list before putting this in front of more users.**
  Note the repository is public.
- **A benign yfinance `TzCache` warning** repeats on every boot.
- **SEC EDGAR and huggingface are unreachable from the dev sandbox**, so live
  EDGAR fetches and the first ONNX weight download are only ever verified in
  production.

### Rollback

Railway keeps previous deployments. Roll back in the dashboard by redeploying
the last known-good deployment — fastest option, no git needed.

For a code rollback: `git revert <sha>` on `main` and push. Prefer revert over
force-push; `main` is the deploy trigger and rewriting it is how you get a
surprise deploy.

**Before rolling back a release that changed the schema**, check whether
`_migrate()` added a column. Added columns are backwards-compatible (older code
ignores them), so most rollbacks are safe — but SQLite cannot drop a column
easily, so a forward fix is usually better than a rollback.

---

## 5. Development conventions

### Coding standards

There is no formatter or linter configured. Match the file you are editing.
The house style is distinctive and worth preserving:

- **Comments explain *why*, not *what*** — especially the non-obvious constraint
  that forced the code into its current shape. Many comments record a bug that
  already happened. Don't delete them.
- Module docstrings state the module's job and its honesty constraints.
- Prefer extending an existing helper to adding a parallel one.
- All in-process caches must be **bounded** (`TTLCache`/`LRUCache`), never a
  plain dict. `tests/test_cache_bounds.py` enforces this.
- Frontend: everything through `esc()`; use `data-*` + `addEventListener`, not
  inline `onclick`.

### Testing

`pytest tests/` — **427 passed, 1 skipped** at the time of writing. 31 modules.

- Tests are isolated with `tmp_path` and `monkeypatch`; never touch the real DB.
- Network is always stubbed. Fixtures stand in for EDGAR filings and yfinance.
- The embedding model is substituted with a deterministic fake so retrieval
  logic is tested everywhere, including offline.
- Frontend has no test runner. UI changes are verified in **Chromium via
  Playwright** at 1280px and 390px — that is the established pattern, and it has
  caught real regressions the unit tests could not.

### Branch and PR process

- Develop on a feature branch; the current one is
  `claude/project-enterprise-analysis-pd53yb`.
- **Never push directly to `main` without intent** — it deploys.
- PRs are optional; several changes have been fast-forward merged after review.
- **Bump the `?v=` asset version** in `web/index.html` whenever `app.js` or
  `styles.css` changes, or browsers serve stale assets.

### Agent instructions

`CLAUDE.md` is the working agreement and is read automatically on every task in
this repo. It mandates a senior-engineer diff review before any push or merge —
correctness, regressions, reuse, security, tests — with findings stated plainly.
It also carries the UI design rules (the closed token scales and an audit
command). **If you change conventions, change `CLAUDE.md` too**, or the next
session will not know.

---

## 6. Project history

### Why the technology choices

**FastAPI + SQLite + vanilla JS, single node.** Chosen for low operational
burden and cost. The consequence is accepted deliberately: no horizontal scale,
no scale-to-zero. `BACKLOG.md` P2 describes the Postgres/Redis path when it is
actually needed. It is not needed yet.

**No ORM.** `store.py` uses raw `sqlite3` with hand-rolled migrations. Adequate
at this size; P2-A is the exit.

**Multi-provider LLM with fallback** (`app/llm.py`). Free-tier models get
renamed, retired and rate-limited constantly, so a single hard-coded model
silently kills the whole AI feature. The provider chain plus a live catalogue
check exists because that happened.

**SEC EDGAR as the fact-sheet source.** Free, authoritative, and legally
required to exist — unlike scraping AMC PDFs. Form N-1A prescribes the
headings, which is what makes parsing tractable.

**ONNX (`fastembed`) instead of `sentence-transformers`.** Same
`all-MiniLM-L6-v2` weights. Measured: the app was 834 MB resident with PyTorch
and 162 MB without, and `pip install sentence-transformers` was pulling 2.7 GB
of CUDA libraries into a GPU-less container. Memory was ~98% of the hosting
bill.

**Curated market facts, never LLM-generated.** A wrong "fun fact" on a finance
product is the same failure as a fabricated number, and generating one would add
latency to the very thing it exists to mask.

### Decisions worth preserving

- Honesty rules enforced **in code**, not in prompts (§3).
- Long work goes to a **background task with a polled status**, never a held
  request.
- The Ask AI brain is **shared** between web and WhatsApp — one place to fix.
- The UI has **closed type/space/radius scales**; adding a value is a design
  decision made in the token block.

### Known failed approaches — read this section

- **A fabricated fee figure** shipped in `fund_rag.py`: an expression that
  evaluated to $0 above a 1% expense ratio and a constant below it. Deleted.
  This is the origin of rule 1 in §3.
- **The original "RAG" retrieved self-generated text, not documents.** It
  embedded summaries the app had itself produced, so "grounded in the filing"
  was false. The entire `app/docs/` + fact-sheet effort exists because of that
  finding. Check what a retrieval layer actually indexes.
- **Hosting cost was misattributed to CPU.** The 4-minute cache warmer looked
  like the culprit from reading the code; the invoice showed vCPU at $0.18
  against $24.01 of memory. Reason from the bill, not the source.
- **A single post-deploy memory reading was mistaken for steady state.** 0.44 GB
  right after a restart became 1.97 GB a month later. Watch slopes.
- **Prompt-level provenance did not hold.** Asking the model to only use given
  numbers was insufficient; the numeric check had to move into code.
- **A mechanical CSS refactor carried two silent regex bugs** — one anchored to
  line starts, one that consumed the `;` separator so every other declaration
  was skipped — and its rounding wrapped the sidebar nav. Caught only by looking
  at the rendered page. Substitution counts are not verification.
- **The `esc()` helper does not escape `'`** (`BACKLOG.md` S11). Safe today only
  because it is used with double-quoted attributes.

### Knowledge base

`docs/` is organised by what you are trying to do: `design/` (one file per feature),
`architecture/`, `schemas/`, `api/openapi.yaml`, `security/policy.md`, `adr/` (the *why* behind
each decision, including the measurements) and `workflows/` (runbooks). The decisions recorded in
this section each have an ADR with the full reasoning. Start at [`docs/README.md`](docs/README.md).

The six earlier documents were moved: three into `design/` (RAG and monitoring, UI redesign,
bottleneck plan) with status banners, and three into `archive/` (the July session snapshot,
the strategy review, and the separate TeamOps design). They are useful for intent but **partly
superseded**; trust the code and the current `docs/` over them.

---

## 7. Current state

### Complete and in production

- Analyst recommendation tracking (US + India), append-only, with outcome scoring
- Feed, leaderboard, themes, per-stock detail, stock overview for any ticker
- Accounts, sessions, watchlists with groups
- Fund portfolio tracking, two-fund compare, Pareto return drivers
- **Fund fact sheets** from real SEC filings, with citations and enforced
  numeric provenance
- **Portfolio X-Ray** — overlap, blended fee, fee on duplicated exposure,
  concentration
- Ask AI on web + WhatsApp, with guardrails and graceful degradation
- Market facts during network waits, with a user toggle
- Admin dashboards: usage, AI telemetry, SRE metrics
- ONNX embeddings, consolidated UI design scales

### In flight

- **Memory growth.** Fixed in `c605b9d`; flat at +24h. Remaining action: the +72h
  confirmation. If the floor has risen, profile with `tracemalloc` before changing
  anything, and only then consider a scheduled restart as a hard cap.
- The `docs/` knowledge base (this reorganisation).

### Technical debt

Ordered by what would hurt first:

1. **Security** — `BACKLOG.md` P1 (S1–S12). Some still open, one Critical. The
   blocker for any real user growth.
2. **No rate limiting on `POST /api/chat`** — public, unauthenticated, calls the
   LLM on every request, and **does not check the AI budget** (only the
   fact-sheet path does). A scripted loop can exhaust the day's AI for everyone.
3. **Memory/cost**, above.
4. **SQLite + hand-rolled migrations** — P2-A.
5. **Per-process caches** — hit rate collapses with more than one worker. P2-B.
6. **In-process scheduler** — must move out before running multiple instances.
   P2-C.
7. **Unpinned dependencies** — `>=` only, no lockfile. S9.
8. **No pagination** on feed or leaderboard. P2-D.
9. **No frontend test runner** — Playwright checks are written per-change and
   not retained as a suite.

### Next planned changes

From `BACKLOG.md` and the agreed roadmap, in order:

1. **P1 security**, especially the admin-promotion path and chat rate limiting.
2. **Public fund pages (SEO)** — fact sheets are already generated and cached;
   serving them publicly turns existing content into acquisition, with X-Ray as
   the signup gate. Gated on item 1 — do not drive traffic first.
3. **India funds via AMFI** — free daily NAV plus monthly portfolio
   disclosures; extends X-Ray to India and is the biggest TAM unlock.
4. **"What changed" alerts** — retention. `recommendations` is already
   append-only with entry dates, and the WhatsApp brief job already delivers on
   a schedule.

A fuller **agent-ops** plan (tool-calling Ask AI, agent tracing, internal ops
agents) was drafted but deliberately **not started**: it multiplies LLM calls
per question by 3–8×, which is unsafe until item 1's rate limiting and budget
enforcement are in place.

### Market position, briefly

Researched against TipRanks, Simply Wall St, Danelfin, AnaChart and
Kaleidoscope. Most obvious "differentiators" are already owned by someone —
AnaChart does analyst-accuracy tracking with over two decades of data,
Kaleidoscope does cited SEC-filing AI for free, and WhatsApp stock bots are
crowded in India. Two things here are genuinely defensible: the **accumulating
outcome history** (nobody can backfill a track record) and **the fund novice**,
especially in India, since the competitors above are all built for
stock-pickers. Aim there.
