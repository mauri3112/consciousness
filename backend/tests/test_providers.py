from __future__ import annotations

import json
from collections import deque
from threading import Event
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from consciousness.models import ContextManifest, ModelProfile, ProcedureState, RunOutput, StateKind
from consciousness.providers import (
    OllamaProvider,
    OpenAIResponsesProvider,
    ProviderError,
    ProviderRequest,
    ProviderTool,
)


def output(summary: str = "done") -> RunOutput:
    return RunOutput(
        summary=summary,
        confidence=0.9,
        next_transition_recommendation="curate",
    )


def provider_request(**updates: Any) -> ProviderRequest:
    values: dict[str, Any] = {
        "state": ProcedureState(
            id="gather",
            name="Gather",
            kind=StateKind.gather,
            domain="memory",
            goal_template="Find evidence",
            prompt_contract="Use evidence",
            output_contract="Context bundle",
        ),
        "model": ModelProfile(
            id="test-model",
            provider="openai",
            model="test-model",
            context_window=32_768,
            relative_cost=1,
            max_run_budget=1,
            quality_tier=3,
        ),
        "context": ContextManifest(),
        "previous_output": None,
        "instructions": "Return the contract.",
        "input_text": "Gather evidence.",
    }
    values.update(updates)
    return ProviderRequest(**values)


def openai_response(
    parsed: RunOutput | None,
    *,
    response_id: str = "resp-1",
    text: str = "",
    response_output: list[Any] | None = None,
    input_tokens: int = 11,
    output_tokens: int = 7,
    cached_tokens: int = 3,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=response_id,
        output_parsed=parsed,
        output_text=text,
        output=response_output or [],
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            input_tokens_details=SimpleNamespace(cached_tokens=cached_tokens),
        ),
    )


class FakeOpenAI:
    queued: deque[Any] = deque()
    instances: list["FakeOpenAI"] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.calls: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.responses = SimpleNamespace(parse=self.parse, cancel=self.cancel)
        self.instances.append(self)

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        value = self.queued.popleft()
        if isinstance(value, Exception):
            raise value
        return value

    def cancel(self, request_id: str) -> None:
        self.cancelled.append(request_id)


@pytest.fixture()
def fake_openai(monkeypatch: pytest.MonkeyPatch) -> type[FakeOpenAI]:
    FakeOpenAI.queued.clear()
    FakeOpenAI.instances.clear()
    monkeypatch.setattr("openai.OpenAI", FakeOpenAI)
    return FakeOpenAI


def test_openai_success_records_usage_and_disables_sdk_retries(fake_openai: type[FakeOpenAI]) -> None:
    fake_openai.queued.append(openai_response(output()))

    result = OpenAIResponsesProvider("secret").execute(provider_request())

    assert result.output.summary == "done"
    assert (result.input_tokens, result.output_tokens, result.cached_tokens) == (11, 7, 3)
    assert result.request_id == "resp-1"
    assert fake_openai.instances[0].kwargs["max_retries"] == 0
    assert fake_openai.instances[0].calls[0]["store"] is False


def test_openai_refusal_is_not_repaired(fake_openai: type[FakeOpenAI]) -> None:
    refusal = {"type": "message", "content": [{"type": "refusal", "refusal": "Cannot comply"}]}
    fake_openai.queued.append(openai_response(None, text="ignored", response_output=[refusal]))

    with pytest.raises(ProviderError) as raised:
        OpenAIResponsesProvider("secret").execute(provider_request())

    assert raised.value.category == "refusal"
    assert raised.value.retryable is False
    assert len(fake_openai.instances[0].calls) == 1


def test_openai_missing_parsed_output_without_text_is_invalid(fake_openai: type[FakeOpenAI]) -> None:
    fake_openai.queued.append(openai_response(None))

    with pytest.raises(ProviderError, match="no parsed") as raised:
        OpenAIResponsesProvider("secret").execute(provider_request())

    assert raised.value.category == "invalid_output"
    assert len(fake_openai.instances[0].calls) == 1


