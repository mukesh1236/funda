# 1. SQLite, one node, in-process scheduler

- **Status:** Accepted
- **Date:** 2026-06-20
- **Implemented in:** initial commit `4c072d4`

## Context

A personal-scale analyst tracker with one operator and a small user base. It needs
persistence, a daily collection job, and low cost and operational burden. A managed
database and a separate worker would each add cost and moving parts the product did
not yet need.

## Decision

One FastAPI process serves the API and the static frontend. State lives in SQLite
(WAL mode) and FAISS indexes on a single mounted volume. APScheduler runs inside
the same process. `job_lock` makes the daily run once-per-day even if more than one
process starts a scheduler.

## Consequences

- **Easier:** one service, one volume, no network hop to a database, trivial local
  development, very low cost, simple to reason about.
- **Harder:** no horizontal scale. Caches are per process, SQLite is single-writer,
  and the scheduler would run once per replica.
- **Rules out scale-to-zero and serverless hosting** without a rewrite, because the
  app needs a persistent volume and a running scheduler. This is what decided
  [ADR 0008](0008-stay-on-railway.md).
- **Exit path** is written down: Postgres, Redis, and an external scheduler
  (BACKLOG P2-A, P2-B, P2-C). It is not needed yet.

## Alternatives considered

- **Postgres from day one:** correct at scale, costs more than the whole current
  bill on a managed tier, and solves problems the product does not have.
- **Serverless functions:** cheap idle, but cannot hold SQLite writes or an
  in-process scheduler.
