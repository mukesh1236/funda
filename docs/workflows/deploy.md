# Deploy

**Merging to `main` redeploys.** There is no separate deploy step and no Railway CLI
in use. The build takes roughly 35 to 90 seconds.

## Before

1. Clean tree on your feature branch: `git status --short` prints nothing.
2. `git fetch origin main`, then confirm `main` is a strict ancestor:
   `git log --oneline origin/main..HEAD` lists only your commits.
3. `pytest tests/` is green. CI runs the same, but **CI does not gate the deploy**;
   Railway watches the branch independently, so a red build still ships.
4. If you changed `web/app.js` or `web/styles.css`, **bump `?v=` in `web/index.html`**
   or browsers serve stale assets.
5. The senior-engineer diff review from `CLAUDE.md` is done and its findings stated.

## Ship

```sh
git checkout main
git merge --ff-only <your-branch>     # refuses if main has diverged
git push origin main                  # this triggers the redeploy
git checkout <your-branch>
```

Use `--ff-only`. A merge commit on `main` is the surprise you do not want.

## Verify (the part that matters)

1. **Railway dashboard, service Deployments:** a deployment for your commit reaches
   `SUCCESS`. **Do not trust the status field alone**; it has read `BUILDING` for
   well over a minute after the app was already serving.
2. **Read the runtime log.** You want `Application startup complete` and
   `Uvicorn running on http://0.0.0.0:<port>`, then the scheduler line
   (`_warm_feed_caches ... interval[0:30:00]`). That proves the *new* container is
   serving.
3. Hard-refresh the site and check the thing you changed.
4. Note memory right after the restart, to compare later. **A post-restart reading
   proves nothing**; see [`memory-investigation.md`](memory-investigation.md).

## Things that look alarming and are not

- **Every runtime log line is `severity: error`.** The app writes to stderr, so
  Railway tags it all as error. Nearly all of it is `INFO`.
- **`yfinance: Failed to create TzCache ... File exists`** repeats on every boot. It
  falls back to running without its timezone cache.

## Docs-only changes

`docs/` is not in the Docker image (the `Dockerfile` copies `app/`, `web/` and
`scripts/`), so a docs-only merge has no production effect, though a push to `main`
still triggers a build.

## First use of a changed dependency

The sandbox this repo is developed in cannot reach `sec.gov` or Hugging Face, so a new
network-dependent path (live EDGAR fetch, the first embedding-weights download) is only
ever verified in production. After deploying one, exercise it once deliberately.
