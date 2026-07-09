-- =============================================================================
-- upgrade_postgresql.sql — Portfolio CMS v4.1 PATCHED
-- HIGH-05 FIX: Rewrites upgrade.sql from MySQL DDL to PostgreSQL-compatible DDL.
--
-- Changes from the original MySQL-only upgrade.sql:
--   • MEDIUMTEXT → TEXT  (PostgreSQL has only TEXT, unlimited size)
--   • INT NOT NULL AUTO_INCREMENT → SERIAL  (or BIGSERIAL for large tables)
--   • ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=... → removed (PG defaults)
--   • IGNORE → ON CONFLICT DO NOTHING  (MySQL INSERT IGNORE ≠ PG)
--   • All IF NOT EXISTS guards preserved
--
-- Safe to re-run: all DDL uses IF NOT EXISTS / ON CONFLICT DO NOTHING.
-- Run against Supabase via: psql $DATABASE_URL -f upgrade_postgresql.sql
-- =============================================================================

-- ── webhook_events: add payload column ────────────────────────────────────────
ALTER TABLE webhook_events
    ADD COLUMN IF NOT EXISTS payload TEXT NULL;

-- ── subscriptions: add renewal_date column ────────────────────────────────────
ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS renewal_date TIMESTAMP WITH TIME ZONE NULL;

-- ── payments (if needed as separate table) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS payments (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    subscription_id INTEGER REFERENCES subscriptions(id) ON DELETE SET NULL,
    amount          NUMERIC(10,2) NOT NULL DEFAULT 0.00,
    currency        VARCHAR(10)   NOT NULL DEFAULT 'PHP',
    status          VARCHAR(30)   NOT NULL DEFAULT 'pending',
    paymongo_id     VARCHAR(255),
    reference       VARCHAR(255),
    notes           TEXT,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_payments_tenant_id        ON payments(tenant_id);
CREATE INDEX IF NOT EXISTS ix_payments_subscription_id  ON payments(subscription_id);
CREATE INDEX IF NOT EXISTS ix_payments_status           ON payments(status);

-- ── invoices ──────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS invoices (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    payment_id      INTEGER REFERENCES payments(id) ON DELETE SET NULL,
    invoice_number  VARCHAR(50)   UNIQUE,
    amount          NUMERIC(10,2) NOT NULL DEFAULT 0.00,
    currency        VARCHAR(10)   NOT NULL DEFAULT 'PHP',
    status          VARCHAR(30)   NOT NULL DEFAULT 'draft',
    issued_at       TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    due_at          TIMESTAMP WITH TIME ZONE,
    paid_at         TIMESTAMP WITH TIME ZONE,
    notes           TEXT,
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_invoices_tenant_id ON invoices(tenant_id);

-- ── billing_plans (platform-level plan catalog) ───────────────────────────────
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
    created_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at      TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Seed default plans (idempotent)
INSERT INTO billing_plans (name, slug, price_monthly, price_yearly, duration_days, features, display_order)
VALUES
    ('Basic',        'basic',        0.00,   0.00,    30,  '{"max_projects": 5,   "custom_domain": false}', 1),
    ('Pro',          'pro',        299.00, 2990.00,   30,  '{"max_projects": 20,  "custom_domain": true}',  2),
    ('Business',     'business',   799.00, 7990.00,   30,  '{"max_projects": 100, "custom_domain": true}',  3)
ON CONFLICT (slug) DO NOTHING;

-- ── subscription_notifications ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS subscription_notifications (
    id              SERIAL PRIMARY KEY,
    tenant_id       INTEGER NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
    subscription_id INTEGER REFERENCES subscriptions(id) ON DELETE SET NULL,
    event_type      VARCHAR(50)  NOT NULL,
    sent_at         TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    recipient_email VARCHAR(255),
    success         BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX IF NOT EXISTS ix_sub_notif_tenant ON subscription_notifications(tenant_id);

-- ── Ensure tenant_form_settings is NOT created (MED-03) ─────────────────────
-- The 0022 migration creates this table, but form_provider and basin_endpoint
-- are columns on the tenants table directly. Drop the orphan table if it was
-- created by mistake.
-- UNCOMMENT ONLY IF YOU WANT TO CLEAN UP:
-- DROP TABLE IF EXISTS tenant_form_settings;

-- ── Updated_at trigger (optional, PG best practice) ──────────────────────────
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Apply to tables that have updated_at but no automatic trigger
DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['payments', 'invoices', 'billing_plans', 'subscription_notifications']
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_trigger
            WHERE tgname = 'set_updated_at_' || t
        ) THEN
            EXECUTE format(
                'CREATE TRIGGER set_updated_at_%I
                 BEFORE UPDATE ON %I
                 FOR EACH ROW EXECUTE FUNCTION update_updated_at_column()',
                t, t
            );
        END IF;
    END LOOP;
END;
$$;
