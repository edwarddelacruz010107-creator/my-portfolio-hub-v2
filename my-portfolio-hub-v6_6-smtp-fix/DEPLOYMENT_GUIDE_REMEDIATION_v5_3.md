================================================================================
DEPLOYMENT GUIDE: Portfolio CMS v5.3 Database Remediation
================================================================================

Version: 1.0
Date: June 19, 2026
Status: Ready for Production

================================================================================
TABLE OF CONTENTS
================================================================================

1. Quick Start
2. Detailed Deployment Steps
3. Post-Deployment Validation
4. Troubleshooting
5. Rollback Procedures

================================================================================
1. QUICK START
================================================================================

DEPLOYMENT CHECKLIST:

Before deployment:
  [ ] Run: python scripts/preflight_checks.py
  [ ] Verify: All checks pass
  [ ] Backup: Database backed up
  [ ] Approval: Technical lead approval obtained

Deployment (Render):
  [ ] git push origin main
  [ ] Render automatically deploys within 1 minute
  [ ] Monitor: render logs --follow

Deployment (Supabase/Manual):
  [ ] flask db upgrade
  [ ] flask ensure-tenant-schema
  [ ] Start application

Post-deployment:
  [ ] curl https://app-url/health → {"status": "ok"}
  [ ] curl https://app-url/health/ready → {"status": "ready"}
  [ ] Test form submission
  [ ] Verify email_only provider in admin

Expected results:
  ✓ Zero downtime
  ✓ All tenant data intact
  ✓ No "relation profile does not exist" errors
  ✓ Form providers working (basin, email_only, web3forms, disabled)

================================================================================
2. DETAILED DEPLOYMENT STEPS
================================================================================

RENDER DEPLOYMENT:
─────────────────────────────────────────────────────────────────────────────

1. Merge code to main branch
   $ git checkout main
   $ git merge fix/db-remediation-v5.3
   $ git push origin main

2. Render automatically deploys
   - preDeployCommand runs: flask db upgrade
   - No manual intervention needed
   - Deployment time: 3-5 minutes

3. Monitor deployment progress
   $ render logs portfolio-cms-service --follow
   
   Expected output:
     [INFO] Building application...
     [INFO] Installing dependencies...
     [INFO] Running migrations: 0027_contact_delivery_fields
     [INFO] Running migrations: 0028_add_email_only_provider
     [INFO] Deployment successful

4. Verify deployment
   $ curl https://your-app-url/health/ready

SUPABASE DEPLOYMENT (PostgreSQL):
─────────────────────────────────────────────────────────────────────────────

1. Push code to your repository
   $ git push origin main

2. Deploy via CI/CD pipeline (Docker/k8s/etc)
   Example:
   $ docker build -t portfolio-cms:latest .
   $ docker push registry.example.com/portfolio-cms:latest
   $ kubectl set image deployment/portfolio-cms \
     container=portfolio-cms:latest --record

3. Run migrations on Supabase PostgreSQL
   Before starting application:
   
   $ export DATABASE_URL="postgresql://..."
   $ export CORE_DATABASE_URL="postgresql://..."
   $ export TENANT_DATABASE_URL="postgresql://..."
   $ flask db upgrade

4. Create tenant schema
   $ flask ensure-tenant-schema

5. Start application
   $ gunicorn wsgi:app --bind 0.0.0.0:8000

LOCAL DEVELOPMENT:
─────────────────────────────────────────────────────────────────────────────

