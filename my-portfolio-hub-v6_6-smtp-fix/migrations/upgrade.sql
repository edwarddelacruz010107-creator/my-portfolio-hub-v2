-- =============================================================================
-- upgrade.sql — Portfolio CMS v3.9 Production Readiness Migration
-- Covers: BUG-002, BUG-012, SCHEMA-001..005
-- Safe to re-run: all statements use IF NOT EXISTS / IGNORE semantics.
-- =============================================================================

-- ─────────────────────────────────────────────────────────────────────────────
-- 1. subscriptions — add missing columns and constraints
-- ─────────────────────────────────────────────────────────────────────────────

-- BUG-002 / SCHEMA-001: paymongo_id already exists but may lack UNIQUE.
-- Add external_checkout_id as the spec-required dedicated column (maps to session_id).
ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS external_checkout_id VARCHAR(255) NULL UNIQUE
        COMMENT 'PayMongo checkout session ID — unique constraint prevents duplicate checkout links';

-- Backfill from existing paymongo_id where external_checkout_id is NULL
UPDATE subscriptions
SET    external_checkout_id = paymongo_id
WHERE  external_checkout_id IS NULL
  AND  paymongo_id IS NOT NULL;

-- Add renewal_date and cancelled_at if missing
ALTER TABLE subscriptions
    ADD COLUMN IF NOT EXISTS renewal_date DATETIME NULL,
    ADD COLUMN IF NOT EXISTS started_at   DATETIME NULL;

-- Ensure paymongo_id index
CREATE INDEX IF NOT EXISTS ix_subscriptions_paymongo_id ON subscriptions (paymongo_id);

-- ─────────────────────────────────────────────────────────────────────────────
-- 2. webhook_events — add full payload column (SCHEMA-002)
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE webhook_events
    ADD COLUMN IF NOT EXISTS payload      MEDIUMTEXT NULL
        COMMENT 'Full raw JSON payload for debugging and replay',
    ADD COLUMN IF NOT EXISTS processed_at DATETIME   NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- 3. payments — dedicated table (SCHEMA-003)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS payments (
    id                   INT           NOT NULL AUTO_INCREMENT,
    subscription_id      INT           NOT NULL,
    tenant_id            INT           NOT NULL,
    amount               DECIMAL(12,2) NOT NULL,
    currency             VARCHAR(3)    NOT NULL DEFAULT 'PHP',
    status               VARCHAR(30)   NOT NULL DEFAULT 'pending',
    external_payment_id  VARCHAR(255)  NULL UNIQUE  COMMENT 'PayMongo payment ID',
    external_checkout_id VARCHAR(255)  NULL         COMMENT 'PayMongo checkout session ID',
    created_at           DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    CONSTRAINT fk_payments_subscription FOREIGN KEY (subscription_id)
        REFERENCES subscriptions (id) ON DELETE RESTRICT ON UPDATE CASCADE,
    INDEX ix_payments_subscription_id (subscription_id),
    INDEX ix_payments_tenant_id       (tenant_id),
    INDEX ix_payments_status          (status),
    INDEX ix_payments_external_pay    (external_payment_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ─────────────────────────────────────────────────────────────────────────────
-- 4. invoices table (spec requirement)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS invoices (
    id              INT           NOT NULL AUTO_INCREMENT,
    subscription_id INT           NOT NULL,
    invoice_number  VARCHAR(50)   NOT NULL UNIQUE,
    amount          DECIMAL(12,2) NOT NULL,
    status          VARCHAR(30)   NOT NULL DEFAULT 'pending',
    issued_at       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    paid_at         DATETIME      NULL,
    created_at      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    CONSTRAINT fk_invoices_subscription FOREIGN KEY (subscription_id)
        REFERENCES subscriptions (id) ON DELETE RESTRICT ON UPDATE CASCADE,
    INDEX ix_invoices_subscription_id (subscription_id),
    INDEX ix_invoices_status           (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ─────────────────────────────────────────────────────────────────────────────
-- 5. plans table (spec requirement / SCHEMA-005)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS plans (
    id            INT           NOT NULL AUTO_INCREMENT,
    name          VARCHAR(80)   NOT NULL UNIQUE,
    slug          VARCHAR(80)   NOT NULL UNIQUE,
    price_monthly DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    price_yearly  DECIMAL(10,2) NOT NULL DEFAULT 0.00,
    features      TEXT          NULL     COMMENT 'JSON array of feature strings',
    is_active     TINYINT(1)    NOT NULL DEFAULT 1,
    created_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    INDEX ix_plans_slug   (slug),
    INDEX ix_plans_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Seed from BILLING_PLANS defaults if table is empty
INSERT IGNORE INTO plans (name, slug, price_monthly, price_yearly, features)
VALUES
    ('Basic',      'basic',      0.00,    0.00,   '["1 portfolio","5 projects","Basic themes"]'),
    ('Pro',        'pro',        299.00,  2990.00, '["Unlimited projects","Custom domain","Analytics","Priority support"]'),
    ('Enterprise', 'enterprise', 999.00,  9990.00, '["Everything in Pro","White-label","SLA","Dedicated support"]');

-- ─────────────────────────────────────────────────────────────────────────────
-- 6. password_reset_requests — add used_at (SCHEMA-004)
-- ─────────────────────────────────────────────────────────────────────────────

ALTER TABLE password_reset_otps
    ADD COLUMN IF NOT EXISTS used_at DATETIME NULL
        COMMENT 'Timestamp when OTP was successfully used';

-- Backfill used_at for already-used records
UPDATE password_reset_otps
SET    used_at = updated_at
WHERE  used = 1 AND used_at IS NULL;

-- ─────────────────────────────────────────────────────────────────────────────
-- 7. Rate-limit tracking table (supports BUG-009 fix at DB level)
-- ─────────────────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS rate_limit_log (
    id         INT          NOT NULL AUTO_INCREMENT,
    action     VARCHAR(50)  NOT NULL COMMENT 'e.g. pw_reset_request, otp_verify',
    identifier VARCHAR(255) NOT NULL COMMENT 'email or IP depending on action',
    attempted_at DATETIME   NOT NULL DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (id),
    INDEX ix_rll_action_identifier_time (action, identifier, attempted_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ─────────────────────────────────────────────────────────────────────────────
-- 8. Add missing indexes
-- ─────────────────────────────────────────────────────────────────────────────

-- Subscription lookup by paymongo_payment_id (already indexed but ensure)
CREATE INDEX IF NOT EXISTS ix_subscriptions_paymongo_payment_id
    ON subscriptions (paymongo_payment_id);

-- Faster expiry scanning
CREATE INDEX IF NOT EXISTS ix_subscriptions_expires_at
    ON subscriptions (expires_at);

-- OTP cleanup
CREATE INDEX IF NOT EXISTS ix_otp_user_type_user_id
    ON password_reset_otps (user_type, user_id);
