-- ============================================================================
-- Portfolio CMS v5.0 — PostgreSQL Database Initialization
-- ============================================================================
--
-- DUAL-DATABASE ARCHITECTURE:
--   1. CORE DATABASE (portfolio_core): Auth, billing, tenants, platform
--   2. TENANT DATABASE (portfolio_tenant): Portfolio content per tenant
--
-- USAGE:
--   1. Connect to CORE database and run the CORE section
--   2. Connect to TENANT database and run the TENANT section
--   3. Run VERIFICATION section in each database
--
-- For Render:
--   - Create two PostgreSQL services on Render
--   - Get connection strings
--   - Set CORE_DATABASE_URL and TENANT_DATABASE_URL in environment
--   - Run migrations: flask db upgrade (automatically applies schema)
--
-- ============================================================================

-- ============================================================================
-- CORE DATABASE SCHEMA VALIDATION
-- ============================================================================
--
-- This section validates the core database structure.
-- If migrations haven't run yet, run: flask db upgrade
--

-- Users & Authentication
SELECT 'TABLE: user' AS check_name;
SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'user') AS exists;

SELECT 'TABLE: admin' AS check_name;
SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'admin') AS exists;

-- Tenants & Subscriptions
SELECT 'TABLE: tenant' AS check_name;
SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'tenant') AS exists;

SELECT 'TABLE: subscription' AS check_name;
SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'subscription') AS exists;

SELECT 'TABLE: billing_transaction' AS check_name;
SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'billing_transaction') AS exists;

-- Platform Configuration
SELECT 'TABLE: global_email_config' AS check_name;
SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'global_email_config') AS exists;

SELECT 'TABLE: audit_log' AS check_name;
SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'audit_log') AS exists;

-- ============================================================================
-- CORE DATABASE CRITICAL COLUMNS
-- ============================================================================

-- Check if MailerSend columns exist (v5.0 migration)
SELECT 'COLUMN: global_email_config.mailersend_api_key' AS check_name;
SELECT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name = 'global_email_config' 
    AND column_name = 'mailersend_api_key'
) AS exists;

SELECT 'COLUMN: global_email_config.has_mailersend' AS check_name;
SELECT EXISTS (
    SELECT 1 FROM information_schema.columns 
    WHERE table_name = 'global_email_config' 
    AND column_name = 'has_mailersend'
) AS exists;

-- ============================================================================
-- CORE DATABASE CRITICAL INDEXES
-- ============================================================================

SELECT 'INDEX: user(email)' AS check_name;
SELECT EXISTS (
    SELECT 1 FROM pg_indexes 
    WHERE table_name = 'user' 
    AND column_name LIKE '%email%'
) AS exists;

SELECT 'INDEX: tenant(slug)' AS check_name;
SELECT EXISTS (
    SELECT 1 FROM pg_indexes 
    WHERE table_name = 'tenant' 
    AND column_name LIKE '%slug%'
) AS exists;

SELECT 'INDEX: subscription(tenant_id)' AS check_name;
SELECT EXISTS (
    SELECT 1 FROM pg_indexes 
    WHERE table_name = 'subscription' 
    AND column_name LIKE '%tenant_id%'
) AS exists;

-- ============================================================================
-- TENANT DATABASE SCHEMA VALIDATION
-- ============================================================================
--
-- This section validates the tenant database structure.
-- If migrations haven't run yet, run: flask db upgrade
--

SELECT 'TABLE: profile' AS check_name;
SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'profile') AS exists;

SELECT 'TABLE: project' AS check_name;
SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'project') AS exists;

SELECT 'TABLE: skill' AS check_name;
SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'skill') AS exists;

SELECT 'TABLE: inquiry' AS check_name;
SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'inquiry') AS exists;

SELECT 'TABLE: message' AS check_name;
SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'message') AS exists;

SELECT 'TABLE: tenant_form_settings' AS check_name;
SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name = 'tenant_form_settings') AS exists;

-- ============================================================================
-- TENANT DATABASE CRITICAL INDEXES
-- ============================================================================

SELECT 'INDEX: profile(tenant_id)' AS check_name;
SELECT EXISTS (
    SELECT 1 FROM pg_indexes 
    WHERE table_name = 'profile' 
    AND column_name LIKE '%tenant_id%'
) AS exists;

