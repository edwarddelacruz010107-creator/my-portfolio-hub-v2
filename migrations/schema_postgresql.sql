-- =============================================================================
-- schema_postgresql.sql — Portfolio CMS v4.1 Complete Production Schema
-- Target: PostgreSQL 14+ / Supabase
-- Generated: 2026-06-14
--
-- Architecture: Single database, tenant isolation via tenant_id FK + RLS-ready
-- All tables include: tenant_id FK, created_at, updated_at
-- All text fields use PostgreSQL TEXT (not VARCHAR where unbounded)
-- All IDs use INTEGER (SERIAL); switch to BIGINT if you expect >2B rows
-- =============================================================================

-- ── Extensions ────────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- for gen_random_uuid() if needed

-- ── Helper: auto-update updated_at ────────────────────────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- =============================================================================
-- PLATFORM TABLES (no tenant_id — platform-wide)
-- =============================================================================

-- ── tenants ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tenants (
    id              SERIAL PRIMARY KEY,
    slug            VARCHAR(120) NOT NULL UNIQUE,
    company_name    VARCHAR(200) NOT NULL DEFAULT '',
    email           VARCHAR(200) NOT NULL DEFAULT '',
    status          VARCHAR(30)  NOT NULL DEFAULT 'active',  -- active | suspended | deleted
    plan            VARCHAR(50)  NOT NULL DEFAULT 'Basic',
    -- Contact form routing (v4.1)
    form_provider   VARCHAR(20)  NOT NULL DEFAULT 'internal',  -- internal | basin
    basin_endpoint  TEXT,
    -- Timestamps
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_tenants_slug ON tenants(slug);
CREATE INDEX        IF NOT EXISTS ix_tenants_status ON tenants(status);

CREATE TRIGGER trg_tenants_updated_at
    BEFORE UPDATE ON tenants
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- ── users ─────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS users (
    id                      SERIAL PRIMARY KEY,
    tenant_id               INTEGER REFERENCES tenants(id) ON DELETE SET NULL,
    tenant_slug             VARCHAR(120) NOT NULL DEFAULT 'default',
    username                VARCHAR(80)  NOT NULL,
    email                   VARCHAR(120) NOT NULL,
    password_hash           VARCHAR(256),
    is_superadmin           BOOLEAN      NOT NULL DEFAULT FALSE,
    is_active               BOOLEAN      NOT NULL DEFAULT TRUE,
    -- TOTP 2FA
    totp_secret             VARCHAR(64),
    totp_enabled            BOOLEAN      NOT NULL DEFAULT FALSE,
    totp_backup_codes       TEXT,         -- JSON array of hashed backup codes
    last_totp_verified_at   TIMESTAMP WITH TIME ZONE,
    last_totp_code_hash     VARCHAR(64),
    -- Session security
    session_token           VARCHAR(255),
    last_login_ip           VARCHAR(45),
    -- Password reset
    password_reset_token    VARCHAR(100),
    password_reset_expires  TIMESTAMP WITH TIME ZONE,
    require_password_reset  BOOLEAN      NOT NULL DEFAULT FALSE,
    last_password_changed   TIMESTAMP WITH TIME ZONE,
    -- Login attempts
    failed_login_attempts   INTEGER      NOT NULL DEFAULT 0,
    last_failed_login_at    TIMESTAMP WITH TIME ZONE,
    -- Timestamps
    created_at              TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_users_username_tenant ON users(username, tenant_slug);
CREATE UNIQUE INDEX IF NOT EXISTS uq_users_email_tenant    ON users(email, tenant_slug);
CREATE INDEX        IF NOT EXISTS ix_users_tenant_id       ON users(tenant_id);
CREATE INDEX        IF NOT EXISTS ix_users_tenant_slug     ON users(tenant_slug);
CREATE INDEX        IF NOT EXISTS ix_users_is_superadmin   ON users(is_superadmin);

-- ── platform_settings ─────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS platform_settings (
    id          SERIAL PRIMARY KEY,
    key         VARCHAR(100) NOT NULL UNIQUE,
    value       TEXT,
    created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- ── global_email_config ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS global_email_config (
    id              SERIAL PRIMARY KEY,
    resend_api_key  TEXT,   -- Fernet-encrypted at application layer
    sender_name     VARCHAR(120) DEFAULT 'Portfolio CMS',
    sender_email    VARCHAR(200),
    smtp_server     VARCHAR(200),
    smtp_port       INTEGER DEFAULT 587,
    smtp_username   VARCHAR(200),
    smtp_password   TEXT,   -- Fernet-encrypted at application layer
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- ── billing_plans ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS billing_plans (
    id              SERIAL PRIMARY KEY,
    name            VARCHAR(100)  NOT NULL UNIQUE,
    slug            VARCHAR(50)   NOT NULL UNIQUE,
    price_monthly   NUMERIC(10,2) NOT NULL DEFAULT 0.00,
    price_yearly    NUMERIC(10,2) NOT NULL DEFAULT 0.00,
    duration_days   INTEGER       NOT NULL DEFAULT 30,
    features        JSONB         NOT NULL DEFAULT '{}',
    is_active       BOOLEAN       NOT NULL DEFAULT TRUE,
    display_order   INTEGER       NOT NULL DEFAULT 0,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- ── webhook_events ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS webhook_events (
    id              SERIAL PRIMARY KEY,
    event_id        VARCHAR(255) NOT NULL UNIQUE,
    event_type      VARCHAR(100) NOT NULL,
    tenant_id       INTEGER REFERENCES tenants(id) ON DELETE SET NULL,
    payload_summary VARCHAR(500) DEFAULT '',
    payload         TEXT,         -- full raw payload (added v4.1)
    processed       BOOLEAN      NOT NULL DEFAULT FALSE,
    received_at     TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_webhook_events_type_received ON webhook_events(event_type, received_at);
CREATE INDEX IF NOT EXISTS ix_webhook_events_tenant        ON webhook_events(tenant_id);

-- ── payment_methods ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS payment_methods (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER REFERENCES tenants(id) ON DELETE CASCADE,  -- NULL = global
    name            VARCHAR(120) NOT NULL DEFAULT '',
    method_type     VARCHAR(30)  NOT NULL DEFAULT 'ewallet',
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    is_default      BOOLEAN      NOT NULL DEFAULT FALSE,
    instructions    TEXT DEFAULT '',
    qr_image        VARCHAR(255) DEFAULT '',
    account_name    VARCHAR(120) DEFAULT '',
    account_number  VARCHAR(120) DEFAULT '',
    mobile_number   VARCHAR(50)  DEFAULT '',
    bank_name       VARCHAR(120) DEFAULT '',
    notes           TEXT DEFAULT '',
    display_order   INTEGER      NOT NULL DEFAULT 0,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_payment_methods_tenant_active   ON payment_methods(tenant_id, is_active);
CREATE INDEX IF NOT EXISTS ix_payment_methods_display_order   ON payment_methods(display_order);

-- ── payment_instructions ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS payment_instructions (
    id              SERIAL PRIMARY KEY,
    title           VARCHAR(200) DEFAULT '',
    body            TEXT DEFAULT '',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- =============================================================================
-- TENANT DATA TABLES (all have tenant_id FK)
-- =============================================================================

-- ── profile ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS profile (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    tenant_slug         VARCHAR(120) NOT NULL DEFAULT 'default',
    -- Personal info
    name                VARCHAR(120) NOT NULL DEFAULT '',
    title               VARCHAR(200) NOT NULL DEFAULT '',
    subtitle            VARCHAR(300) NOT NULL DEFAULT '',
    bio                 TEXT NOT NULL DEFAULT '',
    email               VARCHAR(120) NOT NULL DEFAULT '',
    phone               VARCHAR(50)  NOT NULL DEFAULT '',
    location            VARCHAR(200) NOT NULL DEFAULT '',
    avatar_url          VARCHAR(500) NOT NULL DEFAULT '',
    -- Social links
    social_links        JSONB NOT NULL DEFAULT '{}',
    -- Plan / billing
    plan                VARCHAR(50)  NOT NULL DEFAULT 'Basic',
    clients_count       INTEGER      NOT NULL DEFAULT 0,
    -- Free trial
    free_trial_days     INTEGER      NOT NULL DEFAULT 0,
    free_trial_ends     TIMESTAMP WITH TIME ZONE,
    -- SEO / Open Graph
    meta_title          VARCHAR(200) NOT NULL DEFAULT '',
    meta_description    VARCHAR(300) NOT NULL DEFAULT '',
    og_image            VARCHAR(255) NOT NULL DEFAULT '',
    -- Internal
    internal_notes      TEXT NOT NULL DEFAULT '',
    -- Timestamps
    created_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_profile_tenant_id   ON profile(tenant_id);
CREATE UNIQUE INDEX IF NOT EXISTS uq_profile_tenant_slug ON profile(tenant_slug);

-- ── projects ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS projects (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    tenant_slug     VARCHAR(120) NOT NULL DEFAULT 'default',
    title           VARCHAR(200) NOT NULL DEFAULT '',
    slug            VARCHAR(200) NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    long_description TEXT NOT NULL DEFAULT '',
    tech_stack      TEXT NOT NULL DEFAULT '',
    image_url       VARCHAR(500) NOT NULL DEFAULT '',
    project_url     VARCHAR(500) NOT NULL DEFAULT '',
    github_url      VARCHAR(500) NOT NULL DEFAULT '',
    category        VARCHAR(100) NOT NULL DEFAULT '',
    status          VARCHAR(30)  NOT NULL DEFAULT 'draft',
    is_featured     BOOLEAN NOT NULL DEFAULT FALSE,
    display_order   INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_projects_tenant_id   ON projects(tenant_id);
CREATE INDEX IF NOT EXISTS ix_projects_status      ON projects(status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_projects_slug_tenant ON projects(slug, tenant_id);

-- ── skills ────────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS skills (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    tenant_slug     VARCHAR(120) NOT NULL DEFAULT 'default',
    name            VARCHAR(100) NOT NULL DEFAULT '',
    category        VARCHAR(100) NOT NULL DEFAULT '',
    proficiency     INTEGER NOT NULL DEFAULT 0,
    icon            VARCHAR(200) NOT NULL DEFAULT '',
    is_visible      BOOLEAN NOT NULL DEFAULT TRUE,
    "order"         INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_skills_tenant_id ON skills(tenant_id);

-- ── testimonials ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS testimonials (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    tenant_slug     VARCHAR(120) NOT NULL DEFAULT 'default',
    name            VARCHAR(120) NOT NULL DEFAULT '',
    company         VARCHAR(120) NOT NULL DEFAULT '',
    role            VARCHAR(120) NOT NULL DEFAULT '',
    content         TEXT NOT NULL DEFAULT '',
    avatar_url      VARCHAR(500) NOT NULL DEFAULT '',
    rating          INTEGER NOT NULL DEFAULT 5,
    is_visible      BOOLEAN NOT NULL DEFAULT TRUE,
    "order"         INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_testimonials_tenant_id ON testimonials(tenant_id);

-- ── services ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS services (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    tenant_slug     VARCHAR(120) NOT NULL DEFAULT 'default',
    title           VARCHAR(200) NOT NULL DEFAULT '',
    description     TEXT NOT NULL DEFAULT '',
    icon            VARCHAR(200) NOT NULL DEFAULT '',
    is_visible      BOOLEAN NOT NULL DEFAULT TRUE,
    display_order   INTEGER NOT NULL DEFAULT 0,
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_services_tenant_id ON services(tenant_id);

-- ── activity_log ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS activity_log (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER REFERENCES tenants(id) ON DELETE SET NULL,
    tenant_slug     VARCHAR(120) NOT NULL DEFAULT 'default',
    user_id         INTEGER REFERENCES users(id) ON DELETE SET NULL,
    action          VARCHAR(200) NOT NULL DEFAULT '',
    detail          TEXT NOT NULL DEFAULT '',
    ip_address      VARCHAR(45)  NOT NULL DEFAULT '',
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_activity_log_tenant_id ON activity_log(tenant_id);
CREATE INDEX IF NOT EXISTS ix_activity_log_created   ON activity_log(created_at DESC);

-- ── inquiries ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS inquiries (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    tenant_slug     VARCHAR(120) NOT NULL DEFAULT 'default',
    name            VARCHAR(200) NOT NULL DEFAULT '',
    email           VARCHAR(200) NOT NULL DEFAULT '',
    subject         VARCHAR(500) NOT NULL DEFAULT '',
    message         TEXT NOT NULL DEFAULT '',
    is_read         BOOLEAN NOT NULL DEFAULT FALSE,
    is_archived     BOOLEAN NOT NULL DEFAULT FALSE,
    ip_address      VARCHAR(45)  NOT NULL DEFAULT '',
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_inquiries_tenant_id ON inquiries(tenant_id);
CREATE INDEX IF NOT EXISTS ix_inquiries_created   ON inquiries(created_at DESC);

-- ── inquiry_replies ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS inquiry_replies (
    id              SERIAL PRIMARY KEY,
    inquiry_id      INTEGER NOT NULL REFERENCES inquiries(id) ON DELETE CASCADE,
    user_id         INTEGER REFERENCES users(id) ON DELETE SET NULL,
    body            TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- ── subscriptions ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS subscriptions (
    id                          SERIAL PRIMARY KEY,
    tenant_id                   INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    plan                        VARCHAR(50)  NOT NULL DEFAULT 'Basic',
    status                      VARCHAR(30)  NOT NULL DEFAULT 'pending',
    billing_cycle               VARCHAR(20)  DEFAULT 'monthly',
    amount_paid                 NUMERIC(10,2) NOT NULL DEFAULT 0.00,
    payment_method              VARCHAR(100) DEFAULT '',
    -- PayMongo references
    paymongo_id                 VARCHAR(255),                     -- checkout session ID
    paymongo_customer_id        VARCHAR(255),
    paymongo_subscription_id    VARCHAR(255) UNIQUE,
    paymongo_payment_id         VARCHAR(255) UNIQUE,
    -- Lifecycle timestamps
    started_at                  TIMESTAMP WITH TIME ZONE,
    expires_at                  TIMESTAMP WITH TIME ZONE,
    renewal_date                TIMESTAMP WITH TIME ZONE,         -- added v4.1
    cancelled_at                TIMESTAMP WITH TIME ZONE,
    last_webhook_at             TIMESTAMP WITH TIME ZONE,
    -- Renewal reminder dedup flags
    reminder_sent_7d            BOOLEAN NOT NULL DEFAULT FALSE,
    reminder_sent_30d           BOOLEAN NOT NULL DEFAULT FALSE,
    -- Timestamps
    created_at                  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_subscriptions_tenant_id       ON subscriptions(tenant_id);
CREATE INDEX IF NOT EXISTS ix_subscriptions_status_expires  ON subscriptions(status, expires_at);
CREATE INDEX IF NOT EXISTS ix_subscriptions_tenant_status   ON subscriptions(tenant_id, status);

-- ── payment_submissions ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS payment_submissions (
    id                  SERIAL PRIMARY KEY,
    tenant_id           INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    subscription_id     INTEGER REFERENCES subscriptions(id) ON DELETE SET NULL,
    payment_method_id   INTEGER REFERENCES payment_methods(id) ON DELETE SET NULL,
    plan                VARCHAR(50)  NOT NULL DEFAULT 'Basic',
    billing_cycle       VARCHAR(20)  NOT NULL DEFAULT 'monthly',
    amount_paid         NUMERIC(10,2) NOT NULL DEFAULT 0.00,
    payment_reference   VARCHAR(200) DEFAULT '',
    proof_filename      VARCHAR(255) DEFAULT '',
    note                TEXT DEFAULT '',
    status              VARCHAR(30)  NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
    reviewed_at         TIMESTAMP WITH TIME ZONE,
    reviewed_by_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_payment_submissions_tenant ON payment_submissions(tenant_id);
CREATE INDEX IF NOT EXISTS ix_payment_submissions_status ON payment_submissions(status);

-- ── password_reset_otps ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS password_reset_otps (
    id          SERIAL PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    otp_hash    VARCHAR(256) NOT NULL,
    expires_at  TIMESTAMP WITH TIME ZONE NOT NULL,
    used        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_password_reset_otps_user ON password_reset_otps(user_id);

-- ── tenant_communication_settings ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tenant_communication_settings (
    id                          SERIAL PRIMARY KEY,
    tenant_id                   INTEGER NOT NULL UNIQUE REFERENCES tenants(id) ON DELETE CASCADE,
    notify_subscription_expiry  BOOLEAN NOT NULL DEFAULT TRUE,
    notify_payment_approved     BOOLEAN NOT NULL DEFAULT TRUE,
    notify_payment_rejected     BOOLEAN NOT NULL DEFAULT TRUE,
    contact_email_override      VARCHAR(200),
    created_at                  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW()
);

-- ── subscription_notifications ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS subscription_notifications (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    subscription_id INTEGER REFERENCES subscriptions(id) ON DELETE SET NULL,
    event_type      VARCHAR(50)  NOT NULL,
    sent_at         TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    recipient_email VARCHAR(255),
    success         BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS ix_sub_notif_tenant ON subscription_notifications(tenant_id);
CREATE INDEX IF NOT EXISTS ix_sub_notif_event  ON subscription_notifications(event_type, sent_at);

-- =============================================================================
-- ROW-LEVEL SECURITY (optional — enable for Supabase direct client access)
-- Uncomment if you use Supabase RLS policies.
-- For server-side-only access (Gunicorn → PG), RLS is not strictly needed
-- because all queries go through the Flask ORM with explicit tenant_id filters.
-- =============================================================================

-- ALTER TABLE profile ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY tenant_isolation ON profile
--     USING (tenant_id = current_setting('app.current_tenant_id')::int);
-- (Repeat for each tenant-scoped table if using RLS)

-- =============================================================================
-- Seed: default plans
-- =============================================================================
INSERT INTO billing_plans (name, slug, price_monthly, price_yearly, duration_days, features, display_order)
VALUES
    ('Basic',    'basic',      0.00,    0.00, 30, '{"max_projects": 5,   "custom_domain": false, "analytics": false}', 1),
    ('Pro',      'pro',      299.00, 2990.00, 30, '{"max_projects": 20,  "custom_domain": true,  "analytics": true}',  2),
    ('Business', 'business', 799.00, 7990.00, 30, '{"max_projects": 100, "custom_domain": true,  "analytics": true, "priority_support": true}', 3)
ON CONFLICT (slug) DO NOTHING;
