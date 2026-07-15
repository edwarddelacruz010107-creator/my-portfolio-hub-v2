"""Validated Superadmin AI Center reads and mutations.

Routes remain transport-only.  This module is the sole admin configuration
boundary and returns masked dictionaries rather than ORM objects containing
credentials or encrypted request payloads.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Mapping

from sqlalchemy import func, text

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
from app.models.core import Tenant
from app.services.ai.ai_service import _audit, _scope_key
from app.services.ai.domain import (
    AIContractError,
    PROTOCOL_CAPABILITIES,
    PROVIDER_CATALOG,
    prompt_variables,
    validate_capabilities,
    validate_key,
    validate_model_key,
    validate_provider_endpoint,
)
from app.utils.datetime_utils import utc_now


ALLOWED_PLANS = frozenset({
    "trial", "basic", "starter", "pro", "premium", "enterprise",
    "business", "agency", "administrator", "admin",
})


def _bounded_int(value: Any, *, label: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise AIContractError(f"{label} must be an integer") from exc
    if isinstance(value, bool) or not minimum <= parsed <= maximum:
        raise AIContractError(f"{label} must be between {minimum} and {maximum}")
    return parsed


def _optional_nonnegative_int(value: Any, *, label: str, maximum: int = 10**15) -> int | None:
    if value is None or str(value).strip() == "":
        return None
    return _bounded_int(value, label=label, minimum=0, maximum=maximum)


def _clean_text(value: Any, *, label: str, maximum: int, required: bool = True) -> str:
    clean = str(value or "").strip()
    if required and not clean:
        raise AIContractError(f"{label} is required")
    if len(clean) > maximum:
        raise AIContractError(f"{label} is too long")
    return clean


def _provider_view(
    config: AIProviderConfig | None,
    provider_code: str,
    health_evidence: AIUsageRequest | None,
) -> dict[str, Any]:
    meta = PROVIDER_CATALOG[provider_code]
    if config is None:
        health = "unconfigured"
    elif not config.enabled:
        health = "disabled"
    elif health_evidence is None:
        health = "no live evidence"
    elif health_evidence.outcome == "succeeded":
        health = "healthy"
    else:
        health = "degraded"
    return {
        "id": config.id if config else "",
        "code": provider_code,
        "name": meta["name"],
        "protocol": meta["protocol"],
        "docs": meta["docs"],
        "base_url": config.base_url if config else meta["default_base_url"],
        "enabled": bool(config.enabled) if config else False,
        "configured": config is not None,
        "has_credential": bool(config.has_credential) if config else False,
        "masked_credential": config.masked_credential if config else "Not configured",
        "requires_key": bool(meta["requires_key"]),
        "timeout_seconds": int(config.timeout_seconds) if config else 45,
        "updated_at": config.updated_at if config else None,
        "health": health,
        "health_observed_at": health_evidence.created_at if health_evidence else None,
    }


def save_provider(data: Mapping[str, Any], *, actor_user_id: int) -> str:
    code = validate_key(data.get("provider_code"), label="provider code")
    if code not in PROVIDER_CATALOG:
        raise AIContractError("unsupported AI provider")
    endpoint = validate_provider_endpoint(code, data.get("base_url"))
    timeout = _bounded_int(data.get("timeout_seconds", 45), label="timeout", minimum=1, maximum=300)
    enabled = str(data.get("enabled", "")).lower() in {"1", "true", "on", "yes"}
    clear_credential = str(data.get("clear_credential", "")).lower() in {"1", "true", "on", "yes"}
    credential = str(data.get("api_key") or "").strip()
    if len(credential) > 20_000:
        raise AIContractError("credential is too long")

    config = AIProviderConfig.query.filter_by(provider_code=code).with_for_update().first()
    created = config is None
    if config is None:
        config = AIProviderConfig(
            provider_code=code,
            display_name=PROVIDER_CATALOG[code]["name"],
            base_url=endpoint,
            enabled=False,
            timeout_seconds=timeout,
            created_by_id=actor_user_id,
        )
        db.session.add(config)
    config.display_name = PROVIDER_CATALOG[code]["name"]
    config.base_url = endpoint
    config.timeout_seconds = timeout
    config.updated_by_id = actor_user_id
    if clear_credential:
        config.api_key = ""
    elif credential:
        config.api_key = credential
    if enabled and PROVIDER_CATALOG[code]["requires_key"] and not config.has_credential:
        raise AIContractError("store a credential before enabling this provider")
    config.enabled = enabled
    db.session.flush()
    _audit(
        "ai.provider.created" if created else "ai.provider.updated",
        "ai.provider",
        config.id,
        actor_user_id=actor_user_id,
        metadata={
            "provider": code,
            "enabled": enabled,
            "credential_changed": bool(credential or clear_credential),
        },
    )
    db.session.commit()
    return config.id


def save_model(data: Mapping[str, Any], *, actor_user_id: int) -> str:
    provider_id = _clean_text(data.get("provider_config_id"), label="provider", maximum=36)
    provider = AIProviderConfig.query.filter_by(id=provider_id).first()
    if provider is None:
        raise AIContractError("configured provider was not found")
    model_key = validate_model_key(data.get("model_key"))
    capabilities = validate_capabilities(data.get("capabilities") or [])
    supported = PROTOCOL_CAPABILITIES[PROVIDER_CATALOG[provider.provider_code]["protocol"]]
    if set(capabilities) - set(supported):
        raise AIContractError("selected capabilities are not implemented by this provider protocol")
    enabled = str(data.get("enabled", "")).lower() in {"1", "true", "on", "yes"}
    if enabled and not provider.enabled:
        raise AIContractError("enable the provider before enabling its model")
    context_window = _bounded_int(
        data.get("context_window"), label="context window", minimum=1, maximum=10_000_000
    )
    input_price = _bounded_int(
        data.get("input_price_microunits_per_million", 0),
        label="input price", minimum=0, maximum=10**15,
    )
    output_price = _bounded_int(
        data.get("output_price_microunits_per_million", 0),
        label="output price", minimum=0, maximum=10**15,
    )
    currency = _clean_text(data.get("pricing_currency", "USD"), label="currency", maximum=3).upper()
    if len(currency) != 3 or not currency.isalpha():
        raise AIContractError("currency must be a three-letter code")
    pricing_version = _clean_text(data.get("pricing_version"), label="pricing version", maximum=80)
    display_name = _clean_text(data.get("display_name") or model_key, label="display name", maximum=160)

    model = AIModelConfig.query.filter_by(
        provider_config_id=provider.id, model_key=model_key
    ).with_for_update().first()
    created = model is None
    if model is None:
        model = AIModelConfig(
            provider_config_id=provider.id,
            model_key=model_key,
            created_by_id=actor_user_id,
            pricing_version=pricing_version,
        )
        db.session.add(model)
    model.display_name = display_name
    model.capabilities = list(capabilities)
    model.enabled = enabled
    model.context_window = context_window
    model.input_price_microunits_per_million = input_price
    model.output_price_microunits_per_million = output_price
    model.pricing_currency = currency
    model.pricing_version = pricing_version
    model.updated_by_id = actor_user_id
    db.session.flush()
    _audit(
        "ai.model.created" if created else "ai.model.updated",
        "ai.model",
        model.id,
        actor_user_id=actor_user_id,
        metadata={
            "provider": provider.provider_code,
            "model": model_key,
            "capabilities": list(capabilities),
            "enabled": enabled,
            "pricing_version": pricing_version,
        },
    )
    db.session.commit()
    return model.id


def save_feature_policy(data: Mapping[str, Any], *, actor_user_id: int) -> str:
    feature_key = validate_key(data.get("feature_key"), label="feature key")
    model_id = _clean_text(data.get("model_config_id"), label="model", maximum=36)
    model = AIModelConfig.query.filter_by(id=model_id).first()
    if model is None:
        raise AIContractError("model was not found")
    tenant_raw = str(data.get("tenant_id") or "").strip()
    tenant_id = None
    if tenant_raw:
        tenant_id = _bounded_int(tenant_raw, label="tenant ID", minimum=1, maximum=2_147_483_647)
        if Tenant.query.filter_by(id=tenant_id).first() is None:
            raise AIContractError("tenant was not found")
    scope_key = _scope_key(tenant_id)
    min_plan = _clean_text(data.get("min_plan", "enterprise"), label="minimum plan", maximum=40).lower()
    if min_plan not in ALLOWED_PLANS:
        raise AIContractError("minimum plan is not supported")
    budget = _optional_nonnegative_int(data.get("daily_budget_microunits"), label="daily budget")
    max_output = _bounded_int(
        data.get("max_output_units", 1024), label="max output units", minimum=1, maximum=32_768
    )
    retention = _bounded_int(
        data.get("retention_days", 7), label="retention days", minimum=0, maximum=3650
    )
    enabled = str(data.get("enabled", "")).lower() in {"1", "true", "on", "yes"}
    if enabled and (not model.enabled or not model.provider or not model.provider.enabled):
        raise AIContractError("enable the selected provider and model before enabling this feature")

    policy = AIFeaturePolicy.query.filter_by(
        scope_key=scope_key, feature_key=feature_key
    ).with_for_update().first()
    created = policy is None
    if policy is None:
        policy = AIFeaturePolicy(scope_key=scope_key, tenant_id=tenant_id, feature_key=feature_key)
        db.session.add(policy)
    policy.tenant_id = tenant_id
    policy.model_config_id = model.id
    policy.enabled = enabled
    policy.min_plan = min_plan
    policy.daily_budget_microunits = budget
    policy.max_output_units = max_output
    policy.retention_days = retention
    policy.updated_by_id = actor_user_id
    db.session.flush()
    _audit(
        "ai.feature.created" if created else "ai.feature.updated",
        "ai.feature",
        policy.id,
        actor_user_id=actor_user_id,
        tenant_id=tenant_id,
        metadata={
            "feature": feature_key,
            "scope": scope_key,
            "model_id": model.id,
            "enabled": enabled,
            "budget_microunits": budget,
        },
    )
    db.session.commit()
    return policy.id


def publish_prompt_version(data: Mapping[str, Any], *, actor_user_id: int) -> str:
    prompt_key = validate_key(data.get("prompt_key"), label="prompt key")
    feature_key = validate_key(data.get("feature_key"), label="feature key")
    name = _clean_text(data.get("name"), label="prompt name", maximum=160)
    description = _clean_text(data.get("description"), label="description", maximum=500, required=False)
    system_text = _clean_text(data.get("system_text"), label="system text", maximum=50_000, required=False)
    template_text = _clean_text(data.get("template_text"), label="template", maximum=200_000)
    change_note = _clean_text(data.get("change_note"), label="change note", maximum=500)
    variables = list(prompt_variables(template_text))

    bind = db.session.get_bind(mapper=AIPromptDefinition)
    if bind.dialect.name == "postgresql":
        db.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:prompt_key, 0))"),
            {"prompt_key": f"ai-prompt:{prompt_key}"},
        )
    prompt = AIPromptDefinition.query.filter_by(prompt_key=prompt_key).with_for_update().first()
    created = prompt is None
    if prompt is None:
        prompt = AIPromptDefinition(
            prompt_key=prompt_key,
            feature_key=feature_key,
            name=name,
            description=description,
            created_by_id=actor_user_id,
            updated_by_id=actor_user_id,
        )
        db.session.add(prompt)
        db.session.flush()
    elif prompt.feature_key != feature_key:
        raise AIContractError("an existing prompt cannot be moved to another feature")
    prompt.name = name
    prompt.description = description
    prompt.updated_by_id = actor_user_id
    latest = db.session.query(func.max(AIPromptVersion.version_number)).filter_by(prompt_id=prompt.id).scalar() or 0
    version = AIPromptVersion(
        prompt_id=prompt.id,
        version_number=int(latest) + 1,
        system_text=system_text,
        template_text=template_text,
        variables=variables,
        change_note=change_note,
        created_by_id=actor_user_id,
    )
    db.session.add(version)
    db.session.flush()
    prompt.active_version_id = version.id
    _audit(
        "ai.prompt.created" if created else "ai.prompt.published",
        "ai.prompt",
        prompt.id,
        actor_user_id=actor_user_id,
        metadata={
            "prompt": prompt_key,
            "feature": feature_key,
            "version": version.version_number,
            "variables": variables,
        },
    )
    db.session.commit()
    return version.id


def activate_prompt_version(prompt_id: str, version_id: str, *, actor_user_id: int) -> None:
    prompt = AIPromptDefinition.query.filter_by(id=str(prompt_id)).with_for_update().first()
    if prompt is None:
        raise AIContractError("prompt was not found")
    version = AIPromptVersion.query.filter_by(id=str(version_id), prompt_id=prompt.id).first()
    if version is None:
        raise AIContractError("prompt version was not found")
    previous = prompt.active_version_id
    prompt.active_version_id = version.id
    prompt.updated_by_id = actor_user_id
    _audit(
        "ai.prompt.activated",
        "ai.prompt",
        prompt.id,
        actor_user_id=actor_user_id,
        metadata={
            "prompt": prompt.prompt_key,
            "previous_version_id": previous,
            "active_version_id": version.id,
            "version": version.version_number,
        },
    )
    db.session.commit()


def _model_view(model: AIModelConfig) -> dict[str, Any]:
    return {
        "id": model.id,
        "provider_id": model.provider_config_id,
        "provider": model.provider.provider_code if model.provider else "unavailable",
        "model_key": model.model_key,
        "display_name": model.display_name,
        "capabilities": tuple(model.capabilities or []),
        "enabled": bool(model.enabled),
        "context_window": model.context_window,
        "input_price": model.input_price_microunits_per_million,
        "output_price": model.output_price_microunits_per_million,
        "currency": model.pricing_currency,
        "pricing_version": model.pricing_version,
    }


def _prompt_views() -> list[dict[str, Any]]:
    prompts = AIPromptDefinition.query.order_by(AIPromptDefinition.prompt_key.asc()).all()
    result = []
    for prompt in prompts:
        versions = AIPromptVersion.query.filter_by(prompt_id=prompt.id).order_by(
            AIPromptVersion.version_number.desc()
        ).limit(20).all()
        result.append({
            "id": prompt.id,
            "key": prompt.prompt_key,
            "feature": prompt.feature_key,
            "name": prompt.name,
            "description": prompt.description,
            "active_version_id": prompt.active_version_id,
            "versions": [{
                "id": version.id,
                "number": version.version_number,
                "variables": tuple(version.variables or []),
                "change_note": version.change_note,
                "created_at": version.created_at,
                "active": version.id == prompt.active_version_id,
            } for version in versions],
        })
    return result


def get_center_context(*, selected_tab: str) -> dict[str, Any]:
    configs = {item.provider_code: item for item in AIProviderConfig.query.all()}
    usage = AIUsageRequest.query.order_by(AIUsageRequest.created_at.desc()).limit(250).all()
    latest_by_provider = {}
    for item in usage:
        latest_by_provider.setdefault(item.provider_code, item)
    providers = [
        _provider_view(configs.get(code), code, latest_by_provider.get(code))
        for code in PROVIDER_CATALOG
    ]
    models = [_model_view(item) for item in AIModelConfig.query.order_by(AIModelConfig.display_name.asc()).all()]
    policies = AIFeaturePolicy.query.order_by(
        AIFeaturePolicy.scope_key.asc(), AIFeaturePolicy.feature_key.asc()
    ).all()
    jobs = AIRequestJob.query.order_by(AIRequestJob.created_at.desc()).limit(100).all()
    audits = AIAuditEvent.query.order_by(AIAuditEvent.created_at.desc()).limit(100).all()
    daily = AIUsageDaily.query.filter(AIUsageDaily.usage_date >= (utc_now().date() - timedelta(days=30))).all()

    known_cost = sum(int(item.cost_microunits) for item in usage if item.cost_microunits is not None)
    unavailable_cost = sum(1 for item in usage if item.cost_microunits is None)
    successful_latency = [int(item.latency_ms) for item in usage if item.outcome == "succeeded"]
    usage_view = [{
        "id": item.id,
        "tenant_id": item.tenant_id,
        "feature": item.feature_key,
        "provider": item.provider_code,
        "model": item.model_key,
        "operation": item.operation,
        "input_units": item.input_units,
        "output_units": item.output_units,
        "latency_ms": item.latency_ms,
        "outcome": item.outcome,
        "cost_microunits": item.cost_microunits,
        "currency": item.pricing_currency,
        "request_suffix": item.provider_request_suffix,
        "error_class": item.error_class,
        "created_at": item.created_at,
    } for item in usage]
    return {
        "selected_tab": selected_tab,
        "providers": providers,
        "models": models,
        "policies": [{
            "id": item.id,
            "scope": item.scope_key,
            "tenant_id": item.tenant_id,
            "feature": item.feature_key,
            "enabled": bool(item.enabled),
            "model_id": item.model_config_id,
            "model": item.model.display_name if item.model else "Unavailable",
            "min_plan": item.min_plan,
            "budget": item.daily_budget_microunits,
            "max_output": item.max_output_units,
            "retention_days": item.retention_days,
        } for item in policies],
        "prompts": _prompt_views(),
        "usage": usage_view,
        "jobs": [{
            "id": item.id,
            "tenant_id": item.tenant_id,
            "feature": item.feature_key,
            "operation": item.operation,
            "status": item.status,
            "attempts": item.attempt_count,
            "error_class": item.last_error_class,
            "error_message": item.last_error_message,
            "created_at": item.created_at,
            "completed_at": item.completed_at,
        } for item in jobs],
        "audits": [{
            "id": item.id,
            "event": item.event_type,
            "entity": item.entity_type,
            "entity_id": item.entity_id,
            "tenant_id": item.tenant_id,
            "metadata": item.safe_metadata,
            "created_at": item.created_at,
        } for item in audits],
        "metrics": {
            "requests": len(usage),
            "known_cost_microunits": known_cost,
            "unavailable_cost_count": unavailable_cost,
            "average_latency_ms": (
                sum(successful_latency) // len(successful_latency) if successful_latency else None
            ),
            "failures": sum(1 for item in usage if item.outcome == "failed"),
            "reserved_microunits_30d": sum(int(item.reserved_microunits or 0) for item in daily),
            "actual_microunits_30d": sum(int(item.actual_microunits or 0) for item in daily),
        },
        "knowledge_base": {
            "available": False,
            "status": "Not available in Phase 8",
            "prerequisites": (
                "versioned source ingestion",
                "tenant-scoped chunk isolation",
                "embedding lifecycle and deletion",
                "retrieval provenance and retention",
            ),
        },
    }