SELECT 'INDEX: project(tenant_id)' AS check_name;
SELECT EXISTS (
    SELECT 1 FROM pg_indexes 
    WHERE table_name = 'project' 
    AND column_name LIKE '%tenant_id%'
) AS exists;

-- ============================================================================
-- DATA VERIFICATION (after first deployment)
-- ============================================================================

-- CORE DATABASE: Check default tenant
SELECT 'DEFAULT TENANT' AS check_name;
SELECT EXISTS (SELECT 1 FROM tenant WHERE slug = 'default') AS exists;

-- CORE DATABASE: Check default superadmin
SELECT 'DEFAULT SUPERADMIN' AS check_name;
SELECT EXISTS (SELECT 1 FROM "user" WHERE username = 'superadmin' AND is_superadmin = true) AS exists;

-- TENANT DATABASE: Check default tenant profile (if exists)
SELECT 'DEFAULT PROFILE (if tenant exists)' AS check_name;
SELECT COUNT(*) as profile_count FROM profile WHERE tenant_id = 1;

-- ============================================================================
-- PERFORMANCE RECOMMENDATIONS
-- ============================================================================
--
-- After schema is validated, run these optimizations:
--

-- Create indexes for common queries
CREATE INDEX IF NOT EXISTS idx_user_email ON "user"(email) WHERE active = true;
CREATE INDEX IF NOT EXISTS idx_user_tenant ON "user"(tenant_id);
CREATE INDEX IF NOT EXISTS idx_tenant_slug ON tenant(slug) WHERE active = true;
CREATE INDEX IF NOT EXISTS idx_subscription_tenant ON subscription(tenant_id);
CREATE INDEX IF NOT EXISTS idx_subscription_status ON subscription(status);

-- Tenant database
CREATE INDEX IF NOT EXISTS idx_profile_tenant ON profile(tenant_id);
CREATE INDEX IF NOT EXISTS idx_project_tenant ON project(tenant_id);
CREATE INDEX IF NOT EXISTS idx_inquiry_tenant ON inquiry(tenant_id);
CREATE INDEX IF NOT EXISTS idx_inquiry_created ON inquiry(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_message_tenant ON message(tenant_id);

-- ============================================================================
-- VACUUM AND ANALYZE (after indexes created)
-- ============================================================================
--
-- Run these manually in production (non-peak hours):
--

-- ANALYZE;  -- Update table statistics
-- VACUUM;   -- Free up space from deleted rows

-- ============================================================================
-- MONITORING QUERIES
-- ============================================================================
--
-- Check database health and performance:
--

-- Connection count
SELECT datname, count(*) as connections 
FROM pg_stat_activity 
GROUP BY datname 
ORDER BY connections DESC;

-- Table sizes (largest first)
SELECT 
    schemaname,
    tablename,
    pg_size_pretty(pg_total_relation_size(schemaname||'.'||tablename)) AS size
FROM pg_tables 
WHERE schemaname NOT IN ('pg_catalog', 'information_schema')
ORDER BY pg_total_relation_size(schemaname||'.'||tablename) DESC;

-- Slow queries (if pg_stat_statements enabled)
SELECT 
    query,
    calls,
    mean_exec_time,
    max_exec_time
FROM pg_stat_statements
ORDER BY mean_exec_time DESC
LIMIT 10;

-- ============================================================================
-- BACKUP INSTRUCTIONS (Render-specific)
-- ============================================================================
--
-- For Render PostgreSQL services:
--
--   1. In Render Dashboard, select your PostgreSQL service
--   2. Go to "Backups" tab
--   3. Click "Automatic Backups" to enable
--   4. Retention: 7 days (minimum)
--   5. Set backup window to off-peak hours
--
-- To restore from backup:
--   1. Click "Download" on the backup
--   2. Or use: pg_restore -d portfolio_core backup.dump
--

-- ============================================================================
-- DISASTER RECOVERY TEST
-- ============================================================================
--
-- Test your backup/restore process quarterly:
--
--   1. Export current data: pg_dump -Fc portfolio_core > backup.dump
--   2. Create test database: createdb portfolio_core_test
--   3. Restore: pg_restore -d portfolio_core_test backup.dump
--   4. Verify row counts match
--   5. Drop test database: dropdb portfolio_core_test
--

-- ============================================================================
-- END OF SCHEMA VALIDATION AND SETUP
-- ============================================================================
