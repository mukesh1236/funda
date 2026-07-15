# Session Context — July 2026 working session

*Snapshot of everything built/decided in this Claude Code session, so any
future session (or collaborator) can pick up without re-deriving it.*

## State

- **Branch:** `claude/project-enterprise-analysis-pd53yb` → **PR #1** against `main`
  (https://github.com/mukesh1236/funda/pull/1). All work below is on this PR;
  merging it triggers the Railway deploy (repo is connected to Railway).
- **Tests:** 162 passed, 1 skipped (RAG model download blocked in sandbox).
- **Separate project:** TeamOps (multi-agent engineering team platform,
  rule-based v1) is built + tested in a local `teamops/` repo — NOT pushed to
  GitHub yet; needs a `mukesh1236/teamops` repo created to push to. Design
  record: `docs/TEAMOPS_DESIGN.md`.

## What shipped this session (newest first)

1. **Fund Return Drivers — Pareto 80/20** (`8ea9bed`)
   - WHERE IN THE UI: **Funds (sidebar) → a fund card → "Details ▾" →
     "Return drivers (Pareto 80/20)"** section, with 3M/6M/1Y selector.
   - Complete holdings from SEC N-PORT filings (`app/sources/nport.py`),
     pctVal = true fund weight; CUSIP→ticker via OpenFIGI; fallback to
     yfinance top-10 with honest coverage labels.
   - Math in `app/fund_analytics.py::pareto_drivers` (pure, unit-tested).
   - API: `GET /api/funds/{symbol}/drivers?period=1y` (first run of a big
     fund returns `status:"computing"`, UI polls; 24h cache after).
   - Chat knows the cached headline ("what's driving QQQ?").
2. **OpenRouter LLM provider** (`8ea9bed`) — free open-source models; default
   `deepseek/deepseek-chat-v3.1:free`; `auto` tries OpenRouter first.
3. **Chat LLM-first** (`e46687c`) — rule engine demoted to fallback; reasoning
   prompt; `source` field in ChatResponse + fallback labels in the UI.
4. **Enterprise UI redesign** (`8fec112`) — dark glass theme, sidebar nav,
   global search, SRE dashboard view (demo data), Ask-AI assistant redesign.
   Design record: `docs/UI_REDESIGN.md`.
5. **Performance + chatbot bug fixes** (`acc4e60`) — WAL, response caching,
   parallelized detail/funds/jobs, day-change cache poisoning fix, grok
   provider bug, fund-ticker false positives. Plan: `docs/BOTTLENECK_FIX_PLAN.md`.
6. **Strategy docs** — `docs/ENTERPRISE_ANALYSIS.md` (career/income roadmap),
   `docs/TEAMOPS_DESIGN.md`.

## Railway variables still to set (before/at merge)

Required: `SUMMARY_PROVIDER=auto`, `OPENROUTER_API_KEY`,
`EDGAR_USER_AGENT="AlphaFunds/1.0 (your-email)"`.
Verify set: `SESSION_SECRET`, `ADMIN_EMAIL`.
Optional: `OPENFIGI_API_KEY`, `GEMINI_API_KEY` (fallback), `OPENROUTER_MODEL`.
Check live config at `/api/health` (`llm` block).

## Open threads / next steps

- Merge PR #1 → Railway deploys; then set the env vars above.
- Create `mukesh1236/teamops` repo so TeamOps can be pushed.
- SRE Dashboard tab in funda runs on demo data until TeamOps is deployed
  somewhere funda can reach (it consumes TeamOps' `/api/dashboard/sre` shape).
- From the enterprise roadmap, highest-value next builds: analyst accuracy
  engine (F4), LLM eval harness, weekly auto-report for distribution.
