"""Single policy, budget, idempotency, provider, and usage boundary for AI."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
import hashlib
import json
import time
from typing import Any, Mapping

from sqlalchemy import text

from app import db
from app.models.ai_center import (
    AIAuditEvent,
    AIFeaturePolicy,
    AIModelConfig,
    AIPromptDefinition,
    AIPromptVersion,
    AIProviderConfig,
    AIRequestJob,
    AIUsageDaily,
    AIUsageRequest,
)
from app.models.core import Tenant, normalize_plan_name
from app.services.ai.adapters import ProviderResult, build_adapter
from app.services.ai.domain import (
    AIBudgetExceeded,
    AIContractError,
    AIProviderError,
    AIRequest,
    AIUnavailableError,
    CONTROL_PLANE_VERSION,
    PRICING_SNAPSHOT_VERSION,
    PROVIDER_CATALOG,
    calculate_cost_microunits,
    estimate_text_units,
    redact_sensitive,
    render_prompt,
    request_digest,
    safe_error_class,
    safe_error_message,
    validate_capabilities,
    validate_key,
)
from app.utils.datetime_utils import utc_now


PLAN_RANK = {
    "trial": 0, "basic": 1, "starter": 1, "pro": 2,
    "premium": 2, "enterprise": 3, "business": 3, "agency": 3,
    "administrator": 99, "admin": 99,
}
MAX_ATTEMPTS = 3
RETRY_SECONDS = (30, 120, 600)


def _scope_key(tenant_id: int | None) -> str:
    return f"tenant:{int(tenant_id)}" if tenant_id is not None else "global"


def _audit(event_type: str, entity_type: str, entity_id: str, *, actor_user_id=None, tenant_id=None, metadata=None):
    db.session.add(AIAuditEvent(
        actor_user_id=actor_user_id,
        tenant_id=tenant_id,
        event_type=validate_key(event_type, label="audit event"),
        entity_type=validate_key(entity_type, label="audit entity"),
        entity_id=str(entity_id or "")[:160],
        safe_metadata=redact_sensitive(metadata or {}),
    ))


class AIService:
    """Provider-neutral execution API. Features call this class, never adapters."""

    def _policy(self, tenant_id: int | None, feature_key: str) -> AIFeaturePolicy:
        feature = validate_key(feature_key, label="feature key")
        if tenant_id is not None:
            policy = AIFeaturePolicy.query.filter_by(
                scope_key=_scope_key(tenant_id), tenant_id=int(tenant_id), feature_key=feature
            ).first()
            if policy is not None:
                return policy
        policy = AIFeaturePolicy.query.filter_by(scope_key="global", feature_key=feature).first()
        if policy is None:
            raise AIUnavailableError("AI feature is not configured")
        return policy

    def _authorize_policy(self, policy: AIFeaturePolicy, tenant_id: int | None) -> None:
        if not policy.enabled:
            raise AIUnavailableError("AI feature is disabled")
        model = policy.model
        if not model or not model.enabled or not model.provider or not model.provider.enabled:
            raise AIUnavailableError("AI feature has no enabled provider and model")
        provider_meta = PROVIDER_CATALOG.get(model.provider.provider_code)
        if not provider_meta:
            raise AIUnavailableError("AI provider is not supported")
        if provider_meta["requires_key"] and not model.provider.api_key:
            raise AIUnavailableError("AI provider credentials are unavailable")
        if tenant_id is None:
            return
        tenant = Tenant.query.filter_by(id=int(tenant_id), status="active").first()
        if tenant is None:
            raise AIUnavailableError("tenant is not active")
        have = PLAN_RANK.get(str(normalize_plan_name(getattr(tenant, "plan", "Basic"))).lower(), 0)
        need = PLAN_RANK.get(str(policy.min_plan or "enterprise").lower(), 99)
        if have < need:
            raise AIUnavailableError("tenant plan does not permit this AI feature")

    def _apply_prompt(
        self,
        request: AIRequest,
        prompt_key: str | None,
        prompt_values: Mapping[str, Any] | None,
    ) -> tuple[AIRequest, AIPromptVersion | None]:
        if not prompt_key:
            return request, None
        prompt = AIPromptDefinition.query.filter_by(
            prompt_key=validate_key(prompt_key, label="prompt key"),
            feature_key=request.feature_key,
        ).first()
        if prompt is None or not prompt.active_version_id:
            raise AIUnavailableError("prompt is not published")
        version = AIPromptVersion.query.filter_by(id=prompt.active_version_id, prompt_id=prompt.id).first()
        if version is None:
            raise AIUnavailableError("published prompt version is unavailable")
        rendered = render_prompt(version.template_text, prompt_values or {}, version.variables or [])
        return replace(request, input_text=rendered, system_text=version.system_text or request.system_text), version

    def _reserve_budget(
        self,
        policy: AIFeaturePolicy,
        tenant_id: int | None,
        estimated_cost: int,
        *,
        usage_date=None,
    ) -> AIUsageDaily:
        today = usage_date or utc_now().date()
        scope = _scope_key(tenant_id)
        bind = db.session.get_bind(mapper=AIUsageDaily)
        if bind.dialect.name == "postgresql":
            db.session.execute(
                text("SELECT pg_advisory_xact_lock(hashtextextended(:budget_key, 0))"),
                {"budget_key": f"ai-budget:{today.isoformat()}:{scope}:{policy.feature_key}"},
            )
        row = AIUsageDaily.query.filter_by(
            usage_date=today, scope_key=scope, feature_key=policy.feature_key
        ).with_for_update().first()
        if row is None:
            row = AIUsageDaily(
                usage_date=today,
                scope_key=scope,
                tenant_id=tenant_id,
                feature_key=policy.feature_key,
                reserved_microunits=0,
                actual_microunits=0,
                request_count=0,
            )
            db.session.add(row)
            db.session.flush()
        budget = policy.daily_budget_microunits
        projected = int(row.actual_microunits or 0) + int(row.reserved_microunits or 0) + estimated_cost
        if budget is not None and projected > int(budget):
            raise AIBudgetExceeded("AI daily budget would be exceeded")
        row.reserved_microunits = int(row.reserved_microunits or 0) + estimated_cost
        return row

    def _settle_budget(
        self,
        job: AIRequestJob,
        reserved_cost: int,
        actual_cost: int | None,
        *,
        count_request: bool,
    ) -> None:
        row = AIUsageDaily.query.filter_by(
            usage_date=job.budget_date,
            scope_key=_scope_key(job.tenant_id),
            feature_key=job.feature_key,
        ).with_for_update().first()
        if row is None:
            return
        row.reserved_microunits = max(0, int(row.reserved_microunits or 0) - reserved_cost)
        if actual_cost is not None:
            row.actual_microunits = int(row.actual_microunits or 0) + int(actual_cost)
        if count_request:
            row.request_count = int(row.request_count or 0) + 1

    def execute(
        self,
        request: AIRequest,
        *,
        tenant_id: int | None,
        user_id: int | None,
        idempotency_key: str,
        prompt_key: str | None = None,
        prompt_values: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        policy = self._policy(tenant_id, request.feature_key)
        self._authorize_policy(policy, tenant_id)
        request, prompt_version = self._apply_prompt(request, prompt_key, prompt_values)
        if request.max_output_units > policy.max_output_units:
            raise AIContractError("requested output exceeds the feature policy")
        model = policy.model
        capabilities = validate_capabilities(model.capabilities or [])
        if request.operation not in capabilities:
            raise AIUnavailableError("selected model does not support this operation")
        key = str(idempotency_key or "").strip()
        if not 16 <= len(key) <= 160:
            raise AIContractError("idempotency key must be 16 to 160 characters")
        payload = request.safe_payload()
        digest = request_digest(payload)
        existing = AIRequestJob.query.filter_by(idempotency_key=key).first()
        if existing is not None:
            if existing.request_digest != digest or existing.tenant_id != tenant_id or existing.user_id != user_id:
                raise AIContractError("idempotency key was already used for a different request")
            if existing.status == "succeeded" and existing.response_payload:
                return json.loads(existing.response_payload)
            raise AIUnavailableError(f"AI request is currently {existing.status}")

        estimated_input = estimate_text_units(request.input_text + request.system_text)
        estimated_cost = calculate_cost_microunits(
            estimated_input,
            request.max_output_units,
            model.input_price_microunits_per_million,
            model.output_price_microunits_per_million,
        )
        job = AIRequestJob(
            idempotency_key=key,
            tenant_id=tenant_id,
            user_id=user_id,
            feature_key=policy.feature_key,
            model_config_id=model.id,
            prompt_version_id=prompt_version.id if prompt_version else None,
            operation=request.operation,
            status="queued",
            request_digest=digest,
            budget_date=utc_now().date(),
            reserved_cost_microunits=estimated_cost,
            attempt_count=0,
        )
        job.request_payload = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        db.session.add(job)
        db.session.flush()
        self._reserve_budget(policy, tenant_id, estimated_cost)
        _audit(
            "ai.request.queued", "ai.request", job.id,
            actor_user_id=user_id, tenant_id=tenant_id,
            metadata={"feature": policy.feature_key, "operation": request.operation, "model_id": model.id},
        )
        db.session.commit()
        return self._execute_job(job.id, reserved_cost=estimated_cost)

    def _execute_job(self, job_id: str, *, reserved_cost: int | None = None) -> dict[str, Any]:
        job = AIRequestJob.query.filter_by(id=job_id).with_for_update().first()
        if job is None:
            raise AIUnavailableError("AI request job was not found")
        if job.status == "succeeded" and job.response_payload:
            return json.loads(job.response_payload)
        model = AIModelConfig.query.filter_by(id=job.model_config_id).first()
        if model is None or not model.provider:
            raise AIUnavailableError("AI request model is unavailable")
        policy = self._policy(job.tenant_id, job.feature_key)
        self._authorize_policy(policy, job.tenant_id)
        payload = json.loads(job.request_payload)
        request = AIRequest(**payload)
        if reserved_cost is None:
            reserved_cost = int(job.reserved_cost_microunits or 0)
            if reserved_cost == 0:
                reserved_cost = calculate_cost_microunits(
                    estimate_text_units(request.input_text + request.system_text),
                    request.max_output_units,
                    model.input_price_microunits_per_million,
                    model.output_price_microunits_per_million,
                )
                self._reserve_budget(policy, job.tenant_id, reserved_cost, usage_date=job.budget_date)
                job.reserved_cost_microunits = reserved_cost
        job.status = "running"
        job.attempt_count = int(job.attempt_count or 0) + 1
        job.leased_until = utc_now() + timedelta(minutes=5)
        db.session.commit()

        started = time.monotonic()
        try:
            adapter = build_adapter(
                provider_code=model.provider.provider_code,
                base_url=model.provider.base_url,
                api_key=model.provider.api_key,
                timeout_seconds=model.provider.timeout_seconds,
            )
            result = adapter.execute(model.model_key, request)
            latency_ms = max(0, int((time.monotonic() - started) * 1000))
            return self._finish_success(job.id, result, latency_ms, reserved_cost)
        except Exception as exc:
            latency_ms = max(0, int((time.monotonic() - started) * 1000))
            self._finish_failure(job.id, exc, latency_ms, reserved_cost)
            raise

    def _pricing_snapshot(self, model: AIModelConfig) -> dict[str, Any]:
        return {
            "control_plane_version": CONTROL_PLANE_VERSION,
            "snapshot_schema": PRICING_SNAPSHOT_VERSION,
            "model_pricing_version": model.pricing_version,
            "currency": model.pricing_currency,
            "input_microunits_per_million": model.input_price_microunits_per_million,
            "output_microunits_per_million": model.output_price_microunits_per_million,
        }

    def _finish_success(self, job_id: str, result: ProviderResult, latency_ms: int, reserved_cost: int) -> dict[str, Any]:
        job = AIRequestJob.query.filter_by(id=job_id).with_for_update().first()
        model = AIModelConfig.query.filter_by(id=job.model_config_id).first()
        actual_cost = (
            calculate_cost_microunits(
                result.input_units,
                result.output_units,
                model.input_price_microunits_per_million,
                model.output_price_microunits_per_million,
            )
            if result.usage_complete else None
        )
        response = {
            "text": result.text,
            "structured": dict(result.structured) if result.structured is not None else None,
            "usage": {
                "input_units": result.input_units if result.usage_complete else None,
                "output_units": result.output_units if result.usage_complete else None,
                "cost_microunits": actual_cost,
                "currency": model.pricing_currency if actual_cost is not None else None,
            },
            "request_id": job.id,
        }
        job.status = "succeeded"
        job.completed_at = utc_now()
        job.leased_until = None
        job.last_error_class = ""
        job.last_error_message = ""
        job.reserved_cost_microunits = 0
        job.request_payload = "{}"
        job.response_payload = json.dumps(response, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        request_hash = hashlib.sha256(result.provider_request_id.encode()).hexdigest() if result.provider_request_id else ""
        db.session.add(AIUsageRequest(
            job_id=job.id,
            tenant_id=job.tenant_id,
            user_id=job.user_id,
            feature_key=job.feature_key,
            provider_code=model.provider.provider_code,
            model_key=model.model_key,
            operation=job.operation,
            prompt_version_id=job.prompt_version_id,
            input_units=result.input_units if result.usage_complete else None,
            output_units=result.output_units if result.usage_complete else None,
            latency_ms=latency_ms,
            outcome="succeeded",
            provider_request_hash=request_hash,
            provider_request_suffix=result.provider_request_id[-8:] if result.provider_request_id else "",
            cost_microunits=actual_cost,
            pricing_currency=model.pricing_currency,
            pricing_snapshot=self._pricing_snapshot(model),
            error_class="",
        ))
        self._settle_budget(job, reserved_cost, actual_cost, count_request=True)
        _audit(
            "ai.request.succeeded", "ai.request", job.id,
            actor_user_id=job.user_id, tenant_id=job.tenant_id,
            metadata={"provider": model.provider.provider_code, "model": model.model_key, "usage_complete": result.usage_complete},
        )
        db.session.commit()
        return response

    def _finish_failure(self, job_id: str, exc: Exception, latency_ms: int, reserved_cost: int) -> None:
        job = AIRequestJob.query.filter_by(id=job_id).with_for_update().first()
        model = AIModelConfig.query.filter_by(id=job.model_config_id).first()
        retryable = isinstance(exc, AIProviderError) and exc.retryable and job.attempt_count < MAX_ATTEMPTS
        job.last_error_class = safe_error_class(exc)
        job.last_error_message = safe_error_message(exc)
        job.leased_until = None
        self._settle_budget(job, reserved_cost, None, count_request=not retryable)
        job.reserved_cost_microunits = 0
        if retryable:
            job.status = "retry_wait"
            job.available_at = utc_now() + timedelta(seconds=RETRY_SECONDS[job.attempt_count - 1])
        else:
            job.status = "failed"
            job.completed_at = utc_now()
            job.request_payload = "{}"
            db.session.add(AIUsageRequest(
                job_id=job.id,
                tenant_id=job.tenant_id,
                user_id=job.user_id,
                feature_key=job.feature_key,
                provider_code=model.provider.provider_code,
                model_key=model.model_key,
                operation=job.operation,
                prompt_version_id=job.prompt_version_id,
                input_units=None,
                output_units=None,
                latency_ms=latency_ms,
                outcome="failed",
                provider_request_hash="",
                provider_request_suffix="",
                cost_microunits=None,
                pricing_currency=model.pricing_currency,
                pricing_snapshot=self._pricing_snapshot(model),
                error_class=job.last_error_class,
            ))
        _audit(
            "ai.request.retry" if retryable else "ai.request.failed",
            "ai.request", job.id,
            actor_user_id=job.user_id, tenant_id=job.tenant_id,
            metadata={"error_class": job.last_error_class, "attempt": job.attempt_count},
        )
        db.session.commit()

    def run_due_jobs(self, *, limit: int = 20) -> dict[str, int]:
        expired = (
            AIRequestJob.query
            .filter(AIRequestJob.status == "running", AIRequestJob.leased_until <= utc_now())
            .order_by(AIRequestJob.leased_until.asc())
            .limit(max(1, min(int(limit), 100)))
            .all()
        )
        recovered = 0
        for job in expired:
            self._finish_failure(
                job.id,
                AIProviderError("worker lease expired with an uncertain provider outcome", retryable=False),
                0,
                int(job.reserved_cost_microunits or 0),
            )
            recovered += 1
        due = (
            AIRequestJob.query
            .filter(AIRequestJob.status.in_(["queued", "retry_wait"]))
            .filter(AIRequestJob.available_at <= utc_now())
            .order_by(AIRequestJob.available_at.asc(), AIRequestJob.id.asc())
            .limit(max(1, min(int(limit), 100)))
            .all()
        )
        result = {"succeeded": 0, "failed": 0, "retry_wait": 0, "recovered": recovered}
        for job in due:
            try:
                self._execute_job(job.id)
                result["succeeded"] += 1
            except Exception:
                db.session.rollback()
                current = AIRequestJob.query.filter_by(id=job.id).first()
                key = current.status if current and current.status in {"failed", "retry_wait"} else "failed"
                result[key] += 1
        return result

    def purge_expired_response_payloads(self, *, limit: int = 500) -> int:
        jobs = (
            AIRequestJob.query
            .filter(AIRequestJob.status == "succeeded", AIRequestJob._response_ciphertext != "")
            .order_by(AIRequestJob.completed_at.asc())
            .limit(max(1, min(int(limit), 5000)))
            .all()
        )
        now = utc_now()
        purged = 0
        for job in jobs:
            policy = self._policy(job.tenant_id, job.feature_key)
            if job.completed_at and job.completed_at + timedelta(days=policy.retention_days) <= now:
                job.response_payload = ""
                purged += 1
        if purged:
            db.session.commit()
        return purged


def get_ai_service() -> AIService:
    return AIService()
