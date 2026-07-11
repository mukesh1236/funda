# Bottleneck Fix Plan

*Code-level performance review of the current application. Written 2026-07-11.
Every finding below cites the actual file/line it lives in.*

## Summary of findings

The app's bottlenecks are almost entirely **network I/O on the request path**
(yfinance/scraper/LLM calls made while the user waits), not CPU or the database.
SQLite is nowhere near its limits at this scale — but it's misconfigured for
concurrency. The fixes below are ordered so that ~80% of the felt latency
disappears in the first phase, which is less than a day of work.

---

## Phase 0 — Measure first (~1 hour)

Add a timing middleware before changing anything, so every fix is verified with
numbers instead of vibes:

- Middleware that logs `method path status duration_ms` and sets a
  `Server-Timing` header (visible in browser dev tools).
- Log any request > 1s at WARNING.
- Optional: a `/api/admin/perf` endpoint summarizing p50/p95 per route from an
  in-memory ring buffer.

**Acceptance:** you can name the p95 of `/api/recommendations/feed` and
`/api/recommendations/{symbol}` before and after each phase.

---

## Phase 1 — Quick wins (~half a day, ~80% of felt latency)

### 1.1 Stop reloading the embedding model on every RAG call — `app/fund_rag.py:46`
`_embed()` constructs `SentenceTransformer(_EMBED_MODEL)` from disk on **every
ingest and every chat query**. That's 1–3s + hundreds of MB of allocation per
question before any actual work happens.

```python
from functools import lru_cache

@lru_cache(maxsize=1)
def _model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer(_EMBED_MODEL)

def _embed(texts):
    return _model().encode(texts, normalize_embeddings=True, show_progress_bar=False)
```
**Impact:** fund chat queries drop from seconds to ~10ms of embedding time.

### 1.2 SQLite concurrency pragmas — `app/store.py`
Connections are opened per call with defaults: rollback journal, no busy
timeout. During the daily job's write bursts, readers can hit
`database is locked` or stall. Set on every connection:

```sql
PRAGMA journal_mode=WAL;       -- readers never block on the writer
PRAGMA synchronous=NORMAL;     -- safe with WAL, much faster commits
PRAGMA busy_timeout=5000;      -- wait instead of erroring on contention
```
Also note the module-level `_write_lock` (`store.py:17`) only serializes writers
**within one process** — with multiple uvicorn workers it does nothing. WAL +
busy_timeout is what actually protects the multi-worker case.
**Impact:** eliminates lock errors and dashboard stalls during the daily run.

### 1.3 Response cache for read endpoints — `app/main.py`
`/api/recommendations/feed`, `/leaderboard`, and `/themes` recompute everything
per request, but the underlying data changes once a day (plus a 5-min day-change
cache). Add a small TTL cache (60s) keyed by `(route, market, theme, days, ...)`
in front of `build_feed` / `build_leaderboard`.
**Impact:** dashboard loads go from "rebuild + possible yfinance call" to a dict
lookup for every visitor after the first each minute.

### 1.4 Fix the day-change cache poisoning bug — `app/service.py:102-133`
`_DAY_CHANGE_CACHE` is keyed by **market only**, but `_batch_day_changes()` is
called with whatever symbol subset the current request's theme filter produced.
A user loading `?theme=AI` first caches *only AI tickers* for the whole market
for 5 minutes — everyone else's feed then shows missing day-changes.
Fix: always fetch the **full market universe** (it's one batched
`yf.download` either way) and let callers pick their symbols out of it.
**Impact:** correctness + fewer cold fetches.

### 1.5 GZip + cache headers — `app/main.py`
One line: `app.add_middleware(GZipMiddleware, minimum_size=1000)`. The feed JSON
and the 1,150-line `app.js` currently ship uncompressed. Add
`Cache-Control: public, max-age=86400` for `/web` static files (cache-busting via
`?v=` already exists).
**Impact:** ~70–85% smaller transfers; visibly faster on mobile.

---

## Phase 2 — Request-path restructuring (~a weekend)

### 2.1 Parallelize `build_detail` — `app/service.py:330-405`
Expanding a stock row currently runs **sequentially**: `get_news` →
`fetch_ownership` → `fetch_fundamentals` → `fetch_insider_trades` → optional LLM
summary (`build_summary`, up to 20s timeout). Worst case is 5 network round-trips
stacked end to end.

- Run the four data fetches concurrently in a `ThreadPoolExecutor` (they're
  independent) — same pattern already used in `build_watchlist` (`service.py:240`).
- Cache the assembled `StockDetailResult` for ~10 minutes per symbol.
- Cache the LLM narrative per (symbol, day) — analyst data changes daily, so
  regenerating prose per click burns latency and free-tier quota for identical
  output.

