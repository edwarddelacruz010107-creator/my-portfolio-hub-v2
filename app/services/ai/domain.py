"""Dependency-free AI policy, pricing, redaction, and request contracts."""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import re
import string
from typing import Any, Mapping
from urllib.parse import urlparse


CONTROL_PLANE_VERSION = "ai-control-plane-2026.07-v1"
PRICING_SNAPSHOT_VERSION = "ai-pricing-microunits-v1"
OPERATIONS = frozenset({"text", "structured", "embeddings", "moderation"})
CAPABILITIES = OPERATIONS
KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_.-]{1,79}$")
MODEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/~-]{0,159}$")
CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
SECRET_KEY_PATTERN = re.compile(r"(?:api[_-]?key|authorization|secret|password|token|credential)", re.I)
BEARER_PATTERN = re.compile(r"(?i)bearer\s+[A-Za-z0-9._~+/=-]{8,}")
LIKELY_SECRET_PATTERN = re.compile(r"\b(?:sk|key|token)-[A-Za-z0-9_-]{12,}\b", re.I)


PROVIDER_CATALOG: dict[str, dict[str, Any]] = {
    "openai": {
        "name": "OpenAI", "protocol": "openai_responses",
        "default_base_url": "https://api.openai.com/v1", "requires_key": True,
        "docs": "https://platform.openai.com/docs/api-reference/responses/create",
    },
    "anthropic": {
        "name": "Anthropic", "protocol": "anthropic_messages",
        "default_base_url": "https://api.anthropic.com", "requires_key": True,
        "docs": "https://docs.anthropic.com/en/api/messages",
    },
    "gemini": {
        "name": "Gemini", "protocol": "gemini_generate",
        "default_base_url": "https://generativelanguage.googleapis.com/v1beta", "requires_key": True,
        "docs": "https://ai.google.dev/api/generate-content",
    },
    "groq": {
        "name": "Groq", "protocol": "openai_chat",
        "default_base_url": "https://api.groq.com/openai/v1", "requires_key": True,
        "docs": "https://console.groq.com/docs/api-reference",
    },
    "openrouter": {
        "name": "OpenRouter", "protocol": "openai_chat",
        "default_base_url": "https://openrouter.ai/api/v1", "requires_key": True,
        "docs": "https://openrouter.ai/docs/api/reference/overview",
    },
    "ollama": {
        "name": "Ollama", "protocol": "openai_responses",
        "default_base_url": "http://localhost:11434/v1", "requires_key": False,
        "docs": "https://docs.ollama.com/api/openai-compatibility",
    },
    "azure_openai": {
        "name": "Azure OpenAI", "protocol": "openai_responses",
        "default_base_url": "", "requires_key": True,
        "docs": "https://learn.microsoft.com/azure/foundry/openai/how-to/responses",
    },
}

PROTOCOL_CAPABILITIES = {
    "openai_responses": frozenset({"text", "structured"}),
    "openai_chat": frozenset({"text", "structured"}),
    "anthropic_messages": frozenset({"text"}),
    "gemini_generate": frozenset({"text", "structured"}),
}


class AIContractError(ValueError):
    pass


class AIUnavailableError(RuntimeError):
    pass


class AIBudgetExceeded(RuntimeError):
    pass


class AIProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = False, status_code: int | None = None):
        super().__init__(safe_error_message(message))
        self.retryable = bool(retryable)
        self.status_code = status_code


def validate_key(value: str, *, label: str) -> str:
    normalized = str(value or "").strip().lower()
    if not KEY_PATTERN.fullmatch(normalized):
        raise AIContractError(f"{label} must use lowercase letters, numbers, dot, dash, or underscore")
    return normalized


def validate_model_key(value: str) -> str:
    normalized = str(value or "").strip()
    if not MODEL_PATTERN.fullmatch(normalized):
        raise AIContractError("model identifier contains unsupported characters")
    return normalized


def validate_capabilities(values) -> tuple[str, ...]:
    normalized = tuple(sorted({str(item).strip().lower() for item in (values or []) if str(item).strip()}))
    unknown = set(normalized) - CAPABILITIES
    if unknown:
        raise AIContractError("unsupported capabilities: " + ", ".join(sorted(unknown)))
    if not normalized:
        raise AIContractError("at least one capability is required")
    return normalized


def validate_provider_endpoint(provider_code: str, value: str | None) -> str:
    provider = PROVIDER_CATALOG.get(str(provider_code or "").strip().lower())
    if provider is None:
        raise AIContractError("unsupported AI provider")
    raw = str(value or provider["default_base_url"] or "").strip().rstrip("/")
    if not raw:
        raise AIContractError("provider endpoint is required")
    parsed = urlparse(raw)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AIContractError("provider endpoint cannot contain credentials, query parameters, or fragments")
    host = (parsed.hostname or "").lower()
    if provider_code == "ollama":
        allowed_local = parsed.scheme == "http" and host in {"localhost", "127.0.0.1", "::1"}
        allowed_cloud = parsed.scheme == "https" and host == "ollama.com"
        if not (allowed_local or allowed_cloud):
            raise AIContractError("Ollama endpoint must be local HTTP or https://ollama.com")
    elif provider_code == "azure_openai":
        if parsed.scheme != "https" or not (
            host.endswith(".openai.azure.com") or host.endswith(".services.ai.azure.com")
        ) or not parsed.path.rstrip("/").endswith("/openai/v1"):
            raise AIContractError("Azure OpenAI endpoint must be an approved HTTPS /openai/v1 resource URL")
    else:
        expected = urlparse(provider["default_base_url"])
        if parsed.scheme != "https" or host != expected.hostname or parsed.path.rstrip("/") != expected.path.rstrip("/"):
            raise AIContractError("provider endpoint must match the provider's official API origin")
    return raw


