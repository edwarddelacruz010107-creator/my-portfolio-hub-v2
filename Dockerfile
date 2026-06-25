# Portfolio CMS v5.0 — Production Dockerfile
# 
# Multi-stage build optimized for size and security:
# - Stage 1: Build (install dependencies)
# - Stage 2: Runtime (minimal image)
#
# Features:
#   ✅ Non-root user (security)
#   ✅ Health checks
#   ✅ Environment validation
#   ✅ Efficient caching
#   ✅ Minimal final size
#   ✅ Production-ready

# ─────────────────────────────────────────────────────────────────────
# STAGE 1: BUILDER
# ─────────────────────────────────────────────────────────────────────

FROM python:3.12-slim as builder

# Set working directory
WORKDIR /build

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    postgresql-client \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# ─────────────────────────────────────────────────────────────────────
# STAGE 2: RUNTIME
# ─────────────────────────────────────────────────────────────────────

FROM python:3.12-slim

# Labels
LABEL maintainer="devops@yourdomain.com"
LABEL description="Portfolio CMS v5.0 - Production Ready"
LABEL version="5.0.0"

# Environment
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    FLASK_APP=wsgi.py \
    FLASK_ENV=production

# Create app user (non-root for security)
RUN useradd -m -u 1000 appuser

# Set working directory
WORKDIR /app

# Install runtime dependencies only
RUN apt-get update && apt-get install -y --no-install-recommends \
    postgresql-client \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy Python packages from builder
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application code
COPY --chown=appuser:appuser . .

# Create required directories
RUN mkdir -p /app/logs /app/storage && \
    chown -R appuser:appuser /app

# Switch to non-root user
USER appuser

# Expose port
EXPOSE 5000

# Health check
# Checks if application is running and responsive
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:5000/health || exit 1

# Validate environment on startup
RUN echo '#!/bin/bash\n\
set -e\n\
\n\
# Check required environment variables\n\
required_vars=("SECRET_KEY" "FERNET_KEY" "CORE_DATABASE_URL" "TENANT_DATABASE_URL")\n\
for var in "${required_vars[@]}"; do\n\
  if [ -z "${!var}" ]; then\n\
    echo "ERROR: Required environment variable $var is not set"\n\
    exit 1\n\
  fi\n\
done\n\
\n\
# Run database migrations (conditional)\n\
if [ "$RUN_MIGRATIONS" = "true" ]; then\n\
  echo "Running database migrations..."\n\
  flask db upgrade-core\n\
  flask db upgrade-tenant\n\
fi\n\
\n\
# Start application\n\
exec "$@"\n\
' > /app/entrypoint.sh && chmod +x /app/entrypoint.sh

ENTRYPOINT ["/app/entrypoint.sh"]

# Start Flask with gunicorn
# Configuration:
#   - 4 worker processes (adjust based on CPU cores)
#   - 4 threads per worker
#   - 30s worker timeout
#   - 60s graceful timeout
CMD ["gunicorn", \
     "--bind=0.0.0.0:5000", \
     "--workers=1", \
     "--threads=2", \
     "--timeout=30", \
     "--graceful-timeout=60", \
     "--access-logfile=-", \
     "--error-logfile=-", \
     "--log-level=info", \
     "wsgi:app"]
