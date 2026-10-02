# Rollback

Decide first: is it faster to roll back, or to fix forward? For a bad deploy that is
hurting users now, roll back, then fix calmly.

## Option 1: redeploy the previous deployment (fastest, no git)

Railway dashboard, service, Deployments: pick the last known-good deployment and
redeploy it. Railway keeps previous deployments (`canRollback`). The volume is
untouched, so data and indexes stay as they are.

## Option 2: revert in git

```sh
git checkout main && git pull --ff-only
git revert <sha>              # one commit; use -m 1 only for a merge commit
git push origin main          # redeploys
```

**Revert, do not force-push.** `main` is the deploy trigger, and rewriting it is how you
get a surprise deploy. Then re-land the fix on a feature branch.

## Before rolling back across a schema change

Check whether `RecommendationStore._migrate()` added a column in the release being
undone.

- **An added column is backwards compatible.** Older code ignores it, so rolling back
  is normally safe.
- **SQLite cannot cleanly drop a column**, so there is no "down" migration. If the
  release also *wrote data* in a new shape, prefer a forward fix.
- Never undo a change by dropping a table that holds live rows.

## State that survives a rollback

| State | Behaviour |
|---|---|
| SQLite database | On the volume; unaffected |
| FAISS indexes | On the volume; stamped with a schema version (`meta.json`). An index of a different version is treated as absent and rebuilt |
| Embedding weights | Same `all-MiniLM-L6-v2` checkpoint across backends, so indexes built by the old PyTorch backend and the ONNX backend are interchangeable |
| In-process caches | Lost on restart; they rebuild |

## After

Verify exactly as in [`deploy.md`](deploy.md), then write down what failed and why in
the commit that re-lands the fix.
