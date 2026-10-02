# "Why analysts recommend it": how the summary is built

*Code: `app/summarize.py`, `app/service.py` (`build_detail_core`, `build_detail_extras`),
`web/app.js` (`toggleExpand`, `renderDetail`).*

## What it is

The block at the top of an opened stock row: a one-line headline, a few reasons, and
optionally a short prose narrative. It is **rules over data already in our database**; no
model is involved unless `SUMMARY_PROVIDER` is changed from its default of `rule`.

## How it is computed

`build_rule_summary(detail)` is deterministic:

- **Stance** (`_stance`): net score (buy minus sell) at or below zero with more sells than
  buys is "Bearish lean" or "Strongly bearish"; exactly zero is "Mixed / hold"; `buy >= 3 x
  sell` and `buy >= 60%` of ratings is "Strongly bullish"; otherwise "Bullish lean".
- **Reasons**, in this order: the buy/hold/sell counts and net score; average price target
  against the current price (percent upside or downside); how many named firms recently
  raised or cut a target (matched on the words "raise", "lower", "cut" in the analyst note)
  and one example; the segments the stock belongs to; recurring themes in recent
  headlines (keyword match: AI, chips, cloud, earnings, demand, ...); "target already
  reached" when the outcome tracker says so; the target-hit confidence label and rationale.
- **Headline:** stance, "N/M rate Buy", and "~X% to target" when a price is known.
- **Narrative** (only when `SUMMARY_PROVIDER` is not `rule`): 3-4 sentences from a prompt
  built out of the same facts plus headlines. Best-effort with a 20 s timeout; on failure
  the rule summary stands.

## Two halves, served separately

Opening a row used to wait on one request that also fetched news, ownership, fundamentals and
insider trades from Yahoo and SEC. The detail is now served in two parts and drawn as each
arrives:

| Part | Endpoint | Source | Contents |
|---|---|---|---|
| core | `GET /api/recommendations/{symbol}/core` | SQLite only | consensus, analyst calls, rule summary |
| extras | `GET /api/recommendations/{symbol}/extras` | network, cached 10 min | fundamentals, ownership, news, insider trades, and a refreshed summary |

`GET /api/recommendations/{symbol}` still returns the whole detail (core plus extras joined)
for callers that need it; the two paths share code, and a test asserts they agree.

**Rules that must not change**

- Core makes **no network call and no LLM call**. A test patches every fetcher to raise.
- Extras are served only for symbols we track, so the endpoint is not a general proxy to
  Yahoo or SEC.
- The extras summary is `null` unless news themes or a narrative changed it; the UI then
  leaves the instant summary alone.
- An extras failure is local: the core stays usable and the slow sections show a Retry. The
  extras request is started concurrently with core and must never reject unhandled, or the
  global handler replaces the page with an error banner.
- `_EXTRAS_CACHE` is bounded ([ADR 0010](../adr/0010-bounded-caches.md)) and cleared by
  "Refresh now" together with the detail cache.

## Known limits

- The summary is memoised per symbol per day (`_SUMMARY_CACHE`), so it does not change within
  a day even if ratings do.
- The first opener of a stock after a deploy or quiet spell still waits for the extras
  (shown as placeholders now, not a blank panel). Persisting extras overnight is a separate,
  not yet made, decision.
