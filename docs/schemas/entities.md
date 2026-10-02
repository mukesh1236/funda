# Entities

The SQLite schema. Everything here is persisted through `app/store.py`
(`RecommendationStore`) using raw `sqlite3`; there is no ORM.

**The column reference at the bottom is generated** from `app/store.py::_SCHEMA`
by `scripts/export_schema_doc.py` and checked by `tests/test_docs_in_sync.py`.
Do not edit it by hand. The prose above it is hand-written.

## What each table is for

| Group | Table | Purpose |
|---|---|---|
| **Core product** | `recommendations` | One analyst call. **Append-only**: never update or delete rows; the accumulated history is the product's asset. |
| | `outcomes` | Scored result of a recommendation: `hit`, `missed`, `pending` or `expired`. 1:1 with `recommendations`, cascades on delete. |
| | `profiles` | Per-symbol cached profile: company name, 1/3/6/12-month returns, institutional ownership. |
| | `stock_extras` | The slow half of a stock's detail panel (fundamentals, ownership, news, insider trades, optional LLM narrative) as one JSON payload per symbol, refreshed by the daily job. **Derived data: safe to delete**, it is rebuilt on demand. |
| **Accounts** | `users` | Accounts. `role` is `user`, `beta` or `admin`. |
| | `password_reset_tokens` | Single-use, short-TTL reset links. |
| | `watchlist` | Per-user pinned stocks, with the price captured on the pin day. Composite key `(user_id, symbol, grp)`. |
| **Funds** | `fund_portfolio` | The funds a user tracks. `amount` is optional and nullable by design: Portfolio X-Ray equal-weights without it and reports no currency figures. |
| | `fund_holdings` | Complete holdings per fund (SEC N-PORT, or the yfinance top-10 fallback). Replaced wholesale on each refresh. |
| | `security_ids` | Permanent CUSIP to ticker mappings, resolved once. |
| | `fund_documents` | Filings actually used (SEC EDGAR today). `accession` is the stable identity that keys the summary. |
| | `fund_factsheets` | Generated plain-English fact sheets. One row per `(symbol, doc_key, schema_ver)`, so a new filing or a new summary schema regenerates it, and nothing else does. |
| | `fund_factsheet_status` | Ingest progress for the polling UI. One row per fund, overwritten in place. |
| **WhatsApp** | `whatsapp_links` | Verified phone to user binding, keyed by phone: one phone maps to exactly one account. `opted_in` gates all outbound messages. |
| | `whatsapp_link_codes` | Pending 6-digit linking codes (10-minute TTL, single use). |
| **Operations** | `llm_calls` | One row per LLM call, any provider: tokens, latency, outcome. Powers `/api/admin/ai-stats` and the daily AI budget. |
| | `chat_answers` | Which layer answered each chat question. The fallback *rate* is the best "is the AI healthy" signal. |
| | `metrics_daily` | Per-day page loads and first-time visitors. |
| | `job_lock` | Claim row so the daily job runs once per day even if several processes start. |

## Relationships

```
users 1--* watchlist
users 1--* fund_portfolio            (symbol is a ticker, not a foreign key)
users 1--* whatsapp_link_codes
users 1--* whatsapp_links           (keyed by phone_e164; a phone belongs to one user)
users 1--* password_reset_tokens

recommendations 1--1 outcomes        (outcomes.rec_id, on delete cascade)

fund_factsheets: one row per (symbol, doc_key, schema_ver)
                 doc_key = the filing's accession, or its URL when it has none
fund_factsheet_status: one row per fund symbol, overwritten in place
```

Fund tables key on the **ticker symbol**, not a surrogate id, because a symbol is
what every other part of the app already passes around.

## Rules that matter

- **`recommendations` is append-only.** The dedupe index
  `idx_rec_dedupe` uses `IFNULL(firm, '')` because SQLite treats NULLs as
  distinct in a UNIQUE constraint, which would otherwise admit duplicate
  firm-less rows. Preserve that when touching it.
- **Schema changes go in two places.** `_SCHEMA` (fresh databases) *and*
  `RecommendationStore._migrate()` (existing ones). See
  [`workflows/add-a-column.md`](../workflows/add-a-column.md).
- **There is no Alembic.** Migration is hand-rolled `ALTER TABLE ... ADD
  COLUMN`; replacing it is BACKLOG item P2-A.

## Column reference (generated)

<!-- BEGIN GENERATED: scripts/export_schema_doc.py -->

19 tables, generated from `app/store.py::_SCHEMA`.

