# TeamOps — Multi-Agent Software Engineering Team Platform

## Context

The user wants a system that behaves like a real software engineering team: 8 role-based
agents (Engineering Manager, 2 Developers, QA Tester, Product Analyst, Performance
Engineer, SRE Engineer, DevOps Engineer) that collaborate on tickets and production
incidents, with admin-only configuration, automated hourly/daily operations, an SRE
dashboard, and fully logged workflows.

Decisions made with the user:
- **Standalone project** (new codebase, not inside funda) — but designed as a
  **pluggable monitoring platform**: any target project (funda first) is registered
  via connectors, so the agent team can watch multiple apps.
- **Rule-based agents first, LLM later** — deterministic decision tables and state
  machines now, behind a `Brain` interface so an LLM brain can be swapped in per-role later.
- **Simulated/demo data first** — a built-in ticket store and synthetic metrics
  generator make it demo-able day one; real connectors (funda health, Sentry webhook)
  ship alongside and use the same interfaces.

Stack: FastAPI + SQLite + APScheduler + vanilla-JS dashboard — the same stack as funda,
so patterns for auth (bcrypt + signed session cookies + `require_admin`), store,
scheduler, and notifications can be ported directly from `funda/app/auth.py`,
`store.py`, `main.py`, `notifications/`.

## Step 0 — finish in-flight funda work (before starting TeamOps)

The funda bottleneck fixes + chatbot fixes are implemented locally but NOT yet
tested/committed (interrupted mid-task). First action: run `pytest`, fix any failures,
commit and push to `claude/project-enterprise-analysis-pd53yb`. The container is
ephemeral — uncommitted work can be lost.

## Repo location

New project directory `teamops/` created locally and pushed to a new GitHub repo
(`mukesh1236/teamops`) if repo creation is permitted in this session; otherwise
delivered on the funda branch under `teamops/` as a self-contained folder the user can
split out (`git filter-repo` note in README).

## Architecture

```
                        ┌────────────────────────────────────────────┐
                        │                TeamOps                     │
 Admin UI ──────────────►  FastAPI (RBAC: admin | user)              │
 User UI  ──────────────►  ├─ /api/admin/*  agent CRUD, assign,      │
                        │  │                overrides, triggers      │
                        │  ├─ /api/tickets, /api/incidents (user)    │
                        │  ├─ /api/dashboard/sre  (metrics, SLOs)    │
                        │  └─ /api/webhooks/sentry                   │
                        │                                            │
                        │  Orchestrator ── Workflow state machines   │
                        │      │            (ticket, incident)       │
                        │      ▼                                     │
                        │  Agent registry (8 role agents, YAML+DB)   │
                        │      each = Role config + RuleBrain        │
                        │      (Brain protocol → LLMBrain later)     │
                        │                                            │
                        │  Scheduler (APScheduler)                   │
                        │   ├─ ops tick (hourly): poll sources,      │
                        │   │   assign, escalate, notify             │
                        │   ├─ monitor tick (60s): connectors →      │
                        │   │   metrics store → detection rules      │
                        │   └─ daily routines per agent (standup)    │
                        │                                            │
                        │  Connector layer (the plugin boundary)     │
                        │   ├─ TicketSource:  builtin | github(later)│
                        │   └─ MonitorSource: simulated | http_health│
                        │                     | sentry_webhook       │
                        └───────┬────────────────────┬───────────────┘
                                │ polls /api/health  │ Sentry webhook
                                ▼                    ▼
                             funda (project #1)   any future project
```

### How it connects to funda (the plugin model)
A `projects` table holds registered targets; each project has N connector configs:
- `http_health` connector → polls funda `GET /api/health` every 60s: up/down,
  `last_updated` (daily-job freshness), source availability; measures request latency
  itself and reads funda's `Server-Timing` header.
- `sentry_webhook` connector → funda's Sentry alert rule posts to
  `POST /api/webhooks/sentry` → error events become incident candidates.
- Funda-specific SLO rules in project config: e.g. `daily_job_fresh_by: "09:00"`,
  `p95_latency_ms: 2000`, `availability: 99.5%`.
