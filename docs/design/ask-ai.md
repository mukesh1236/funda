# Ask AI

A grounded assistant that answers questions about the tracked stocks and funds.
One brain, `app/chat.py::answer_question` (and `answer_question_stream`), serves
both the website chat and the WhatsApp bot, so there is exactly one place to fix
behaviour.

## Contract

`answer_question(store, settings, question, market, symbol)` returns
`(answer, error, source)`. `source` is the layer that produced the text, and it is
shown in the UI so a fallback or refusal never looks like a reasoned answer.
Values are in [`schemas/error-codes.md`](../schemas/error-codes.md).

## Order of decisions

1. **Scope guard** (`_in_scope`). A denylist of off-topic and prompt-injection
   phrasing ("write a poem", "ignore previous instructions", "system prompt").
   A hit returns `out-of-scope` **before any work**: no feed build, no LLM call,
   no AI budget spent, and no chance for an injection to reach a model.
2. **Advice guard** (`_is_personal_advice`). "Should I buy...", "what to buy",
   "is it a good time to buy" and similar. Returns the educational disclaimer
   plus, if a stock is named, the analyst consensus for it, deterministically.
   The model is never called, so it cannot be coaxed into a recommendation.
3. **Fund question.** If a fund ticker is detected: structured fund data, passages
   retrieved from the fund's own SEC filing (tagged with form and section so a claim
   can cite a document), and the cached fact-sheet risk bullets, then one LLM call.
4. **General question.** Feed, leaderboard, symbol context and optional web search,
   then one LLM call.
5. **Degrade.** LLM off or unreachable: `_rule_answer` for structured questions,
   then `_overview`. A lesser answer, never a 500.

## Which stock a question is about

`_resolve_symbol` decides once, in this order: a ticker or company-name word found in the
feed; a known alias for names that are not in the legal name (`_NAME_ALIASES`: Facebook
and Instagram are META, Google, Alphabet and YouTube are GOOGL); a live Yahoo search for
anything else; and only then the stock the user has open in the dashboard.

**The open row is a default, not an override.** The page sends it with every question, so
letting it win answered "caterpillar fundamentals" about whichever row was opened last, and
no Caterpillar lookup ran (seen in production). Now: a question that refers back ("its
fundamentals", "this stock") stays on the open stock and skips the name search; any other
question searches for the company it names, with boilerplate ("company", "inc") removed
from the query. While a row is open, a search hit is accepted only if its name shares a
whole word with the question, so leftover words ("good", "going up") cannot hijack it.
The rule fallback follows the same preference, without the live search.
**Whether the stock is tracked is decided by the database**, not by which step found
it, because a search can land on a stock we track.

- Tracked: analyst context (`_fmt_symbol`), plus the market-data overview when the
  question asks for fundamentals or news.
- Not tracked: the market-data overview (price, fundamentals, returns, news).
- Fundamentals are passed in full (`_fmt_fundamentals`: P/E, forward P/E, PEG, EPS,
  revenue growth, margin, ROE, debt/equity, dividend, beta, price/book, 52-week
  range). When the source returns none, the prompt says "unavailable right now"
  rather than leaving the model to call the data absent.
- The rule fallback answers a fundamentals question with the same block, so it works
  without an LLM too.
- Named analyst calls go through `analytics.distinct_named_calls`: Morningstar
  re-records the same star rating daily, so only its newest is kept, and exact
  repeats from other firms are dropped. The same list feeds the detail panel.

## Rules that must not change

- **The two guards run before the model.** Both must stay deterministic and
  model-free.
- **Never give personal investment advice.** The product shows what analysts say;
  the user decides.
- **Every number must come from supplied data.** The fund prompt says so, and the
  fund fact-sheet path additionally enforces it in code
  ([`fund-factsheets.md`](fund-factsheets.md)).
- **Degrade, never break.** No key, no network, no provider: still an answer.
- **Fallback rate is a health signal.** Every answer is recorded in `chat_answers`
  (`store.add_chat_answer`). Availability metrics alone miss silent degradation to
  rule answers, so this is the figure to watch.

## LLM providers

`app/llm.py::generate_narrative` routes by `SUMMARY_PROVIDER`:
`gemini`, `grok`, `openrouter`, `ollama`, or `auto` (OpenRouter, then Grok, then
Gemini, then Ollama, by which keys exist). Any other value (for example `rule`)
disables the LLM and the app runs on rule answers only.

OpenRouter tries the configured model first, then a chain of free models, filtered
against the live model catalogue, because free-tier slugs are renamed and retired
often enough that one hard-coded model would silently kill the feature. Only
OpenRouter streams token by token; other providers deliver one chunk.

Every call, success or failure, is recorded in `llm_calls` (provider, model,
latency, tokens, error) and surfaced at `GET /api/admin/ai-stats`.

## Known gaps

- **`POST /api/chat` has no rate limit and does not check
  `AI_DAILY_CALL_BUDGET`** (BACKLOG S12). Only the fact-sheet paths enforce the
  budget. A scripted loop can exhaust the day's AI for every user.
- **This matters before any agent work.** Tool-calling would multiply LLM calls per
  question by roughly 3 to 8 times, so rate limiting and budget enforcement must
  land first.

## WhatsApp

The same function answers inbound WhatsApp messages; see
[`whatsapp-bot.md`](whatsapp-bot.md). WhatsApp uses the non-streaming path because
a single message cannot be streamed.

## Code

`app/chat.py`, `app/llm.py`; route wiring in `app/main.py` (`/api/chat`,
`/api/chat/stream`); tests in `tests/test_chat.py`, `tests/test_chat_stream.py`.