def test_openai_malformed_output_gets_exactly_one_repair(fake_openai: type[FakeOpenAI]) -> None:
    fake_openai.queued.extend(
        [
            openai_response(None, text="{bad", response_id="resp-bad"),
            openai_response(output("repaired"), response_id="resp-fixed", input_tokens=5, output_tokens=2),
        ]
    )

    result = OpenAIResponsesProvider("secret").execute(provider_request())

    assert result.output.summary == "repaired"
    assert result.request_id == "resp-fixed"
    assert (result.input_tokens, result.output_tokens) == (16, 9)
    assert len(fake_openai.instances[0].calls) == 2
    assert "Repair the following response" in fake_openai.instances[0].calls[1]["input"][0]["content"]


def test_openai_parse_validation_error_gets_exactly_one_repair(fake_openai: type[FakeOpenAI]) -> None:
    fake_openai.queued.extend(
        [
            json.JSONDecodeError("invalid", "{bad", 1),
            openai_response(output("regenerated")),
        ]
    )

    result = OpenAIResponsesProvider("secret").execute(provider_request())

    assert result.output.summary == "regenerated"
    assert len(fake_openai.instances[0].calls) == 2
    assert "Validation error: invalid" in fake_openai.instances[0].calls[1]["input"][0]["content"]


def test_openai_tool_call_loop_executes_declared_tool_and_accumulates_usage(fake_openai: type[FakeOpenAI]) -> None:
    fake_openai.queued.extend(
        [
            openai_response(
                None,
                response_output=[
                    {"type": "function_call", "call_id": "call-1", "name": "memory.search", "arguments": '{"q":"x"}'}
                ],
            ),
            openai_response(output("after tool"), input_tokens=4, output_tokens=2, cached_tokens=1),
        ]
    )
    executed: list[tuple[str, dict[str, Any]]] = []
    request = provider_request(
        tools=[ProviderTool("memory.search", "Search memory", {"type": "object", "properties": {"q": {"type": "string"}}})],
        execute_tool=lambda name, args: executed.append((name, args)) or {"hits": 1},
    )

    result = OpenAIResponsesProvider("secret").execute(request)

    assert result.output.summary == "after tool"
    assert executed == [("memory.search", {"q": "x"})]
    assert result.input_tokens == 15
    second_input = fake_openai.instances[0].calls[1]["input"]
    assert second_input[-1] == {"type": "function_call_output", "call_id": "call-1", "output": '{"hits": 1}'}


@pytest.mark.parametrize(
    ("error_name", "status", "category", "retryable"),
    [
        ("APITimeoutError", None, "timeout", True),
        ("RateLimitError", 429, "rate_limit", True),
        ("APIConnectionError", None, "unavailable", True),
        ("InternalServerError", 503, "unavailable", True),
        ("BadRequestError", 400, "openai_http_error", False),
    ],
)
def test_openai_error_classification(
    fake_openai: type[FakeOpenAI], error_name: str, status: int | None, category: str, retryable: bool
) -> None:
    error_type = type(error_name, (RuntimeError,), {})
    error = error_type("provider failed")
    error.status_code = status
    fake_openai.queued.append(error)

    with pytest.raises(ProviderError) as raised:
        OpenAIResponsesProvider("secret").execute(provider_request())

    assert (raised.value.category, raised.value.retryable) == (category, retryable)


def test_openai_cancel_and_cooperative_cancellation(fake_openai: type[FakeOpenAI]) -> None:
    provider = OpenAIResponsesProvider("secret")
    fake_openai.queued.append(openai_response(output()))
    provider.execute(provider_request())

    assert provider.cancel("resp-live") is True
    assert fake_openai.instances[0].cancelled == ["resp-live"]

    cancelled = Event()
    cancelled.set()
    with pytest.raises(ProviderError) as raised:
        provider.execute(provider_request(cancel_event=cancelled))
    assert raised.value.category == "cancelled"


class FakeHTTPResponse:
    def __init__(self, body: dict[str, Any], status_code: int = 200) -> None:
        self.body = body
        self.status_code = status_code
        self.request = httpx.Request("POST", "http://ollama/api/chat")

    def json(self) -> dict[str, Any]:
        return self.body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "request failed",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )


