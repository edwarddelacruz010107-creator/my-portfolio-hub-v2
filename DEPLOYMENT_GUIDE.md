# PRODUCTION DEPLOYMENT GUIDE — Portfolio CMS v5.0

**Last Updated:** June 15, 2026  
**Version:** 5.0  
**Status:** Production Ready

---

## TABLE OF CONTENTS

1. [Pre-Deployment Checklist](#pre-deployment-checklist)
2. [Infrastructure Setup](#infrastructure-setup)
3. [Database Configuration](#database-configuration)
4. [Environment Variables](#environment-variables)
5. [Docker Deployment](#docker-deployment)
6. [Render.com Deployment](#rendercom-deployment)
7. [Post-Deployment Verification](#post-deployment-verification)
8. [Monitoring & Maintenance](#monitoring--maintenance)
9. [Disaster Recovery](#disaster-recovery)
10. [Troubleshooting](#troubleshooting)

---

## PRE-DEPLOYMENT CHECKLIST

### Code Quality
- [ ] All tests passing: `pytest --cov=app tests/ -v`
- [ ] No security warnings: `bandit -r app/`
- [ ] No dependency vulnerabilities: `snyk test`
- [ ] Code coverage >= 80%
- [ ] All TODOs resolved
- [ ] No debug statements in code

### Security
- [ ] All secrets removed from repository
- [ ] `.env.example` properly configured
- [ ] `.gitignore` includes `.env`, `*.db`, `instance/`, `__pycache__/`
- [ ] API keys rotated
- [ ] FERNET_KEY generated and stored
- [ ] CSRF tokens enabled
- [ ] Rate limiting enabled
- [ ] Security headers configured
- [ ] HTTPS enforced
- [ ] Database encryption enabled

### Configuration
- [ ] `FLASK_ENV=production`
- [ ] `DEBUG=False`
- [ ] All required env vars defined
- [ ] Database URLs verified (different core & tenant DBs)
- [ ] Redis URL configured
- [ ] Email service tested
- [ ] Sentry DSN configured
- [ ] PayMongo keys verified

### Database
- [ ] Migrations created: `flask db upgrade-core && flask db upgrade-tenant`
- [ ] Database backed up
- [ ] Indexes created for performance
- [ ] Foreign keys verified
- [ ] Connection pool tested

### Monitoring
- [ ] Sentry project created
- [ ] BetterStack heartbeat configured
- [ ] Log aggregation set up (DataDog, LogRocket, etc.)
- [ ] Uptime monitoring enabled
- [ ] Alerts configured
- [ ] Health check endpoint working

### Documentation
- [ ] Deployment guide completed
- [ ] Runbooks created
- [ ] Incident response plan documented
- [ ] Team trained on production procedures

---

## INFRASTRUCTURE SETUP

### Minimum Requirements

| Component | Development | Production |
|-----------|-------------|-----------|
| CPU | 1 core | 2+ cores |
| RAM | 2GB | 4GB+ |
| Disk | 10GB | 100GB+ |
| Database | SQLite | PostgreSQL 12+ |
| Cache | In-memory | Redis 6+ |
| CDN | None | Cloudflare/AWS CloudFront |
| WAF | None | Cloudflare WAF / AWS WAF |

### Recommended Cloud Providers

**Option 1: Render.com (Recommended for small-medium SaaS)**
- Integrated PostgreSQL
- Automatic HTTPS
- Built-in monitoring
- Easy deployment from Git
- Good free tier for testing

**Option 2: AWS**
- Elastic Beanstalk (PaaS)
- RDS for database
- ElastiCache for Redis
- CloudFront for CDN
- WAF for protection

**Option 3: DigitalOcean**
- App Platform (PaaS)
- Managed Databases
- Spaces for file storage
- Reasonably priced

### Network Security

```yaml
# Firewall Rules (example)
Inbound:
  - Port 443 (HTTPS): All
  - Port 80 (HTTP): Redirect to 443
  - Port 5432 (PostgreSQL): Internal only
  - Port 6379 (Redis): Internal only
  
Outbound:
  - SMTP (587): For Resend
  - HTTPS (443): For API calls
  - DNS (53): Always allow
```

---

## DATABASE CONFIGURATION

### PostgreSQL Setup

```bash
# Create databases
createdb portfolio_core_prod
createdb portfolio_tenant_prod

# Create user
createuser portfolio_app -P  # Prompted for password

# Grant permissions
psql -d portfolio_core_prod -c "GRANT ALL PRIVILEGES ON DATABASE portfolio_core_prod TO portfolio_app;"
psql -d portfolio_tenant_prod -c "GRANT ALL PRIVILEGES ON DATABASE portfolio_tenant_prod TO portfolio_app;"

# Run migrations
FLASK_ENV=production flask db upgrade-core
FLASK_ENV=production flask db upgrade-tenant
```

### Connection Strings

```bash
# Development
DEV_CORE_DATABASE_URL=postgresql://user:pass@localhost:5432/portfolio_core_dev
DEV_TENANT_DATABASE_URL=postgresql://user:pass@localhost:5432/portfolio_tenant_dev

# Production
CORE_DATABASE_URL=postgresql://user:pass@host:5432/portfolio_core_prod
TENANT_DATABASE_URL=postgresql://user:pass@host:5432/portfolio_tenant_prod
```

### Optimization

```sql
-- Create indexes for common queries
CREATE INDEX idx_portfolio_tenant_id ON portfolio(tenant_id);
CREATE INDEX idx_project_tenant_id ON project(tenant_id);
CREATE INDEX idx_subscription_tenant_id ON subscription(tenant_id);
CREATE INDEX idx_user_email ON user(email);

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";

-- Configure settings
ALTER SYSTEM SET shared_buffers = '256MB';
ALTER SYSTEM SET effective_cache_size = '1GB';
ALTER SYSTEM SET maintenance_work_mem = '64MB';
```

### Backup Strategy

```bash
#!/bin/bash
# backup.sh - Daily backup script

BACKUP_DIR="/backups/postgresql"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Backup core database
pg_dump -h $DB_HOST -U portfolio_app portfolio_core_prod | \
  gzip > "$BACKUP_DIR/portfolio_core_$TIMESTAMP.sql.gz"

# Backup tenant database
pg_dump -h $DB_HOST -U portfolio_app portfolio_tenant_prod | \
  gzip > "$BACKUP_DIR/portfolio_tenant_$TIMESTAMP.sql.gz"

# Upload to S3
aws s3 cp "$BACKUP_DIR/portfolio_core_$TIMESTAMP.sql.gz" \
  s3://my-backups/databases/

# Keep only last 30 days
find "$BACKUP_DIR" -name "*.sql.gz" -mtime +30 -delete
```

Schedule via cron:
```
# Daily backup at 2 AM
0 2 * * * /home/app/backup.sh
```

---

## ENVIRONMENT VARIABLES

### Required for Production

Create `.env` on production server:

```bash
# ─────────────────────────────────────────────────────────────
# ENVIRONMENT & DEBUG
# ─────────────────────────────────────────────────────────────
FLASK_ENV=production
FLASK_DEBUG=False

# ─────────────────────────────────────────────────────────────
# SECURITY SECRETS (generate new for production)
# ─────────────────────────────────────────────────────────────
SECRET_KEY=<generate with: python -c "import secrets; print(secrets.token_urlsafe(32))">
FERNET_KEY=<generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">

# ─────────────────────────────────────────────────────────────
# DATABASES
# ─────────────────────────────────────────────────────────────
CORE_DATABASE_URL=postgresql://user:pass@host:5432/portfolio_core_prod
TENANT_DATABASE_URL=postgresql://user:pass@host:5432/portfolio_tenant_prod

# ─────────────────────────────────────────────────────────────
# REDIS (for caching and rate limiting)
# ─────────────────────────────────────────────────────────────
REDIS_URL=redis://default:password@host:6379/0

# ─────────────────────────────────────────────────────────────
# PAYMONGO
# ─────────────────────────────────────────────────────────────
PAYMONGO_ENABLED=true
PAYMONGO_PUBLIC_KEY=pk_live_xxx
PAYMONGO_SECRET_KEY=sk_live_xxx
PAYMONGO_WEBHOOK_SECRET=whsk_live_xxx
PAYMENT_TIMEOUT_SECONDS=600

# ─────────────────────────────────────────────────────────────
# RESEND EMAIL
# ─────────────────────────────────────────────────────────────
RESEND_API_KEY=re_xxx
RESEND_FROM_EMAIL=noreply@yourdomain.com

# ─────────────────────────────────────────────────────────────
# APPLICATION
# ─────────────────────────────────────────────────────────────
APP_BASE_URL=https://app.yourdomain.com
BILLING_GRACE_PERIOD_DAYS=3

# ─────────────────────────────────────────────────────────────
# MONITORING
# ─────────────────────────────────────────────────────────────
SENTRY_DSN=https://xxx@xxx.ingest.sentry.io/xxx
BETTERSTACK_HEARTBEAT_URL=https://betterstack.com/api/v1/heartbeats/xxx
LOG_LEVEL=INFO
```

### Securing Environment Variables

**Render.com:**
1. Settings → Environment
2. Add each variable
3. Variables are encrypted at rest
4. Not visible in source code

**Docker:**
```bash
# Use .env file (not committed)
docker run --env-file .env myapp:latest

# Or pass individually
docker run \
  -e SECRET_KEY=xxx \
  -e FERNET_KEY=xxx \
  myapp:latest
```

**Docker Compose:**
```yaml
services:
  web:
    image: portfolio-cms:5.0
    env_file: .env
    environment:
      - FLASK_ENV=production
    # Variables override env_file
```

---

## DOCKER DEPLOYMENT

### Build Image

```bash
# From project root
docker build -t portfolio-cms:5.0 .

# Tag for registry
docker tag portfolio-cms:5.0 myregistry/portfolio-cms:5.0

# Push to registry
docker push myregistry/portfolio-cms:5.0
```

### Docker Compose (Production)

```yaml
# docker-compose.prod.yml
version: '3.8'

services:
  web:
    image: portfolio-cms:5.0
    container_name: portfolio-cms-app
    env_file: .env
    environment:
      - FLASK_ENV=production
    ports:
      - "5000:5000"
    depends_on:
      - postgres-core
      - postgres-tenant
      - redis
    volumes:
      - ./logs:/app/logs
      - ./storage:/app/storage
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:5000/health"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s

  postgres-core:
    image: postgres:15-alpine
    container_name: portfolio-postgres-core
    environment:
      - POSTGRES_DB=portfolio_core_prod
      - POSTGRES_USER=portfolio_app
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
    volumes:
      - postgres-core-data:/var/lib/postgresql/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U portfolio_app"]
      interval: 10s
      timeout: 5s
      retries: 5

  postgres-tenant:
    image: postgres:15-alpine
    container_name: portfolio-postgres-tenant
    environment:
      - POSTGRES_DB=portfolio_tenant_prod
      - POSTGRES_USER=portfolio_app
      - POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
    volumes:
      - postgres-tenant-data:/var/lib/postgresql/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U portfolio_app"]
      interval: 10s
      timeout: 5s
      retries: 5

  redis:
    image: redis:7-alpine
    container_name: portfolio-redis
    command: redis-server --requirepass ${REDIS_PASSWORD}
    volumes:
      - redis-data:/data
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5

volumes:
  postgres-core-data:
  postgres-tenant-data:
  redis-data:

networks:
  default:
    name: portfolio-network
```

### Run with Docker Compose

```bash
# Start services
docker-compose -f docker-compose.prod.yml up -d

# Run migrations
docker-compose -f docker-compose.prod.yml exec web \
  flask db upgrade-core

docker-compose -f docker-compose.prod.yml exec web \
  flask db upgrade-tenant

# Check logs
docker-compose -f docker-compose.prod.yml logs -f web

# Stop services
docker-compose -f docker-compose.prod.yml down
```

---

## RENDER.COM DEPLOYMENT

### Step 1: Prepare Repository

```bash
# Ensure all secrets are in .env.example (no values)
# Create render.yaml
touch render.yaml
```

### Step 2: Create Render Services

**render.yaml:**
```yaml
services:
  - type: web
    name: portfolio-cms-api
    env: python
    buildCommand: pip install -r requirements.txt
    startCommand: flask run --host=0.0.0.0 --port=$PORT
    envVars:
      - key: FLASK_ENV
        value: production
      - key: PYTHON_VERSION
        value: 3.12.0
    disk:
      name: app-storage
      mountPath: /app/storage
      sizeGB: 50

  - type: pserv
    name: portfolio-postgres-core
    ipAllowList: []
    plan: standard
    region: oregon
    databaseName: portfolio_core_prod
    user: portfolio_app

  - type: pserv
    name: portfolio-postgres-tenant
    ipAllowList: []
    plan: standard
    region: oregon
    databaseName: portfolio_tenant_prod
    user: portfolio_app

  - type: redis
    name: portfolio-redis
    ipAllowList: []
    plan: standard
    region: oregon
    maxmemoryPolicy: noeviction
```

### Step 3: Environment Variables

In Render Dashboard:
1. Select Web Service
2. Settings → Environment
3. Add each variable from `.env.example`:
   - SECRET_KEY
   - FERNET_KEY
   - PAYMONGO_SECRET_KEY
   - PAYMONGO_WEBHOOK_SECRET
   - RESEND_API_KEY
   - SENTRY_DSN
   - etc.

For databases:
- CORE_DATABASE_URL: Use the PostgreSQL Core connection string
- TENANT_DATABASE_URL: Use the PostgreSQL Tenant connection string
- REDIS_URL: Use the Redis connection string

### Step 4: Deploy

```bash
# Push to Git
git add .
git commit -m "Production deployment"
git push origin main

# Render automatically deploys via webhook

# Or manually trigger
# Render Dashboard → Web Service → Manual Deploy
```

### Step 5: Run Migrations

```bash
# Via Render Shell
render login
render logs --service portfolio-cms-api --follow

# Connect to shell
render ssh --service portfolio-cms-api

# Run migrations
cd /app
flask db upgrade-core
flask db upgrade-tenant

# Verify
flask db current-core
flask db current-tenant
```

---

## POST-DEPLOYMENT VERIFICATION

### Health Checks

```bash
# API health
curl https://app.yourdomain.com/health

# Expected response
{
  "status": "ok",
  "version": "5.0.0",
  "environment": "production"
}

# Webhook health
curl https://app.yourdomain.com/webhooks/health

# Expected response
{
  "status": "ok",
  "endpoint": "/webhooks/paymongo"
}
```

### Database Verification

```bash
# Connect to core database
psql -h host -U portfolio_app -d portfolio_core_prod

# Check tables
\dt

# Verify migrations
SELECT * FROM alembic_version;

# Same for tenant database
psql -h host -U portfolio_app -d portfolio_tenant_prod
```

### Application Verification

```bash
# Check logs
tail -f /var/log/portfolio-cms/app.log

# Test login flow
curl -X POST https://app.yourdomain.com/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"test@example.com","password":"Test123!@#"}'

# Test superadmin
curl https://app.yourdomain.com/superadmin/dashboard \
  -H "Cookie: session=..."

# Test API authentication
curl https://app.yourdomain.com/api/tenant \
  -H "Authorization: Bearer pk_live_..."
```

### Security Verification

```bash
# Check HTTPS
curl -I https://app.yourdomain.com

# Verify security headers
curl -I https://app.yourdomain.com | grep -E "Strict-Transport|Content-Security|X-Frame"

# HTTPS report
https://www.ssllabs.com/ssltest/analyze.html?d=app.yourdomain.com
```

---

## MONITORING & MAINTENANCE

### Logging Strategy

```bash
# Centralized logging (recommended)
# Stream logs to DataDog, LogRocket, or CloudWatch

# Local logs
logs/
├── app.log (general)
├── auth.log (authentication)
├── payment.log (payments)
├── webhook.log (webhooks)
└── security.log (security events)

# View logs
tail -f logs/app.log
grep "error" logs/app.log | tail -20
```

### Monitoring Dashboard (Render)

- Metrics: CPU, memory, disk
- HTTP status codes
- Error rate
- Response time
- Deployment history

### Alerts

Configure alerts for:
- Error rate > 1%
- Response time > 2s
- CPU > 80%
- Disk > 80%
- Database connection pool exhausted
- Redis memory near limit

### Database Maintenance

```bash
# Weekly: Analyze and vacuum
psql -d portfolio_core_prod -c "VACUUM ANALYZE;"
psql -d portfolio_tenant_prod -c "VACUUM ANALYZE;"

# Monthly: Reindex
psql -d portfolio_core_prod -c "REINDEX DATABASE portfolio_core_prod;"
psql -d portfolio_tenant_prod -c "REINDEX DATABASE portfolio_tenant_prod;"

# Quarterly: Full backup test
# Restore from backup to staging
# Verify data integrity
```

### Dependency Updates

```bash
# Monthly: Check for updates
pip list --outdated

# Update safely
pip install --upgrade pip
pip install -r requirements.txt --upgrade

# Test in staging first
# Run full test suite
pytest tests/ -v

# Deploy to production
```

---

## DISASTER RECOVERY

### Backup & Restore

```bash
# Automated daily backups (see backup.sh above)

# Manual backup
pg_dump -h host -U portfolio_app portfolio_core_prod | gzip > backup.sql.gz
pg_dump -h host -U portfolio_app portfolio_tenant_prod | gzip > backup.sql.gz

# Restore (in case of disaster)
gunzip backup.sql.gz
psql -h host -U portfolio_app portfolio_core_prod < backup.sql
psql -h host -U portfolio_app portfolio_tenant_prod < backup.sql
```

### Disaster Recovery Plan

**Recovery Time Objective (RTO):** 1 hour  
**Recovery Point Objective (RPO):** 1 hour

1. **Database Corruption:**
   - [ ] Restore from latest backup
   - [ ] Verify data integrity
   - [ ] Run migrations
   - [ ] Test critical flows

2. **Service Crash:**
   - [ ] Restart container
   - [ ] Check logs
   - [ ] Verify database connection
   - [ ] Test health endpoint

3. **Data Loss:**
   - [ ] Restore from backup
   - [ ] Notify affected users
   - [ ] Document incident
   - [ ] Implement preventive measures

---

## TROUBLESHOOTING

### Common Issues

**503 Service Unavailable**
```bash
# Check if service is running
docker ps | grep portfolio
curl http://localhost:5000/health

# Check logs
docker logs portfolio-cms-app

# Restart service
docker restart portfolio-cms-app
```

**Database Connection Failed**
```bash
# Verify database is running
psql -h host -U portfolio_app -d portfolio_core_prod

# Check connection string
echo $CORE_DATABASE_URL

# Verify credentials
# Verify firewall rules
```

**Redis Connection Failed**
```bash
# Test Redis connection
redis-cli -h host ping

# Check REDIS_URL
echo $REDIS_URL

# Verify credentials
# Verify port
```

**PayMongo Webhook Not Processing**
```bash
# Check webhook logs
grep "PayMongo webhook" logs/webhook.log

# Verify webhook secret
echo $PAYMONGO_WEBHOOK_SECRET

# Test webhook manually
curl -X POST http://localhost:5000/webhooks/paymongo \
  -H "Paymongo-Signature: ..." \
  -H "Content-Type: application/json" \
  -d '{"data":{"id":"evt_test"}}'
```

---

## PRODUCTION READINESS CHECKLIST

- [ ] All tests passing
- [ ] No security warnings
- [ ] Environment variables configured
- [ ] Databases migrated
- [ ] Backups working
- [ ] Monitoring enabled
- [ ] Logging configured
- [ ] HTTPS enforced
- [ ] Health checks working
- [ ] Incident response plan documented
- [ ] Team trained
- [ ] Rollback procedure tested

**Status:** ✅ READY FOR PRODUCTION

---

## SUPPORT & ESCALATION

### Technical Support
- Email: support@yourdomain.com
- Slack: #portfolio-cms-incidents
- On-call: [Phone number]

### Escalation Path
1. **Severity P1 (Critical):** Immediate notification + incident room
2. **Severity P2 (High):** 1 hour response + war room
3. **Severity P3 (Medium):** 4 hour response + ticket
4. **Severity P4 (Low):** Next business day

---

**Deployment completed successfully! 🚀**
