# Outcome scoring

The product's core: record what analysts said, then later check whether they were
right. The accumulated, append-only history is the one asset a competitor cannot
backfill without having run for the same period.

## Flow

1. **Collect** (`jobs.collect`, daily): pull recommendations from every configured
   source into `recommendations`. Append-only.
2. **Validate** (`jobs.validate`, daily): for every *pending* recommendation,
   fetch the current price and call `analytics.evaluate_outcome`; write the result
   to `outcomes`. Prices are prefetched in parallel (up to 8 threads) for the
   distinct symbols.
3. **Consensus** (`analytics.compute_consensus`): per-stock buy/hold/sell counts and
   score for the feed.

## Scoring rules (`analytics.evaluate_outcome`)

| Action | Hit when | Otherwise |
|---|---|---|
| `buy` | `current_price >= target_price` | `pending`; `missed` once the horizon elapses |
| `sell` | `current_price <= target_price` | `pending`; `missed` once the horizon elapses |
| `hold` | never (not directionally testable) | `pending`, then `expired` past the horizon |
| any action with no usable target | never | `pending`, then `expired` |

Statuses: `hit`, `missed`, `pending`, `expired`. `missed` is assigned **only** to
directional (buy/sell) calls whose horizon elapsed without hitting the target; a
call that cannot be judged is `expired`, never `missed`. `pct_to_target` is
`(current - target) / target x 100`.

The horizon is `OUTCOME_HORIZON_DAYS` (default **365**).

## Rules that must not change

- **`recommendations` is append-only.** Never update or delete a row. The dedupe
  index (`idx_rec_dedupe`) prevents double-inserts and deliberately treats a
  missing firm as `''`.
- **Do not count unjudgeable calls as misses.** `expired` exists so a hold, or a
  call with no target, cannot drag a firm's hit rate down.
- **Outcomes are terminal.** Only calls with no outcome yet, or still `pending`, are
  re-checked (`store.pending_recommendations`). Once a call is `hit`, `missed` or
  `expired` it is final, so a later fall back below target does not undo a hit.
- **Honesty about measurement.** A hit means the price was at or past target at a
  daily check. It is not a claim about the path the price took between checks,
  and it is checked once a day.

## Concurrency

`jobs.run_daily` claims `job_lock` first (`store.claim_daily_job`), so the job runs
once per day even if more than one process starts a scheduler.

## Where it surfaces

Leaderboard by `hit_rate` (`GET /api/recommendations/leaderboard?metric=hit_rate`),
per-stock outcome history, and the admin stats' resolved hit rate.

## Not built yet

Per-firm accuracy backtesting (BACKLOG F4) is a query plus UI over data that
already exists. Note the market is not empty here: dedicated trackers already
publish analyst accuracy over a much longer history, so the defensible part is
*this* app's own accumulating record and the audience (fund novices), not the
metric itself.

## Code and tests

`app/jobs.py`, `app/analytics.py`, `app/store.py`; `tests/test_analytics.py`.
