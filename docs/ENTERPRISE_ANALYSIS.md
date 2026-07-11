# Funda — Enterprise Comparison & Strategic Roadmap

*An architect + business review of the Analyst Recommendation Tracker. Written 2026-07-11.*

---

## 1. What this project is today

A FastAPI monolith (~7k LOC) that:

- Ingests analyst recommendations daily from 5 free sources (Yahoo, Finnhub, Morningstar, TipRanks, FMP) with graceful per-source degradation
- Computes consensus scores and **validates over time whether price targets were actually hit** (append-only history + outcomes)
- Serves a vanilla-JS dashboard: thematic segments (US + India markets), highlights, watchlists, leaderboard, fund tracker
- Has a real AI layer: multi-provider LLM routing (Gemini / Grok / Ollama with auto-fallback), a hybrid rule-engine + LLM chat over live data, and a FAISS + sentence-transformers RAG pipeline over synthesized fund documents
- Auth (bcrypt sessions, roles, password reset), admin panel, Sentry hooks, Docker, CI, ~15 test files

This is genuinely above-average for a side project. The parts that impress a hiring manager or acquirer are **not** the CRUD — they're the outcome-validation loop, the multi-source degradation design, and the hybrid deterministic/LLM answer engine.

---

## 2. Enterprise comparison — where you stand

Scored against what a comparable enterprise fintech product (e.g. an internal tool at a broker, or TipRanks/Zacks as commercial products) would have.

| Dimension | Enterprise standard | Funda today | Gap |
|---|---|---|---|
| **Domain logic** | Consensus + backtested accuracy | Consensus + target-hit validation ✅ | Small — this is your strength |
| **Data layer** | Postgres/TimescaleDB, migrations, replicas | SQLite, hand-rolled `_migrate()` | Large |
| **Caching** | Redis, shared across workers | Per-process `TTLCache` | Large at >1 worker |
| **Jobs** | Dedicated workers (Celery/Temporal), retries, DLQ | APScheduler inside the API process (with a DB claim lock) | Medium |
| **API contract** | Versioned, paginated, rate-limited, OpenAPI-governed | Unversioned, unpaginated feed, no rate limits | Medium |
| **AuthN/Z** | OIDC/SSO, MFA, session revocation, audit log | Cookie sessions, roles, reset tokens; no revocation/verification | Medium |
| **Observability** | Structured logs, traces, metrics, SLOs, alerting | `logging.basicConfig` + optional Sentry | Medium |
| **Frontend** | React/Next, typed API client, component tests | 1,150-line hand-wired `app.js` | Medium (fine for now) |
| **AI/LLM layer** | Eval harness, prompt versioning, cost tracking, guardrails | Multi-provider routing + RAG, but no evals or observability | Medium — **highest-leverage gap for your career** |
| **Compliance** | Data licensing, disclaimers, GDPR export/delete | Scraping ToS risk (TipRanks/Morningstar/yfinance) | **Blocking for paid product** |
| **Security** | Rate limiting, secure cookies, secrets mgmt, pen tests | Tracked in BACKLOG P1, not done | Do before real users |

**Verdict:** architecture is a clean, well-factored monolith — the right choice at this stage. Do not microservice it. The enterprise gaps that actually matter for you are (a) the AI-engineering maturity items, because they build your career story, and (b) data licensing + security, because they gate any revenue.

---

## 3. What already makes it stand out — double down here

1. **The outcomes table is your moat.** Everyone shows analyst ratings; almost nobody *grades the analysts*. Your append-only history + target-hit validation compounds in value every day it runs — a competitor starting today can't backfill it. F4 in your backlog ("Analyst accuracy backtesting") is the single most important feature in this repo.
2. **Graceful multi-source degradation** is a real distributed-systems talking point for interviews.
3. **Hybrid rule-engine → LLM fallback in `chat.py`** is exactly the "don't use an LLM when a query will do" judgment that senior AI-engineering interviews probe for.

---

## 4. Features that will advance your AI career

Ranked by (interview signal × effort). These turn the project from "app that calls an LLM" into "production AI system" — the distinction that matters in 2026 hiring.

### A1. Analyst Accuracy Engine (the differentiator) — ~1–2 weekends
"Which firm's calls actually hit, per sector, per horizon?" The data model already supports it: it's a query + UI + a nightly aggregation. Expose it as a leaderboard of *analysts*, not stocks. This is the feature to lead your README, demo video, and portfolio with.

### A2. LLM eval harness + observability — ~1 weekend, highest career ROI per hour
- A `tests/evals/` suite: 30–50 golden Q&A pairs over a fixture DB, scored with an LLM-judge + exact-match hybrid; run in CI, fail on regression.
- Log every LLM call (provider, model, latency, token cost, fallback path) to a table; a small admin panel showing cost/day and fallback rates.
- Version prompts in files, not f-strings.

