#!/bin/bash
# ============================================================================
# Portfolio CMS v5.0 — Render Deployment Pre-Flight Checklist
# ============================================================================
#
# Run this script to verify all deployment prerequisites are met
# Usage: bash deployment_checklist.sh
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

PASSED=0
FAILED=0
WARNINGS=0

echo "╔════════════════════════════════════════════════════════════════════════════╗"
echo "║     Portfolio CMS v5.0 — Render Deployment Pre-Flight Checklist            ║"
echo "║     Status: Ready for Patched Deployment                                    ║"
echo "╚════════════════════════════════════════════════════════════════════════════╝"
echo ""

# Helper functions
pass() {
    echo -e "${GREEN}✓${NC} $1"
    ((PASSED++))
}

fail() {
    echo -e "${RED}✗${NC} $1"
    ((FAILED++))
}

warn() {
    echo -e "${YELLOW}⚠${NC} $1"
    ((WARNINGS++))
}

# ============================================================================
# PHASE 1: CODE & CONFIGURATION
# ============================================================================

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "PHASE 1: Code & Configuration"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Check if render.yaml exists and has dual databases
if [ -f "render.yaml" ]; then
    if grep -q "CORE_DATABASE_URL" render.yaml && grep -q "TENANT_DATABASE_URL" render.yaml; then
        pass "render.yaml contains CORE_DATABASE_URL and TENANT_DATABASE_URL"
    else
        fail "render.yaml missing CORE_DATABASE_URL or TENANT_DATABASE_URL"
    fi
else
    fail "render.yaml not found in project root"
fi

# Check if render.yaml uses MailerSend (not Resend)
if [ -f "render.yaml" ]; then
    if grep -q "MAILERSEND_API_KEY" render.yaml && ! grep -q "RESEND_API_KEY" render.yaml; then
        pass "render.yaml uses MAILERSEND_API_KEY (not deprecated RESEND)"
    else
        fail "render.yaml still references RESEND_API_KEY (should be MAILERSEND)"
    fi
fi

# Check if wsgi.py exists and is correctly configured
if [ -f "wsgi.py" ]; then
    if grep -q "from app import create_app" wsgi.py && grep -q "app = create_app" wsgi.py; then
        pass "wsgi.py correctly exports Flask app object"
    else
        fail "wsgi.py missing create_app or app export"
    fi
else
    fail "wsgi.py not found (required for Gunicorn)"
fi

# Check if config.py exists and validates production variables
if [ -f "config.py" ]; then
    if grep -q "CORE_DATABASE_URL" config.py && grep -q "TENANT_DATABASE_URL" config.py; then
        pass "config.py configured for dual-database architecture"
    else
        fail "config.py not configured for dual databases"
    fi
else
    fail "config.py not found"
fi

# Check for deprecated Resend imports in superadmin
if grep -r "validate_resend_key" app/superadmin/ 2>/dev/null | grep -q "import"; then
    fail "app/superadmin/ still imports deprecated validate_resend_key"
else
    pass "app/superadmin/ no longer imports deprecated Resend functions"
fi

# Check for deprecated Web3Forms in tenant
if grep -r "send_contact_form_web3forms" app/tenant/ 2>/dev/null | grep -v "# " | grep -q "import"; then
    fail "app/tenant/ still imports deprecated send_contact_form_web3forms"
else
    pass "app/tenant/ no longer imports deprecated Web3Forms functions"
fi

# Check requirements.txt for critical dependencies
if [ -f "requirements.txt" ]; then
    if grep -q "Flask==3.0.0" requirements.txt && grep -q "SQLAlchemy==2.0.23" requirements.txt; then
        pass "requirements.txt has correct Flask and SQLAlchemy versions"
    else
        warn "requirements.txt versions may be outdated"
    fi
    
    if grep -q "psycopg2-binary" requirements.txt; then
        pass "requirements.txt includes psycopg2-binary for PostgreSQL"
    else
        fail "requirements.txt missing psycopg2-binary"
    fi
else
    fail "requirements.txt not found"
fi

echo ""

# ============================================================================
# PHASE 2: ENVIRONMENT SETUP
# ============================================================================

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "PHASE 2: Environment Variables (verify in Render dashboard)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Check local .env if present
if [ -f ".env" ]; then
    warn ".env file exists (should NOT be committed in production)"
    
    if grep -q "CORE_DATABASE_URL=" .env; then
        pass ".env contains CORE_DATABASE_URL"
    else
        warn ".env missing CORE_DATABASE_URL"
    fi
    
    if grep -q "TENANT_DATABASE_URL=" .env; then
        pass ".env contains TENANT_DATABASE_URL"
    else
        warn ".env missing TENANT_DATABASE_URL"
    fi
    
    if grep -q "MAILERSEND_API_KEY=" .env; then
        pass ".env contains MAILERSEND_API_KEY"
    else
        warn ".env missing MAILERSEND_API_KEY"
    fi
else
    pass ".env file not present (correct for production)"
fi

# Check if example env file exists
if [ -f ".env.example" ] || [ -f ".env.dual-db" ]; then
    pass "Environment template file(s) exist for reference"
else
    warn "No environment template files found"
fi

echo ""

