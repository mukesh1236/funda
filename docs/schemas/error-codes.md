# Error codes and status enums

What the API actually returns, taken from every `HTTPException` in `app/` and
the `status` / `source` fields in `app/models.py`. If you add a raise site or an
enum value, update this file in the same change.

Errors use FastAPI's standard shape: `{"detail": "<message>"}`.

## HTTP status codes

| Code | Raised by | Meaning | What a client should do |
|---|---|---|---|
| **200** | most routes | Success. | |
| **201** | `POST /api/funds` | Fund added to the portfolio. | |
| **202** | `POST /api/funds/{sym}/factsheet/refresh`, `POST /api/funds/{sym}/reindex` | Accepted; work continues in the background. | Poll the matching `GET`. |
| **400** | `POST /api/auth/reset-password` | Reset link invalid, used, or expired. | Request a new link. |
| **401** | `auth.get_current_user` (every authenticated route); login; change-password | No valid session cookie, or the user no longer exists; wrong email/password; wrong current password. | Prompt sign-in. The web client treats it as `AuthError` and opens the auth overlay. |
| **403** | `require_admin` (`/api/admin/*`); WhatsApp webhook | Not an admin; or Twilio signature failed verification. | Do not retry. |
| **404** | symbol lookups, fund routes, watchlist add | Ticker did not resolve, has no tracked data, or is not in the user's funds. | Check the ticker. |
| **409** | `POST /api/auth/register` | Email already registered. | Sign in instead. |
| **422** | every route taking a symbol; request-body validation | Symbol failed `normalize_symbol`, or the body failed Pydantic validation (for example a negative fund amount). | Fix the input. |
| **429** | `POST /api/funds/{sym}/factsheet/refresh` | A forced rebuild already happened for this symbol in the last hour. | Wait; the existing fact sheet is still served. |
| **503** | `POST /api/admin/sentry-test`; `POST /api/chat` | Sentry not configured; or chat reported an error. | See below. |

### Known gaps in this table

- **`require_beta` is defined but unused.** `auth.require_beta` (403
  "Beta access required.") is not attached to any route today, so that 403 is
  unreachable. The `beta` role exists in `users.role` but gates nothing.
- **`/api/chat`'s 503 is unreachable in practice.** `answer_question()` always
  returns an `error` of `None`, so that branch never fires; failures degrade to a
  lower layer (rule answer, then overview) and still return 200.
- **No 429 on chat.** `POST /api/chat` and `POST /api/funds/{sym}/factsheet/ask`
  are public and unrate-limited (BACKLOG S12). The WhatsApp webhook *is*
  limited, to 20 messages per phone per 60 seconds.

## Status enums

These are returned inside a 200 response. Long operations never hold a request
open; they return a `status` and the client polls.

| Field | Values | Where |
|---|---|---|
| Fund drivers `status` | `ready` \| `computing` \| `unavailable` | `GET /api/funds/{sym}/drivers` |
| Portfolio X-Ray `status` | `ready` \| `computing` \| `empty` | `GET /api/funds/portfolio/xray` |
| Fact sheet `status` | `queued` \| `fetching` \| `parsing` \| `indexing` \| `summarizing` \| `ready` \| `unavailable` \| `budget_exhausted` | `GET /api/funds/{sym}/factsheet` |
| Recommendation outcome `status` | `hit` \| `missed` \| `pending` \| `expired` | `outcomes.status` |

`computing` is not an error: poll again. The web client caps polling (about 10
attempts at 8 seconds) so a failed background job cannot spin forever.

## `source` on answers

`source` says *which layer produced the text*, so the UI can show when an answer
was not reasoned by the model.

| Endpoint | Values |
|---|---|
| `POST /api/chat`, `/api/chat/stream` | `llm` \| `fund-data` \| `rule` \| `overview` \| `out-of-scope` \| `advice-declined` |
| `POST /api/funds/{sym}/factsheet/ask` | `llm` \| `no-context` \| `out-of-scope` \| `advice-declined` \| `budget_exhausted` \| `unavailable` \| `unverifiable` |

- `advice-declined` — a personal buy/sell question; the model is never called.
- `out-of-scope` — a denylist hit (off-topic or prompt-injection phrasing); the
  model is never called.
- `no-context` — nothing in the fund's filing cleared the similarity floor, so the
  answer says the filing does not cover it rather than guessing.
- `unavailable` — the LLM provider returned nothing (down, rate limited, or no key); try again shortly.
- `unverifiable` — the model's answer contained a number not present in the
  supplied facts or excerpts, so it was withheld.
- `budget_exhausted` — `AI_DAILY_CALL_BUDGET` is spent (fact-sheet paths only; see
  [`design/ask-ai.md`](../design/ask-ai.md)).
