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

# Persistent data directory — mount a Volume here in production
RUN mkdir -p /data
ENV RECOMMENDATIONS_DB_PATH=/data/recommendations.db

# Railway (and most PaaS) injects PORT at runtime
ENV PORT=8100

EXPOSE 8100

# Non-root user for security
RUN useradd -m appuser && chown -R appuser /app /data
USER appuser

CMD uvicorn app.main:app --host 0.0.0.0 --port ${PORT}
