-- =============================================================================
-- rollback.sql — Portfolio CMS v3.9 Migration Rollback
-- Reverses all changes in upgrade.sql.
-- WARNING: Dropping payments/invoices/plans tables loses data. Back up first.
-- =============================================================================

-- ─── 8. Remove added indexes ─────────────────────────────────────────────────
DROP INDEX IF EXISTS ix_subscriptions_paymongo_payment_id ON subscriptions;
DROP INDEX IF EXISTS ix_subscriptions_expires_at          ON subscriptions;
DROP INDEX IF EXISTS ix_otp_user_type_user_id             ON password_reset_otps;

-- ─── 7. Drop rate_limit_log ───────────────────────────────────────────────────
DROP TABLE IF EXISTS rate_limit_log;

-- ─── 6. Remove used_at from password_reset_otps ──────────────────────────────
ALTER TABLE password_reset_otps DROP COLUMN IF EXISTS used_at;

-- ─── 5. Drop plans table ─────────────────────────────────────────────────────
DROP TABLE IF EXISTS plans;

-- ─── 4. Drop invoices table ──────────────────────────────────────────────────
DROP TABLE IF EXISTS invoices;

-- ─── 3. Drop payments table ──────────────────────────────────────────────────
DROP TABLE IF EXISTS payments;

-- ─── 2. Remove webhook_events additions ──────────────────────────────────────
ALTER TABLE webhook_events
    DROP COLUMN IF EXISTS payload,
    DROP COLUMN IF EXISTS processed_at;

-- ─── 1. Remove subscriptions additions ───────────────────────────────────────
DROP INDEX IF EXISTS ix_subscriptions_paymongo_id ON subscriptions;

ALTER TABLE subscriptions
    DROP COLUMN IF EXISTS external_checkout_id,
    DROP COLUMN IF EXISTS renewal_date;
-- Note: started_at is retained as it may be used by existing code.
-- To also remove: ALTER TABLE subscriptions DROP COLUMN IF EXISTS started_at;
