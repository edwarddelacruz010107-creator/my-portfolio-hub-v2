# Portfolio Hub — Production Dockerfile for Render
#
# Safe defaults:
# - Non-root runtime user
# - Render $PORT support
# - Optional migrations and seed commands controlled by env vars
# - Checked-in entrypoint runs `flask db-upgrade-all` when explicitly enabled
# - Single PostgreSQL Option 1 support: TENANT_DATABASE_URL may be blank
# - No credentials baked into the image

FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
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

# Signature download and verification happen in the 2 GiB runtime. Running
# freshclam in Render's 512 MiB build worker can exhaust its memory.
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    postgresql-client \
    curl \
    clamav \
    clamav-freshclam \
    tini \
    && sed -i '/^[[:space:]]*UpdateLogFile[[:space:]]/d' /etc/clamav/freshclam.conf \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY --chown=appuser:appuser . .
COPY --chown=appuser:appuser docker-entrypoint.sh /app/docker-entrypoint.sh

RUN mkdir -p /app/logs /app/storage /app/instance /app/app/static/uploads \
        /var/lib/clamav /var/log/clamav && \
    chown -R appuser:appuser /app /var/lib/clamav /var/log/clamav

RUN chmod 0555 /app/docker-entrypoint.sh

USER appuser

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl --fail --silent --show-error --max-time 8 "http://localhost:${PORT:-5000}/readyz" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/app/docker-entrypoint.sh"]

CMD ["sh", "-c", "gunicorn --bind 0.0.0.0:${PORT:-5000} --workers=${WEB_CONCURRENCY:-1} --threads=${GUNICORN_THREADS:-2} --timeout=${GUNICORN_TIMEOUT:-60} --graceful-timeout=60 --access-logfile=- --error-logfile=- --log-level=${GUNICORN_LOG_LEVEL:-info} wsgi:app"]
