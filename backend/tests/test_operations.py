from __future__ import annotations

import json
import logging

from consciousness.operations import JsonFormatter, REDACTED, redact


def test_redact_removes_sensitive_keys_and_credential_shapes() -> None:
    original = {
        "authorization": "Bearer top-secret",
        "nested": {"api_key": "sk-examplecredential", "safe": "visible"},
        "input_tokens": 321,
        "message": "request used sk-anothercredential and Bearer abc.def-123",
    }

    result = redact(original)

    assert result["authorization"] == REDACTED
    assert result["nested"] == {"api_key": REDACTED, "safe": "visible"}
    assert result["input_tokens"] == 321
    serialized = json.dumps(result)
    assert "examplecredential" not in serialized
    assert "anothercredential" not in serialized
    assert "abc.def-123" not in serialized


def test_json_formatter_emits_structured_redacted_record() -> None:
    record = logging.LogRecord("consciousness.worker", logging.INFO, __file__, 1, "run complete", (), None)
    record.fields = {"run_id": "run-1", "token": "secret-value"}

    payload = json.loads(JsonFormatter().format(record))

    assert payload["level"] == "info"
    assert payload["logger"] == "consciousness.worker"
    assert payload["message"] == "run complete"
    assert payload["run_id"] == "run-1"
    assert payload["token"] == REDACTED
    assert payload["timestamp"].endswith("+00:00")
