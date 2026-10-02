# Data flow

How data moves for each path that matters. Each flow names the function that owns
the step, so you can jump straight to it. Behavioural rules for each feature are
in [`design/`](../design/).

## 1. Daily collection (scheduled)

```
APScheduler 08:00 ─► jobs.run_daily
   ├─ store.claim_daily_job(today)     already ran today? ─► return {skipped: true}
   ├─ collect()        every configured source ─► store.add_recommendation (append-only)
   ├─ validate()       pending recs vs current price ─► analytics.evaluate_outcome ─► outcomes
   ├─ refresh_profiles()
   ├─ build_feed(days=1) ─► digest ─► notifier.send(digest)
   └─ send_whatsapp_briefs()           opted-in users only; one failure never aborts the rest
```

`claim_daily_job` uses the `job_lock` table, so the run is single-shot per day
even if more than one process starts a scheduler. Each stage after the claim is
wrapped so a delivery failure cannot fail the run.

## 2. Reading the feed

```
GET /api/recommendations/feed
   └─ service.build_feed(store, days, market)
        ├─ store: recommendations in window ─► analytics.compute_consensus
        ├─ _batch_day_changes(symbols)      yfinance batch, cached 5 min per symbol-set
        └─ _RESPONSE_CACHE (60 s)           shared across users; the leaderboard uses it too
```

"Refresh now" (`POST /api/recommendations/refresh`) clears the response, detail and
overview caches so newly collected data shows immediately.

The scheduler warms the day-change cache every 30 minutes so most requests skip
the cold yfinance download. The first request after a quiet spell pays for it.

## 3. Fund fact sheet (background and polled)

```
GET /api/funds/{sym}/factsheet           always 200
   ├─ summary already stored for (sym, filing, schema version)? ─► return it
   ├─ marked "unavailable" within the last 6 h?               ─► return that, do not retry
   └─ else queue funds._factsheet_bg once (guarded by _FS_PENDING) and report progress

_factsheet_bg   status row is updated at each step so the UI can say what is happening
   fetching    docs.get_fetcher ─► find_documents (497K, then 497, then 485BPOS)
   parsing     fetcher.fetch(ref): validates the filing belongs to THIS fund; on a
               combined filing, locate_fund_span isolates the series or refuses
   (cache hit) stored summary for this accession ─► ready
   indexing    fund_rag.chunks_from_document ─► build_index (ONNX embeddings ─► FAISS)
   summarizing factsheet.generate_summary ─► numbers checked against the FACTS block
   ready       store.save_factsheet
```

If a candidate filing fails to fetch, the next one down the priority list is
tried. If none can be matched to the specific fund, the status is `unavailable`:
**no fact sheet is better than a confident summary of a different fund.**

**Asking a question** (`POST /api/funds/{sym}/factsheet/ask`):

```
advice guard ─► scope guard ─► fund_rag.retrieve (score floor)  ── nothing? ─► "no-context"
   ─► budget_ok?  no ─► "budget_exhausted"
   ─► LLM with FACTS + numbered excerpts
   ─► find_unsupported_numbers(answer)  any number not in FACTS/excerpts ─► "unverifiable"
   ─► answer + citations
```

## 4. Ask AI (chat)

```
POST /api/chat  (or /api/chat/stream, SSE)  ◄── same brain as the WhatsApp bot
   chat.answer_question
   1. _in_scope()            denylist hit ─► out-of-scope        (model never called)
   2. _is_personal_advice()  advice ask   ─► advice-declined     (model never called)
   3. fund ticker in question?  fund context + filing excerpts + cached fact sheet ─► LLM
   4. build_feed + leaderboard + symbol context (+ web search) ─► LLM
   5. LLM unavailable ─► _rule_answer ─► _overview     (a lesser answer, never a 500)
   store.add_chat_answer(source)       feeds the fallback-rate SLO
```

Streaming yields `{"delta": ...}` chunks then one `{"done": true, "source": ...}`.
Only OpenRouter streams token by token today; other providers fall back to one
chunk.

## 5. Portfolio X-Ray (background and polled)

```
GET /api/funds/portfolio/xray
   ├─ cached for this user (6 h)?        ─► status "ready"
   ├─ user has no funds?                 ─► status "empty"
   └─ else queue funds._compute_xray_bg once ─► status "computing"

_compute_xray_bg
   _xray_inputs(user)   per fund, in parallel:
        _load_all_holdings: stored ─► fresh SEC N-PORT ─► yfinance top-10 (partial)
        get_fund_info ─► expense ratio
        funds with no readable holdings are collected as `excluded`, never dropped
   portfolio.build_xray(inputs, excluded)    pure maths
   ─► cached per user
```

The cached X-Ray is invalidated whenever the portfolio changes: a fund is added
or removed, or an amount is set (`add_fund`, `remove_fund`,
`PATCH /{sym}/amount`).

## 6. WhatsApp inbound

```
Twilio POST /api/whatsapp/webhook   (signature verified when the auth token is set)
   1. message is a 6-digit code ─► bind phone to account ─► confirm
   2. STOP / UNSUBSCRIBE / CANCEL / END / QUIT ─► opt out
   3. phone not linked          ─► tell them how to link
   4. otherwise                 ─► answer_question() ─► TwiML reply
                                   per-phone limit: 20 messages / 60 s
```

## Why background-and-poll

Fetching a prospectus or an N-PORT filing takes tens of seconds, far longer than a
request should hold. Every slow path returns `200` with a `status` and the client
polls (capped, so a failed job cannot spin forever). Reuse this pattern rather
than inventing another; [`schemas/error-codes.md`](../schemas/error-codes.md)
lists the status values.
