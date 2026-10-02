# 2. Raw `sqlite3`, no ORM, hand-rolled migrations

- **Status:** Accepted
- **Date:** 2026-06-20
- **Implemented in:** initial commit `4c072d4`; `app/store.py`

## Context

The schema is small (18 tables) and the access patterns are simple and hot-path
sensitive. An ORM and a migration framework are real dependencies with their own
failure modes and learning curve.

## Decision

All persistence goes through `RecommendationStore` in `app/store.py`, using raw
`sqlite3`. New tables go in `_SCHEMA`; columns added later also go in
`RecommendationStore._migrate()`, which inspects `PRAGMA table_info` and issues
`ALTER TABLE ... ADD COLUMN` when a column is missing.

## Consequences

- **Easier:** no dependency, full control of SQL, the whole data layer fits in one
  file, generated docs can read the schema directly
  (`scripts/export_schema_doc.py`).
- **Harder:** a schema change must be made in **two places**, and forgetting
  `_migrate()` means existing databases silently lack the column
  ([runbook](../workflows/add-a-column.md)). There is no migration history and no
  downgrade.
- **A sharp edge exists.** Rebuilding a table with a changed primary key is done by
  dropping it (`watchlist`, when per-user ownership was added), which discards its
  rows. That was acceptable pre-production only; do not repeat it on live data.
- Replacing this with SQLAlchemy Core and Alembic is BACKLOG P2-A, to be done with
  the move to Postgres rather than before.

## Alternatives considered

- **SQLAlchemy plus Alembic now:** more machinery than 18 tables justify while the
  database is SQLite on one node.
- **An ORM only:** adds the dependency without solving migrations, the actual pain.
