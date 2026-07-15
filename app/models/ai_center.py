"""Core-database models for the provider-agnostic AI control plane."""
from __future__ import annotations

import uuid

from sqlalchemy import event

from app.extensions import db
from app.models.core import decrypt_secret, encrypt_secret
from app.utils.datetime_utils import utc_now


def _uuid() -> str:
    return str(uuid.uuid4())


class AIProviderConfig(db.Model):
    __tablename__ = "ai_provider_configs"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    provider_code = db.Column(db.String(40), nullable=False, unique=True)
    display_name = db.Column(db.String(100), nullable=False)
    base_url = db.Column(db.String(500), nullable=False)
    _credential_ciphertext = db.Column("credential_ciphertext", db.Text, nullable=False, default="")
    enabled = db.Column(db.Boolean, nullable=False, default=False)
    timeout_seconds = db.Column(db.Integer, nullable=False, default=45)
    nonsecret_config = db.Column(db.JSON, nullable=False, default=dict)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    @property
    def api_key(self) -> str:
        return decrypt_secret(self._credential_ciphertext)

    @api_key.setter
    def api_key(self, value: str) -> None:
        self._credential_ciphertext = encrypt_secret(value) if value else ""

    @property
    def has_credential(self) -> bool:
        return bool(self._credential_ciphertext)

    @property
    def masked_credential(self) -> str:
        if not self._credential_ciphertext:
            return "Not configured"
        raw = self.api_key
        return f"••••{raw[-4:]}" if raw else "Stored — decryption unavailable"


