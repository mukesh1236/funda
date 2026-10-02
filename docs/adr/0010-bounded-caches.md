# 10. Every in-process cache must be bounded

- **Status:** Accepted
- **Date:** 2026-09-30
- **Implemented in:** `c605b9d`; earlier instance `33e9644`; `tests/test_cache_bounds.py`

## Context

Production memory reset on every deploy and then climbed steadily: **0.44 GB**
immediately after the 31 August deploy, **1.18 GB** five days later, **1.97 GB** on
30 September, roughly 32 MB a day. That growth is why the hosting bill stayed at about
$24 even after [ADR 0003](0003-onnx-embeddings.md) removed 717 MB.

Every cache in the codebase was a bounded `TTLCache` or `LRUCache` (25 of them)
**except three plain dicts in `app/service.py`**: `_DAY_CHANGE_CACHE`, `_DETAIL_CACHE`
and `_OVERVIEW_CACHE`. All three checked their TTL on *read* but never deleted expired
entries, so they only ever grew. `_DAY_CHANGE_CACHE` was the worst: it is keyed by a
`frozenset` of symbols, so every distinct ticker combination became a permanent key,
and the cache warmer alone created about 96 a day.

The same bug class had already shipped once: `fund_rag` carries a comment about "the
previous unbounded dict grew for the process lifetime".

## Decision

Every in-process cache is a bounded `TTLCache` or `LRUCache`; a plain `dict` used as a
cache is a defect. Also: `MALLOC_ARENA_MAX=2` in the `Dockerfile` (glibc gives each
thread its own malloc arena and does not return freed arena memory to the OS), and the
FAISS index cache reduced from 8 entries to 3.

## Consequences

- `tests/test_cache_bounds.py` fails if one of these caches stops being a bounded
  container, fails to evict past its ceiling, or if a module-level `_CACHE` in
  `service.py` becomes a plain dict again.
- The read guard on these caches is `if cached is not None`, not `if cached:`,
  because `_DAY_CHANGE_CACHE` legitimately stores `{}` when yfinance returns nothing
  and a truthiness check would re-fetch on every request.
- **Measured result.** Fresh after the 30 September deploy: 0.250 GB. At +24 hours the
  6-hour and 12-hour windows had **identical floors and ceilings (0.3844 to 0.3994
  GB)**, which a 32 MB/day leak would have contradicted. Implied cost about $5 a
  month, against $24.45. A +72-hour confirmation was scheduled; see
  [`workflows/memory-investigation.md`](../workflows/memory-investigation.md).
- Honest sizing at the time of the fix: the three dicts were worth tens of MB, not
  the roughly 800 MB observed, and the larger share was most likely allocator
  behaviour. The claim was kept narrow until the slope was measured.
- Caches are still per process (BACKLOG P2-B).

## Alternatives considered

- **Scheduled restart as a hard cap:** pragmatic and kept in reserve, but it hides a
  leak rather than fixing it.
- **Profile with `tracemalloc` first:** the right next step if the slope had persisted;
  unnecessary once the measured slope went flat.
