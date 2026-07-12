from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from threading import Event
from typing import Any, Callable

import httpx
from pydantic import ValidationError

from .models import (
    AuditDecision,
    ContextBundle,
    ContextManifest,
    MemoryChangeProposal,
    ModelProfile,
    ProcedureState,
    PublishReceipt,
    RunOutput,
    SourceLink,
    SynthesisArtifact,
    ValidationReport,
)


@dataclass(frozen=True, slots=True)
class ProviderTool:
    name: str
    description: str
    input_schema: dict[str, Any]


ToolExecutor = Callable[[str, dict[str, Any]], dict[str, Any]]


@dataclass(slots=True)
class ProviderRequest:
    state: ProcedureState
    model: ModelProfile
    context: ContextManifest
    previous_output: RunOutput | None
    instructions: str
    input_text: str
    tools: list[ProviderTool] = field(default_factory=list)
    execute_tool: ToolExecutor | None = None
    cancel_event: Event | None = None


@dataclass(slots=True)
class ProviderResult:
    output: RunOutput
    request_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0


class ProviderError(RuntimeError):
    def __init__(self, category: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.category = category
        self.retryable = retryable


class ModelProvider(ABC):
    name: str

    @abstractmethod
    def health(self) -> dict[str, Any]: ...

    @abstractmethod
    def execute(self, request: ProviderRequest) -> ProviderResult: ...

    def cancel(self, request_id: str) -> bool:
        return False


class PreviewProvider(ModelProvider):
    name = "preview"

    def health(self) -> dict[str, Any]:
        return {"status": "ok", "mode": "preview"}

    def execute(self, request: ProviderRequest) -> ProviderResult:
        _check_cancelled(request)
        next_state = {
            "gather": "curate",
            "curate": "synthesize",
            "synthesize": "validate",
            "validate": "publish",
            "publish": "audit",
            "audit": "gather",
        }.get(request.state.id, "gather")
        payload: Any
        if request.state.kind == "gather":
            payload = ContextBundle(query=request.state.goal_template, items=request.context.items)
        elif request.state.kind == "curate":
            payload = MemoryChangeProposal(changes=[])
        elif request.state.kind == "synthesize":
            payload = SynthesisArtifact(
                title="Preview synthesis",
                body="A durable preview artifact assembled from the current context manifest.",
                dependencies=[item.id for item in request.context.items],
            )
        elif request.state.kind == "validate":
            payload = ValidationReport(sufficient_evidence=True, findings=[])
        elif request.state.kind == "publish":
            payload = PublishReceipt()
        else:
            payload = AuditDecision(decision="continue")
        output = RunOutput(
            summary=request.state.output_contract,
            confidence=0.76,
            source_links=[
                SourceLink(label=item.label, kind="context", uri=item.source_uri)
                for item in request.context.items
                if item.source_uri
            ],
            unresolved_risks=["Preview execution does not call a live model provider."],
            next_transition_recommendation=next_state,
            payload=payload,
        )
        estimated_input = max(1, len(request.input_text) // 4)
        estimated_output = max(1, len(output.model_dump_json()) // 4)
        return ProviderResult(output=output, request_id="preview", input_tokens=estimated_input, output_tokens=estimated_output)


class OpenAIResponsesProvider(ModelProvider):
    name = "openai"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._client: Any | None = None

    def health(self) -> dict[str, Any]:
        return {"status": "configured", "provider": self.name}

    def execute(self, request: ProviderRequest) -> ProviderResult:
        try:
            from openai import OpenAI
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise ProviderError("provider_not_installed", "Install the openai Python package.") from exc

        _check_cancelled(request)
        try:
            # Retry policy is owned by the durable runner so every retry is recorded.
            client = OpenAI(api_key=self.api_key, max_retries=0)
            self._client = client
            input_items: list[Any] = [{"role": "user", "content": request.input_text}]
            totals = _UsageTotals()
            repair_used = False
            try:
                response = self._openai_parse(client, request, input_items)
            except (ValidationError, json.JSONDecodeError) as exc:
                repair_used = True
                response = self._openai_schema_repair(client, request, request.input_text, str(exc))
            totals.add_openai(response)

            for _ in range(8):
                _check_cancelled(request)
                tool_calls = _openai_tool_calls(response)
                if not tool_calls:
                    break
                if request.execute_tool is None:
                    raise ProviderError("tool_execution_unavailable", "OpenAI requested tools but no executor was provided.")
                input_items.extend(_openai_output_items(response))
                for call in tool_calls:
                    arguments = _decode_tool_arguments(call.get("arguments"), call.get("name", "unknown"))
                    result = _execute_tool(request, str(call.get("name", "")), arguments)
                    input_items.append(
                        {
                            "type": "function_call_output",
                            "call_id": call.get("call_id"),
                            "output": json.dumps(result, sort_keys=True),
                        }
                    )
                response = self._openai_parse(client, request, input_items)
                totals.add_openai(response)
            else:
                raise ProviderError("tool_loop_exhausted", "OpenAI exceeded the eight-step tool-call limit.")

            parsed = getattr(response, "output_parsed", None)
            if parsed is None:
                refusal = _openai_refusal(response)
                if refusal:
                    raise ProviderError("refusal", refusal)
                raw_output = getattr(response, "output_text", "") or ""
                if not raw_output:
                    raise ProviderError("invalid_output", "OpenAI returned no parsed structured output.")
                if repair_used:
                    raise ProviderError("invalid_output", "OpenAI schema repair did not return parsed output.")
                repair_used = True
                try:
                    response = self._openai_schema_repair(client, request, raw_output, "parsed output was missing")
                except (ValidationError, json.JSONDecodeError) as exc:
                    raise ProviderError("invalid_output", f"OpenAI schema repair failed: {exc}") from exc
                totals.add_openai(response)
                parsed = getattr(response, "output_parsed", None)
                if parsed is None:
                    refusal = _openai_refusal(response)
                    if refusal:
                        raise ProviderError("refusal", refusal)
                    raise ProviderError("invalid_output", "OpenAI schema repair did not return parsed output.")
            _check_cancelled(request)
            return totals.result(parsed, getattr(response, "id", None))
        except ProviderError:
            raise
        except Exception as exc:  # pragma: no cover - exercised with SDK-shaped mocks
            raise _classify_openai_error(exc) from exc

    def _openai_parse(self, client: Any, request: ProviderRequest, input_items: list[Any]) -> Any:
        kwargs: dict[str, Any] = {
            "model": request.model.model,
            "instructions": request.instructions,
            "input": input_items,
            "text_format": RunOutput,
            "store": False,
        }
        if request.tools:
            kwargs["tools"] = [
                {
                    "type": "function",
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                    "strict": True,
                }
                for tool in request.tools
            ]
        return client.responses.parse(**kwargs)

    def _openai_schema_repair(
        self, client: Any, request: ProviderRequest, malformed_output: str, validation_error: str
    ) -> Any:
        repair_input = [
            {
                "role": "user",
                "content": (
                    "Repair the following response to match the required JSON schema. "
                    "Return only the repaired structured result.\n"
                    f"Validation error: {validation_error}\n\n{malformed_output}"
                ),
            }
        ]
        try:
            return self._openai_parse(client, request, repair_input)
        except (ValidationError, json.JSONDecodeError) as exc:
            raise ProviderError("invalid_output", f"OpenAI schema repair failed: {exc}") from exc

    def cancel(self, request_id: str) -> bool:
        if not request_id or self._client is None:
            return False
        try:
            self._client.responses.cancel(request_id)
            return True
        except Exception:
            return False


class OllamaProvider(ModelProvider):
    name = "ollama"

    def __init__(self, base_url: str, timeout: float = 120) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def health(self) -> dict[str, Any]:
        try:
            response = httpx.get(f"{self.base_url}/api/tags", timeout=5)
            response.raise_for_status()
            models = [item.get("name") for item in response.json().get("models", [])]
            return {"status": "healthy", "models": models}
        except Exception as exc:
            return {"status": "unreachable", "error": str(exc)}

    def execute(self, request: ProviderRequest) -> ProviderResult:
        _check_cancelled(request)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": request.instructions},
            {"role": "user", "content": request.input_text},
        ]
        totals = _UsageTotals()
        repair_attempts = 0
        for _ in range(8):
            _check_cancelled(request)
            body = self._ollama_chat(request, messages)
            totals.add_ollama(body)
            message = body.get("message", {})
            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                if request.execute_tool is None:
                    raise ProviderError("tool_execution_unavailable", "Ollama requested tools but no executor was provided.")
                messages.append(message)
                for call in tool_calls:
                    function = call.get("function", {})
                    name = str(function.get("name", ""))
                    arguments = _decode_tool_arguments(function.get("arguments"), name or "unknown")
                    result = _execute_tool(request, name, arguments)
                    messages.append({"role": "tool", "tool_name": name, "content": json.dumps(result, sort_keys=True)})
                continue

            content = message.get("content", "")
            try:
                output = _parse_run_output(content)
            except (ValidationError, json.JSONDecodeError, ValueError) as exc:
                if repair_attempts >= 2:
                    raise ProviderError("invalid_output", f"Ollama schema repair failed: {exc}") from exc
                repair_attempts += 1
                messages.append(message)
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Repair the previous response to match the RunOutput JSON schema. "
                            f"Validation error: {exc}. Return JSON only. Required top-level keys are "
                            "summary, confidence, changed_resources, source_links, unresolved_risks, "
                            "next_transition_recommendation, and payload. Do not return the state payload "
                            f"at the top level. {_ollama_payload_repair_hint(request.state.kind)}"
                        ),
                    }
                )
                continue
            _check_cancelled(request)
            return totals.result(output, body.get("created_at"))
        raise ProviderError("tool_loop_exhausted", "Ollama exceeded the eight-step tool/repair limit.")

    def _ollama_chat(self, request: ProviderRequest, messages: list[dict[str, Any]]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": request.model.model,
            "stream": False,
            "think": False,
            "format": RunOutput.model_json_schema(),
            "messages": messages,
            "options": {
                "temperature": 0,
                "num_ctx": min(
                    request.model.context_window,
                    max(
                        8_192,
                        request.context.total_estimated_tokens
                        + request.context.reserved_output_tokens
                        + len(request.instructions) // 4
                        + len(request.input_text) // 4,
                    ),
                ),
            },
        }
        if request.tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in request.tools
            ]
        try:
            response = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise _classify_http_error("ollama", exc.response.status_code, str(exc)) from exc
        except httpx.TimeoutException as exc:
            raise ProviderError("timeout", str(exc), retryable=True) from exc
        except httpx.RequestError as exc:
            raise ProviderError("unavailable", str(exc), retryable=True) from exc
        except (json.JSONDecodeError, ValueError) as exc:
            raise ProviderError("invalid_response", f"Ollama returned an invalid HTTP response: {exc}") from exc


