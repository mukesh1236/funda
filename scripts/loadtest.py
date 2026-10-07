"""Find how many concurrent signed-in users the app serves seamlessly.

Each virtual user logs in, then loops a realistic journey (dashboard, leaders, a stock's
detail in two halves, search, watchlist, digest) with think time between steps, exactly the
calls the web app makes. Concurrency is raised in steps; a step passes when p95 latency and
the error rate stay under the limits. The answer is the highest passing step.

    python scripts/loadtest.py                                    # local app on :8000
    python scripts/loadtest.py --steps 5,10,25 --step-seconds 30
    python scripts/loadtest.py --base-url https://example.up.railway.app --allow-remote

Safety:
  * A non-local target is refused without --allow-remote. Load on production costs real
    money (Railway bills CPU and memory), shares upstream quotas (Yahoo, OpenRouter) with
    real users, and resets the memory watch. Prefer a staging copy.
  * The Ask AI chat is OFF by default: every message spends OpenRouter credit. --chat adds
    it at a low rate.
  * Accounts are created as loadtest-<run>-<n>@loadtest.example.com (the app has no
    delete-user route; remove them from the admin page or the DB afterwards).
"""
import argparse
import asyncio
import json
import random
import statistics
import sys
import time
import uuid
from collections import defaultdict
from urllib.parse import urlparse

import httpx

LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
PASSWORD = "Loadtest-Passw0rd!"
SEARCH_TERMS = ["apple", "micro", "nvid", "tesla", "amaz", "meta", "bank", "pharma"]
FALLBACK_SYMBOLS = ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA"]


class Recorder:
    def __init__(self):
        self.samples = []          # (endpoint, status, ms)

    def add(self, name, status, ms):
        self.samples.append((name, status, ms))


def pct(values, p):
    if not values:
        return 0.0
    values = sorted(values)
    return values[min(len(values) - 1, int(round(p / 100 * (len(values) - 1))))]


async def timed(client, rec, name, method, url, **kw):
    t0 = time.perf_counter()
    try:
        r = await client.request(method, url, **kw)
        status = r.status_code
    except Exception as e:                       # timeouts, resets: count as a failure
        r, status = None, f"ERR:{type(e).__name__}"
    rec.add(name, status, (time.perf_counter() - t0) * 1000)
    return r


def ok(status):
    """Not a failure. A 404/422 is the app answering correctly (unknown ticker, bad date);
    5xx, 429, auth failures and transport errors are failures."""
    return isinstance(status, int) and status < 500 and status not in (401, 403, 429)


async def login(client, rec, email):
    """Sign in, creating the account on first use. Both paths are recorded as "login"
    (register and login each cost one bcrypt check); the expected 409 is not an error."""
    r = await timed(client, rec, "login", "POST", "/api/auth/register",
                    json={"email": email, "password": PASSWORD, "display_name": "Load Test"})
    if r is not None and r.status_code == 409:       # account exists from an earlier step
        rec.samples.pop()
        r = await timed(client, rec, "login", "POST", "/api/auth/login",
                        json={"email": email, "password": PASSWORD})
    return r is not None and r.status_code < 400


async def warm_up(base, args):
    """First hit after a quiet spell pays a cold upstream fetch; time it once, separately,
    so the steady-state numbers are not dominated by it."""
    print("warm-up (cold first requests, not part of the steps):")
    async with httpx.AsyncClient(base_url=base, timeout=120) as client:
        for name, url in (("feed", "/api/recommendations/feed?days=7&market=us"),
                          ("leaders", "/api/recommendations/leaderboard?metric=consensus&limit=25&market=us"),
                          ("scoreboard", "/api/scoreboard?market=us")):
            t0 = time.perf_counter()
            try:
                r = await client.get(url)
                note = r.status_code
            except Exception as e:
                note = type(e).__name__
            print(f"  {name:<11} {note}  {(time.perf_counter() - t0) * 1000:,.0f} ms")


async def journey(client, rec, args, stop_at):
    """One pass through the app, the way a person uses it."""
    think = lambda: asyncio.sleep(random.expovariate(1 / args.think) if args.think else 0)
    await timed(client, rec, "me", "GET", "/api/auth/me")
    await timed(client, rec, "themes", "GET", "/api/themes?market=us")
    r = await timed(client, rec, "feed", "GET", "/api/recommendations/feed?days=7&market=us")
    symbols = FALLBACK_SYMBOLS
    if r is not None and r.status_code == 200:
        try:
            symbols = [s["symbol"] for s in r.json().get("stocks", [])[:30]] or symbols
        except Exception:
            pass
    await think()
    if time.time() > stop_at:
        return
    await timed(client, rec, "leaders", "GET", "/api/recommendations/leaderboard?metric=consensus&limit=25&market=us")
    await timed(client, rec, "scoreboard", "GET", "/api/scoreboard?market=us")
    await think()
    for _ in range(random.randint(1, 3)):        # open a few stocks: instant half, then the rest
        if time.time() > stop_at:
            return
        sym = random.choice(symbols)
        await timed(client, rec, "stock_core", "GET", f"/api/recommendations/{sym}/core")
        if not args.no_upstream:
            await timed(client, rec, "stock_extras", "GET", f"/api/recommendations/{sym}/extras")
        await think()
    if not args.no_upstream:
        await timed(client, rec, "search", "GET", f"/api/search?q={random.choice(SEARCH_TERMS)}&market=us")
    await timed(client, rec, "watchlist", "GET", "/api/watchlist?market=us")
    await think()
    if not args.no_upstream and random.random() < 0.3:
        await timed(client, rec, "digest", "GET", "/api/market/digest?market=us")
    if not args.no_upstream and random.random() < 0.2:
        await timed(client, rec, "whatif", "GET",
                    f"/api/whatif?symbol={random.choice(symbols)}&start=2025-10-01&amount=1000")
    if args.chat and random.random() < 0.2:
        await timed(client, rec, "chat", "POST", "/api/chat",
                    json={"question": "Which stocks have the strongest buy consensus?", "market": "us"})
    await think()


