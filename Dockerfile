# syntax=docker/dockerfile:1.6
# Kirana Master Backend — production image
# Mirrors the kirana-ml conda env (Python 3.11) used in development.

# ── Stage 1: build wheels ────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

# Build deps for psycopg2, xgboost wheels, cryptography, etc.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        gcc \
        libpq-dev \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip wheel --wheel-dir=/wheels -r requirements.txt

# ── Stage 2: runtime ─────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    MASTER_HOST=0.0.0.0 \
    MASTER_PORT=9000

# Runtime libs: libpq for psycopg2, libgomp for xgboost
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        libgomp1 \
        curl \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --system app && useradd --system --gid app --home /app app

WORKDIR /app

COPY --from=builder /wheels /wheels
COPY requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt && rm -rf /wheels

# Copy application source (respects .dockerignore)
COPY . .

# Logs dir written at runtime
RUN mkdir -p /app/logs /app/temp /app/outputs && chown -R app:app /app

USER app

EXPOSE 9000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://localhost:9000/health || exit 1

# 1 worker per container by design: each worker is a separate process that runs
# the full lifespan (ML bootstrap + in-process scheduler), so >1 worker means
# duplicate startup logs, duplicate scheduled notifications, and 2x ML memory.
# Scale horizontally with Container App replicas instead. Override via WORKERS env.
CMD ["sh", "-c", "uvicorn main:app --host ${MASTER_HOST} --port ${MASTER_PORT} --workers ${WORKERS:-1} --proxy-headers --forwarded-allow-ips='*'"]