def _ollama_payload_repair_hint(state_kind: str) -> str:
    if state_kind == "validate":
        return (
            'For Validate, payload must be shaped exactly like '
            '{"kind":"validation_report","sufficient_evidence":true,"findings":['
            '{"change_index":0,"accepted":true,"reason":"evidence supports the change",'
            '"evidence_ids":[]}]}. Every findings item requires change_index, accepted, reason, '
            "and evidence_ids; never use label or confidence inside findings."
        )
    return "Keep the payload kind and fields exactly as shown in required_result_envelope."


def _parse_run_output(content: str) -> RunOutput:
    try:
        return RunOutput.model_validate_json(content)
    except ValidationError as original:
        stripped = content.strip()
        try:
            value, end = json.JSONDecoder().raw_decode(stripped)
        except (json.JSONDecodeError, ValueError):
            raise original
        if stripped[end:].strip() != ",":
            raise original
        return RunOutput.model_validate(value)


@dataclass(slots=True)
class _UsageTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0

    def add_openai(self, response: Any) -> None:
        usage = getattr(response, "usage", None)
        input_details = getattr(usage, "input_tokens_details", None)
        self.input_tokens += int(getattr(usage, "input_tokens", 0) or 0)
        self.output_tokens += int(getattr(usage, "output_tokens", 0) or 0)
        self.cached_tokens += int(getattr(input_details, "cached_tokens", 0) or 0)

    def add_ollama(self, body: dict[str, Any]) -> None:
        self.input_tokens += int(body.get("prompt_eval_count", 0) or 0)
        self.output_tokens += int(body.get("eval_count", 0) or 0)

    def result(self, output: RunOutput, request_id: str | None) -> ProviderResult:
        return ProviderResult(
            output=output,
            request_id=request_id,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            cached_tokens=self.cached_tokens,
        )