def calculate_cost_microunits(
    input_units: int,
    output_units: int,
    input_price_microunits_per_million: int,
    output_price_microunits_per_million: int,
) -> int:
    values = (input_units, output_units, input_price_microunits_per_million, output_price_microunits_per_million)
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
        raise AIContractError("usage and pricing values must be non-negative integers")
    numerator = (
        input_units * input_price_microunits_per_million
        + output_units * output_price_microunits_per_million
    )
    return math.ceil(numerator / 1_000_000) if numerator else 0


def estimate_text_units(value: str) -> int:
    """Conservative provider-independent budget estimate, never billed as actual."""
    return max(1, math.ceil(len(str(value or "").encode("utf-8")) / 3))


def redact_sensitive(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if SECRET_KEY_PATTERN.search(str(key)) else redact_sensitive(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact_sensitive(item) for item in value]
    if isinstance(value, str):
        return LIKELY_SECRET_PATTERN.sub("[REDACTED]", BEARER_PATTERN.sub("Bearer [REDACTED]", value))[:1000]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:200]


def safe_error_message(value: Any) -> str:
    return str(redact_sensitive(str(value or "AI provider error"))).replace("\n", " ")[:500]


def safe_error_class(exc: BaseException) -> str:
    name = exc.__class__.__name__
    return name if re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,79}", name) else "AIError"


def request_digest(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def prompt_variables(template: str) -> tuple[str, ...]:
    names: set[str] = set()
    try:
        for _, field_name, format_spec, conversion in string.Formatter().parse(str(template or "")):
            if field_name is None:
                continue
            if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", field_name):
                raise AIContractError("prompt variables must be simple lowercase identifiers")
            if format_spec or conversion:
                raise AIContractError("prompt variables cannot use conversion or format expressions")
            names.add(field_name)
    except ValueError as exc:
        raise AIContractError("prompt template contains unmatched braces") from exc
    return tuple(sorted(names))


def render_prompt(template: str, variables: Mapping[str, Any], declared_variables) -> str:
    declared = tuple(sorted(str(item) for item in (declared_variables or [])))
    actual = prompt_variables(template)
    if actual != declared:
        raise AIContractError("stored prompt variable declaration does not match the template")
    supplied = set(variables or {})
    if supplied != set(declared):
        raise AIContractError("prompt variables must match the declared allowlist exactly")
    clean = {key: str(variables[key]) for key in declared}
    return template.format_map(clean)


def validate_json_schema(schema: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(schema, Mapping) or schema.get("type") != "object":
        raise AIContractError("structured output requires an object JSON schema")
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    if len(encoded) > 20_000 or "$ref" in encoded or "http://" in encoded or "https://" in encoded:
        raise AIContractError("structured output schema is too large or contains external references")
    return json.loads(encoded)


@dataclass(frozen=True)
class AIRequest:
    operation: str
    feature_key: str
    input_text: str
    max_output_units: int
    temperature: float = 0.2
    system_text: str = ""
    output_schema: Mapping[str, Any] | None = None

    def __post_init__(self):
        operation = str(self.operation or "").strip().lower()
        feature_key = validate_key(self.feature_key, label="feature key")
        if operation not in OPERATIONS:
            raise AIContractError("unsupported AI operation")
        if not isinstance(self.input_text, str) or not self.input_text.strip():
            raise AIContractError("AI input cannot be empty")
        if len(self.input_text) > 200_000 or len(self.system_text or "") > 50_000:
            raise AIContractError("AI input exceeds the configured safety boundary")
        if isinstance(self.max_output_units, bool) or not isinstance(self.max_output_units, int) or not 1 <= self.max_output_units <= 32_768:
            raise AIContractError("max output units must be between 1 and 32768")
        if isinstance(self.temperature, bool) or not isinstance(self.temperature, (int, float)) or not 0 <= float(self.temperature) <= 2:
            raise AIContractError("temperature must be between 0 and 2")
        if operation == "structured":
            validate_json_schema(self.output_schema)
        object.__setattr__(self, "operation", operation)
        object.__setattr__(self, "feature_key", feature_key)

    def safe_payload(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "feature_key": self.feature_key,
            "input_text": self.input_text,
            "system_text": self.system_text,
            "max_output_units": self.max_output_units,
            "temperature": self.temperature,
            "output_schema": dict(self.output_schema or {}),
        }