### `chat_answers`

| column | type | null | default | notes |
|---|---|---|---|---|
| `id` | INTEGER | no |  | PK |
| `ts` | TEXT | no |  |  |
| `source` | TEXT | no |  |  |

Indexes: `(ts)`

### `fund_documents`

| column | type | null | default | notes |
|---|---|---|---|---|
| `id` | INTEGER | no |  | PK |
| `symbol` | TEXT | no |  |  |
| `source` | TEXT | no |  | 'edgar' \| 'amfi' \| 'amc' |
| `form_type` | TEXT | no |  | '497K' \| '497' \| '485BPOS' \| 'N-CSR' |
| `doc_role` | TEXT | no | `'primary'` | 'primary' \| 'commentary' |
| `accession` | TEXT | yes |  |  |
| `filed_date` | TEXT | yes |  |  |
| `period_date` | TEXT | yes |  |  |
| `title` | TEXT | yes |  |  |
| `url` | TEXT | no |  |  |
| `char_count` | INTEGER | yes |  |  |
| `sections` | TEXT | yes |  | JSON [{key,heading,start,end}] |
| `fetched_at` | TEXT | no |  |  |

Indexes: `(symbol)`; `unique (symbol, source, IFNULL(accession, ''), url)`

### `fund_factsheet_status`

| column | type | null | default | notes |
|---|---|---|---|---|
| `symbol` | TEXT | no |  | PK |
| `state` | TEXT | no |  | queued\|fetching\|parsing\|indexing\|summarizing\|ready\|unavailable\|budget_exhausted |
| `detail` | TEXT | yes |  |  |
| `doc_key` | TEXT | yes |  |  |
| `chunk_count` | INTEGER | yes |  |  |
| `error` | TEXT | yes |  |  |
| `updated_at` | TEXT | no |  |  |

### `fund_factsheets`

| column | type | null | default | notes |
|---|---|---|---|---|
| `symbol` | TEXT | no |  | PK |
| `doc_key` | TEXT | no |  | PK; accession, or the URL when there is none |
| `schema_ver` | INTEGER | no | `1` | PK |
| `summary_json` | TEXT | no |  |  |
| `model` | TEXT | yes |  |  |
| `generated_at` | TEXT | no |  |  |

### `fund_holdings`

| column | type | null | default | notes |
|---|---|---|---|---|
| `fund` | TEXT | no |  |  |
| `ticker` | TEXT | yes |  |  |
| `cusip` | TEXT | yes |  |  |
| `name` | TEXT | no |  |  |
| `weight` | REAL | no |  |  |
| `as_of` | TEXT | yes |  |  |
| `source` | TEXT | no | `'nport'` | 'nport' \| 'yfinance_top10' |

Indexes: `(fund)`

### `fund_portfolio`

| column | type | null | default | notes |
|---|---|---|---|---|
| `id` | INTEGER | no |  | PK |
| `user_id` | INTEGER | no |  |  |
| `symbol` | TEXT | no |  |  |
| `amount` | REAL | yes |  |  |
| `added_at` | TEXT | no | `datetime('now')` |  |

Foreign keys: `user_id` -> `users.id` (on delete cascade)

Indexes: `unique (user_id, symbol) [UNIQUE constraint]`

### `job_lock`

| column | type | null | default | notes |
|---|---|---|---|---|
| `job_id` | TEXT | no |  | PK |
| `last_run` | TEXT | no |  |  |

### `llm_calls`

| column | type | null | default | notes |
|---|---|---|---|---|
| `id` | INTEGER | no |  | PK |
| `ts` | TEXT | no |  |  |
| `provider` | TEXT | no |  |  |
| `model` | TEXT | yes |  |  |
| `ok` | INTEGER | no |  |  |
| `latency_ms` | REAL | yes |  |  |
| `prompt_tokens` | INTEGER | yes |  |  |
| `completion_tokens` | INTEGER | yes |  |  |
| `error` | TEXT | yes |  |  |

Indexes: `(ts)`

### `metrics_daily`

| column | type | null | default | notes |
|---|---|---|---|---|
| `day` | TEXT | no |  | PK; "YYYY-MM-DD" |
| `hits` | INTEGER | no | `0` | app page loads that day |
| `visitors` | INTEGER | no | `0` | first-time (new-cookie) visitors |

### `outcomes`

