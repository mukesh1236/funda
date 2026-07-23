# AlphaFunds — working agreement for Claude

## Engineering workflow (always)

**Review before every push/merge.** Before pushing code or merging to `main`,
review the diff as a senior engineer would — not just "do the tests pass," but:

- **Correctness**: edge cases, error/None paths, wrong conditions, races,
  anything that breaks a real user flow.
- **Regressions**: does this change behavior something else relied on? Check
  callers/callees of anything touched.
- **Reuse & simplicity**: is there an existing helper/pattern this should use
  instead of reinventing? Can it be simpler?
- **Security**: no secrets logged or sent to the LLM; inputs validated;
  auth/scope correct.
- **Tests**: new behavior has a test; the full suite (`pytest tests/`) is green.

State the review findings plainly (even "looks clean"), fix anything real
first, and only then push/merge. When the diff is non-trivial, do this as a
distinct step — don't fold it silently into the commit.

## Project shape (quick orientation)

- FastAPI backend (`app/`), vanilla JS/HTML/CSS frontend (`web/`), SQLite
  (`app/store.py`), APScheduler jobs (`app/jobs.py`).
- Ask AI brain: `app/chat.py::answer_question` (+ `answer_question_stream`) —
  shared by the website chat and the WhatsApp bot. LLM via `app/llm.py`
  (OpenRouter/Gemini/Grok/Ollama); degrades gracefully to rule/overview.
- Bump the `?v=` asset version in `web/index.html` when changing `web/app.js`
  or `web/styles.css` so browsers don't serve stale cached assets.
- Deploy: merging to `main` triggers a Railway redeploy.
