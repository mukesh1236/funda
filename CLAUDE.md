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

## UI design rules (`web/styles.css`)

The stylesheet once carried **19 font sizes, 21 spacing steps and 13 radii** —
each value picked to look right on one screen and never reconciled with the
rest. That inconsistency is what makes an interface read as machine-assembled
rather than designed, and it accumulates one feature at a time.

The scales are now closed sets, defined as tokens at the top of
`web/styles.css`. **Use a token; do not introduce a new raw value.**

- **Type** — `--fs-micro` 11 / `--fs-meta` 12 / `--fs-body` 13 /
  `--fs-strong` 15 / `--fs-title` 18 / `--fs-stat` 22 / `--fs-hero` 28.
- **Space** — `--sp-1` 2 / `--sp-2` 4 / `--sp-3` 6 / `--sp-4` 8 / `--sp-5` 12 /
  `--sp-6` 16 / `--sp-7` 24 / `--sp-8` 32. Values above 32px are layout
  offsets, not rhythm (e.g. `.main-col`'s 90px bottom pad clears the Ask AI
  button) — leave those alone.
- **Radius** — `--radius-xs` 4 / `--radius-sm` 8 / `--radius` 12 /
  `--radius-pill`.
- **Colour** — always a `:root` token, never a raw hex/rgba. Every token has a light
  value on `:root` and a dark value in **both** dark blocks (`prefers-color-scheme` and
  `[data-theme="dark"]`, kept identical). New colours must pass
  `tests/test_design_tokens.py` (WCAG AA in both themes); it also fails on a raw colour
  outside the token blocks and on light/dark drift.
- **Panels** — a block of content sits in the card the rest of the page uses
  (`background: var(--surface)`, `1px solid var(--border)`, `border-radius: var(--radius)`,
  `box-shadow: var(--shadow)`). A panel floating bare beside carded siblings reads as
  unfinished. Legacy `--bg-glass` still works as an alias.
- **Icons** — from the inline SVG sprite in `web/index.html` (`icon('name')` in JS). No
  emoji or text glyphs as icons in new UI. Add a new symbol to the sprite (Lucide, ISC).
- **Gain / loss** — never colour alone; pair it with an arrow, sign or word.

Adding a step to a scale is a design decision — make it in the token block with
a reason, not inline. Audit any time (should print only the values above):

```sh
awk '/^:root/{r=1} r&&/^}/{r=0;next} !r' web/styles.css \
  | grep -oE '(font-size|border-radius|padding|margin|gap)[a-z-]*:[^;}]*' \
  | grep -oE '[0-9.]+px' | sort -gu | tr '\n' ' '
```

Judgement still beats the scale: a value sitting between two steps should round
to whichever keeps the layout intact — nav items went to 13px, not 15px,
because "Coverage & Leaders" wraps in the 218px rail at 15px. Check the browser,
don't just check the number.

## Knowledge base (`docs/`)

Look here before searching the code, and **update it in the same change** as the code it
describes. Index: [`docs/README.md`](docs/README.md). For a narrative tour,
[`HANDOFF.md`](HANDOFF.md).

| You are about to... | Read first |
|---|---|
| Touch a feature | its file in `docs/design/` (rules that must not change, edge cases) |
| Change the database | `docs/schemas/entities.md`, then `docs/workflows/add-a-column.md` |
| Add or change an API status or `source` value | `docs/schemas/error-codes.md` |
| Add a route | regenerate `docs/api/openapi.yaml` (below) |
| Pick a technology or reverse a past decision | `docs/adr/` (do not rewrite an accepted ADR; supersede it) |
| Deploy, roll back, or chase memory/cost | `docs/workflows/` |
| Touch auth, secrets, or anything exposed publicly | `docs/security/policy.md` |

**Generated, never hand-edited:** `docs/api/openapi.yaml` (`python scripts/export_openapi.py`)
and the table reference in `docs/schemas/entities.md` (`python scripts/export_schema_doc.py`).
`tests/test_docs_in_sync.py` fails if a route or table is added without regenerating, and
if a relative link in the docs stops resolving.

**Keeping it honest:** a new ADR for any decision that was measured or argued over; a runbook
for anything operational done twice; mark claims you could not verify (the sandbox cannot reach
`sec.gov`) as unverified rather than writing them as fact.

**The repository is public.** Do not put attack mechanics, credentials, or infrastructure IDs
in `docs/`, `BACKLOG.md`, or commit messages. Reference security items by ID and severity.

## Project shape (quick orientation)

- FastAPI backend (`app/`), vanilla JS/HTML/CSS frontend (`web/`), SQLite
  (`app/store.py`), APScheduler jobs (`app/jobs.py`).
- Ask AI brain: `app/chat.py::answer_question` (+ `answer_question_stream`) —
  shared by the website chat and the WhatsApp bot. LLM via `app/llm.py`
  (OpenRouter/Gemini/Grok/Ollama); degrades gracefully to rule/overview.
- Bump the `?v=` asset version in `web/index.html` when changing `web/app.js`
  or `web/styles.css` so browsers don't serve stale cached assets.
- Deploy: merging to `main` triggers a Railway redeploy.
