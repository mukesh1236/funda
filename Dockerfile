# ── Stage 1: build deps in a throw-away layer ────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# System deps needed to compile some Python packages (e.g. bcrypt)
RUN apt-get update && apt-get install -y --no-install-recommends gcc && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt


# ── Stage 2: lean runtime image ───────────────────────────────────────────────
FROM python:3.12-slim

WORKDIR /app

# Copy only the installed packages from the builder stage (no gcc in prod)
COPY --from=builder /install /usr/local

# Copy application code
COPY app/    ./app/
COPY web/    ./web/
COPY scripts/ ./scripts/

# Persistent data directory — mount a Railway Volume at /data
RUN mkdir -p /data
ENV RECOMMENDATIONS_DB_PATH=/data/recommendations.db
# FAISS fund-document indexes must live on the same volume as the DB, or they
# are rebuilt from scratch after every redeploy.
ENV FUND_INDEX_DIR=/data/fund_index
# Same reasoning for the ONNX embedding weights: cache them on the volume so
# they are downloaded once, not on every container start.
ENV FASTEMBED_CACHE_PATH=/data/fastembed

# glibc gives each thread its own malloc arena, and memory freed back to an
# arena is not returned to the OS. This process is threaded (ThreadPoolExecutor
# in service.py, funds.py) and churns pandas/numpy frames on every yfinance
# download, so RSS climbs steadily even when nothing is retained — and Railway
# bills resident memory. Capping the arenas trades a little allocator contention
# for a far flatter memory curve.
ENV MALLOC_ARENA_MAX=2

# Railway (and most PaaS) injects PORT at runtime
ENV PORT=8100

EXPOSE 8100

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
