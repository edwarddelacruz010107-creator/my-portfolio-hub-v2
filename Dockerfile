# Portfolio Hub — Production Dockerfile for Render
#
# Safe defaults:
# - Non-root runtime user
# - Render $PORT support
# - Optional migrations and seed commands controlled by env vars
# - Single PostgreSQL Option 1 support: TENANT_DATABASE_URL may be blank
# - No credentials baked into the image

FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt


FROM python:3.12-slim

LABEL description="Portfolio Hub - Production Docker Image"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    FLASK_APP=wsgi.py \
    FLASK_ENV=production \
    FLASK_DEBUG=0 \
    PORT=5000

RUN useradd -m -u 1000 appuser

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY --chown=appuser:appuser . .

RUN mkdir -p /app/logs /app/storage /app/instance /app/app/static/uploads && \
    chown -R appuser:appuser /app

RUN cat > /app/entrypoint.sh <<'EOF_ENTRYPOINT'
#!/bin/sh
set -e

echo "Starting Portfolio Hub..."
echo "FLASK_ENV=${FLASK_ENV:-production}"
echo "FLASK_DEBUG=${FLASK_DEBUG:-0}"
echo "PORT=${PORT:-5000}"

required_vars="SECRET_KEY FERNET_KEY CORE_DATABASE_URL"
for var in $required_vars; do
  eval value=\$$var
  if [ -z "$value" ]; then
    echo "ERROR: Required environment variable $var is not set"
    exit 1
  fi
done

if [ -z "$TENANT_DATABASE_URL" ]; then
  echo "TENANT_DATABASE_URL is not set. Tenant bind will reuse CORE_DATABASE_URL."
fi

# Optional: run migrations before the web server starts.
# In Render, set RUN_MIGRATIONS=true for simple deployments.
if [ "$RUN_MIGRATIONS" = "true" ]; then
  echo "Running Alembic migrations..."
  flask db upgrade

  echo "Ensuring tenant-bound schema..."
  flask ensure-tenant-schema

  echo "Ensuring default tenant/profile records..."
  flask ensure-default-tenant
fi

# Optional: first-deploy SuperAdmin bootstrap.
# Set CREATE_SUPERADMIN_ON_STARTUP=true once, then set it back to false.
if [ "$CREATE_SUPERADMIN_ON_STARTUP" = "true" ]; then
  echo "Ensuring SuperAdmin account..."
  flask create-superadmin
fi

echo "Launching app..."
exec "$@"
EOF_ENTRYPOINT

RUN chmod +x /app/entrypoint.sh && chown appuser:appuser /app/entrypoint.sh

USER appuser

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f "http://localhost:${PORT:-5000}/health" || curl -f "http://localhost:${PORT:-5000}/healthz" || exit 1

ENTRYPOINT ["/app/entrypoint.sh"]

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5000} --workers=${WEB_CONCURRENCY:-1} --threads=${GUNICORN_THREADS:-2} --timeout=${GUNICORN_TIMEOUT:-60} --graceful-timeout=60 --access-logfile=- --error-logfile=- --log-level=${GUNICORN_LOG_LEVEL:-info} wsgi:app"]
