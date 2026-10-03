# "What if I'd bought?" and the analyst scoreboard

*Code: `app/backtest.py` (maths, orchestration), `app/sources/history.py` (price history),
`app/main.py` (`/api/whatif`, `/api/scoreboard`), `web/app.js` (`_whatIfForm`, `_renderScoreboard`),
`web/styles.css` (`.whatif-*`, `.scoreboard`). Tests: `tests/test_backtest.py`.*

## What it is

Phase 1 of the virtual-trading idea. Nothing is stored and nobody trades: both features replay
past prices.

- **What if I'd bought?** An expander in a stock's detail. Pick an amount and a start date
  (presets: 1, 3, 6 months, 1 year, "since we started tracking"). It shows what that money would
  be worth today, and what the same money in the market index would be worth.
- **Scoreboard** (Leaders page). Every week since recording began, take the 10 stocks with the
  strongest analyst consensus *as it stood that day*, buy them, and measure 30/60/90 days later
  against the index.

## Rules that must not change

These are what make the numbers honest. A change that breaks one makes the feature flattering,
not useful.

- **No look-ahead.** A snapshot's picks use only recommendations dated on or before that day
  (`_picks_as_of`). Today's consensus is never applied to the past.
- **Buy at the next close.** A signal on day D is bought at the first close *after* D, not at
  D's close, because the call could not have been acted on until it was published.
  The what-if buys at the first close on or after the chosen date.
- **Always beside an index.** SPY for the US, NIFTYBEES.NS for India (`BENCHMARKS`). A return
  with no comparison reads as skill in a rising market.
- **Unfinished windows are never scored.** A 90-day result needs 90 days of prices after the
  buy. Thin snapshots (under `MIN_PICKS` priced picks) are skipped, not padded.
- **Every result carries its caveats** (`PRICE_NOTE` and the scoreboard notes): adjusted
  prices, no fees/tax/slippage, overlapping windows, survivorship, hypothetical.
- **Failures are never cached** as data (`history.get_closes`), so a Yahoo outage cannot
  freeze a wrong answer for an hour.

## Endpoints

Both are public and read-only (see `docs/api/openapi.yaml`).

| Route | Behaviour |
|---|---|
| `GET /api/whatif?symbol=&start=&amount=` | `amount` > 0 and ≤ 10,000,000. `start` must be in the past and on or after 2000-01-01. 404 when there is no price history; 422 for every other `WhatIfError` (too recent, bad amount). |
| `GET /api/scoreboard?market=us\|in` | Returns `status: "computing"` on first call and builds in a background thread; the page polls every 5 s (20 tries). `ready` results are cached 6 h. |

Scoreboard build: single-flight (`_SCORE_PENDING` + `_SCORE_LOCK`), cache `_SCORE_CACHE`
(6 h), and a failure backoff `_SCORE_FAILED` (10 min) so a broken upstream is not hammered.
Prices for the whole watched universe come from chunked `yf.download` calls (50 symbols a
chunk, frame released between chunks) to keep resident memory bounded. All caches are
bounded and listed in `tests/test_cache_bounds.py` ([ADR 0010](../adr/0010-bounded-caches.md)).

## Known limits

- **No rate limit** on `/api/whatif`; each uncached symbol/start makes one Yahoo call. Same
  gap as the other public upstream-backed endpoints (security item S12).
- **Unverified in production** (the sandbox cannot reach Yahoo): that adjusted closes behave
  as assumed, that `NIFTYBEES.NS` returns history, and the time and memory of the chunked
  scoreboard download. The numbers seen so far in browser checks came from synthetic prices.
  Check the first real scoreboard against a known stock's chart before trusting it.
- **Short history.** Recording began recently, so early scoreboards have few snapshots and
  often no 90-day tile. The UI says so rather than showing a partial average.

## Decided for Phase 2 (paper account, not built)

Recorded so the next session does not re-ask.

- Users may **add as much virtual money as they like**; each addition is recorded as a
  *deposit*, so returns are computed net of deposits, not flattered by them.
- **Buy and sell only. No shorting.**
- One account **per market** (US / India), so currencies are never mixed.
- **No backdated trades.** Trading is from today; backdating belongs to the what-if.
- **No public user leaderboard** until security items S1 and S4 are closed.