| column | type | null | default | notes |
|---|---|---|---|---|
| `rec_id` | INTEGER | no |  | PK |
| `current_price` | REAL | yes |  |  |
| `target_price` | REAL | yes |  |  |
| `pct_to_target` | REAL | yes |  |  |
| `status` | TEXT | no |  |  |
| `days_held` | INTEGER | no | `0` |  |
| `last_checked` | TEXT | yes |  |  |

Foreign keys: `rec_id` -> `recommendations.id` (on delete cascade)

### `password_reset_tokens`

| column | type | null | default | notes |
|---|---|---|---|---|
| `token` | TEXT | no |  | PK |
| `user_id` | INTEGER | no |  |  |
| `expires_at` | TEXT | no |  |  |
| `used` | INTEGER | no | `0` |  |

Foreign keys: `user_id` -> `users.id` (on delete cascade)

### `profiles`

| column | type | null | default | notes |
|---|---|---|---|---|
| `symbol` | TEXT | no |  | PK |
| `company_name` | TEXT | yes |  |  |
| `ret_1m` | REAL | yes |  |  |
| `ret_3m` | REAL | yes |  |  |
| `ret_6m` | REAL | yes |  |  |
| `ret_12m` | REAL | yes |  |  |
| `inst_pct` | REAL | yes |  |  |
| `fund_holders` | INTEGER | yes |  |  |
| `top_buyer` | TEXT | yes |  |  |
| `top_buyer_change` | REAL | yes |  |  |
| `updated_at` | TEXT | yes |  |  |

### `recommendations`

| column | type | null | default | notes |
|---|---|---|---|---|
| `id` | INTEGER | no |  | PK |
| `symbol` | TEXT | no |  |  |
| `source` | TEXT | no |  |  |
| `firm` | TEXT | yes |  |  |
| `analyst` | TEXT | yes |  |  |
| `action` | TEXT | no |  |  |
| `count` | INTEGER | no | `1` |  |
| `note` | TEXT | yes |  |  |
| `url` | TEXT | yes |  |  |
| `target_price` | REAL | yes |  |  |
| `entry_price` | REAL | yes |  |  |
| `entry_date` | TEXT | no |  |  |
| `created_at` | TEXT | no |  |  |

Indexes: `unique (symbol, source, IFNULL(firm, ''), action, entry_date)`; `(entry_date)`; `(symbol)`

### `security_ids`

| column | type | null | default | notes |
|---|---|---|---|---|
| `cusip` | TEXT | no |  | PK |
| `ticker` | TEXT | yes |  |  |
| `name` | TEXT | yes |  |  |

### `stock_extras`

| column | type | null | default | notes |
|---|---|---|---|---|
| `symbol` | TEXT | no |  | PK |
| `payload` | TEXT | no |  | JSON: fundamentals, ownership, news, insider_trades, narrative |
| `fetched_at` | TEXT | no |  |  |

### `users`

| column | type | null | default | notes |
|---|---|---|---|---|
| `id` | INTEGER | no |  | PK |
| `email` | TEXT | no |  |  |
| `password_hash` | TEXT | no |  |  |
| `display_name` | TEXT | yes |  |  |
| `role` | TEXT | no | `'user'` |  |
| `created_at` | TEXT | no |  |  |

Indexes: `unique (email) [UNIQUE constraint]`

### `watchlist`

| column | type | null | default | notes |
|---|---|---|---|---|
| `user_id` | INTEGER | no |  | PK |
| `symbol` | TEXT | no |  | PK |
| `grp` | TEXT | no | `'My Watchlist'` | PK |
| `pin_date` | TEXT | no |  | day the price was pinned |
| `pin_price` | REAL | yes |  | price snapshot on the pin day |
| `company_name` | TEXT | yes |  |  |
| `added_at` | TEXT | yes |  |  |

### `whatsapp_link_codes`

| column | type | null | default | notes |
|---|---|---|---|---|
| `code` | TEXT | no |  | PK |
| `user_id` | INTEGER | no |  |  |
| `expires_at` | TEXT | no |  |  |
| `used` | INTEGER | no | `0` |  |

Foreign keys: `user_id` -> `users.id` (on delete cascade)

### `whatsapp_links`

| column | type | null | default | notes |
|---|---|---|---|---|
| `user_id` | INTEGER | no |  |  |
| `phone_e164` | TEXT | no |  | PK; one phone maps to one account |
| `opted_in` | INTEGER | no | `1` |  |
| `verified_at` | TEXT | no |  |  |
| `created_at` | TEXT | no |  |  |

Foreign keys: `user_id` -> `users.id` (on delete cascade)

Indexes: `(user_id)`

<!-- END GENERATED -->