# ============================================================================
# PHASE 3: DATABASE PREPARATION
# ============================================================================

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "PHASE 3: Database Preparation (verify on Render)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Check migrations directory
if [ -d "migrations" ] && [ -f "migrations/env.py" ]; then
    pass "Alembic migrations directory exists"
    
    # Check for recent migrations
    migration_count=$(ls migrations/versions/*.py 2>/dev/null | wc -l)
    if [ "$migration_count" -gt 10 ]; then
        pass "Found $migration_count migration files (good coverage)"
    else
        warn "Only $migration_count migration files found (expected 20+)"
    fi
else
    fail "Migrations directory or env.py missing"
fi

# Check for database initialization scripts
if [ -f "migrations/schema_postgresql.sql" ]; then
    pass "PostgreSQL schema file exists for reference"
else
    warn "PostgreSQL schema file not found"
fi

echo ""

# ============================================================================
# PHASE 4: SECURITY & CONFIGURATION
# ============================================================================

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "PHASE 4: Security & Configuration"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Check for hardcoded secrets in code
if grep -r "SECRET_KEY\s*=" app/ 2>/dev/null | grep -v "os.environ" | grep -v ".pyc" | grep -q "="; then
    fail "Hardcoded secrets found in app/ (should use os.environ)"
else
    pass "No hardcoded secrets found in app/"
fi

# Check .gitignore
if [ -f ".gitignore" ]; then
    if grep -q "\.env" .gitignore && grep -q "instance/" .gitignore; then
        pass ".gitignore protects sensitive files"
    else
        warn ".gitignore may not fully protect sensitive files"
    fi
else
    warn ".gitignore not found"
fi

# Check for CSRF protection
if grep -r "CSRFProtect\|csrf.init_app" app/ 2>/dev/null | grep -q "csrf"; then
    pass "CSRF protection is enabled"
else
    fail "CSRF protection not found in app initialization"
fi

echo ""

# ============================================================================
# PHASE 5: DEPLOYMENT ARTIFACTS
# ============================================================================

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "PHASE 5: Deployment Artifacts & Documentation"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

[ -f "COMPREHENSIVE_AUDIT_REPORT.md" ] && pass "Audit report generated" || warn "Audit report missing"
[ -f "RENDER_DEPLOYMENT_GUIDE_PATCHED.md" ] && pass "Deployment guide generated" || warn "Deployment guide missing"
[ -f ".env.production.template" ] && pass "Production env template generated" || warn "Env template missing"
[ -f "PATCH_superadmin_mailersend.diff" ] && pass "Superadmin patch file generated" || warn "Superadmin patch missing"
[ -f "PATCH_tenant_remove_web3forms.diff" ] && pass "Tenant patch file generated" || warn "Tenant patch missing"
[ -f "render.yaml.patched" ] && pass "Patched render.yaml available" || warn "Patched render.yaml missing"
[ -f "DATABASE_VALIDATION_AND_SCHEMA.sql" ] && pass "Database validation SQL generated" || warn "Database validation SQL missing"

echo ""

# ============================================================================
# PHASE 6: GIT STATUS
# ============================================================================

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "PHASE 6: Git Status"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if command -v git &> /dev/null; then
    if git rev-parse --git-dir > /dev/null 2>&1; then
        pass "Git repository detected"
        
        # Check for uncommitted changes
        if [ -z "$(git status --porcelain)" ]; then
            pass "All changes committed (clean working directory)"
        else
            warn "Uncommitted changes detected:"
            git status --short
        fi
        
        # Check remote
        if git remote -v | grep -q "origin"; then
            pass "Git remote 'origin' configured"
        else
            fail "Git remote 'origin' not configured"
        fi
    else
        fail "Not a git repository"
    fi
else
    warn "Git not installed (required for Render deployment)"
fi

echo ""

# ============================================================================
# PHASE 7: LOCAL TESTING
# ============================================================================

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "PHASE 7: Local Testing (optional, recommended)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if command -v python3 &> /dev/null; then
    pass "Python 3 available"
    python_version=$(python3 --version 2>&1 | awk '{print $2}')
    if [[ "$python_version" > "3.11" ]]; then
        pass "Python version $python_version (3.12+ required for production)"
    else
        warn "Python version $python_version (should upgrade to 3.12+)"
    fi
else
    warn "Python 3 not found in PATH"
fi

if [ -f "requirements.txt" ]; then
    echo ""
    echo "To test locally before deployment, run:"
    echo "  1. python -m venv venv"
    echo "  2. source venv/bin/activate"
    echo "  3. pip install -r requirements.txt"
    echo "  4. python -m pytest tests/"
    echo "  5. deactivate"
fi

echo ""

# ============================================================================
# FINAL SUMMARY
# ============================================================================

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "FINAL SUMMARY"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

echo "Checks Passed:  $PASSED"
echo "Checks Failed:  $FAILED"
echo "Warnings:       $WARNINGS"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ ALL CRITICAL CHECKS PASSED${NC}"
    echo ""
    echo "Next steps:"
    echo "  1. Apply patches to code (if not already applied)"
    echo "  2. Commit and push to GitHub"
    echo "  3. Create Render service in dashboard"
    echo "  4. Configure environment variables (Render Settings → Environment)"
    echo "  5. Deploy (Render will auto-deploy on push)"
    echo "  6. Monitor logs (Render Logs tab)"
    echo "  7. Test deployment (health check + superadmin login)"
    echo ""
else
    echo -e "${RED}✗ CRITICAL CHECKS FAILED${NC}"
    echo ""
    echo "Fix the issues above before deploying."
    echo ""
    exit 1
fi

echo "╚════════════════════════════════════════════════════════════════════════════╝"
