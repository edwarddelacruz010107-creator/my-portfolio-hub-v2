"""HTTP adapters kept behind one provider-neutral request/response contract.

No adapter may apply feature policy, select a model, calculate cost, retry, or
write usage. Those controls belong to AIService.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import socket
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from app.services.ai.domain import (
    AIContractError,
    AIProviderError,
    AIRequest,
    PROTOCOL_CAPABILITIES,
    PROVIDER_CATALOG,
    safe_error_message,
    validate_json_schema,
)


@dataclass(frozen=True)
class ProviderResult:
    text: str
    input_units: int
    output_units: int
    provider_request_id: str = ""
    structured: Mapping[str, Any] | None = None
    usage_complete: bool = False


def _post_json(url: str, payload: Mapping[str, Any], headers: Mapping[str, str], timeout_seconds: int) -> tuple[dict, Mapping[str, str]]:
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    request = Request(url, data=body, headers={"Content-Type": "application/json", **dict(headers)}, method="POST")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # nosec B310: endpoints are validated centrally
            raw = response.read(8_000_000)
            response_headers = dict(response.headers.items())
    except HTTPError as exc:
        detail = exc.read(16_000).decode("utf-8", errors="replace")
        raise AIProviderError(
            f"provider returned HTTP {exc.code}: {detail}",
            # Only an explicit rate-limit rejection is safe to replay across
            # every supported protocol. Other failures can have an uncertain
            # paid-call outcome and are therefore terminal by default.
            retryable=exc.code == 429,
            status_code=exc.code,
        ) from exc
    except (URLError, TimeoutError, socket.timeout) as exc:
        raise AIProviderError(f"provider network failure: {exc}", retryable=False) from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AIProviderError("provider returned an invalid JSON response", retryable=False) from exc
    if not isinstance(data, dict):
        raise AIProviderError("provider returned an unexpected response shape", retryable=False)
    return data, response_headers


def _structured(text: str, request: AIRequest):
    if request.operation != "structured":
        return None
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise AIProviderError("provider did not return valid structured JSON", retryable=False) from exc
    if not isinstance(parsed, dict):
        raise AIProviderError("structured response must be a JSON object", retryable=False)
    return parsed


class BaseAdapter:
    protocol = ""

    def __init__(self, *, provider_code: str, base_url: str, api_key: str, timeout_seconds: int):
        self.provider_code = provider_code
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = int(timeout_seconds)

    def require_capability(self, request: AIRequest) -> None:
        if request.operation not in PROTOCOL_CAPABILITIES[self.protocol]:
            raise AIContractError(f"{self.provider_code} adapter does not support {request.operation}")

    def execute(self, model_key: str, request: AIRequest) -> ProviderResult:
        raise NotImplementedError


class OpenAIResponsesAdapter(BaseAdapter):
    protocol = "openai_responses"

    def execute(self, model_key: str, request: AIRequest) -> ProviderResult:
        self.require_capability(request)
        payload: dict[str, Any] = {
            "model": model_key,
            "input": request.input_text,
            "instructions": request.system_text or None,
            "max_output_tokens": request.max_output_units,
            "temperature": float(request.temperature),
            "store": False,
        }
        if request.operation == "structured":
            payload["text"] = {
                "format": {
                    "type": "json_schema",
                    "name": "portfolio_hub_output",
                    "schema": validate_json_schema(request.output_schema),
                    "strict": True,
                }
            }
        headers: dict[str, str] = {}
        if self.provider_code == "azure_openai":
            headers["api-key"] = self.api_key
        elif self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        data, response_headers = _post_json(
            f"{self.base_url}/responses", payload, headers, self.timeout_seconds
        )
        texts: list[str] = []
        for item in data.get("output") or []:
            if not isinstance(item, Mapping):
                continue
            for content in item.get("content") or []:
                if isinstance(content, Mapping) and content.get("type") in {"output_text", "text"}:
                    texts.append(str(content.get("text") or ""))
        text = "".join(texts).strip()
        if not text:
            text = str(data.get("output_text") or "").strip()
        if not text:
            raise AIProviderError("provider response contained no output text", retryable=False)
        usage = data.get("usage") if isinstance(data.get("usage"), Mapping) else {}
        request_id = str(data.get("id") or response_headers.get("x-request-id") or "")
        return ProviderResult(
            text=text,
            structured=_structured(text, request),
            input_units=int(usage.get("input_tokens") or 0),
            output_units=int(usage.get("output_tokens") or 0),
            provider_request_id=request_id,
            usage_complete="input_tokens" in usage and "output_tokens" in usage,
        )


class OpenAIChatAdapter(BaseAdapter):
    protocol = "openai_chat"

    def execute(self, model_key: str, request: AIRequest) -> ProviderResult:
        self.require_capability(request)
        messages = []
        if request.system_text:
            messages.append({"role": "system", "content": request.system_text})
        messages.append({"role": "user", "content": request.input_text})
        payload: dict[str, Any] = {
            "model": model_key,
            "messages": messages,
            "max_tokens": request.max_output_units,
            "temperature": float(request.temperature),
            "stream": False,
        }
        if request.operation == "structured":
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "portfolio_hub_output",
                    "strict": True,
                    "schema": validate_json_schema(request.output_schema),
                },
            }
        data, response_headers = _post_json(
            f"{self.base_url}/chat/completions",
            payload,
            {"Authorization": f"Bearer {self.api_key}"},
            self.timeout_seconds,
        )
        choices = data.get("choices") or []
        message = choices[0].get("message") if choices and isinstance(choices[0], Mapping) else {}
        text = str(message.get("content") or "").strip() if isinstance(message, Mapping) else ""
        if not text:
            raise AIProviderError("provider response contained no output text", retryable=False)
        usage = data.get("usage") if isinstance(data.get("usage"), Mapping) else {}
        return ProviderResult(
            text=text,
            structured=_structured(text, request),
            input_units=int(usage.get("prompt_tokens") or 0),
            output_units=int(usage.get("completion_tokens") or 0),
            provider_request_id=str(data.get("id") or response_headers.get("x-request-id") or ""),
            usage_complete="prompt_tokens" in usage and "completion_tokens" in usage,
        )


class AnthropicMessagesAdapter(BaseAdapter):
    protocol = "anthropic_messages"

    def execute(self, model_key: str, request: AIRequest) -> ProviderResult:
        self.require_capability(request)
        payload = {
            "model": model_key,
            "max_tokens": request.max_output_units,
            "temperature": float(request.temperature),
            "messages": [{"role": "user", "content": request.input_text}],
        }
        if request.system_text:
            payload["system"] = request.system_text
        data, response_headers = _post_json(
            f"{self.base_url}/v1/messages",
            payload,
            {"x-api-key": self.api_key, "anthropic-version": "2023-06-01"},
            self.timeout_seconds,
        )
        text = "".join(
            str(item.get("text") or "")
            for item in (data.get("content") or [])
            if isinstance(item, Mapping) and item.get("type") == "text"
        ).strip()
        if not text:
            raise AIProviderError("provider response contained no output text", retryable=False)
        usage = data.get("usage") if isinstance(data.get("usage"), Mapping) else {}
        return ProviderResult(
            text=text,
            input_units=int(usage.get("input_tokens") or 0),
            output_units=int(usage.get("output_tokens") or 0),
            provider_request_id=str(data.get("id") or response_headers.get("request-id") or ""),
            usage_complete="input_tokens" in usage and "output_tokens" in usage,
        )


class GeminiGenerateAdapter(BaseAdapter):
    protocol = "gemini_generate"

    def execute(self, model_key: str, request: AIRequest) -> ProviderResult:
        self.require_capability(request)
        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": request.input_text}]}],
            "generationConfig": {
                "maxOutputTokens": request.max_output_units,
                "temperature": float(request.temperature),
            },
        }
        if request.system_text:
            payload["systemInstruction"] = {"parts": [{"text": request.system_text}]}
        if request.operation == "structured":
            payload["generationConfig"].update({
                "responseMimeType": "application/json",
                "responseJsonSchema": validate_json_schema(request.output_schema),
            })
        data, response_headers = _post_json(
            f"{self.base_url}/models/{quote(model_key, safe='._-')}:generateContent",
            payload,
            {"x-goog-api-key": self.api_key},
            self.timeout_seconds,
        )
        candidates = data.get("candidates") or []
        content = candidates[0].get("content") if candidates and isinstance(candidates[0], Mapping) else {}
        parts = content.get("parts") if isinstance(content, Mapping) else []
        text = "".join(str(part.get("text") or "") for part in (parts or []) if isinstance(part, Mapping)).strip()
        if not text:
            raise AIProviderError("provider response contained no output text", retryable=False)
        usage = data.get("usageMetadata") if isinstance(data.get("usageMetadata"), Mapping) else {}
        return ProviderResult(
            text=text,
            structured=_structured(text, request),
            input_units=int(usage.get("promptTokenCount") or 0),
            output_units=int(usage.get("candidatesTokenCount") or 0),
            provider_request_id=str(response_headers.get("x-request-id") or ""),
            usage_complete="promptTokenCount" in usage and "candidatesTokenCount" in usage,
        )


ADAPTERS = {
    "openai_responses": OpenAIResponsesAdapter,
    "openai_chat": OpenAIChatAdapter,
    "anthropic_messages": AnthropicMessagesAdapter,
    "gemini_generate": GeminiGenerateAdapter,
}


def build_adapter(*, provider_code: str, base_url: str, api_key: str, timeout_seconds: int) -> BaseAdapter:
    provider = PROVIDER_CATALOG.get(provider_code)
    if provider is None:
        raise AIContractError("unsupported AI provider")
    adapter_class = ADAPTERS[provider["protocol"]]
    return adapter_class(
        provider_code=provider_code,
        base_url=base_url,
        api_key=api_key,
        timeout_seconds=timeout_seconds,
    )
