# Load test: how many signed-in users can the app serve?

*Tool: `scripts/loadtest.py` (asyncio + httpx, no new dependency). Tests: `tests/test_loadtest_script.py`.*

## What it measures

Each virtual user signs in, then loops the calls the web app makes: auth check, themes, feed,
leaders, scoreboard, one to three stocks (the instant half, then the extras), search, watchlist,
and now and then the digest and a what-if, with think time between steps. Concurrency is raised
in steps. A step **passes** when read p95 latency is under 1.5 s, sign-in p95 under 4.5 s and
errors under 1 %. The answer is the highest passing step. A 404/422 (unknown ticker, bad date) is
the app answering correctly; 5xx, 429, 401/403 and timeouts are failures.

```sh
python scripts/loadtest.py --base-url http://127.0.0.1:8000 --no-upstream \
    --steps 50,100,200,400 --step-seconds 90 --ramp-seconds 45 --think 3
```

| Flag | Use |
|---|---|
| `--ramp-seconds` | How the sign-ins are spread. Short = a login stampede (everyone arrives at once); long = steady arrivals. Run both: they answer different questions |
| `--think` | Mean seconds between actions. Real people pause 6 to 10 s; 3 s is a heavy user |
| `--no-upstream` | Skip calls that need Yahoo or an LLM. Use it from a machine with no internet, or to measure the app itself |
| `--chat` | Adds Ask AI. **Spends OpenRouter credit**, off by default |
| `--allow-remote` | Required for any non-local target |

## Rules

- **Do not point it at production by default.** Railway bills CPU and memory, Yahoo and OpenRouter
  quotas are shared with real users, a restart or memory spike disturbs the memory check-ins
  ([`memory-investigation.md`](memory-investigation.md)), and there is no rate limit
  (BACKLOG S12), so a test is indistinguishable from abuse. Use a staging copy of the service
  (own volume, own DB), or a quiet hour on production with a small `--steps`.
- The script **creates accounts** `loadtest-<run>-<n>@loadtest.example.com`. There is no
  delete-user route: remove them from the admin page or the DB afterwards, and never run it against
  a database you cannot clean.
- The first request after a quiet spell pays a cold upstream fetch. The script times that once as a
  warm-up and keeps it out of the steps.

## Findings from the first run (6 Oct 2026, sandbox)

Local app, one uvicorn worker (as deployed), demo database, 4-core sandbox, **no internet**
(so `--no-upstream`). These are *app-only* numbers on different hardware from Railway:
use them for the shape, not as a capacity promise. **Not yet run against Railway.**

| Scenario | Result |
|---|---|
| Steady arrivals, think 3 s | **200 users pass** (about 59 requests/s, read p95 42 ms). 400 users fail: throughput stays at about 60 requests/s, so everything queues (me 20 s, login 18 s) |
| Login stampede (users arrive within 3 s) | 25 users fine; **50 users: login p95 3.4 s; 100 users: 11.6 s**, and reads slow to about 0.8 s for everyone while it lasts |
| Reads | Cached or SQLite reads are cheap (feed 4 ms warm). The cost is not the data |

What this says:

1. **Sign-in is the first bottleneck.** A login is a bcrypt check, about 0.3 s of CPU, and one
   worker does about 3 a second. A burst of sign-ins (a marketing push, the morning open, a
   restart after which every open tab reloads and re-authenticates) queues behind that and drags
   every other request with it. Spread arrivals, lower the bcrypt cost only with a security review,
   or run more than one worker.
2. **One worker saturates near 60 requests/s.** In users that is about 200 at a 3 s think time, and
   an estimated 400 to 600 at realistic 6 to 10 s pauses (estimate, not measured).
3. **Unmeasured here:** the Yahoo-bound calls (stock extras, search, digest, what-if), the LLM,
   and the cache-expiry moment. When the 30-minute feed cache expires, requests that arrive during
   the rebuild each wait for a cold fetch; in the sandbox that fetch hung for 20 s and failed some
   requests in the 100-user step. Whether production stampedes the same way is **unverified**:
   check whether the feed build is single-flight before trusting a high number.

## Reading the result

- `PASS`/`FAIL` per step, then *Highest step that stayed seamless*. The first failing step stops the
  run, because higher ones can only be worse.
- `slowest` names the three worst endpoints by p95: fix those, not the average.
- `--json FILE` keeps the per-step numbers for a before/after comparison. If a result is worth
  keeping, record it here with its date, target and flags, like the invoice readings in
  [`cost-check.md`](cost-check.md).
