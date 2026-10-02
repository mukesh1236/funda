# Add a column (or table)

There is no Alembic. Schema changes are hand-rolled, and a change has to be made in
**two places** or existing databases silently lack it
([ADR 0002](../adr/0002-raw-sqlite-no-orm.md)).

## Steps

1. **`app/store.py::_SCHEMA`**: add the column to the `CREATE TABLE`. This is what a
   fresh database gets. A new table goes here too, with `CREATE TABLE IF NOT EXISTS`.
2. **`RecommendationStore._migrate()`**: add the column for databases that already
   exist, following the pattern already there:
   ```python
   fp_cols = {row["name"] for row in conn.execute("PRAGMA table_info(fund_portfolio)")}
   if fp_cols and "amount" not in fp_cols:
       conn.execute("ALTER TABLE fund_portfolio ADD COLUMN amount REAL")
   ```
   The `fp_cols and` guard skips a table that does not exist yet.
3. **Make it nullable, or give it a default.** SQLite refuses
   `ADD COLUMN ... NOT NULL` without a non-null default.
4. **Store methods and models:** extend the read and write methods and the Pydantic
   model. Return the new field from the list method so the API carries it.
5. **Regenerate the schema docs:** `python scripts/export_schema_doc.py`. If the change
   adds an API field, `python scripts/export_openapi.py` too.
6. **Test** against a temporary database (`tmp_path`), including an *old-shape* database
   that gets migrated, not just a fresh one.
7. **Update the prose** in `docs/schemas/entities.md` if the table's purpose changed.

## Worked example

`fund_portfolio.amount` (Portfolio X-Ray): added to `_SCHEMA`, added to `_migrate()`,
`set_fund_amount()` added to the store, `amount` added to `FundPortfolioItem` and
returned by `list_fund_portfolio`. Nullable on purpose, since "unknown" is a real state
the feature depends on.

## Never

- **Drop a table that holds live rows** to change its shape. `watchlist` was once
  rebuilt by `DROP TABLE` when per-user ownership was added; that discarded its rows and
  was acceptable only pre-production.
- **Update or delete rows in `recommendations`.** It is append-only.
- **Edit the generated block in `docs/schemas/entities.md` by hand.** Regenerate it;
  `tests/test_docs_in_sync.py` fails if it is out of date.

## Rolling back

SQLite cannot cleanly drop a column. Older code ignores an extra column, so rolling the
code back is normally safe; see [`rollback.md`](rollback.md).
