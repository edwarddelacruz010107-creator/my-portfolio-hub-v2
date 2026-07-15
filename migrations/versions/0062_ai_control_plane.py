"""add provider-agnostic AI control plane

Revision ID: 0062
Revises: 0061
"""
from alembic import op
import sqlalchemy as sa


revision = "0062"
down_revision = "0061"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "ai_provider_configs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider_code", sa.String(length=40), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("credential_ciphertext", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("timeout_seconds", sa.Integer(), nullable=False),
        sa.Column("nonsecret_config", sa.JSON(), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("timeout_seconds BETWEEN 1 AND 300", name="ck_ai_provider_timeout"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_code", name="uq_ai_provider_code"),
    )
    op.create_table(
        "ai_model_configs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("provider_config_id", sa.String(length=36), nullable=False),
        sa.Column("model_key", sa.String(length=160), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("context_window", sa.Integer(), nullable=False),
        sa.Column("input_price_microunits_per_million", sa.BigInteger(), nullable=False),
        sa.Column("output_price_microunits_per_million", sa.BigInteger(), nullable=False),
        sa.Column("pricing_currency", sa.String(length=3), nullable=False),
        sa.Column("pricing_version", sa.String(length=80), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("context_window > 0", name="ck_ai_model_context_positive"),
        sa.CheckConstraint("input_price_microunits_per_million >= 0", name="ck_ai_model_input_price_nonnegative"),
        sa.CheckConstraint("output_price_microunits_per_million >= 0", name="ck_ai_model_output_price_nonnegative"),
        sa.ForeignKeyConstraint(["provider_config_id"], ["ai_provider_configs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_config_id", "model_key", name="uq_ai_model_provider_key"),
    )
    op.create_index("ix_ai_model_configs_provider_config_id", "ai_model_configs", ["provider_config_id"])
    op.create_table(
        "ai_feature_policies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("scope_key", sa.String(length=80), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("feature_key", sa.String(length=80), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("model_config_id", sa.String(length=36), nullable=False),
        sa.Column("min_plan", sa.String(length=40), nullable=False),
        sa.Column("daily_budget_microunits", sa.BigInteger(), nullable=True),
        sa.Column("max_output_units", sa.Integer(), nullable=False),
        sa.Column("retention_days", sa.Integer(), nullable=False),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("daily_budget_microunits IS NULL OR daily_budget_microunits >= 0", name="ck_ai_feature_budget_nonnegative"),
        sa.CheckConstraint("max_output_units > 0", name="ck_ai_feature_output_positive"),
        sa.CheckConstraint("retention_days >= 0", name="ck_ai_feature_retention_nonnegative"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["model_config_id"], ["ai_model_configs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_key", "feature_key", name="uq_ai_feature_scope_key"),
    )
    op.create_index("ix_ai_feature_policies_tenant_id", "ai_feature_policies", ["tenant_id"])
    op.create_table(
        "ai_prompt_definitions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("prompt_key", sa.String(length=80), nullable=False),
        sa.Column("feature_key", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.String(length=500), nullable=False),
        sa.Column("active_version_id", sa.String(length=36), nullable=True),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("updated_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("prompt_key", name="uq_ai_prompt_key"),
    )
    op.create_index("ix_ai_prompt_definitions_feature_key", "ai_prompt_definitions", ["feature_key"])
    op.create_table(
        "ai_prompt_versions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("prompt_id", sa.String(length=36), nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("system_text", sa.Text(), nullable=False),
        sa.Column("template_text", sa.Text(), nullable=False),
        sa.Column("variables", sa.JSON(), nullable=False),
        sa.Column("change_note", sa.String(length=500), nullable=False),
        sa.Column("created_by_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version_number > 0", name="ck_ai_prompt_version_positive"),
        sa.ForeignKeyConstraint(["prompt_id"], ["ai_prompt_definitions.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("prompt_id", "version_number", name="uq_ai_prompt_version_number"),
    )
    op.create_index("ix_ai_prompt_versions_prompt_id", "ai_prompt_versions", ["prompt_id"])
    op.create_table(
        "ai_request_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("feature_key", sa.String(length=80), nullable=False),
        sa.Column("model_config_id", sa.String(length=36), nullable=False),
        sa.Column("prompt_version_id", sa.String(length=36), nullable=True),
        sa.Column("operation", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("request_digest", sa.String(length=64), nullable=False),
        sa.Column("budget_date", sa.Date(), nullable=False),
        sa.Column("reserved_cost_microunits", sa.BigInteger(), nullable=False),
        sa.Column("request_ciphertext", sa.Text(), nullable=False),
        sa.Column("response_ciphertext", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("leased_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_class", sa.String(length=80), nullable=False),
        sa.Column("last_error_message", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("status IN ('queued','running','succeeded','failed','retry_wait','cancelled')", name="ck_ai_job_status"),
        sa.CheckConstraint("attempt_count >= 0", name="ck_ai_job_attempt_nonnegative"),
        sa.CheckConstraint("reserved_cost_microunits >= 0", name="ck_ai_job_reserved_nonnegative"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["model_config_id"], ["ai_model_configs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["prompt_version_id"], ["ai_prompt_versions.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key", name="uq_ai_job_idempotency"),
    )
    op.create_index("ix_ai_request_jobs_tenant_id", "ai_request_jobs", ["tenant_id"])
    op.create_index("ix_ai_request_jobs_feature_key", "ai_request_jobs", ["feature_key"])
    op.create_index("ix_ai_request_jobs_status", "ai_request_jobs", ["status"])
    op.create_table(
        "ai_usage_requests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("feature_key", sa.String(length=80), nullable=False),
        sa.Column("provider_code", sa.String(length=40), nullable=False),
        sa.Column("model_key", sa.String(length=160), nullable=False),
        sa.Column("operation", sa.String(length=30), nullable=False),
        sa.Column("prompt_version_id", sa.String(length=36), nullable=True),
        sa.Column("input_units", sa.Integer(), nullable=True),
        sa.Column("output_units", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=False),
        sa.Column("provider_request_hash", sa.String(length=64), nullable=False),
        sa.Column("provider_request_suffix", sa.String(length=8), nullable=False),
        sa.Column("cost_microunits", sa.BigInteger(), nullable=True),
        sa.Column("pricing_currency", sa.String(length=3), nullable=False),
        sa.Column("pricing_snapshot", sa.JSON(), nullable=False),
        sa.Column("error_class", sa.String(length=80), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("input_units IS NULL OR input_units >= 0", name="ck_ai_usage_input_nonnegative"),
        sa.CheckConstraint("output_units IS NULL OR output_units >= 0", name="ck_ai_usage_output_nonnegative"),
        sa.CheckConstraint("latency_ms >= 0", name="ck_ai_usage_latency_nonnegative"),
        sa.CheckConstraint("cost_microunits IS NULL OR cost_microunits >= 0", name="ck_ai_usage_cost_nonnegative"),
        sa.CheckConstraint("outcome IN ('succeeded','failed','cancelled')", name="ck_ai_usage_outcome"),
        sa.ForeignKeyConstraint(["job_id"], ["ai_request_jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_ai_usage_job"),
    )
    op.create_index("ix_ai_usage_requests_tenant_id", "ai_usage_requests", ["tenant_id"])
    op.create_index("ix_ai_usage_requests_feature_key", "ai_usage_requests", ["feature_key"])
    op.create_index("ix_ai_usage_requests_provider_code", "ai_usage_requests", ["provider_code"])
    op.create_index("ix_ai_usage_requests_created_at", "ai_usage_requests", ["created_at"])
    op.create_table(
        "ai_usage_daily",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("usage_date", sa.Date(), nullable=False),
        sa.Column("scope_key", sa.String(length=80), nullable=False),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("feature_key", sa.String(length=80), nullable=False),
        sa.Column("reserved_microunits", sa.BigInteger(), nullable=False),
        sa.Column("actual_microunits", sa.BigInteger(), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("reserved_microunits >= 0", name="ck_ai_daily_reserved_nonnegative"),
        sa.CheckConstraint("actual_microunits >= 0", name="ck_ai_daily_actual_nonnegative"),
        sa.CheckConstraint("request_count >= 0", name="ck_ai_daily_requests_nonnegative"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("usage_date", "scope_key", "feature_key", name="uq_ai_daily_scope_feature"),
    )
    op.create_index("ix_ai_usage_daily_tenant_id", "ai_usage_daily", ["tenant_id"])
    op.create_table(
        "ai_audit_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("actor_user_id", sa.Integer(), nullable=True),
        sa.Column("tenant_id", sa.Integer(), nullable=True),
        sa.Column("event_type", sa.String(length=80), nullable=False),
        sa.Column("entity_type", sa.String(length=80), nullable=False),
        sa.Column("entity_id", sa.String(length=160), nullable=False),
        sa.Column("safe_metadata", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ai_audit_created", "ai_audit_events", ["created_at"])
    op.create_index("ix_ai_audit_entity", "ai_audit_events", ["entity_type", "entity_id"])

    if op.get_bind().dialect.name == "postgresql":
        op.execute("""
            CREATE FUNCTION reject_ai_append_only_mutation() RETURNS trigger AS $$
            BEGIN
              RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
            END;
            $$ LANGUAGE plpgsql
        """)
        for table in ("ai_prompt_versions", "ai_usage_requests", "ai_audit_events"):
            op.execute(
                f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION reject_ai_append_only_mutation()"
            )


def downgrade():
    if op.get_bind().dialect.name == "postgresql":
        for table in ("ai_prompt_versions", "ai_usage_requests", "ai_audit_events"):
            op.execute(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}")
        op.execute("DROP FUNCTION IF EXISTS reject_ai_append_only_mutation()")
    op.drop_index("ix_ai_audit_entity", table_name="ai_audit_events")
    op.drop_index("ix_ai_audit_created", table_name="ai_audit_events")
    op.drop_table("ai_audit_events")
    op.drop_index("ix_ai_usage_daily_tenant_id", table_name="ai_usage_daily")
    op.drop_table("ai_usage_daily")
    op.drop_index("ix_ai_usage_requests_created_at", table_name="ai_usage_requests")
    op.drop_index("ix_ai_usage_requests_provider_code", table_name="ai_usage_requests")
    op.drop_index("ix_ai_usage_requests_feature_key", table_name="ai_usage_requests")
    op.drop_index("ix_ai_usage_requests_tenant_id", table_name="ai_usage_requests")
    op.drop_table("ai_usage_requests")
    op.drop_index("ix_ai_request_jobs_status", table_name="ai_request_jobs")
    op.drop_index("ix_ai_request_jobs_feature_key", table_name="ai_request_jobs")
    op.drop_index("ix_ai_request_jobs_tenant_id", table_name="ai_request_jobs")
    op.drop_table("ai_request_jobs")
    op.drop_index("ix_ai_prompt_versions_prompt_id", table_name="ai_prompt_versions")
    op.drop_table("ai_prompt_versions")
    op.drop_index("ix_ai_prompt_definitions_feature_key", table_name="ai_prompt_definitions")
    op.drop_table("ai_prompt_definitions")
    op.drop_index("ix_ai_feature_policies_tenant_id", table_name="ai_feature_policies")
    op.drop_table("ai_feature_policies")
    op.drop_index("ix_ai_model_configs_provider_config_id", table_name="ai_model_configs")
    op.drop_table("ai_model_configs")
    op.drop_table("ai_provider_configs")
