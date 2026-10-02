# Architecture Decision Records

One short file per significant decision: what forced it, what we chose, and what
it costs. They exist because the *why* behind a choice is exactly what is lost
when the people (or sessions) who made it are gone.

**Conventions**

- Number sequentially; never reuse a number. Copy [`0000-template.md`](0000-template.md).
- **Do not rewrite an accepted record.** If a decision changes, write a new ADR and
  mark the old one `Superseded by NNNN`. History is the point.
- Keep it to one screen where possible. Link to code and commits rather than
  pasting them.
- Record decisions that were *measured or fought for*, not every choice.

| # | Decision | Status |
|---|---|---|
| [0001](0001-sqlite-single-node.md) | SQLite, one node, in-process scheduler | Accepted |
| [0002](0002-raw-sqlite-no-orm.md) | Raw `sqlite3`, no ORM, hand-rolled migrations | Accepted |
| [0003](0003-onnx-embeddings.md) | ONNX `fastembed` instead of `sentence-transformers` | Accepted |
| [0004](0004-sec-edgar-source.md) | SEC EDGAR as the fund fact-sheet source | Accepted |
| [0005](0005-provenance-in-code.md) | Numeric provenance is enforced in code, not prompts | Accepted |
| [0006](0006-multi-provider-llm.md) | Multi-provider LLM with graceful degradation | Accepted |
| [0007](0007-curated-market-facts.md) | Market facts are curated, never LLM-generated | Accepted |
| [0008](0008-stay-on-railway.md) | Stay on Railway rather than move to AWS or Azure | Accepted |
| [0009](0009-closed-ui-scales.md) | The UI uses closed type, space and radius scales | Accepted |
| [0010](0010-bounded-caches.md) | Every in-process cache must be bounded | Accepted |
