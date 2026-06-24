-- ============================================================
-- Migration: 0022_tenant_form_settings
-- Adds per-tenant form provider isolation (Basin + Web3Forms)
-- Backward-compatible: no existing columns are dropped.
-- Author: Portfolio CMS v4.2
-- ============================================================

BEGIN;

-- ── 1. ENUM type for provider ─────────────────────────────────────────────
DO $$ BEGIN
    CREATE TYPE form_provider_enum AS ENUM ('basin', 'web3forms', 'disabled');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ── 2. tenant_form_settings table ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS tenant_form_settings (
    id               SERIAL PRIMARY KEY,
    tenant_id        INTEGER NOT NULL
                     REFERENCES tenants(id) ON DELETE CASCADE,

    provider         form_provider_enum NOT NULL DEFAULT 'disabled',

    -- Fernet-encrypted; NEVER store plaintext
    api_key_encrypted TEXT    NOT NULL DEFAULT '',

    -- Basin: https://usebasin.com/f/<id>
    -- Web3Forms: https://api.web3forms.com/submit (static, key is in api_key)
    form_endpoint    TEXT,

    receiver_email   VARCHAR(200),
    sender_name      VARCHAR(200),

    is_enabled       BOOLEAN NOT NULL DEFAULT FALSE,

    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    CONSTRAINT uq_tenant_form_settings UNIQUE (tenant_id)
);

-- ── 3. Indexes ─────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS ix_tfs_tenant_id
    ON tenant_form_settings (tenant_id);

CREATE INDEX IF NOT EXISTS ix_tfs_provider
    ON tenant_form_settings (provider);

CREATE INDEX IF NOT EXISTS ix_tfs_is_enabled
    ON tenant_form_settings (is_enabled);

-- ── 4. updated_at trigger ─────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_tfs_updated_at ON tenant_form_settings;
CREATE TRIGGER trg_tfs_updated_at
    BEFORE UPDATE ON tenant_form_settings
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ── 5. Backfill: migrate existing basin tenants ───────────────────────────
-- Creates a row in tenant_form_settings for every tenant that already has
-- form_provider = 'basin' set in the tenants table (from migration 0021).
INSERT INTO tenant_form_settings (
    tenant_id,
    provider,
    form_endpoint,
    receiver_email,
    sender_name,
    is_enabled,
    created_at,
    updated_at
)
SELECT
    t.id,
    'basin'::form_provider_enum,
    t.basin_endpoint,
    t.contact_email,
    t.company_name,
    CASE WHEN t.basin_endpoint IS NOT NULL AND t.basin_endpoint != '' THEN TRUE ELSE FALSE END,
    NOW(),
    NOW()
FROM tenants t
WHERE t.form_provider = 'basin'
  AND t.basin_endpoint IS NOT NULL
  AND t.basin_endpoint != ''
ON CONFLICT (tenant_id) DO NOTHING;

COMMIT;

-- ── Rollback script (run manually if needed) ──────────────────────────────
-- BEGIN;
-- DROP TABLE IF EXISTS tenant_form_settings;
-- DROP TYPE IF EXISTS form_provider_enum;
-- COMMIT;
