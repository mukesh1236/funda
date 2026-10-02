# Architecture overview

AlphaFunds is a **single stateful node**: one FastAPI process serves the API
*and* the static frontend, owns a SQLite database and FAISS indexes on one
mounted volume, and runs its own scheduler in-process. That shape is deliberate
(see [`adr/0001`](../adr/0001-sqlite-single-node.md)); it is what keeps the app
cheap and simple, and it is also what rules out scale-to-zero hosting.

```
Browser ──┐                       ┌─► SQLite   /data/recommendations.db   (WAL)
          ├─► FastAPI (uvicorn,   ├─► FAISS    /data/fund_index/{SYMBOL}/
WhatsApp ─┘    1 worker)  ────────┼─► ONNX     /data/fastembed/            (embedding weights)
 (Twilio)         │               └─► in-process TTL caches (bounded)
                  │
                  ├─► APScheduler (same process): daily job, feed-cache warm, alerts
                  └─► outbound: yfinance · SEC EDGAR · Finnhub/FMP/Polygon/TipRanks ·
                                Tavily · OpenRouter/Gemini/Grok/Ollama · Twilio · Sentry
```

## Components

| Module | Responsibility |
|---|---|
| `app/main.py` | App factory, 28 routes, lifespan and scheduler wiring, middleware, static mount |
| `app/store.py` | All persistence. Raw `sqlite3`, 18 tables, hand-rolled `_migrate()` |
| `app/service.py` | Read side: feed, per-symbol detail, leaderboard, stock overview |
| `app/funds.py` | `/api/funds` router: portfolio, compare, drivers, fact sheets, X-Ray |
| `app/chat.py` | The Ask AI brain, shared by the website and WhatsApp |
| `app/llm.py` | Multi-provider LLM with fallback chain and per-call telemetry |
| `app/fund_rag.py` | FAISS retrieval over SEC filing chunks, ONNX embeddings |
| `app/factsheet.py` | Fact-sheet generation with enforced numeric provenance |
| `app/portfolio.py` | Portfolio X-Ray maths: pure, no network |
| `app/docs/` | SEC EDGAR fetching and Form N-1A section parsing |
| `app/sources/` | External data adapters (15 modules), each with its own bounded cache |
| `app/jobs.py` | Daily collection, outcome validation, WhatsApp briefs |
| `app/whatsapp/` | Twilio webhook, signature check, account linking |
| `app/alerts.py`, `app/reqmetrics.py` | Threshold alerts; in-memory request telemetry |
| `web/` | Vanilla JS/HTML/CSS. No build step, no framework |

The detail of each feature lives in [`design/`](../design/); how they connect is
in [`data-flow.md`](data-flow.md).

## Runtime

- **Process model.** `uvicorn app.main:app` with no `--workers`, so exactly one
  process. Request handlers are synchronous; concurrency comes from FastAPI's
  thread pool plus explicit `ThreadPoolExecutor`s (feed building, X-Ray inputs,
  outbound fetches).
- **SQLite.** One connection per call, `check_same_thread=False`,
  `PRAGMA foreign_keys = ON`, `journal_mode = WAL`, `busy_timeout = 5000`.
  A process-wide `_write_lock` serialises writers. WAL lets reads proceed while
  the daily job writes.
- **Scheduler.** APScheduler `BackgroundScheduler` started in the app's
  lifespan. Three jobs: the daily run (08:00 local, guarded by `job_lock` so it
  executes once per day), feed-cache warming (every 30 minutes), and threshold
  alerts (hourly). Cadences are settings, not constants.
- **Background work.** Slow operations (fact-sheet ingest, drivers, X-Ray) run as
  FastAPI background tasks and report a `status` the client polls. A per-process
  set tracks what is in flight so repeated polls never queue duplicate work.
- **Embedding concurrency.** `fund_rag._ingest_sem` allows one ingest at a time
  per process, so two simultaneous "add fund" clicks cannot run two encodes and
  starve the web workers.

## Middleware

1. **CORS**, currently `allow_origins=["*"]` (BACKLOG S7).
2. **`SelectiveGZipMiddleware`**: gzips everything except `/api/chat/stream`,
   because gzip buffers and would defeat Server-Sent Events token streaming.
3. **`timing`**: adds `Server-Timing`, logs requests slower than 1 second, and
   feeds the in-memory request-metrics ring buffer.
4. **`track_traffic`**: counts page loads and first-time visitors (cookie) into
   `metrics_daily`. Best effort; never fails a request.

## Caching

Every in-process cache is a **bounded** `TTLCache` or `LRUCache`. A plain `dict`
used as a cache is a defect: it is the cause of the memory growth fixed in
[`adr/0010`](../adr/0010-bounded-caches.md). `tests/test_cache_bounds.py`
enforces it for the service caches. Caches are per-process, so hit rate would
collapse with more than one worker (BACKLOG P2-B).

## Hosting

Railway, built from the `Dockerfile`. Merging to `main` redeploys. One service
with a persistent volume at `/data` holding the database, the FAISS indexes and
the ONNX weights. Railway injects `PORT`.

| Environment variable | Purpose |
|---|---|
| `RECOMMENDATIONS_DB_PATH` | SQLite file; must be on the volume |
| `FUND_INDEX_DIR` | FAISS indexes; must be on the volume or they rebuild on every deploy |
| `FASTEMBED_CACHE_PATH` | ONNX weights; on the volume so they download once |
| `MALLOC_ARENA_MAX=2` | Caps glibc malloc arenas; see [`workflows/memory-investigation.md`](../workflows/memory-investigation.md) |

Cost is dominated by **resident memory**, billed per GB-month. The decision to
stay on Railway rather than move to AWS or Azure is
[`adr/0008`](../adr/0008-stay-on-railway.md).

## Scaling limits

Written down so nobody discovers them in production:

| Limit | Why | Exit |
|---|---|---|
| One process only | SQLite single-writer; per-process caches; in-process scheduler | Postgres, Redis, external scheduler (BACKLOG P2) |
| No scale-to-zero | Needs a persistent volume and a running scheduler | Move state to managed services first |
| Feed returns everything | No pagination | BACKLOG P2-D |