class AIModelConfig(db.Model):
    __tablename__ = "ai_model_configs"
    __table_args__ = (
        db.UniqueConstraint("provider_config_id", "model_key", name="uq_ai_model_provider_key"),
        db.CheckConstraint("context_window > 0", name="ck_ai_model_context_positive"),
        db.CheckConstraint("input_price_microunits_per_million >= 0", name="ck_ai_model_input_price_nonnegative"),
        db.CheckConstraint("output_price_microunits_per_million >= 0", name="ck_ai_model_output_price_nonnegative"),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    provider_config_id = db.Column(
        db.String(36), db.ForeignKey("ai_provider_configs.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    model_key = db.Column(db.String(160), nullable=False)
    display_name = db.Column(db.String(160), nullable=False)
    capabilities = db.Column(db.JSON, nullable=False, default=list)
    enabled = db.Column(db.Boolean, nullable=False, default=False)
    context_window = db.Column(db.Integer, nullable=False, default=8192)
    input_price_microunits_per_million = db.Column(db.BigInteger, nullable=False, default=0)
    output_price_microunits_per_million = db.Column(db.BigInteger, nullable=False, default=0)
    pricing_currency = db.Column(db.String(3), nullable=False, default="USD")
    pricing_version = db.Column(db.String(80), nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    provider = db.relationship("AIProviderConfig", lazy="joined")


class AIFeaturePolicy(db.Model):
    __tablename__ = "ai_feature_policies"
    __table_args__ = (
        db.UniqueConstraint("scope_key", "feature_key", name="uq_ai_feature_scope_key"),
        db.CheckConstraint("daily_budget_microunits IS NULL OR daily_budget_microunits >= 0", name="ck_ai_feature_budget_nonnegative"),
        db.CheckConstraint("max_output_units > 0", name="ck_ai_feature_output_positive"),
        db.CheckConstraint("retention_days >= 0", name="ck_ai_feature_retention_nonnegative"),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    scope_key = db.Column(db.String(80), nullable=False, default="global")
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    feature_key = db.Column(db.String(80), nullable=False)
    enabled = db.Column(db.Boolean, nullable=False, default=False)
    model_config_id = db.Column(
        db.String(36), db.ForeignKey("ai_model_configs.id", ondelete="RESTRICT"), nullable=False
    )
    min_plan = db.Column(db.String(40), nullable=False, default="enterprise")
    daily_budget_microunits = db.Column(db.BigInteger, nullable=True)
    max_output_units = db.Column(db.Integer, nullable=False, default=1024)
    retention_days = db.Column(db.Integer, nullable=False, default=7)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)

    model = db.relationship("AIModelConfig", lazy="joined")


class AIPromptDefinition(db.Model):
    __tablename__ = "ai_prompt_definitions"

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    prompt_key = db.Column(db.String(80), nullable=False, unique=True)
    feature_key = db.Column(db.String(80), nullable=False, index=True)
    name = db.Column(db.String(160), nullable=False)
    description = db.Column(db.String(500), nullable=False, default="")
    active_version_id = db.Column(db.String(36), nullable=True)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class AIPromptVersion(db.Model):
    __tablename__ = "ai_prompt_versions"
    __table_args__ = (
        db.UniqueConstraint("prompt_id", "version_number", name="uq_ai_prompt_version_number"),
        db.CheckConstraint("version_number > 0", name="ck_ai_prompt_version_positive"),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    prompt_id = db.Column(
        db.String(36), db.ForeignKey("ai_prompt_definitions.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    version_number = db.Column(db.Integer, nullable=False)
    system_text = db.Column(db.Text, nullable=False, default="")
    template_text = db.Column(db.Text, nullable=False)
    variables = db.Column(db.JSON, nullable=False, default=list)
    change_note = db.Column(db.String(500), nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


class AIRequestJob(db.Model):
    __tablename__ = "ai_request_jobs"
    __table_args__ = (
        db.CheckConstraint(
            "status IN ('queued','running','succeeded','failed','retry_wait','cancelled')",
            name="ck_ai_job_status",
        ),
        db.CheckConstraint("attempt_count >= 0", name="ck_ai_job_attempt_nonnegative"),
        db.CheckConstraint("reserved_cost_microunits >= 0", name="ck_ai_job_reserved_nonnegative"),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    idempotency_key = db.Column(db.String(160), nullable=False, unique=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=True, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    feature_key = db.Column(db.String(80), nullable=False, index=True)
    model_config_id = db.Column(
        db.String(36), db.ForeignKey("ai_model_configs.id", ondelete="RESTRICT"), nullable=False
    )
    prompt_version_id = db.Column(
        db.String(36), db.ForeignKey("ai_prompt_versions.id", ondelete="RESTRICT"), nullable=True
    )
    operation = db.Column(db.String(30), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="queued", index=True)
    request_digest = db.Column(db.String(64), nullable=False)
    budget_date = db.Column(db.Date, nullable=False, default=lambda: utc_now().date())
    reserved_cost_microunits = db.Column(db.BigInteger, nullable=False, default=0)
    _request_ciphertext = db.Column("request_ciphertext", db.Text, nullable=False)
    _response_ciphertext = db.Column("response_ciphertext", db.Text, nullable=False, default="")
    attempt_count = db.Column(db.Integer, nullable=False, default=0)
    available_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    leased_until = db.Column(db.DateTime(timezone=True), nullable=True)
    last_error_class = db.Column(db.String(80), nullable=False, default="")
    last_error_message = db.Column(db.String(500), nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)
    completed_at = db.Column(db.DateTime(timezone=True), nullable=True)

    model = db.relationship("AIModelConfig", lazy="joined")

    @property
    def request_payload(self) -> str:
        return decrypt_secret(self._request_ciphertext)

    @request_payload.setter
    def request_payload(self, value: str) -> None:
        self._request_ciphertext = encrypt_secret(value)

    @property
    def response_payload(self) -> str:
        return decrypt_secret(self._response_ciphertext)

    @response_payload.setter
    def response_payload(self, value: str) -> None:
        self._response_ciphertext = encrypt_secret(value) if value else ""


class AIUsageRequest(db.Model):
    __tablename__ = "ai_usage_requests"
    __table_args__ = (
        db.CheckConstraint("input_units IS NULL OR input_units >= 0", name="ck_ai_usage_input_nonnegative"),
        db.CheckConstraint("output_units IS NULL OR output_units >= 0", name="ck_ai_usage_output_nonnegative"),
        db.CheckConstraint("latency_ms >= 0", name="ck_ai_usage_latency_nonnegative"),
        db.CheckConstraint("cost_microunits IS NULL OR cost_microunits >= 0", name="ck_ai_usage_cost_nonnegative"),
        db.CheckConstraint("outcome IN ('succeeded','failed','cancelled')", name="ck_ai_usage_outcome"),
        db.Index("ix_ai_usage_tenant_created", "tenant_id", "created_at"),
        db.Index("ix_ai_usage_provider_created", "provider_code", "created_at"),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    job_id = db.Column(
        db.String(36), db.ForeignKey("ai_request_jobs.id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="RESTRICT"), nullable=True, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    feature_key = db.Column(db.String(80), nullable=False, index=True)
    provider_code = db.Column(db.String(40), nullable=False, index=True)
    model_key = db.Column(db.String(160), nullable=False)
    operation = db.Column(db.String(30), nullable=False)
    prompt_version_id = db.Column(db.String(36), nullable=True)
    input_units = db.Column(db.Integer, nullable=True)
    output_units = db.Column(db.Integer, nullable=True)
    latency_ms = db.Column(db.Integer, nullable=False, default=0)
    outcome = db.Column(db.String(20), nullable=False)
    provider_request_hash = db.Column(db.String(64), nullable=False, default="")
    provider_request_suffix = db.Column(db.String(8), nullable=False, default="")
    cost_microunits = db.Column(db.BigInteger, nullable=True)
    pricing_currency = db.Column(db.String(3), nullable=False, default="USD")
    pricing_snapshot = db.Column(db.JSON, nullable=False, default=dict)
    error_class = db.Column(db.String(80), nullable=False, default="")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, index=True)


class AIUsageDaily(db.Model):
    __tablename__ = "ai_usage_daily"
    __table_args__ = (
        db.UniqueConstraint("usage_date", "scope_key", "feature_key", name="uq_ai_daily_scope_feature"),
        db.CheckConstraint("reserved_microunits >= 0", name="ck_ai_daily_reserved_nonnegative"),
        db.CheckConstraint("actual_microunits >= 0", name="ck_ai_daily_actual_nonnegative"),
        db.CheckConstraint("request_count >= 0", name="ck_ai_daily_requests_nonnegative"),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    usage_date = db.Column(db.Date, nullable=False)
    scope_key = db.Column(db.String(80), nullable=False)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True, index=True)
    feature_key = db.Column(db.String(80), nullable=False)
    reserved_microunits = db.Column(db.BigInteger, nullable=False, default=0)
    actual_microunits = db.Column(db.BigInteger, nullable=False, default=0)
    request_count = db.Column(db.Integer, nullable=False, default=0)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class AIAuditEvent(db.Model):
    __tablename__ = "ai_audit_events"
    __table_args__ = (
        db.Index("ix_ai_audit_created", "created_at"),
        db.Index("ix_ai_audit_entity", "entity_type", "entity_id"),
    )

    id = db.Column(db.String(36), primary_key=True, default=_uuid)
    actor_user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    tenant_id = db.Column(db.Integer, db.ForeignKey("tenants.id", ondelete="SET NULL"), nullable=True)
    event_type = db.Column(db.String(80), nullable=False)
    entity_type = db.Column(db.String(80), nullable=False)
    entity_id = db.Column(db.String(160), nullable=False)
    safe_metadata = db.Column(db.JSON, nullable=False, default=dict)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


def _reject_append_only_mutation(_mapper, _connection, target):
    raise RuntimeError(f"{target.__class__.__name__} is append-only")


for _model in (AIPromptVersion, AIUsageRequest, AIAuditEvent):
    event.listen(_model, "before_update", _reject_append_only_mutation)
    event.listen(_model, "before_delete", _reject_append_only_mutation)
