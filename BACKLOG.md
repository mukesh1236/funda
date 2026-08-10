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
| F6 | **Stock "likes" / public interest signal** | Let logged-in users *and* anonymous visitors ♥ a stock, so the app can show which stocks people actually care about — and contrast crowd interest against analyst consensus. Full plan below (§F6 detail). |

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

### F6 detail — Stock "likes" / public interest signal

**Goal:** anyone on the site — signed in or not — can ♥ a stock. Surface the
counts so the app can answer "which stocks are people actually interested in?",
and (the differentiated part) contrast that crowd interest against the analyst
consensus already tracked.

**Reuse — most of the plumbing already exists:**
- **Anonymous identity is already solved.** `app/main.py::track_traffic` sets a
  `visitor` cookie (`secrets.token_hex(8)`, 365d, httponly, samesite=lax) on
  `GET /`. Use it as the anonymous like identity — no new cookie needed. It's
  httponly, but the browser still sends it on same-origin `fetch`.
- **Rate limiting:** `_rate_ok()` in `app/whatsapp/webhook.py` — a per-key deque
  sliding window; same shape works for like-spam.
- **Row-per-(identity, symbol) dedupe:** mirrors the `watchlist` composite PK
  and `fund_portfolio`'s `UNIQUE(user_id, symbol)` in `store.py`.

**Data model** — one row per like, not a counter column (a bare `likes INTEGER`
can't be un-liked, deduped, or answer "did *I* like this"):

```sql
CREATE TABLE stock_likes (
    symbol     TEXT NOT NULL COLLATE NOCASE,
    user_id    INTEGER REFERENCES users(id) ON DELETE CASCADE,  -- NULL = anonymous
    visitor_id TEXT,                                            -- the `visitor` cookie
    ip_hash    TEXT,        -- salted hash for abuse analysis; never store raw IP
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX idx_likes_user    ON stock_likes(symbol, user_id)    WHERE user_id IS NOT NULL;
CREATE UNIQUE INDEX idx_likes_visitor ON stock_likes(symbol, visitor_id) WHERE user_id IS NULL;
CREATE INDEX        idx_likes_symbol  ON stock_likes(symbol);
```

Partial unique indexes make double-clicks idempotent for free. Goes in
`_SCHEMA`; existing DBs pick it up via `CREATE TABLE IF NOT EXISTS`, no
migration needed.

**⚠️ Trap 1 — the response cache would leak per-user like state.**
`_RESPONSE_CACHE` (`app/main.py`, TTL 60s) keys the feed on
`("feed", days, theme, market)` with **no user identity**. So:
- `like_count` in the cached feed is fine (shared, public, 60s stale is OK).
- `liked_by_me` in the cached feed is a **bug** — user A's heart state would be
  served to user B for 60s. It must come from a separate uncached endpoint.

Splitting them also means a like never has to bust the shared cache (which
would discard everyone's cached feed on every click). Cover the staleness with
an optimistic UI update on click.

**⚠️ Trap 2 — cookie likes are farmable.** Clearing cookies allows unlimited
re-liking, which undercuts the whole "how many people *really* care" premise.
Mitigations, in value order: (1) rate-limit per IP on the like endpoint;
(2) store a salted `ip_hash` to spot farming later; (3) report **two** numbers —
total likes and likes-from-logged-in-accounts, the latter being the trustworthy
signal. Don't block anonymous likes outright: forcing login would cost more
signal than the noise costs.

**Identity edge case:** someone likes 5 stocks logged out, then signs in — their
likes are stranded on the visitor cookie, and they can be double-counted on the
same stock (once anon, once as user). Fix at login: reassign `visitor_id` rows
to `user_id`, deleting rows that would collide with an existing user-like.
~10 lines, and it directly protects the count accuracy the feature exists for.

**API:**

| Endpoint | Auth | Notes |
|---|---|---|
| `POST /api/stocks/{symbol}/like` | optional | Toggles. Returns `{symbol, like_count, liked}`. Sets the `visitor` cookie if absent. |
| `GET /api/likes/me?symbols=A,B,C` | optional | Uncached, per-identity. Returns which of these this identity liked. |
| `GET /api/likes/top?market=us&limit=10` | none | Most-liked, for the highlights tile. |

Needs a new `get_optional_user` dependency in `app/auth.py` — today's
`get_current_user` raises 401; same body, returns `None` instead. Validate
symbols with `normalize_symbol` so likes work for **any** ticker, not just the
tracked universe (consistent with watchlist and `/api/stocks/{symbol}`, and
search surfaces untracked tickers).

**UI surfaces:**
- **Feed row** — ♥ + count column, sortable (register in `_SORT_COLS`,
  `web/app.js`). The click handler must `stopPropagation()`; the row already has
  a click handler that expands it.
- **Detail / expand panel** — same button.
- **Highlights strip** — a "Most liked" tile beside buzzing / buy / sell
  (`renderHighlights`, `web/app.js`). This is the tile that answers the question
  at a glance.
- **Leaderboard** — widen the metric pattern in `app/main.py` from
  `^(consensus|hit_rate)$` to `^(consensus|hit_rate|likes)$`.
- **Admin stats** — total likes, unique likers, top-liked; slots into the
  existing `/api/admin/stats`.

**Tests:** toggle on/off; double-like is idempotent; anonymous vs logged-in
identities counted separately; claim-on-login merges without collision; invalid
symbol → 422; rate limit trips; and a regression test that `liked_by_me` is
**not** served from the shared feed cache. Plus bump the `?v=` asset version in
`web/index.html`.

**Suggested scope:**
- **v1** — schema, toggle endpoint, `liked_by_me` endpoint, feed count + ♥
  button, highlights tile, rate limit, tests.
- **Later** — leaderboard sort, admin panel, claim-on-login if deferred.
- **Follow-up worth having** — *crowd interest vs analyst consensus*: stocks the
  crowd likes that analysts don't rate, and vice versa. Differentiated for this
  product and nearly free once the counts exist.

**Naming note:** "like" is vague in a finance context and `watchlist` already
covers personal tracking. Suggest keeping the ♥ but labelling the number
**"interest"** — more honest about what it measures. Cosmetic.

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

*Last updated: 2026-08-10 (added F6 — stock likes / public interest signal).
Previously: 2026-07-22 (F3 expanded into full brokerage/retirement integration
plan). Completed: (a) feed N+1 bulk queries, (b) scheduler single-run DB lock.*
