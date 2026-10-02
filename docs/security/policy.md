# Security policy

What protects this application, what must be configured for those protections to hold,
and what is known to be open.

**This repository is public and the application has live accounts.** Open items are
therefore listed here **by ID and severity only**. The working details live in
[`BACKLOG.md`](../../BACKLOG.md); do not restate attack mechanics in this file or in
commit messages.

## Reporting a vulnerability

Report it privately to the repository owner (a GitHub private security advisory or
direct contact). **Do not open a public issue.** There is no published security contact
yet; creating one is an open item.

## Controls in place

| Area | Control | Where |
|---|---|---|
| Passwords | bcrypt hashes; a minimum-length rule on register and change (length only, no complexity check) | `app/auth.py`, `app/models.py` |
| Sessions | Stateless signed cookie (`itsdangerous`), `HttpOnly`, `SameSite=Lax`, expiry from `SESSION_MAX_AGE_DAYS` | `app/auth.py` |
| Authorisation | `get_current_user` on authenticated routes; `require_admin` on `/api/admin/*` | `app/auth.py` |
| Password reset | Single-use token (`secrets.token_urlsafe`), 1-hour TTL, earlier tokens purged; the endpoint answers identically whether or not the email exists | `app/store.py`, `app/main.py` |
| WhatsApp | Twilio HMAC-SHA1 signature check in constant time; per-phone rate limit; phone bound to an account only by a single-use code | `app/whatsapp/` |
| Remote XML | SEC N-PORT filings parsed with `defusedxml`, not the stdlib parser | `app/sources/nport.py` |
| SQL | Parameterised queries throughout; the only interpolated SQL uses constant column names inside `_migrate()` | `app/store.py` |
| Input validation | `normalize_symbol` on every symbol; Pydantic validators; fund amount bounded `0 <= x <= 1e12` | `app/models.py` |
| Output | `esc()` on every value interpolated into HTML | `web/app.js` |
| AI misuse | Scope guard and advice guard run **before** the model and never call it; fetched filing text is labelled third-party with an instruction to ignore embedded instructions | `app/chat.py`, `app/funds.py` |
| AI integrity | Numeric provenance enforced in code ([ADR 0005](../adr/0005-provenance-in-code.md)) | `app/factsheet.py` |
| Secrets | Read from environment; `.env*` and `data/` are git-ignored; secrets are never logged and never sent to an LLM | `app/config.py`, `.gitignore` |
| Container | Image copies only `app/`, `web/`, `scripts/`; `docs/` and tests are not shipped | `Dockerfile` |

## Required production settings

These controls only hold if the setting exists. Each has a safe *development* fallback
that is unsafe in production.

| Setting | Why it is required |
|---|---|
| `SESSION_SECRET` | If empty, a random per-process secret is used: sessions then die on every restart. Set it. |
| `TWILIO_AUTH_TOKEN` | Webhook signature verification is **skipped when this is empty** (a dev and test convenience). Set it wherever the WhatsApp webhook is public. |
| `APP_BASE_URL` | The webhook signature is computed over this URL, and password-reset links are built from it. It must be the public URL. |
| `SENTRY_DSN` | Optional, but without it production errors are only in logs. |

## Roles

`users.role` is `user`, `beta` or `admin`. Only `admin` gates anything today
(`/api/admin/*`). `require_beta` exists in `app/auth.py` but is attached to no route.
How the first admin is provisioned is open item **S5**.

## Open items

Severities are from `BACKLOG.md`. A status of "partly resolved" or "unverified" is
stated plainly rather than rounded up.

| ID | Severity | Item | Status |
|---|---|---|---|
| S5 | **Critical** | Admin role provisioning | Open |
| S6 | High | XML parsing hardening | **Partly resolved:** `defusedxml` is used for N-PORT. Remote RSS in `app/sources/market_news.py` still uses the stdlib parser. The BACKLOG entry predates the fix |
| S1 | High | Rate limiting on login and register | Open |
| S2 | Medium | `Secure` flag on the session cookie | Open |
| S3 | Medium | Session revocation on password change | Open |
| S4 | Medium | Email verification on register | Open |
| S7 | Medium | CORS origin policy | Open |
| S8 | Medium | Container runs as root | Open |
| S9 | Medium | Unpinned dependencies | Open |
| S12 | Medium | No rate limit or AI-budget check on `POST /api/chat` and the fact-sheet ask endpoint | Open |
| S10 | Low | CSRF token (defence in depth) | Open |
| S11 | Low | `esc()` does not escape `'` | Open; safe today only because it is used inside double-quoted attributes |

**Observed, not yet in BACKLOG:** `GET /api/health` is unauthenticated and reports which
integrations are configured plus the last LLM error text. It exposes presence, not
secret values. Low severity; consider trimming it or moving the detail behind admin.

**Sequencing.** P1 security must land before any push to grow users. In particular, do
not start public fund pages or agent work until S5 and S12 are closed.

## When you change code

The review checklist in [`CLAUDE.md`](../../CLAUDE.md) applies to every push: inputs
validated, auth and scope correct, no secrets logged or sent to the LLM. A change that
adds a route must say whether it is public, authenticated or admin, and why.
