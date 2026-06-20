# Production Backlog

Items from the senior architect review, ordered by priority.
Work on these once the P0 items are complete.

---

## P1 — Security (do before any real users)

| # | Item | Why |
|---|------|-----|
| S1 | **Rate-limit `/login` and `/register`** | Credential stuffing / email enumeration. Use `slowapi` (1 line on each route). |
| S2 | **`Secure=True` on session cookie** | Cookie sent over plain HTTP is sniffable. Gate on env: `secure = not settings.dev_mode`. |
| S3 | **Session revocation on password change** | Changing password today doesn't kill old sessions. Add `token_version` column to `users`; bump on password change + logout-all; check in `get_current_user`. |
| S4 | **Email verification on register** | Currently anyone can sign up with a fake address. Send a verification link before granting watchlist access. Ties into the deferred password-reset work. |

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
| F3 | **Portfolio tracking** | Shares + cost basis + P&L view alongside the watchlist. Separate `positions` table. |
| F4 | **Analyst accuracy backtesting** | "Which firm's calls actually hit?" The append-only history + outcomes table already supports this — it's a query + UI. Your real product differentiator. |
| F5 | **Account management page** | Change password UI, data export (GDPR), account deletion. |

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

*Last updated: 2026-06-18. Completed: (a) feed N+1 bulk queries, (b) scheduler single-run DB lock.*
