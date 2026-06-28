# Portfolio CMS v5.0 — Render Deployment Guide (PATCHED)

**Status:** CRITICAL ISSUES FIXED  
**Estimated Setup Time:** 45 minutes  
**Risk Level:** Low (all fixes are isolated)

---

## TABLE OF CONTENTS

1. [Prerequisites](#prerequisites)
2. [Step 1: Generate Secrets](#step-1-generate-secrets)
3. [Step 2: Provision Databases](#step-2-provision-databases)
4. [Step 3: Apply Code Patches](#step-3-apply-code-patches)
5. [Step 4: Push to GitHub](#step-4-push-to-github)
6. [Step 5: Create Render Service](#step-5-create-render-service)
7. [Step 6: Configure Environment Variables](#step-6-configure-environment-variables)
8. [Step 7: Deploy and Verify](#step-7-deploy-and-verify)
9. [Step 8: Post-Deployment Testing](#step-8-post-deployment-testing)
10. [Troubleshooting](#troubleshooting)

---

## Prerequisites

- [ ] GitHub account with Portfolio CMS repository
- [ ] Render.com account (https://render.com)
- [ ] Python 3.12+ installed locally
- [ ] Git CLI installed

---

## Step 1: Generate Secrets

Run these commands locally to generate required secrets:

```bash
# Generate SECRET_KEY
python -c "import secrets; print(secrets.token_urlsafe(32))"
# Output example: aBcD_1234567890_eFgH_1234567890_iJkL

# Generate FERNET_KEY
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Output example: gAAAAABm...EnlYg==
```

**Save these values in a password manager** — you'll need them in Step 6.

---

## Step 2: Provision Databases

### 2.1 Create Core Database (PostgreSQL)

On Render Dashboard:

1. **New → PostgreSQL**
2. **Name:** `portfolio-core`
3. **Region:** Same as web service
4. **Database:** `portfolio_core`
5. **Create**

Copy the connection string (e.g., `postgresql://user:pass@host:5432/portfolio_core`)

### 2.2 Create Tenant Database (PostgreSQL)

1. **New → PostgreSQL**
2. **Name:** `portfolio-tenant`
3. **Region:** Same as core database
4. **Database:** `portfolio_tenant`
5. **Create**

Copy the connection string (e.g., `postgresql://user:pass@host:5432/portfolio_tenant`)

**Keep both connection strings handy for Step 6.**

---

## Step 3: Apply Code Patches

### 3.1 Apply render.yaml Patch

Replace your `render.yaml` with the patched version:

```bash
# From repository root
cp render.yaml render.yaml.backup
cp render.yaml.patched render.yaml
git add render.yaml
git commit -m "Fix: dual-database config and email provider (MailerSend)"
```

### 3.2 Apply Superadmin Patch

Update `app/superadmin/__init__.py`:

Apply the patch `PATCH_superadmin_mailersend.diff`:

```bash
git apply PATCH_superadmin_mailersend.diff
git commit -m "Fix: update email validation from Resend to MailerSend"
```

### 3.3 Apply Tenant Patch

Update `app/tenant/__init__.py`:

Apply the patch `PATCH_tenant_remove_web3forms.diff`:

```bash
git apply PATCH_tenant_remove_web3forms.diff
git commit -m "Fix: remove deprecated Web3Forms contact form code"
```

### 3.4 Verify All Changes

```bash
git log --oneline -3
# Should show all three commits above
```

---

## Step 4: Push to GitHub

```bash
git push origin main
# or your default branch
```

---

## Step 5: Create Render Service

On Render Dashboard:

1. **New → Web Service**
2. **Connect Repository:** Select your Portfolio CMS repo
3. **Name:** `portfolio-cms` (or your preferred name)
4. **Region:** Choose closest to your users
5. **Branch:** `main` (or your deployment branch)
6. **Runtime:** Python
7. **Build Command:** `pip install -r requirements.txt`
8. **Start Command:** *(will be overridden by render.yaml)*
9. **Plan:** Starter (upgrade later if needed)
10. **Click "Create Web Service"**

**Wait for the service to appear in your dashboard** before proceeding to Step 6.

---

## Step 6: Configure Environment Variables

### 6.1 In Render Dashboard

Navigate to **your web service → Settings → Environment**

Add these variables (use the values generated/obtained in Steps 1-2):

#### Core Secrets

| Key | Value |
|-----|-------|
| `FLASK_ENV` | `production` |
| `SECRET_KEY` | *(from Step 1)* |
| `FERNET_KEY` | *(from Step 1)* |
| `PYTHON_VERSION` | `3.12.0` |

#### Dual Databases

| Key | Value |
|-----|-------|
| `CORE_DATABASE_URL` | *(from Step 2.1)* |
| `TENANT_DATABASE_URL` | *(from Step 2.2)* |

#### Email (MailerSend)

1. **Create MailerSend account** at https://mailersend.com
2. **Generate API token** in Dashboard → API Tokens
3. **Verify a sending domain** (e.g., `noreply@yourdomain.com`)

| Key | Value |
|-----|-------|
| `MAILERSEND_API_KEY` | *(from MailerSend dashboard)* |
| `MAILERSEND_FROM_EMAIL` | *(your verified domain)* |
| `MAILERSEND_FROM_NAME` | `Portfolio CMS` |

#### App Configuration

| Key | Value |
|-----|-------|
| `APP_BASE_URL` | `https://<your-service>.onrender.com` |
| `BILLING_GRACE_PERIOD_DAYS` | `3` |

#### Payment (if enabled)

| Key | Value |
|-----|-------|
| `PAYMONGO_ENABLED` | `false` *(or true if configured)* |
| `PAYMONGO_PUBLIC_KEY` | *(from PayMongo dashboard)* |
| `PAYMONGO_SECRET_KEY` | *(from PayMongo dashboard)* |
| `PAYMONGO_WEBHOOK_SECRET` | *(from PayMongo dashboard)* |

#### Optional (Monitoring)

| Key | Value |
|-----|-------|
| `SENTRY_DSN` | *(optional, from Sentry)* |
| `BETTERSTACK_HEARTBEAT_URL` | *(optional, from BetterStack)* |

#### Admin Setup

| Key | Value |
|-----|-------|
| `SUPERADMIN_PASSWORD` | *(temporary, change after login)* |
| `ADMIN_EMAIL` | *(your admin email)* |

### 6.2 Redis Configuration

**Important:** Redis is auto-injected by Render when you provision a Redis service.

To add Redis:

1. In the same Render service, scroll down
2. **Add Service → Redis**
3. **Name:** `portfolio-redis`
4. **Plan:** Free
5. **Max Memory Policy:** `allkeys-lru`
6. **Create**

Render automatically creates `REDIS_URL` environment variable. ✓

---

## Step 7: Deploy and Verify

### 7.1 Trigger Deployment

- If autoDeploy is enabled: Deployment starts automatically when you push to GitHub
- Manual deployment: Click **Deploy** button in Render dashboard

### 7.2 Monitor Pre-Deploy

The pre-deploy command will run:

```bash
flask db upgrade &&
flask ensure-default-tenant
```

**Watch the deployment logs** (click **Logs** tab):

```
=== BUILDING ===
...
=== PRE-DEPLOY ===
Running: flask db upgrade
✓ Database migrations applied
Running: flask ensure-default-tenant
✓ Default tenant created
=== DEPLOYING ===
...
```

**If pre-deploy fails:**
- Check `CORE_DATABASE_URL` and `TENANT_DATABASE_URL` are valid
- Verify database credentials are correct
- Database may be overloaded — wait 30 seconds and retry

### 7.3 Verify Startup

Once deployment succeeds, check the logs for:

```
INFO:werkzeug:Running on http://0.0.0.0:10000
App startup complete
Health check endpoint active: /heartbeat
```

**If startup fails:**
- Check environment variables are set correctly
- Look for `ValueError: Production environment missing required variables`
- See [Troubleshooting](#troubleshooting) section

---

## Step 8: Post-Deployment Testing

### 8.1 Health Check

```bash
curl https://<your-service>.onrender.com/heartbeat
# Expected: 200 OK
```

### 8.2 Superadmin Login

1. Navigate to `https://<your-service>.onrender.com/superadmin/login`
2. Username: `superadmin`
3. Password: *(the SUPERADMIN_PASSWORD you set)*

**First login:**
- [ ] Verify you can access the dashboard
- [ ] Go to Settings → Email & Forms
- [ ] Verify MailerSend fields (not Resend)
- [ ] Change superadmin password immediately

### 8.3 Create Test Tenant

In superadmin dashboard:

1. **Tenants** → **New Tenant**
2. **Name:** Test Portfolio
3. **Slug:** test-portfolio
4. **Create**

### 8.4 Test Email

In superadmin Settings → Email:

1. Enter your test MailerSend API key
2. Click **Validate API Key**
3. Expected response: ✓ Valid

### 8.5 Test Tenant Access

1. Visit `https://<your-service>.onrender.com/test-portfolio`
2. Verify the test tenant portfolio loads
3. Try the contact form
4. Verify you receive an email

---

## Step 9: Production Hardening

Before going live with real users:

- [ ] **Change SUPERADMIN_PASSWORD** in Render environment
- [ ] **Enable Sentry DSN** (if available) for error tracking
- [ ] **Enable BetterStack** (if available) for uptime monitoring
- [ ] **Set PAYMONGO_ENABLED=true** only if billing is ready
- [ ] **Test TOTP (2FA)** in admin panel
- [ ] **Test password reset** flow
- [ ] **Verify file uploads** work (avatars, project images)
- [ ] **Test billing subscription flow** (if PayMongo enabled)

---

## Troubleshooting

### Pre-Deploy Fails: "database does not exist"

**Cause:** PostgreSQL service not fully initialized

**Fix:**
1. Wait 30 seconds for database to initialize
2. Click **Retry Deploy** in Render dashboard
3. Or check Render PostgreSQL dashboard for any errors

### Startup Error: "missing CORE_DATABASE_URL"

**Cause:** Environment variables not synced

**Fix:**
1. In Render dashboard, go to **Settings → Environment**
2. Verify `CORE_DATABASE_URL` and `TENANT_DATABASE_URL` are set
3. Click **Save**
4. Redeploy: Click **Deploy** or push to GitHub

### Email Not Sending

**Cause:** `MAILERSEND_API_KEY` invalid or missing

**Fix:**
1. Verify MailerSend account created: https://mailersend.com
2. Generate new API token if needed
3. Update `MAILERSEND_API_KEY` in Render environment
4. Redeploy

### Health Check Fails (502 Bad Gateway)

**Cause:** Application crashed during startup

**Fix:**
1. Check **Logs** tab for error messages
2. Look for `ImportError`, `ModuleNotFoundError`, `ValueError`
3. If database connection error:
   - Verify `CORE_DATABASE_URL` and `TENANT_DATABASE_URL`
   - Test connection: `psql <your-connection-string>`
4. Redeploy after fixing

### Login Fails at Superadmin

**Cause:** Default superadmin not created

**Fix:**
1. SSH into Render service (if available)
2. Run: `flask create-superadmin`
3. Or check logs for errors during pre-deploy

### CSRF Token Errors

**Cause:** Form submission failing

**Fix:**
- This should NOT happen in production (CSRF enabled)
- Check browser cookies are enabled
- Clear browser cache and retry
- If persists, check logs for CSRF validation errors

---

## Rolling Back

If something goes wrong:

```bash
# In Render dashboard:
# 1. Go to your web service
# 2. Click "Deployments" tab
# 3. Find the previous successful deployment
# 4. Click "Redeploy"
```

Or revert code changes:

```bash
git revert HEAD~3      # Revert last 3 commits
git push origin main
# Render auto-deploys new version
```

---

## Next Steps

- [ ] Set up SSL certificate (Render handles this automatically)
- [ ] Configure custom domain
- [ ] Enable Sentry for error tracking
- [ ] Set up backup strategy for databases
- [ ] Monitor application logs regularly
- [ ] Test disaster recovery procedures

---

## Support

For issues not covered here:

- **Render Documentation:** https://render.com/docs
- **Portfolio CMS Issues:** Check GitHub issues
- **PostgreSQL Help:** https://www.postgresql.org/docs/

---

**Last Updated:** June 16, 2026  
**Portfolio CMS Version:** 5.0 (Patched)  
**Status:** Ready for Production
