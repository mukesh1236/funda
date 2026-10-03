# Fund fact sheets

Turns a fund's own SEC filing into a plain-English summary and lets a user ask
questions about it, with citations. It exists because most people cannot read a
prospectus. It also replaces a retrieval layer that indexed the app's *own
generated text* instead of documents, so "grounded in the filing" was not true.

## What the user gets

Six answers to the questions a first-time investor has:

| Section | Source |
|---|---|
| What you're buying | Objective and strategy from the filing |
| What it costs | Expense ratio from structured data; waiver, loads and turnover from the fee table |
| What it holds | Holdings data (SEC N-PORT) |
| How it's done | Returns from structured data, always with "past performance does not predict future results" |
| What could go wrong | Principal risks from the filing, translated to plain language |
| Who it suits, and who it doesn't | General suitability only, never advice |

Plus a question box that answers from the filing with numbered citations.

## Pipeline

See [`architecture/data-flow.md`](../architecture/data-flow.md) §3 for the flow.
Components: `app/docs/edgar.py` (find and fetch), `app/docs/parse.py` (Form N-1A
sections), `app/fund_rag.py` (chunk, embed, retrieve), `app/factsheet.py`
(summary), `app/funds.py` (routes and the background worker).

**Which filing.** Best first: `497K` (Summary Prospectus, 4 to 10 pages; Form
N-1A Items 2 to 8 mandate exactly the sections a retail reader needs), then `497`
(accepted only if it has at least 3 recognisable sections), then `485BPOS` (full
prospectus, 50 to 200 pages, usually combined across every series in the trust).
`N-CSR` supplies manager commentary and is additive, never primary. At most 6
candidates are fetched per fund.

**Sections** are found by the headings Form N-1A prescribes, which is what makes
parsing tractable. Section text budgets feed the model: objective 1,500
characters, strategy 2,500, risks 3,500, fees 2,000, management 800, 12,000 in
total. Risks get the most because they are the least substitutable content.

**Retrieval** (`fund_rag`): ~250-word chunks with 50-word overlap, FAISS
`IndexFlatIP` over normalised `all-MiniLM-L6-v2` vectors (cosine similarity), a
similarity floor of 0.25, and at most 2 chunks from any one section so a long
risk section cannot crowd out the one paragraph about fees. Every chunk carries
form type, filing date, section, heading and URL, which is what lets an answer
cite a document rather than assert something from nowhere.

## Rules that must not change

1. **Numbers come from data; prose comes from documents.** The app knows the
   expense ratio, returns and holdings exactly. They are assembled into a FACTS
   block handed to the model as authoritative.
2. **The model may not do arithmetic, and this is enforced in code.** Every
   number in a summary must appear verbatim in FACTS or an excerpt
   (`factsheet.find_unsupported_numbers`). Bullets that invent one are dropped
   before the user sees them. Small integers 0 to 10 and years are exempt
   because they are used for counting and dating, not as data. A fabricated fee
   figure once shipped in this codebase; a prompt instruction was not enough.
3. **A wrong-fund summary is the worst possible failure.** `locate_fund_span`
   returns `None` unless it can *prove* which span of a filing belongs to the
   requested fund. A document is treated as combined when it names more than one
   distinct ticker or fund heading; the span is then anchored on our ticker (or
   fund name) and must exceed 500 characters. If it cannot be proven the status is
   `unavailable` ("could not be matched to this specific fund"), not a guess.
4. **Free-text answers get the same guarantee.** In `/factsheet/ask`, one
   unsupported number invalidates the whole answer (`unverifiable`) rather than
   being cut out, because patching a figure out of a sentence leaves broken
   grammar that misleads in a different way.
5. **Retrieval returns nothing rather than the nearest paragraph.** Below the
   score floor the answer says the filing does not cover the question
   (`no-context`).
6. **Tell the model that excerpts are third-party text** and to ignore any
   instruction inside them (prompt-injection defence for fetched content).
7. **Cache on the filing, not the clock.** Summaries are stored per
   `(symbol, doc_key, schema_ver)`. A fact sheet regenerates only when the fund
   files something new, or when `SUMMARY_SCHEMA_VERSION` is bumped.

## Required configuration: `EDGAR_USER_AGENT`

SEC EDGAR returns **HTTP 403** to any client that does not identify itself with contact
information. Without `EDGAR_USER_AGENT` set (for example `AlphaFunds/1.0 (name@domain)`),
every fetch is refused, so **fact sheets and full N-PORT holdings both fail** and funds fall
back to the yfinance top-10 sample. The app now logs a warning at startup when it is unset.
The built-in default does not count as identifying. Seen in production on 2026-10-02: fact
sheets showed as unavailable and the logs held repeated 403s from `sec.gov`.

## Cost and rate controls

| Control | Value | Why |
|---|---|---|
| `factsheet_llm_reserve_pct` | 0.2 | Fact-sheet LLM calls stop at 80% of `AI_DAILY_CALL_BUDGET`, leaving 20% so fact sheets cannot starve the chat assistant |
| Forced refresh | 1 per symbol per hour (429) | A rebuild costs an EDGAR fetch, a re-embed and an LLM call |
| `unavailable` stickiness | 6 hours | "Unavailable" covers a transient EDGAR outage as well as a genuine mismatch; a permanent flag would let one 503 disable a fund for everyone |
| `factsheet_max_chunks` | 1,500 | Bounds index size and memory |
| `factsheet_max_doc_bytes` | 4,000,000 | Bounds a single download |

When the budget is spent, summary generation degrades to a numbers-only summary
rather than failing; the ask box returns `budget_exhausted`.

## Edge cases

- A fund whose primary filing cannot be fetched: the next candidate in the
  priority chain is tried before giving up.
- ETFs: `fund_identity` resolves ticker to CIK for both mutual funds and ETF
  trusts.
- India: no document service yet. `get_fetcher` reports the market unsupported
  (AMFI ingestion is on the roadmap).

## Verification status

Tests use fixtures; the sandbox this was built in cannot reach `sec.gov`. **The
live EDGAR fetch has only ever been verified in production.** Treat the form
priority and parsing as proven against fixtures, not against every real filing.

## Tests

`tests/test_docs_edgar.py`, `tests/test_docs_parse.py`, `tests/test_fund_rag.py`,
`tests/test_factsheet.py`, `tests/test_factsheet_api.py`.
