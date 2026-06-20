# Analyst Recommendation Tracker

Tracks daily US-stock analyst recommendations, aggregates a **consensus rating**
per stock (counts buys/holds/sells — more buys → higher score), and validates
over time whether each **target price was hit**. View it on a dashboard; get a
daily digest (console now, WhatsApp pluggable later).

Standalone project — no dependency on other apps.

## Dashboard features
- **Thematic segments**: filter the feed by segment — AI, Semiconductors, Finance,
  Green Energy, Data Center, EV, Cloud & Software. Each stock is tagged with the
  segments it belongs to (themes defined in `app/themes.py`).
- **Highlights** strip: 🔥 **Top 5 buzzing stocks** of the day (widest analyst
  coverage), ⬆ strongest buy consensus, ⬇ strongest sell consensus — recomputed for
  the selected segment.
- **Big investors / funds** column: % of the company held by institutions, the
  number of fund/ETF holders, and the institution that most recently increased its
  stake. Opening a stock shows recent buyers and top fund/ETF holders. (Percentages
  are each holder's share of the company, from 13F data via yfinance — not the
  stock's weight inside the fund, which would need full fund portfolios.)
- **Consensus feed**: every tracked stock shown by **company name + ticker**, with
  buy/hold/sell counts, score, average target, **1M / 3M / 6M / 12M price returns**,
  a **target-hit confidence** badge (Low/Medium/High), and hit/miss status.
- **Confidence** estimates the chance the consensus target is reached, blending
  proximity to target, consensus strength, 3-month momentum, and the realized
  hit-rate from accumulated history (a heuristic, not a guarantee — it sharpens as
  more targets resolve over time).
- **Expand any row** to see a **"why analysts recommend it" summary** (consensus
  stance, target upside, recent target raises/cuts, segments, and recurring news
  themes), then **which analysts** recommended it and **their rationale** — firm
  name, grade change, and price-target moves (e.g. "UBS: Buy, PT $275 → $280"),
  plus recent news headlines for context. Set `SUMMARY_PROVIDER=ollama` to add an
  LLM-written prose narrative (falls back to the rule summary if Ollama is down).
- **Leaderboard**: rank by consensus score or realized target hit-rate.
- **Watchlist**: pin any stock to a (named) watchlist group — its price is pinned
  from that day, and the tab shows the pinned price, current price, % change since
  pin, today's move, and a **daily-variation sparkline** of closes since you pinned
  it. Works for any ticker, not just the tracked universe.

Named-analyst detail comes from Yahoo's upgrade/downgrade feed (free), FMP grades
(free key), and Morningstar; it's shown on expand but not double-counted into the
consensus totals (those come from the aggregate sources).

## Data sources (all free)

| Source | Key needed? | What it gives | Reliability |
|--------|-------------|---------------|-------------|
| **Yahoo** (yfinance) | No | Buy/hold/sell counts + price target | ✅ Works out of the box — the default workhorse |
| **Finnhub** | Free key | Recommendation trend + price target | ✅ Reliable with a key |
| **Morningstar** | No (scrape) | Star rating → buy/hold/sell | ⚠️ Best-effort |
| **TipRanks** | No (scrape) | Consensus counts + target | ⚠️ Often returns 403; needs a paid API/proxy to be usable |
| **FMP** | Free key | Named-firm analyst grades | ✅ With a key |

Each source degrades gracefully — if one is blocked or missing a key, the others
still run. **You get real data immediately with zero setup via Yahoo.**

## How it works

```
sources (Yahoo · Finnhub · Morningstar · TipRanks · FMP)
   │  daily job (scripts/run_daily.py or in-process scheduler)
   ▼
SQLite (data/recommendations.db)  ──►  consensus + outcome validation (yfinance prices)
   │
   ▼
FastAPI  ──►  /api/recommendations/...  ──►  static dashboard at /
   └──►  notifier (console | whatsapp-stub)
```

- **Consensus** = `buy_count − sell_count`. Finnhub gives aggregate analyst
  counts per stock; each count contributes its full weight.
- **Target validation**: buy hit when price ≥ target, sell hit when price ≤
  target; otherwise pending until the horizon (default 365d), then missed.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate            # Windows
pip install -r requirements.txt
copy .env.example .env            # then add FINNHUB_API_KEY (free at finnhub.io)
```

## Run

```bash
# Collect + validate once, print a digest:
python -m scripts.run_daily

# Start the API + dashboard (also runs the daily scheduler):
uvicorn app.main:app --reload --port 8100
#   Dashboard:  http://localhost:8100/
#   API docs:   http://localhost:8100/docs
```

Click **Refresh now** on the dashboard to fetch on demand.

## Configuration (.env)

| Key | Default | Meaning |
|-----|---------|---------|
| `YAHOO_ENABLED` | true | Free Yahoo analyst data (no key) |
| `FINNHUB_API_KEY` | — | Free Finnhub key (optional second source) |
| `TIPRANKS_ENABLED` | true | Best-effort TipRanks scrape (often 403) |
| `FMP_API_KEY` | — | Free FMP key for named-firm grades (optional) |
| `TRACKED_UNIVERSE` | union of all themes (~54 stocks) | Comma-separated tickers to track (overrides themes) |
| `MORNINGSTAR_SCRAPE_ENABLED` | true | Best-effort star-rating enrichment |
| `OUTCOME_HORIZON_DAYS` | 365 | Days a target has to be hit before "missed" |
| `NOTIFIER` | console | `console` or `whatsapp` (stub) |
| `DAILY_JOB_HOUR` / `_MINUTE` | 8 / 0 | When the in-process scheduler runs |
| `ENABLE_SCHEDULER` | true | Run the daily job inside the API process |

## WhatsApp (later)

`app/notifications/whatsapp.py` is a stub. Implement `send()` with the Twilio
WhatsApp sandbox or Meta Cloud API and set `NOTIFIER=whatsapp`. See that file's
docstring.

## Tests

```bash
pytest -q
```

Covers consensus math, outcome boundaries (hit/miss/pending/expired), store
dedupe/queries, Finnhub mapping, and Morningstar graceful degradation.