Almost no side projects have this. It's the #1 thing that separates "used the API" from "AI engineer" in interviews, and it makes the multi-provider router demonstrably safe to change.

### A3. Agentic research report — ~2 weekends
A `POST /api/reports/{symbol}` that runs a small tool-using agent: pull consensus → pull fundamentals → pull news → check insider activity (source already exists in `sources/sec_insider.py`) → compose a one-page brief with confidence + citations back to your own data. Cache and render it. This is your "I built an agent with real tools over my own data, with grounding and cost controls" story — and it's also the natural **paid feature** (see §5).

### A4. Real RAG upgrade — ~1 weekend
Current RAG indexes *synthesized* yfinance text (clever bootstrap, thin substance). Point the same FAISS pipeline at **SEC EDGAR filings (free, no ToS risk)** — 10-K risk factors, fund prospectuses. "RAG over 10-Ks with citation back to the filing section" is a dramatically stronger demo and interview artifact than RAG over text you generated yourself.

### A5. Prediction layer — later, only after A1
Train a simple calibrated classifier (logistic regression / gradient boosting) on your accumulated history to predict target-hit probability, and **show its calibration curve vs. the current heuristic confidence badge**. Modest ML, but "I replaced a heuristic with a calibrated model and measured it" is a great story.

---

## 5. Income path — realistic sequencing

**Blunt read:** consumer stock-tool subscriptions are a brutally competitive market (TipRanks, Zacks, Simply Wall St, Danelfin). You will not out-feature them. You can win a niche with the analyst-accuracy angle, and the project pays you in career capital even if revenue stays small — that dual-return is why it's worth continuing.

### Phase 0 — prerequisites for charging anything
1. **Data licensing (BACKLOG I4).** Scraped TipRanks/Morningstar and unofficial yfinance cannot back a paid product. Move the paid tier onto licensed feeds — Polygon.io (adapter already exists in `sources/polygon.py`), Finnhub paid, or EODHD. Free tier can stay on free sources.
2. **Security P1 items** (rate limiting, secure cookies, session revocation, email verification) — table stakes.
3. **Disclaimers**: "not investment advice", visible everywhere. You're publishing analysis, not advice — keep it that way (registered-advisor territory is a different business).

### Phase 1 — audience before revenue (months 1–3)
The accuracy engine (A1) generates inherently shareable content: *"We tracked 12,000 analyst calls. Firm X's Buy ratings hit their targets 34% of the time."* Auto-generate a weekly report; post to X/LinkedIn/r/stocks; collect emails on the site. Distribution is the actual bottleneck for side-project income, not features — this feature manufactures distribution.

### Phase 2 — first revenue (months 3–6), pick ONE:
- **Freemium SaaS ($5–10/mo):** free = feed + 3 watchlist slots; paid = accuracy engine, unlimited watchlists, price alerts (F1 — stubs exist), AI research reports (A3), API access. Realistic outcome: dozens-to-hundreds of subscribers, $100–1,000/mo. Steady, slow.
- **Paid newsletter ($8/mo Substack):** weekly "who was right" analysis powered by your engine. Far less product work, tests demand fastest. **Recommended first** — it validates willingness-to-pay before you build billing.
- **B2B data/API:** sell the accuracy dataset to fintech devs and finance newsletters ($50–200/mo/seat). Fewer customers needed for the same revenue; but needs licensing fully sorted.

### Phase 3 — compounding (6+ months)
Whichever won: add Stripe billing, alerts via the existing `notifications/` framework (WhatsApp/email stubs are already there), and the agentic report as the premium hook. India-market coverage (already in the code, `market=in`) is an underserved niche worth testing — far less competition than US tools.

---

## 6. Recommended sequence (each ≈ a weekend or two)

| # | Item | Why first |
|---|---|---|
| 1 | Security P1 (S1–S3) + Postgres/Alembic (P2-A) | Gates everything; small, contained |
| 2 | **A1 Analyst Accuracy Engine** | Product differentiator + content machine |
| 3 | **A2 LLM eval harness + cost logging** | Biggest career signal per hour |
| 4 | Weekly auto-report + email capture → start newsletter | Distribution + demand test |
| 5 | A4 RAG over SEC filings | Strong AI demo, zero licensing risk |
| 6 | A3 Agentic research report | Premium feature + agent portfolio piece |
| 7 | F1 price alerts + Stripe | Convert the audience |

## 7. Explicitly deprioritized

- **Microservices / Kubernetes** — the monolith is correct; splitting it now is negative-value résumé-driven engineering.
- **React rewrite (I5)** — 1,150 lines of vanilla JS is at the pain threshold but not past it; rewrite only when a paid tier justifies it.
- **More data sources** — five is plenty; depth of analysis on existing data beats breadth.
- **Mobile app** — the web dashboard + email/WhatsApp alerts cover the need.
