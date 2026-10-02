# Workflows (runbooks)

Step-by-step procedures for operations that have to be done the same way every time.
Each one records the **check that proves it worked**, because several past mistakes
were reading the wrong signal.

| Runbook | When |
|---|---|
| [`deploy.md`](deploy.md) | Shipping a change to production |
| [`rollback.md`](rollback.md) | A deploy went wrong |
| [`memory-investigation.md`](memory-investigation.md) | Memory looks high or is climbing |
| [`cost-check.md`](cost-check.md) | The hosting bill is higher than expected |
| [`add-a-column.md`](add-a-column.md) | Changing the database schema |
| [`incident-llm-budget.md`](incident-llm-budget.md) | AI answers degrade, or the daily AI budget is spent |

Resource IDs (Railway project and service) are deliberately **not** recorded here.
The repository is public; look them up in the Railway dashboard.