async def virtual_user(base, rec, args, email, stop_at):
    limits = httpx.Limits(max_connections=4)
    async with httpx.AsyncClient(base_url=base, timeout=args.timeout, limits=limits) as client:
        if not await login(client, rec, email):
            return
        while time.time() < stop_at:
            await journey(client, rec, args, stop_at)


async def run_step(base, args, n, run_id):
    rec = Recorder()
    stop_at = time.time() + args.step_seconds
    ramp = args.ramp_seconds if args.ramp_seconds is not None else max(0.0, args.step_seconds * 0.1)

    async def staggered(i):
        await asyncio.sleep(ramp * i / max(1, n))
        await virtual_user(base, rec, args, f"loadtest-{run_id}-{i}@loadtest.example.com", stop_at)

    started = time.time()
    await asyncio.gather(*(staggered(i) for i in range(n)))
    return rec, time.time() - started


def summarise(rec, n, elapsed, args):
    by = defaultdict(list)
    errors = 0
    for name, status, ms in rec.samples:
        by[name].append(ms)
        if not ok(status):
            errors += 1
    total = len(rec.samples)
    reads = [ms for name, st, ms in rec.samples if name != "login" and ok(st)]
    logins = by.get("login", [])
    err_pct = 100 * errors / total if total else 100.0
    p95 = pct(reads, 95)
    login_p95 = pct(logins, 95)
    passed = (total > 0 and err_pct <= args.max_error_pct and p95 <= args.p95_ms
              and login_p95 <= args.p95_ms * 3)
    return {
        "users": n, "requests": total, "rps": round(total / elapsed, 1) if elapsed else 0,
        "error_pct": round(err_pct, 2), "p50_ms": round(pct(reads, 50)), "p95_ms": round(p95),
        "p99_ms": round(pct(reads, 99)), "login_p95_ms": round(login_p95), "passed": passed,
        "slowest": sorted(({"endpoint": k, "p95_ms": round(pct(v, 95)), "n": len(v)} for k, v in by.items()),
                          key=lambda d: -d["p95_ms"])[:3],
        "status_errors": sorted({str(st) for _, st, _ in rec.samples if not ok(st)}),
    }


async def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", default="http://127.0.0.1:8000")
    ap.add_argument("--steps", default="5,10,25,50,100", help="concurrent users per step")
    ap.add_argument("--step-seconds", type=int, default=60)
    ap.add_argument("--ramp-seconds", type=float, default=None,
                    help="spread the users' sign-ins over this long (default 10%% of the step). "
                         "Short = a login stampede; long = steady arrivals")
    ap.add_argument("--think", type=float, default=3.0, help="mean think time between actions (s)")
    ap.add_argument("--p95-ms", type=int, default=1500, help="a step passes if read p95 is under this")
    ap.add_argument("--max-error-pct", type=float, default=1.0)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--no-upstream", action="store_true",
                    help="skip calls that depend on Yahoo/LLM (stock extras, search, digest, what-if) "
                         "to measure the app itself, e.g. from a sandbox with no internet")
    ap.add_argument("--chat", action="store_true", help="include Ask AI (spends OpenRouter credit)")
    ap.add_argument("--allow-remote", action="store_true", help="required for a non-local target")
    ap.add_argument("--json", help="write the per-step results to this file")
    args = ap.parse_args()

    host = urlparse(args.base_url).hostname
    if host not in LOCAL_HOSTS and not args.allow_remote:
        sys.exit(f"Refusing to load-test {host}: pass --allow-remote if you own it and accept the cost "
                 "and the effect on real users (see the module docstring).")

    steps = [int(x) for x in args.steps.split(",") if x.strip()]
    run_id = uuid.uuid4().hex[:6]
    print(f"target {args.base_url}  steps {steps}  {args.step_seconds}s each  think~{args.think}s  "
          f"pass: p95<={args.p95_ms}ms errors<={args.max_error_pct}%  run {run_id}")
    print(f"{'users':>6} {'reqs':>7} {'rps':>6} {'err%':>6} {'p50':>6} {'p95':>6} {'p99':>6} {'login95':>8}  verdict   slowest")
    results, best = [], 0
    await warm_up(args.base_url, args)
    for n in steps:
        rec, elapsed = await run_step(args.base_url, args, n, run_id)
        r = summarise(rec, n, elapsed, args)
        results.append(r)
        slow = ", ".join(f"{d['endpoint']} {d['p95_ms']}ms" for d in r["slowest"])
        print(f"{r['users']:>6} {r['requests']:>7} {r['rps']:>6} {r['error_pct']:>6} {r['p50_ms']:>6} "
              f"{r['p95_ms']:>6} {r['p99_ms']:>6} {r['login_p95_ms']:>8}  {'PASS' if r['passed'] else 'FAIL':<8}  {slow}"
              + (f"  errors:{','.join(r['status_errors'])}" if r['status_errors'] else ""))
        if r["passed"]:
            best = n
        else:
            print("stopping: this step failed, so higher steps would only be worse")
            break
        await asyncio.sleep(2)
    print(f"\nHighest step that stayed seamless: {best} concurrent users" if best
          else "\nNo step passed; try smaller --steps or a larger --p95-ms")
    if args.json:
        with open(args.json, "w") as f:
            json.dump({"target": args.base_url, "args": vars(args), "results": results,
                       "max_seamless_users": best}, f, indent=2)


if __name__ == "__main__":
    asyncio.run(main())