def _check_cancelled(request: ProviderRequest) -> None:
    if request.cancel_event is not None and request.cancel_event.is_set():
        raise ProviderError("cancelled", "Provider request was cancelled.")


def _execute_tool(request: ProviderRequest, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if request.tools and name not in {tool.name for tool in request.tools}:
        raise ProviderError("tool_error", f"Provider requested undeclared tool {name!r}.")
    try:
        assert request.execute_tool is not None
        return request.execute_tool(name, arguments)
    except ProviderError:
        raise
    except Exception as exc:
        raise ProviderError("tool_error", f"Tool {name!r} failed: {exc}") from exc


def _decode_tool_arguments(value: Any, name: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        decoded = json.loads(value or "{}")
    except (json.JSONDecodeError, TypeError) as exc:
        raise ProviderError("tool_error", f"Tool {name!r} returned invalid arguments: {exc}") from exc
    if not isinstance(decoded, dict):
        raise ProviderError("tool_error", f"Tool {name!r} arguments must be a JSON object.")
    return decoded


def _openai_tool_calls(response: Any) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    for item in getattr(response, "output", None) or []:
        item_type = _field(item, "type")
        if item_type == "function_call":
            calls.append(
                {
                    "call_id": _field(item, "call_id") or _field(item, "id"),
                    "name": _field(item, "name"),
                    "arguments": _field(item, "arguments"),
                }
            )
    return calls


def _openai_output_items(response: Any) -> list[Any]:
    values: list[Any] = []
    for item in getattr(response, "output", None) or []:
        if hasattr(item, "model_dump"):
            values.append(item.model_dump(mode="json", exclude_none=True))
        elif isinstance(item, dict):
            values.append(item)
    return values


def _openai_refusal(response: Any) -> str | None:
    for item in getattr(response, "output", None) or []:
        for content in _field(item, "content") or []:
            if _field(content, "type") == "refusal":
                return str(_field(content, "refusal") or "OpenAI refused the request.")
    return None


def _field(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _classify_openai_error(exc: Exception) -> ProviderError:
    status = getattr(exc, "status_code", None)
    name = type(exc).__name__
    if name == "APITimeoutError" or status == 408:
        return ProviderError("timeout", str(exc), retryable=True)
    if name == "RateLimitError" or status == 429:
        return ProviderError("rate_limit", str(exc), retryable=True)
    if name in {"APIConnectionError", "InternalServerError"} or status in {409, 500, 502, 503, 504}:
        return ProviderError("unavailable", str(exc), retryable=True)
    return _classify_http_error("openai", status, str(exc))


def _classify_http_error(provider: str, status: int | None, message: str) -> ProviderError:
    if status == 408:
        return ProviderError("timeout", message, retryable=True)
    if status == 429:
        return ProviderError("rate_limit", message, retryable=True)
    if status is not None and status >= 500:
        return ProviderError("unavailable", message, retryable=True)
    return ProviderError(f"{provider}_http_error", message)


def build_provider(model: ModelProfile, *, execution_mode: str, openai_api_key: str | None, ollama_url: str) -> ModelProvider:
    if execution_mode == "preview":
        return PreviewProvider()
    if model.provider == "openai":
        if not openai_api_key:
            raise ProviderError("missing_credentials", "OPENAI_API_KEY is required for the selected model.")
        return OpenAIResponsesProvider(openai_api_key)
    if model.provider == "ollama":
        return OllamaProvider(ollama_url)
    raise ProviderError("unsupported_provider", f"Provider {model.provider!r} is not implemented in local v1.")
