# 3. ONNX `fastembed` instead of `sentence-transformers`

- **Status:** Accepted
- **Date:** 2026-08-31
- **Implemented in:** `2b07936`

## Context

Fund fact sheets need text embeddings. `sentence-transformers` pulls PyTorch, and
this was measured on the real stack:

| Stack | Resident memory | On disk |
|---|---|---|
| torch + sentence-transformers + faiss | **834 MB** | 1.2 GB torch plus **2.7 GB of NVIDIA CUDA libraries** |
| onnxruntime (fastembed) + faiss | **162 MB** (about 250 MB once the weights load) | 142 MB |

The container has no GPU, yet `pip install sentence-transformers` installs the CUDA
build by default. The embedder is `lru_cache`d, so once any fund was added the
model stayed resident for the life of the process. The July to August invoice
(**$24.45**) put memory at **$24.01** of it and vCPU at **$0.18**: the PyTorch
dependency was the bill.

## Decision

Embed with **`fastembed`** (ONNX Runtime) running the **same**
`all-MiniLM-L6-v2` weights. `_embed()` L2-normalises its own output so the
invariant retrieval depends on (inner product equals cosine similarity) holds in
this code rather than resting on a backend default.

## Consequences

- Resident memory fell from 834 MB to about 162 MB. Deploys now build in roughly 35
  to 80 seconds (the old build time was not measured, so no comparison is claimed).
- **Existing FAISS indexes stay valid.** Same checkpoint, same 384 dimensions. The
  model identity recorded in `meta.json` is deliberately left as
  `all-MiniLM-L6-v2`; changing that string would invalidate every index on the
  volume.
- The weights (about 90 MB) download on first use into `FASTEMBED_CACHE_PATH`
  (`/data/fastembed`) so they download once, not per deploy. Until that completes,
  fact-sheet Q&A degrades quietly rather than erroring.
- `numpy` is now declared in `requirements.txt` because `fund_rag` imports it
  directly.
- The sandbox cannot download the weights, so tests stub the backend; the live
  download is verified in production only.

## Alternatives considered

- **CPU-only PyTorch wheel:** removes the 2.7 GB of CUDA libraries and speeds the
  build, but resident memory stays near 850 MB, so it does not cut the bill.
- **SQLite FTS5 keyword search:** zero ML memory, but gives up semantic matching;
  section routing softens that but does not replace it.
- **A hosted embedding API:** about zero local memory, but adds per-call cost,
  latency and a network dependency to both ingest and query.

## A caution carried forward

This fix was correct and measured, and the bill still did not fall, because a
separate memory growth was refilling the space. See
[ADR 0010](0010-bounded-caches.md) and
[`workflows/memory-investigation.md`](../workflows/memory-investigation.md).