- Zero changes required inside funda for v1.

### Core data model (SQLite, funda store.py pattern)
`users` (role admin|user), `agents` (id, role, name, config_json, status),
`projects` (name, base_url, connectors_json, slo_json),
`tickets` (id, project_id, title, kind, priority, state, assignee_agent, created_by),
`incidents` (id, project_id, severity, state, owner_agent, detection_rule, opened_at),
`metrics` (project_id, ts, error_rate, p95_ms, up),
`activity_log` (ts, actor_agent, work_item, from_state, to_state, decision, detail) —
append-only; every agent action lands here (requirement: "all steps must be logged"),
`notifications_outbox`.

### Agents (rule-based v1)
`agents/base.py` defines:
```python
class Brain(Protocol):
    def decide(self, agent_cfg, work_item, context) -> Decision  # RuleBrain now, LLMBrain later
class Agent:  # role config + brain + handle() that logs every decision
```
Each role = YAML config (responsibilities, decision rules, communication style used in
log/notification text, escalation rules, daily routine) + a small rule class:
- **Manager**: approves incident closure, reassigns stuck items (>SLA in one state),
  daily standup summary notification.
- **Developer ×2**: pick up `implementation`/`fix` tasks (load-balanced by open count),
  mark blocked→escalate to Manager, simulated work completes after configurable delay.
- **QA Tester**: validates items in `validation`; rule: reject if ticket lacks
  acceptance criteria (sends back to Analyst) — demonstrates real collaboration.
- **Product Analyst**: clarifies requirements on new tickets (checks required fields),
  confirms business impact on incidents, and runs the **Product Intelligence routine**
  (see below) — proposes prioritized features to the admin.
- **Performance Engineer**: checks perf impact on incident fixes (compares p95 before/
  after from metrics store).
- **SRE Engineer**: owns monitor tick — evaluates detection rules over metrics,
  opens/updates incidents, first-responder investigation (attaches connector snapshot),
  escalates to DevOps when rule says infra-related (e.g. health DOWN vs error spike).
- **DevOps Engineer**: deployment step of ticket workflow, infra remediation on
  escalated incidents (simulated actions: "restart", "rollback" — logged).

### Workflows (explicit state machines in `workflows.py`)
- **Ticket**: `new → analysis(Analyst) → in_progress(Dev) → validation(QA) →
  deploy(DevOps) → post_deploy_watch(SRE, 24h) → done`. Rejections loop back with
  reason logged.
- **Incident**: `detected(SRE) → investigating(SRE) → [escalated(DevOps)] →
  fixing(Dev) → validating(QA) → perf_check(PerfEng) → impact_review(Analyst) →
  closure_approval(Manager) → closed`. Severity rules set SLA clocks; breaches trigger
  escalation alerts on the dashboard + notifications.

### RBAC (funda auth.py pattern)
Admin-only: agent CRUD, project/connector CRUD, manual assignment, workflow trigger,
decision override (`POST /api/admin/override` — logged as actor "admin"), dashboards
config. Users: create/view tickets, view dashboards read-only.

### SRE Dashboard (`web/`, funda-style vanilla JS)
Panels: system health per project (up/down tiles), error-rate + latency sparklines
(from `metrics`), incident count + active incidents with owner and state, SLO status
(target vs actual, burn indicator), escalation alerts feed, agent roster with current
load. Auto-refreshes every 30s. Admin view adds agent CRUD + override buttons.

### Product Intelligence (Analyst → Admin feature proposals)
The Product Analyst's daily routine mines signals and files **feature proposals** for
the admin — closing the loop from operations back to roadmap:
- Signals (v1, all local): ticket theme frequency (recurring keywords/components),
  incident patterns (same component failing repeatedly → "hardening" proposal),
  SLA-breach hotspots, user-submitted feedback tickets.
- Scoring: **RICE** (reach × impact × confidence / effort) computed rule-based from
  signal counts; each proposal carries its evidence (linked tickets/incidents).
