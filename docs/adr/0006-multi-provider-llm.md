# 6. Multi-provider LLM with graceful degradation

- **Status:** Accepted
- **Date:** 2026-07-15
- **Implemented in:** `ed5ad3a` (fallback chain and diagnosability), `9c92e81`
  (regression fix); `app/llm.py`, `app/chat.py`

## Context

The app runs on free-tier models, whose slugs are renamed, retired and rate-limited
often. A single hard-coded model silently killed the whole AI feature more than
once: a retired slug made every streamed answer 404 and the chat fell back to a
rule answer on every question ("AI unavailable on every question", fixed in
`9c92e81`). The AI feature also has a daily budget, and an outage must not become a
500.

## Decision

- Route by `SUMMARY_PROVIDER` across **OpenRouter, Grok, Gemini and Ollama**, with
  `auto` choosing by which keys exist (OpenRouter first, because its free models
  cost nothing).
- Within OpenRouter, try the configured model, then a chain of known-good free
  models, **filtered against the live model catalogue** so dead slugs are not
  retried.
- If no LLM answers, **degrade**: a deterministic rule answer, then a data
  overview. Never a 500.
- Record every call (provider, model, latency, tokens, outcome) in `llm_calls`, and
  every chat answer's layer in `chat_answers`.

## Consequences

- One provider failing, being renamed or running out of quota does not take the
  feature down.
- The **fallback rate** in `chat_answers` is the real health signal: availability
  metrics alone miss silent degradation to rule answers.
- Behaviour varies by provider. Only OpenRouter streams token by token; others
  return one chunk. Native tool-calling is OpenAI-compatible only (OpenRouter and
  Grok), which constrains any future agent work.
- `AI_DAILY_CALL_BUDGET` defaults to 50, matching OpenRouter's free tier, and is
  enforced only on the fact-sheet paths. The public chat endpoint does not check it
  (BACKLOG S12).

## Alternatives considered

- **One paid provider:** simpler, but cost and a single point of failure.
- **No LLM, rules only:** safe, but the open-ended questions are the point of the
  assistant.
