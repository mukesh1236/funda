# 8. Stay on Railway rather than move to AWS or Azure

- **Status:** Accepted
- **Date:** 2026-09
- **Implemented in:** no change (a decision *not* to migrate)

## Context

The hosting bill was about $25 to $30 a month, so a move to AWS or Azure was
considered. The app is a **stateful single node**: SQLite plus FAISS on one volume
and an in-process scheduler ([ADR 0001](0001-sqlite-single-node.md)). Every genuinely
cheap tier on AWS and Azure is cheap because it is stateless and scales to zero,
which breaks both of those.

| Option | Runs as-is? | Rough $/month |
|---|---|---|
| AWS Lightsail ($5 to $12 plans) | Yes | 5 to 12 |
| AWS EC2 t4g.small + EBS | Yes | about 9 (reserved) to 14 |
| Azure VM (B1s) + disk | Yes | about 9 to 11 |
| Azure App Service B1 (Linux) | Yes | about 13 |
| AWS App Runner, Fargate + EFS, Lambda | No (volume, SQLite locking, scheduler) | rework needed |
| Azure Container Apps, scale-to-zero | No | rework needed |

Figures are US-East ballpark at the time, excluding tax; verify before relying on
them.

## Decision

**Stay on Railway.** The bill was dominated by one thing, resident memory
([ADR 0003](0003-onnx-embeddings.md), [ADR 0010](0010-bounded-caches.md)), and that
is a code problem, not a host problem.

## Consequences

- Migrating would save roughly **$0 to $3 a month** and cost **1 to 2 days of setup
  plus 2 to 4 hours a month** of TLS, patching, deploy pipeline, backups and
  monitoring that Railway does for free.
- **The serverless trap:** using Lambda or Container Apps scale-to-zero requires
  replacing SQLite with a managed database. RDS `t4g.micro` or Azure Postgres
  Flexible burstable run about $12 to $15 a month on their own, more than the entire
  current bill. Serverless would raise the floor, not lower it.
- **Free credits should not choose the architecture.** Year one looks free, then the
  real bill arrives.
- Cheaper hosts exist for this workload (Hetzner CX22, Oracle Cloud's free tier) at
  the same operational burden as any VM.
- Revisit if the driver becomes compliance, a VPC requirement, an existing cloud
  footprint, or scale beyond one node, at which point SQLite has to go regardless
  and that, not the hosting, is the real migration.

## Alternatives considered

The table above.
