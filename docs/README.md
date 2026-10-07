# Knowledge base

Where to look, so nobody has to read everything. For a narrative tour start with
[`../HANDOFF.md`](../HANDOFF.md); for the rules that apply to every change, read
[`../CLAUDE.md`](../CLAUDE.md).

```
docs/
  design/         one file per feature: purpose, flow, rules that must not change, trade-offs
  architecture/   system-level: components, runtime, how data moves
  schemas/        database entities and the error and status codes the API emits
  api/            openapi.yaml (generated from the app)
  security/       policy: controls in place, required settings, open items by ID
  adr/            architecture decision records: why we chose what we chose
  workflows/      runbooks: deploy, rollback, investigations, schema changes
  archive/        stale or off-topic documents kept for history
```

## I want to...

| Question | Read |
|---|---|
| Understand how the system fits together | [`architecture/overview.md`](architecture/overview.md), then [`architecture/data-flow.md`](architecture/data-flow.md) |
| Know what a feature must never do | the feature's file in [`design/`](design/) |
| Add or change a table or column | [`schemas/entities.md`](schemas/entities.md), then [`workflows/add-a-column.md`](workflows/add-a-column.md) |
| Know what an API status code or `source` means | [`schemas/error-codes.md`](schemas/error-codes.md) |
| See every endpoint | [`api/openapi.yaml`](api/openapi.yaml) |
| Know why a technology was chosen | [`adr/`](adr/README.md) |
| Ship, or undo a ship | [`workflows/deploy.md`](workflows/deploy.md), [`workflows/rollback.md`](workflows/rollback.md) |
| Chase memory or cost | [`workflows/memory-investigation.md`](workflows/memory-investigation.md), [`workflows/cost-check.md`](workflows/cost-check.md) |
| Check what is insecure or required in production | [`security/policy.md`](security/policy.md) |
| Handle AI degradation or a spent AI budget | [`workflows/incident-llm-budget.md`](workflows/incident-llm-budget.md) |
| Find how many concurrent users the app serves | [`workflows/load-test.md`](workflows/load-test.md) |

## Features (`design/`)

| Doc | Feature |
|---|---|
| [`fund-factsheets.md`](design/fund-factsheets.md) | Plain-English summaries of SEC filings, with citations |
| [`portfolio-xray.md`](design/portfolio-xray.md) | Overlap, real fee cost and concentration across a user's funds |
| [`ask-ai.md`](design/ask-ai.md) | The grounded assistant, shared by web and WhatsApp |
| [`outcome-scoring.md`](design/outcome-scoring.md) | Recording analyst calls and scoring whether they hit |
| [`whatsapp-bot.md`](design/whatsapp-bot.md) | Phone linking, inbound routing, morning briefs |
| [`market-facts.md`](design/market-facts.md) | Facts shown during network waits, and the toggle |
| [`rag-and-monitoring.md`](design/rag-and-monitoring.md) | Earlier design for retrieval and evaluation (partly superseded by `fund-factsheets.md`) |
| [`ui-redesign.md`](design/ui-redesign.md) | Dashboard layout and palette |
| [`analyst-summary.md`](design/analyst-summary.md) | How "why analysts recommend it" is built, and why it loads in two halves |
| [`product-tour.md`](design/product-tour.md) | The guided tour: steps, when it starts, and the rules that keep it from breaking |
| [`what-if-and-scoreboard.md`](design/what-if-and-scoreboard.md) | "What if I'd bought?" and the analyst scoreboard: honesty rules, caches, and the decided paper-account plan |
| [`bottleneck-fix-plan.md`](design/bottleneck-fix-plan.md) | Performance review and plan |

## Keeping it true

Documentation that drifts is worse than none, so the parts that can be derived are:

| File | How it stays correct |
|---|---|
| `api/openapi.yaml` | Generated: `python scripts/export_openapi.py`. A test fails if a route is added or removed without regenerating |
| The table reference in `schemas/entities.md` | Generated from `_SCHEMA`: `python scripts/export_schema_doc.py`. A test fails if it is stale |
| Every relative link in the docs | A test fails if one stops resolving |

Everything else is hand-written, so **update the relevant doc in the same change as the
code** (see `CLAUDE.md`). If you make a decision that was measured or argued over, add an
ADR. If you do something operational twice, write the runbook.

Facts in these documents were checked against the code when written. Where something is
**unverified** (for example, anything that needs `sec.gov`, which the development sandbox
cannot reach), the document says so.