- Flow: `proposals` table → admin dashboard "Proposals" panel → admin approves/rejects
  (logged) → approved proposal becomes a backlog ticket entering the normal workflow.
- Later (LLM phase): competitor/market scan via web search feeds the same proposal
  pipeline — the interface doesn't change.

### Design-review upgrades (v2)
Applied after a senior review pass of the v1 draft:
1. **Event-driven core**: the append-only `activity_log` doubles as the event stream
   the orchestrator reacts to (emit-then-react instead of poll-then-react). Same
   stack, but every workflow becomes **replayable** — powering a demo "replay
   incident" mode and deterministic tests.
2. **Agent eval harness** (`tests/evals/`): golden scenarios — recorded work-item +
   context → expected decision per role — run in CI. This is what makes the later
   RuleBrain→LLMBrain swap safe and measurable, and it's the strongest career
   artifact in the project.
3. **MCP server interface**: expose TeamOps operations (create ticket, query
   incidents, read dashboard, approve proposal) as MCP tools so Claude or any MCP
   client can operate the team. Thin layer over the existing service functions.
4. **Agent observability**: per-agent metrics on the admin dashboard — decisions/hour,
   items handled, SLA compliance, admin-override rate (which agent gets overridden
   most is exactly what you'd tune first when LLM brains arrive).
5. **Auto-generated incident postmortems**: on closure, assemble timeline from the
   event log into a postmortem doc (template-based v1, LLM-written later).

### Simulation mode
`SimulatedMonitorSource` generates plausible error-rate/latency series with injectable
anomalies (`POST /api/admin/simulate/incident` — admin-only chaos button) so the whole
incident workflow can be demoed end-to-end without any real outage. Seed script creates
demo tickets.

## Deliverables (matches the requested output format)

```
teamops/
  README.md                 architecture + how to register funda + demo script
  agents.yaml               all 8 agent definitions (example config requirement)
  app/
    main.py config.py auth.py store.py
    orchestrator.py workflows.py scheduler.py
    agents/ base.py manager.py developer.py qa.py analyst.py perf.py sre.py devops.py
    connectors/ base.py simulated.py http_health.py sentry_webhook.py
    notifications/ base.py console.py webhook.py
  web/ index.html app.js styles.css        (SRE + admin dashboard)
  docs/ARCHITECTURE.md                     diagrams, interaction rules, example
                                           daily-run log transcript
  scripts/seed_demo.py                     demo tickets + simulated incident
  tests/                                   workflow transitions, RBAC, detection
                                           rules, assignment logic
```

## Implementation order

1. **Step 0**: test + commit + push the pending funda changes.
2. Skeleton: store, auth/RBAC, agent registry loading `agents.yaml`, seed admin.
3. Ticket workflow end-to-end (all 5 roles touching it) + activity log + tests.
4. Metrics store + connectors (simulated + http_health) + detection rules + incident
   workflow end-to-end + tests.
5. Scheduler: 60s monitor tick, hourly ops tick, daily routines, notifications.
6. SRE dashboard + admin dashboard (incl. agent observability + proposals panel).
7. Product Intelligence module (signal mining → RICE proposals → admin approval →
   backlog ticket) + postmortem generator.
8. Agent eval harness (golden scenarios in CI) + MCP server interface.
9. Register funda as project #1 (health URL + SLO config), demo script incl. incident
   replay, docs with example daily-run logs; commit + push.
10. (Later, out of scope now) LLMBrain per role, GitHub Issues connector, real Sentry,
    web-search market scan for proposals.

## Verification

- `pytest` — state-machine transitions (happy path + rejection loops + SLA breach
  escalation), RBAC (403 for non-admin on admin routes), detection rules (synthetic
  metric series → expected incidents), load-balanced dev assignment.
- Live run: `uvicorn app.main:app` → seed demo → watch hourly tick logs → trigger
  `simulate/incident` → follow the incident across all 7 roles on the dashboard →
  verify activity_log rows for every step.
- Funda integration: run funda locally on :8100, register it, stop funda → health
  connector opens a SEV1 within 3 polls; restart funda → auto-recovery note logged.
