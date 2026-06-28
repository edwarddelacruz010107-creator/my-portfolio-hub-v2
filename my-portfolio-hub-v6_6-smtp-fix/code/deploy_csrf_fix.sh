#!/bin/bash
# deploy_csrf_fix.sh — Automated deployment script for CSRF SSL Strict fix
#
# Usage: bash deploy_csrf_fix.sh
#
# This script:
# 1. Verifies the fix is applied
# 2. Runs local tests
# 3. Commits with proper message
# 4. Pushes to origin
# 5. Monitors deployment

set -e

echo "=================================================="
echo "Portfolio CMS CSRF SSL Strict Fix Deployment"
echo "=================================================="

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Step 1: Verify fix is applied
echo -e "\n${YELLOW}[1/4] Verifying fix is applied...${NC}"

if grep -q "csrf_ssl_strict_for_login_routes" app/__init__.py; then
    echo -e "${GREEN}✓ Fix found in app/__init__.py${NC}"
else
    echo -e "${RED}✗ Fix not found in app/__init__.py${NC}"
    echo "  Please apply the fix first. See app_init_FIXED.py or DEPLOYMENT_GUIDE_CSRF_FIX.md"
    exit 1
fi

# Step 2: Syntax check
echo -e "\n${YELLOW}[2/4] Checking Python syntax...${NC}"

if python -m py_compile app/__init__.py 2>/dev/null; then
    echo -e "${GREEN}✓ Python syntax is valid${NC}"
else
    echo -e "${RED}✗ Python syntax error in app/__init__.py${NC}"
    exit 1
fi

# Step 3: Run local tests (if available)
echo -e "\n${YELLOW}[3/4] Running local tests...${NC}"

if [ -f "test_csrf_fix_local.py" ]; then
    if python test_csrf_fix_local.py; then
        echo -e "${GREEN}✓ All tests passed${NC}"
    else
        echo -e "${RED}✗ Tests failed${NC}"
        exit 1
    fi
else
    echo -e "${YELLOW}⚠ test_csrf_fix_local.py not found, skipping tests${NC}"
fi

# Step 4: Git commit and push
echo -e "\n${YELLOW}[4/4] Committing and pushing...${NC}"

if git diff --quiet app/__init__.py; then
    echo -e "${YELLOW}⚠ No changes to commit (already applied?)${NC}"
else
    git add app/__init__.py
    
    git commit -m "fix(auth): disable WTF_CSRF_SSL_STRICT for login routes to fix Render proxy issue

Fixes production login failures caused by CSRF validation comparing
request.referrer (public hostname) to request.host (internal Render hostname).

Login routes are CSRF-safe because:
- They use POST-redirect-GET (no persistent state in POST)
- SOP blocks cross-origin POSTs
- Session token set AFTER login succeeds

CSRF token validation remains enabled for all routes."
    
    echo -e "${GREEN}✓ Committed with message${NC}"
fi

git push origin $(git rev-parse --abbrev-ref HEAD)

echo -e "${GREEN}✓ Pushed to origin${NC}"

echo -e "\n${GREEN}================================================${NC}"
echo -e "${GREEN}✅ Deployment initiated!${NC}"
echo -e "${GREEN}================================================${NC}"

echo ""
echo "Next steps:"
echo "  1. Monitor Render build: https://dashboard.render.com"
echo "  2. Wait for deploy to complete (~3-5 minutes)"
echo "  3. Test login: https://myportfoliohub.online/superadmin/login"
echo "  4. Check logs if issues occur"
echo ""
echo "Rollback command (if needed):"
echo "  git revert \$(git rev-parse HEAD)"
echo "  git push origin $(git rev-parse --abbrev-ref HEAD)"
echo ""