def test_ollama_success_and_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *args, **kwargs: FakeHTTPResponse(
            {
                "created_at": "ollama-1",
                "message": {"role": "assistant", "content": output().model_dump_json()},
                "prompt_eval_count": 12,
                "eval_count": 8,
            }
        ),
    )

    result = OllamaProvider("http://ollama").execute(provider_request())

    assert result.output.summary == "done"
    assert result.request_id == "ollama-1"
    assert (result.input_tokens, result.output_tokens) == (12, 8)


def test_ollama_repairs_malformed_output_once(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = deque(
        [
            FakeHTTPResponse({"message": {"role": "assistant", "content": "bad"}, "prompt_eval_count": 5}),
            FakeHTTPResponse({"message": {"role": "assistant", "content": output("fixed").model_dump_json()}, "eval_count": 3}),
        ]
    )
    calls: list[dict[str, Any]] = []

    def post(*args: Any, **kwargs: Any) -> FakeHTTPResponse:
        calls.append(kwargs["json"])
        return responses.popleft()

    monkeypatch.setattr(httpx, "post", post)
    result = OllamaProvider("http://ollama").execute(provider_request())

    assert result.output.summary == "fixed"
    assert (result.input_tokens, result.output_tokens) == (5, 3)
    assert len(calls) == 2
    assert calls[0]["options"]["num_ctx"] == 8192
    assert calls[1]["messages"][-1]["content"].startswith("Repair the previous")


def test_ollama_accepts_valid_output_with_one_trailing_comma(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []

    def post(*args: Any, **kwargs: Any) -> FakeHTTPResponse:
        calls.append(kwargs["json"])
        return FakeHTTPResponse({"message": {"role": "assistant", "content": output("fixed").model_dump_json() + ","}})

    monkeypatch.setattr(httpx, "post", post)

    result = OllamaProvider("http://ollama").execute(provider_request())

    assert result.output.summary == "fixed"
    assert len(calls) == 1


def test_ollama_local_tool_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = deque(
        [
            FakeHTTPResponse(
                {
                    "message": {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [{"function": {"name": "memory.search", "arguments": {"q": "x"}}}],
                    },
                    "prompt_eval_count": 6,
                }
            ),
            FakeHTTPResponse({"message": {"role": "assistant", "content": output().model_dump_json()}, "eval_count": 4}),
        ]
    )
    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: calls.append(kwargs["json"]) or responses.popleft())
    executed: list[tuple[str, dict[str, Any]]] = []
    request = provider_request(
        tools=[ProviderTool("memory.search", "Search", {"type": "object"})],
        execute_tool=lambda name, args: executed.append((name, args)) or {"hits": ["one"]},
    )

    result = OllamaProvider("http://ollama").execute(request)

    assert result.output.summary == "done"
    assert executed == [("memory.search", {"q": "x"})]
    assert calls[1]["messages"][-1] == {
        "role": "tool",
        "tool_name": "memory.search",
        "content": '{"hits": ["one"]}',
    }


@pytest.mark.parametrize(
    ("raised_error", "status", "category", "retryable"),
    [
        (None, 408, "timeout", True),
        (None, 429, "rate_limit", True),
        (None, 503, "unavailable", True),
        (None, 400, "ollama_http_error", False),
        (httpx.ReadTimeout("slow"), None, "timeout", True),
        (httpx.ConnectError("offline"), None, "unavailable", True),
    ],
)
def test_ollama_error_classification(
    monkeypatch: pytest.MonkeyPatch,
    raised_error: Exception | None,
    status: int | None,
    category: str,
    retryable: bool,
) -> None:
    def post(*args: Any, **kwargs: Any) -> FakeHTTPResponse:
        if raised_error:
            raise raised_error
        return FakeHTTPResponse({}, status_code=status or 500)

    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(ProviderError) as raised:
        OllamaProvider("http://ollama").execute(provider_request())

    assert (raised.value.category, raised.value.retryable) == (category, retryable)


def test_ollama_cooperative_cancellation_does_not_call_http(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: pytest.fail("HTTP should not be called"))
    cancelled = Event()
    cancelled.set()

    with pytest.raises(ProviderError) as raised:
        OllamaProvider("http://ollama").execute(provider_request(cancel_event=cancelled))

    assert raised.value.category == "cancelled"