1. Update code
   $ cp -r portfolio_cms_v5_3_patched/* .
   $ pip install -r requirements.txt

2. Run pre-flight checks
   $ python scripts/preflight_checks.py

3. Initialize database
   $ FLASK_ENV=development flask db upgrade
   $ FLASK_ENV=development flask ensure-tenant-schema

4. Run tests
   $ pytest tests/

5. Start development server
   $ FLASK_ENV=development flask run

================================================================================
3. POST-DEPLOYMENT VALIDATION
================================================================================

IMMEDIATE CHECKS (< 5 minutes):

1. Application is accessible
   $ curl https://app-url
   → Should load without database errors

2. Health endpoint operational
   $ curl https://app-url/health
   → {"status": "ok"} with HTTP 200

3. Ready check passed
   $ curl https://app-url/health/ready
   → {"status": "ready", "checks": {...}}

FUNCTIONAL CHECKS (5-30 minutes):

4. Forms are submittable
   $ curl -X POST https://app-url/contact \
     -d "name=Test&email=test@example.com&message=Hello"
   → Should NOT return database errors

5. Email providers visible
   - Login to /admin
   - Go to Settings → Contact Form Provider
   - Verify all 4 options available:
     * Basin
     * Email Only ← NEW (verifies fix DB-05)
     * Web3Forms
     * Disabled

6. No migration errors in logs
   Check for: "AttributeError", "migrate", "db.engines"
   → Should find 0 results

COMPREHENSIVE CHECKS (30 minutes - 1 hour):

7. Database connectivity verified
   $ python << 'EOFPYTEST'
   from app import create_app, db
   app = create_app('production')
   with app.app_context():
       core = db.engine.url
       tenant = db.get_engine(bind_key='tenant').url
       print(f"Core DB: {core}")
       print(f"Tenant DB: {tenant}")
       assert core != tenant, "Databases should be different!"
   EOFPYTEST

8. Tenant isolation working
   - Login as admin for tenant A
   - Verify cannot see tenant B's data
   - Verify form submissions isolated

9. Billing operations normal
   - Check PayMongo webhook logs
   - Verify subscription creation works
   - Check billing dashboard accessible

SIGN-OFF:
  □ All checks passed
  □ No database errors in logs
  □ Users can access application
  □ Forms working correctly
  □ Email providers functional

================================================================================
4. TROUBLESHOOTING
================================================================================

PROBLEM: "relation profile does not exist"
SOLUTION:
  1. Check if flask ensure-tenant-schema ran:
     $ flask ensure-tenant-schema
  
  2. Verify TENANT_DATABASE_URL is set:
     $ python -c "import os; print(os.getenv('TENANT_DATABASE_URL'))"
  
  3. Check tables exist:
     $ psql $TENANT_DATABASE_URL -c "\dt"
     → Should show: profile, skills, projects, testimonials, services

PROBLEM: "alembic heads returned more than one head"
SOLUTION:
  1. Verify 0027_inquiry_delivery_fields.py deleted:
     $ ls migrations/versions/ | grep "0027"
     → Should only show: 0027_contact_delivery_fields.py
  
  2. Run pre-flight checks:
     $ python scripts/preflight_checks.py --check migrations

PROBLEM: "'SQLAlchemy' object has no attribute 'engines'"
SOLUTION:
  1. Verify code updated:
     $ grep "db.get_engine(bind_key" app/__init__.py
     → Should find matches
     
     $ grep "db.engines" app/__init__.py
     → Should find 0 results
  
  2. Clear cache:
     $ find . -type d -name __pycache__ -exec rm -rf {} +

PROBLEM: Flask startup fails with import error
SOLUTION:
  1. Check Python syntax:
     $ python -m py_compile app/__init__.py
     $ python -m py_compile migrations/env.py
  
  2. Test imports:
     $ python -c "from app import create_app; create_app()"
     → Should complete successfully

PROBLEM: Migration 0028 fails on PostgreSQL
SOLUTION:
  1. Check enum exists:
     $ psql $DATABASE_URL -c "\dT form_provider_enum"
  
  2. Check if email_only already added:
     $ psql $DATABASE_URL -c "SELECT * FROM pg_enum WHERE \
       enumtypid = (SELECT oid FROM pg_type WHERE \
       typname = 'form_provider_enum')"
  
  3. If email_only exists, migration skips (expected)
  
  4. If migration fails, check logs:
     $ grep "0028" deployment.log

For more detailed troubleshooting, see DATABASE_RELIABILITY_REMEDIATION_REPORT.md

================================================================================
5. ROLLBACK PROCEDURES
================================================================================

RARELY NEEDED - Only use if critical issue discovered

Option A: Git revert (recommended for code issues)
  $ git revert HEAD
  $ git push origin main
  # Render automatically redeploys previous version
  # Downtime: 1-2 minutes

Option B: Database rollback (only for migration issues)
  $ flask db downgrade -1
  # Downgrades from 0028 to 0027
  # Only use if 0028 causes PostgreSQL compatibility issue
  # Removes 'email_only' from enum (unavailable after rollback)

Option C: Full database restore (emergency)
  1. Stop application
  2. Contact DevOps for backup restore
  3. Verify data integrity
  4. Restart application
  # Downtime: 5-10 minutes
  # Risk: data loss between backup and restore

RECOMMENDED APPROACH:
  1. Always keep previous version available
  2. If issues within 30 minutes: use Option A
  3. If issues after 30 minutes: investigate in staging first
  4. Never downgrade migration without root cause analysis

Contact escalation if needed:
  • Technical lead: [contact info]
  • Database team: [contact info]

================================================================================
KEY POINTS
================================================================================

✓ ZERO DOWNTIME: Migrations apply with no service interruption
✓ BACKWARDS COMPATIBLE: All 7 fixes are non-breaking changes
✓ DATA SAFE: No data loss, no destructive migrations
✓ FULLY TESTED: Comprehensive validation before deployment

No manual database schema changes needed.
No configuration changes needed.
No downtime required.

================================================================================
END OF QUICK DEPLOYMENT GUIDE
================================================================================
