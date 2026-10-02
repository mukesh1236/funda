# Memory investigation

Railway bills **resident memory**, so memory *is* the hosting bill (see
[`cost-check.md`](cost-check.md)). This runbook exists because memory was misread
twice. **Judge the slope, never a snapshot.**

## The two traps

1. **A post-restart reading is not steady state.** Memory resets on every deploy.
   0.44 GB right after a restart looked like a win and became 1.97 GB a month later.
2. **The cause is not what the code suggests.** The 4-minute cache warmer *looked*
   like the culprit from reading the source; the invoice showed vCPU at $0.18 against
   $24.01 of memory. Reason from the bill and the metrics.

## Procedure

1. **Make sure you are looking at the right service.** List *all* projects and
   services in the Railway workspace. A duplicate, unreachable service with no domain,
   no volume and no environment variables once sat auto-deploying from `main` and
   billing roughly 40% of the memory total.
2. **Record a baseline before any change:** current, 24-hour average and range.
3. **Pull the metric over several windows** (service metrics, memory in GB) at 6, 12
   and 24 hours, and compare the **floors** (`min`), not the current value or average.
   - A leak raises the floor over time: the 12-hour floor sits below the 6-hour floor.
   - A plateau has identical floors and ceilings across windows. That is the test.
4. **Schedule follow-ups** at about +24 hours and +72 hours after any memory-related
   deploy, and carry the baselines in the reminder so it works from a cold start.
5. **Report the reading plainly, including a null result.**

## Reading the result

| At +24h / +72h | Meaning | Next |
|---|---|---|
| Flat floor, about 0.4 GB | Fixed | Expect the bill to fall next cycle |
| Slower but still rising | Partial; something else also retains | `tracemalloc` before changing anything |
| Still about 32 MB/day | The suspected cause was wrong | Profile; consider a scheduled restart as a hard cap |

Reference values for this app: about **250 MB** right after a restart (162 MB of app plus
about 90 MB of ONNX weights), filling to **about 0.39 GB** over roughly 12 hours as
bounded caches reach their ceilings, then flat.

## Known suspects, in order

1. **An unbounded in-process cache.** Every cache must be a `TTLCache` or `LRUCache`
   ([ADR 0010](../adr/0010-bounded-caches.md)); `tests/test_cache_bounds.py` guards the
   service caches. A plain `dict` used as a cache is the first thing to grep for.
2. **glibc malloc arenas** under threaded pandas/numpy work (yfinance downloads).
   `MALLOC_ARENA_MAX=2` is set in the `Dockerfile`.
3. **FAISS indexes held resident.** `fund_rag._cache` is an `LRUCache(maxsize=3)`;
   each entry is tens of MB.
4. **An embedding backend that holds the whole model.** PyTorch held 717 MB; ONNX
   holds about 90 MB ([ADR 0003](../adr/0003-onnx-embeddings.md)).

## Commands

```sh
# Find caches that are plain dicts (the bug class)
grep -rnE '^_[A-Z_]*CACHE[^=]*= *\{\}' app/

# Confirm the guard still holds
pytest tests/test_cache_bounds.py
```

Railway billing is in **arrears**: the invoice after a fix still contains pre-fix
usage. The clean read is the cycle after that.
