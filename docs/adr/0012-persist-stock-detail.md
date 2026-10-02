# 12. Persist the slow half of a stock's detail, serve it stale-while-revalidate

- **Status:** Accepted
- **Date:** 2026-10-02
- **Implemented in:** this change; builds on the core/extras split ([analyst-summary.md](../design/analyst-summary.md))

## Context

Opening a stock row needs fundamentals, ownership, news and insider trades, which come from
Yahoo and SEC. Even after the detail was split so the "why" text appears at once, the first
reader of a symbol after every deploy or quiet spell still waited on four upstream calls,
because the only cache is in process memory (10 minutes, lost on every restart; Railway
redeploys on each merge to `main`). The instant half measured 3-71 ms; the slow half is
bounded below by the slowest upstream call. Production latency of those calls was not
measurable from the build sandbox, so that figure is unverified.

## Decision

- A table `stock_extras` (one JSON row per symbol, `fetched_at`) holds the slow half. It is
  **derived data**: safe to delete, rebuilt on demand.
- **Serve stale-while-revalidate:** a row under 6 hours old is served as-is; one between 6
  hours and 7 days is served immediately and refreshed on a daemon thread (one at a time per
  symbol); a missing or older row is rebuilt live, as before.
- **The daily job refreshes every tracked symbol** (`refresh_stock_details`, two workers, a
  20-minute wall-clock budget, switchable with `PRECOMPUTE_STOCK_DETAILS`). In steady state
  opening a row is one SQLite read (about 40 ms measured locally, against 1.5 s for the live
  path even with the upstream calls failing fast).
- **Store inputs, not conclusions.** The "why" summary is rebuilt from the *current* ratings on
  every read, so it can never disagree with the consensus beside it when the stored parts are
  hours old. Only the LLM narrative, which costs money, is carried over, and it is reused for
  20 hours so a refresh does not mean a new LLM call.
- **A failed upstream call must not erase good data.** A part that comes back empty keeps its
  stored value, and a result that is entirely empty is not persisted when nothing is held.
- The response carries `as_of`, and the UI says "Fundamentals and news as of ...", because
  the data is stored, not live.

## Consequences

- First open after a deploy is as fast as any other, and the instant part no longer depends on
  Yahoo or SEC being up.
- Cost: about four upstream calls per tracked symbol per day, plus one LLM call each when
  `SUMMARY_PROVIDER` is not `rule`. The tracked count in production was not read here; the
  profile job logged 96 symbols on the day checked.
- Stored news can be up to a day old until someone opens the row, which refreshes it in the
  background once it passes 6 hours; the UI states the time.
- Insider trades are stored but not shown in the opened row. A stored empty list is trusted.
- A new table means the schema doc is regenerated and the table added to `_SCHEMA`; no
  `_migrate` step is needed because `CREATE TABLE IF NOT EXISTS` runs on existing databases
  (tested against a database that predates it).
- A payload that no longer parses (schema drift) is treated as missing and rebuilt, never as
  an error.

## Alternatives considered

- **Keep memory-only caching:** the cold start after each deploy is exactly the problem.
- **Redis or another shared cache:** a new service and bill for ~100 small rows that SQLite
  already holds ([ADR 0001](0001-sqlite-single-node.md)).
- **Precompute only, no background refresh:** simpler, but a stock opened the evening after
  the 08:00 run would show a day-old headline list with nothing to refresh it.
- **Store the finished summary:** it would drift from the live consensus between refreshes.
