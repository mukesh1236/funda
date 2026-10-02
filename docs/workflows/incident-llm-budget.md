# Incident: AI answers degrade or the daily AI budget is spent

## Symptoms

- Chat answers are all labelled `rule` or `overview` rather than `llm`.
- Fact-sheet Q&A says the daily AI budget is used up (`budget_exhausted`), or the
  summary shows only the fund's key numbers.
- An alert: budget burn at or above **80%**, success rate below **90%**, or 5xx rate at
  or above **5%** (`app/alerts.py`, sent to `ALERT_WEBHOOK_URL`).

## Triage

1. **`GET /api/health`**: under `llm`, the provider and which keys are set, and
   `last_error` (why the most recent LLM call failed; `null` means it succeeded; for
   example an auth error or an exhausted free-model chain).
2. **`GET /api/admin/ai-stats`** (admin): calls and tokens today, success rate, latency,
   per-model breakdown, recent failures.
3. **`GET /api/admin/sre-metrics`** (admin): `ai_budget` burn, and `chat_sources`, the
   mix of `llm` / `rule` / `overview`. A rising non-`llm` share is the fallback rate: the
   best single "is the AI healthy" signal.

## Is it organic or abuse?

`POST /api/chat` is public, unauthenticated, rate-unlimited, and does **not** check the
budget (BACKLOG S12). A scripted loop can spend the whole day's budget for everyone.
Look at `requests` in `sre-metrics` (hourly request counts and the slowest endpoints): a
spike on `/api/chat` with no matching user growth is not organic.

## Mitigate

| Action | Effect |
|---|---|
| Raise `AI_DAILY_CALL_BUDGET` | More headroom on the fact-sheet paths only; chat ignores it |
| Change `SUMMARY_PROVIDER` or the model | Route around a failing or exhausted provider |
| Set `SUMMARY_PROVIDER=rule` | Stops all LLM spend; the app answers from rules and data only |
| Wait for the day to roll | The budget counts calls today (`llm_calls`) |

Changing a Railway variable normally triggers a redeploy.

## Fact-sheet behaviour while the budget is spent

Fact-sheet LLM calls stop at **80%** of the budget (`factsheet_llm_reserve_pct = 0.2`),
deliberately leaving 20% for chat. Summary generation degrades to a numbers-only summary
and the ask box returns `budget_exhausted`. Nothing errors.

## Follow-up

If it was abuse, the real fix is rate limiting `/api/chat` and
`/api/funds/{symbol}/factsheet/ask` and making chat check the budget (BACKLOG S12). The
fact-sheet refresh endpoint already has the pattern to copy: a `TTLCache` keyed per
symbol (`_FS_REFRESH_LIMIT` in `app/funds.py`).