**Impact:** detail expand drops from 5–25s worst case to ~max(single fetch), and
to ~0 on repeat views.

### 2.2 Move day-changes off the request path
Even with 1.4 fixed, the *first* feed request after each 5-min TTL pays a
multi-second `yf.download` while the user waits. Refresh the cache from a small
background loop (APScheduler interval job, market hours only) so requests only
ever read the cache.
**Impact:** removes the periodic "why is it slow right now" spike entirely.

### 2.3 Parallelize the two remaining serial fan-outs
- `compare_funds` (`app/funds.py:119-151`): metrics A, metrics B, holdings A,
  holdings B — four independent network calls run back-to-back today.
- `list_funds` (`app/funds.py:154-167`): rebuilds metrics per portfolio item
  serially; also persist last-known metrics in the DB so the portfolio renders
  instantly with stale-while-revalidate freshness.

### 2.4 Batch the daily job's slow inner loops — `app/jobs.py`
- `validate()` (`jobs.py:112-134`) fetches prices **one symbol at a time,
  sequentially**. Collect distinct pending symbols first, fetch in one batched
  `yf.download` (like `_batch_day_changes`), then evaluate.
- `collect()` inserts rows one at a time (`jobs.py:100-103`), each opening its
  own connection inside the global lock. Add `store.add_recommendations(batch)`
  wrapping one transaction.
- `_fetch_symbol` (`jobs.py:82-93`) queries the 5 sources sequentially per
  symbol; a per-symbol inner pool (or async) roughly 3–4×'s collection speed.

**Impact:** daily run time drops from many minutes toward ~1 minute; matters
because "Refresh now" runs this same pipeline while users wait.

### 2.5 Protect the shared threadpool
Every endpoint is sync `def`, so all run on the AnyIO threadpool (~40 threads).
A burst of slow yfinance-backed requests (detail, watchlist, funds) can exhaust
it and stall even cheap endpoints. Phases 2.1–2.3 mostly fix the cause; as a
backstop, cap concurrent outbound yfinance work with a semaphore and return
cached/partial data rather than queueing indefinitely.

---

## Phase 3 — Scale-out prerequisites (only when >1 server or >5k users)

These are the BACKLOG P2 items, sequenced; they're not today's bottlenecks:

1. **Postgres + Alembic** (P2-A) — SQLite's single-writer becomes the limit only
   with sustained concurrent writes; WAL buys a long runway first.
2. **Redis** (P2-B) — replaces the per-process `TTLCache`s (`prices.py:10`,
   `profiles.py:16-17`, day-change cache) whose hit rate collapses with multiple
   workers; also backs rate limiting (BACKLOG S1).
3. **External job worker** (P2-C) — move `run_daily` out of the API process so a
   heavy collection can never compete with request threads for CPU/GIL.
4. **Pagination + API versioning** (P2-D) — feed payload is O(universe); fine at
   ~54 tickers, required before growing the universe 10×.
5. **Async httpx clients** for sources — the cleanest long-term fix for the
   threadpool pressure, but a bigger refactor; do it opportunistically when a
   source module is next touched.

---

## What is explicitly NOT a bottleneck (don't spend time here)

- **CPU / consensus math** — `compute_consensus` over ~54 symbols is microseconds.
- **SQLite read throughput** — bulk-load fixes (feed N+1) are already in place
  (`service.py:154-157`, `build_leaderboard`).
- **The vanilla-JS frontend** — payload size (fix 1.5) matters; a framework
  rewrite would not make anything faster.

## Suggested order of execution

| Order | Item | Effort | Expected effect |
|---|---|---|---|
| 1 | 0 — timing middleware | 1h | Baseline numbers |
| 2 | 1.1 — embed-model singleton | 15 min | Chat: seconds → ms |
| 3 | 1.2 — WAL + busy_timeout | 30 min | No more lock stalls |
| 4 | 1.3 — feed/leaderboard TTL cache | 1–2h | Dashboard near-instant |
| 5 | 1.4 — day-change cache fix | 30 min | Correctness + fewer cold fetches |
| 6 | 1.5 — GZip + static caching | 30 min | 70–85% smaller transfers |
| 7 | 2.1 — parallel + cached detail | 3–4h | Expand: 5–25s → <2s |
| 8 | 2.2 — background day-change refresh | 1–2h | Kills periodic spikes |
| 9 | 2.4 — batched validate/collect | 3–4h | Daily job minutes → ~1 min |
| 10 | 2.3 — funds fan-out | 2h | Compare/portfolio 3–4× faster |

Re-run the Phase 0 measurements after each phase and record them in this file.
