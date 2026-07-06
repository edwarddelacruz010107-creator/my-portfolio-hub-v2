# Portfolio Hub — Production Dockerfile
#
# Production-ready Docker setup for Render:
# - Python 3.12 slim
# - Non-root user
# - Gunicorn
# - Render $PORT support
# - Optional automatic migrations
# - Optional one-time SuperAdmin creation
# - Single Postgres Option 1 support:
#   CORE_DATABASE_URL required
#   TENANT_DATABASE_URL optional / blank

# ─────────────────────────────────────────────────────────────────────
# STAGE 1: BUILDER
# ─────────────────────────────────────────────────────────────────────

FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better Docker cache
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt


# ─────────────────────────────────────────────────────────────────────
# STAGE 2: RUNTIME
# ─────────────────────────────────────────────────────────────────────

FROM python:3.12-slim

LABEL description="Portfolio Hub - Production Docker Image"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    FLASK_APP=wsgi.py \
    FLASK_ENV=production \
    FLASK_DEBUG=0 \
    PORT=5000

# Create non-root app user
RUN useradd -m -u 1000 appuser

WORKDIR /app

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY --chown=appuser:appuser . .

# Create writable runtime directories
RUN mkdir -p /app/logs /app/storage /app/instance && \
    chown -R appuser:appuser /app

# Create entrypoint script
RUN cat > /app/entrypoint.sh <<'EOF'
#!/bin/sh
set -e

echo "Starting Portfolio Hub..."
echo "FLASK_ENV=${FLASK_ENV:-production}"
echo "FLASK_DEBUG=${FLASK_DEBUG:-0}"
echo "PORT=${PORT:-5000}"

# Required production variables
required_vars="SECRET_KEY FERNET_KEY CORE_DATABASE_URL"

for var in $required_vars; do
  eval value=\$$var
  if [ -z "$value" ]; then
    echo "ERROR: Required environment variable $var is not set"
    exit 1
  fi
done

# Option 1 support:
# TENANT_DATABASE_URL can be blank/missing.
# App config should reuse CORE_DATABASE_URL for tenant bind.
if [ -z "$TENANT_DATABASE_URL" ]; then
  echo "TENANT_DATABASE_URL is not set. Tenant bind will reuse CORE_DATABASE_URL."
fi

# Optional automatic migrations
# Recommended on Render:
# RUN_MIGRATIONS=true
if [ "$RUN_MIGRATIONS" = "true" ]; then
  echo "Running database migrations..."
  flask db upgrade

  echo "Ensuring tenant schema..."
  flask ensure-tenant-schema || echo "WARNING: ensure-tenant-schema failed or command is unavailable."

  echo "Ensuring default tenant..."
  flask ensure-default-tenant || echo "WARNING: ensure-default-tenant failed or command is unavailable."
fi

# Optional one-time SuperAdmin creation
# Use only on first deploy, then set CREATE_SUPERADMIN_ON_STARTUP=false
if [ "$CREATE_SUPERADMIN_ON_STARTUP" = "true" ]; then
  echo "Creating SuperAdmin if missing..."
  flask create-superadmin || echo "WARNING: create-superadmin failed or already exists."
fi

echo "Launching app..."
exec "$@"
EOF

RUN chmod +x /app/entrypoint.sh && \
    chown appuser:appuser /app/entrypoint.sh

USER appuser

EXPOSE 5000

# Healthcheck supports either /health or /healthz
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f "http://localhost:${PORT:-5000}/health" || curl -f "http://localhost:${PORT:-5000}/healthz" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]

# Render provides $PORT.
# Use sh -c so environment variables expand correctly.
CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5000} --workers=${WEB_CONCURRENCY:-1} --threads=${GUNICORN_THREADS:-2} --timeout=${GUNICORN_TIMEOUT:-60} --graceful-timeout=60 --access-logfile=- --error-logfile=- --log-level=${GUNICORN_LOG_LEVEL:-info} wsgi:app"]
