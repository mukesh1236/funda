# Production Backlog

Items from the senior architect review, ordered by priority.
Work on these once the P0 items are complete.

---

## P1 — Security (do before any real users)

Security-engineer audit (2026-07-16) re-verified this section against the
live code — S1/S2/S3/S4 are all still open, plus five new findings. Full
evidence (file/line citations) for every item is in git history / session
notes; this table is the actionable summary. Ranked by severity.

| # | Item | Severity | Why |
|---|------|----------|-----|
| S5 | **Admin takeover via unverified email + auto-promotion** | 🔴 Critical | `ensure_admin()` (`store.py`) runs on every startup and promotes ANY registered user matching `ADMIN_EMAIL` to admin. `register()` has no email verification, so an attacker who learns/guesses `ADMIN_EMAIL` can register that address and get auto-promoted on the next deploy/restart. Fix: promote by email only when no admin exists yet (bootstrap-once), not unconditionally on every startup. |
| S6 | **XXE risk parsing SEC N-PORT XML** | 🟠 High | `app/sources/nport.py` uses stdlib `xml.etree.ElementTree.fromstring` on externally-fetched filing XML — not hardened against external entities / entity-expansion DoS. Fix: swap to `defusedxml.ElementTree.fromstring` (drop-in), add `defusedxml` to `requirements.txt`. |
| S1 | **Rate-limit `/login` and `/register`** | 🟠 High | Credential stuffing / email enumeration. Use `slowapi` (1 line on each route). Confirmed still absent. |
| S2 | **`Secure=True` on session cookie** | 🟡 Medium | `app/auth.py:set_session_cookie` never passes `secure=`, defaults to `False` — cookie can transmit over plain HTTP. Gate on env: `secure = not settings.app_base_url.startswith("http://localhost")`. Confirmed still absent. |
| S3 | **Session revocation on password change** | 🟡 Medium | Changing password today doesn't kill old sessions. Add `token_version` column to `users`; bump on password change + logout-all; check in `get_current_user`. Confirmed still absent. |
| S4 | **Email verification on register** | 🟡 Medium | Currently anyone can sign up with a fake address (and this is what makes S5 exploitable). Send a verification link before granting watchlist access / any privileged role. |
| S7 | **CORS wildcard origin** | 🟡 Medium | `app/main.py` still has `allow_origins=["*"]` commented "local personal tool" — stale now that the app is public with real accounts. Restrict to the deployed origin(s). |
| S8 | **Docker container likely runs as root** | 🟡 Medium | No `USER` directive in `Dockerfile` before `CMD`. Add a non-root user (CIS Docker Benchmark 4.1). |
| S9 | **Unpinned dependencies** | 🟡 Medium | `requirements.txt` uses `>=` only, no upper bounds/lockfile — every deploy can silently pull a new major version. Pin exact versions; run `pip-audit` in the existing CI workflow. |
| S10 | **No CSRF token (defense-in-depth)** | 🟢 Low | `SameSite=Lax` blocks cross-site POST in modern browsers (acceptable baseline), but a CSRF token on state-changing admin/account routes is standard hardening for a financial-data app. |
| S11 | **`esc()` (web/app.js) doesn't escape `'`** | 🟢 Low | No live exploit found (only ever used with server-validated ticker symbols today), but a latent trap if a future feature reuses the inline-`onclick` pattern with free text. Prefer the `data-*` + `addEventListener` pattern already used in newer code. |

Explicitly checked, no issue found: SQL is parameterized everywhere in
`store.py` (no string-built queries); password strength IS enforced
server-side (`_validate_password`, not just the HTML `minlength`); password
reset tokens use `secrets.token_urlsafe(32)`, 1h TTL, single-use; ticker
symbols are allowlist-validated (`_SYMBOL_RE`); no raw exception messages
leak into HTTP error responses.

Suggested order: S5 (biggest impact, smallest fix) → S2 + S3 (cheap,
already scoped) → S1 → S6 → S7/S8/S9 (config-only) → S10/S11 (opportunistic).

---

## P2 — Platform (do before scaling beyond 1 server)

| # | Item | Why |
|---|------|-----|
| P2-A | **Postgres + Alembic** | SQLite single-writer + no horizontal scale. Replace `store.py`'s raw sqlite3 with SQLAlchemy Core; use Alembic for migrations instead of hand-rolled `_migrate()`. |
| P2-B | **Redis shared cache** | `TTLCache` in every source module is per-process; cache hit rate collapses with >1 worker. Replace with Redis (`cachetools` → `redis-py` with TTL keys). Also enables rate-limit state (S1). |
| P2-C | **Externalize the scheduler** | `claim_daily_job` guard (already in) prevents duplicate runs, but APScheduler still starts in every process. Move to a dedicated worker (Celery beat / k8s CronJob / standalone cron script). |
| P2-D | **API versioning + pagination** | `/api/recommendations/feed` returns everything. Add `/api/v1` prefix; add `?limit=` and cursor pagination to feed + leaderboard. |

---

## P3 — Product features

| # | Item | Why |
|---|------|-----|
| F1 | **Watchlist price alerts** | Notify user when stock crosses analyst target. `notifications/` already stubs this — highest value, lowest effort. |
| F2 | **Password reset via email** | Already deferred from auth session. Needs email sender (SendGrid / SES) + `itsdangerous` timed token. |
| F3 | **Real brokerage / retirement account integration** | Connect real accounts (Robinhood, 401k) → analyze actual holdings, WhatsApp portfolio updates. Full plan below (§F3 detail). |
| F4 | **Analyst accuracy backtesting** | "Which firm's calls actually hit?" The append-only history + outcomes table already supports this — it's a query + UI. Your real product differentiator. |
| F5 | **Account management page** | Change password UI, data export (GDPR), account deletion. |

### F3 detail — Real brokerage / retirement account integration

**Goal:** let a user connect real accounts (Robinhood, other brokers,
retirement/401k) so the app pulls actual positions, analyzes them (return
attribution, overlap vs funds), cross-references them against the analyst
signals already tracked, and pushes portfolio updates to WhatsApp.

**Hard constraints that shape the design:**
- **Robinhood has no official public API** — real connections require an
  aggregator. Recommended: **SnapTrade** (retail-broker-focused: Robinhood,
  Fidelity, Vanguard, 401k providers; free dev tier; read-only). Plaid
  Investments is the alternative but is approval-gated, pricier, weaker on
  Robinhood.
- **"Real-time" is two layers:** aggregators sync *holdings snapshots* (≈daily),
  not live ticks. Live value/day-change comes from re-pricing held quantities
  with quotes — the app already does this (`get_current_price`,
  `_batch_day_changes` in `app/service.py`).

**Phased build (recommended: Phase 1 first — zero third-party approval):**

- **Phase 1 — manual + CSV import.** New per-user `holdings` table (`user_id`,
  `account_id`, `account_name`, `symbol`, `quantity`, `cost_basis`, `as_of`,
  `source`) mirroring `fund_portfolio`'s shape (`store.py:170`). New
  `app/portfolio.py` (mirrors `app/funds.py`): `build_portfolio` (live-repriced
  positions + totals), `portfolio_drivers` (reuse `pareto_drivers`,
  `app/fund_analytics.py:15`), `compare_to_fund` (reuse `_compare_holdings`,
  `app/funds.py:84`). Router mounted like `funds_router`, gated with
  `require_beta`. Frontend "My Portfolio" view reusing existing card/table/meter
  components. WhatsApp: add a portfolio line to `_format_brief` (`jobs.py:179`)
  in the 8am brief + a "my portfolio" intent in `answer_question`.
- **Phase 2 — SnapTrade auto-connect.** `snaptrade_client_id/consumer_key` in
  config; `brokerage_connections` table (mirror `bind_whatsapp` upsert,
  `store.py:511`); `app/sources/snaptrade.py` (register → portal URL →
  list_accounts/list_positions → normalize → `replace_account_holdings`); daily
  re-sync job in `main.py` lifespan. Everything downstream already consumes the
  `holdings` table, so Phase 2 is a pure data-source swap.

**Reuse (already holdings-source-agnostic):** `pareto_drivers`,
`batch_period_returns` (`app/fund_analytics.py`), `_compare_holdings`
(`app/funds.py`), price feeds (`app/sources/prices.py`,
`app/service.py::_batch_day_changes`), the WhatsApp brief job
(`app/jobs.py::send_whatsapp_briefs`), and the shared chat brain
(`app/chat.py::answer_question`).

**Security:** read-only broker scopes only (never trading); Phase-2 broker
secrets encrypted at rest (Fernet — no encryption helper exists today, add one;
relates to the plaintext-secrets class flagged in P1); never log or send broker
secrets to the LLM; keep the "analysis, not investment advice" disclaimer on
portfolio replies.

---

## P4 — Infrastructure / Ops

| # | Item | Why |
|---|------|-----|
| I1 | **Structured logging + Sentry** | Currently `logging.basicConfig`. Add JSON formatter; integrate Sentry for error tracking. |
| I2 | **Docker + CI** | Containerize (`Dockerfile` + `docker-compose`), add GitHub Actions for test + lint on push. |
| I3 | **CDN for static files** | Static `web/` served by the same uvicorn process. Put nginx or Cloudflare in front. |
| I4 | **Market data licensing** | Scraping TipRanks/Morningstar + using unofficial yfinance is fragile and a ToS risk. Evaluate licensed vendor (Refinitiv, Polygon.io) for a paid product. |
| I5 | **Frontend framework** | `app.js` is ~530 LOC of hand-wired DOM. At some point a React/Vue build step will be needed; the FundAI project already uses Next.js. |

---

*Last updated: 2026-07-22 (F3 expanded into full brokerage/retirement
integration plan). Completed: (a) feed N+1 bulk queries, (b) scheduler
single-run DB lock.*
