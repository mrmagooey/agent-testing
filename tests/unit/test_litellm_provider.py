"""Dedicated unit tests for LiteLLMProvider."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from sec_review_framework.models.litellm_provider import LiteLLMProvider
from sec_review_framework.models.base import (
    Message,
    ModelResponse,
    ProviderError,
    ProviderRateLimitError,
    RetryPolicy,
    ToolDefinition,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_litellm_response(
    content: str = "Hello",
    tool_calls: list | None = None,
    prompt_tokens: int = 100,
    completion_tokens: int = 50,
) -> MagicMock:
    """Build a minimal litellm-style response object."""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls or []

    choice = MagicMock()
    choice.message = msg

    usage = MagicMock()
    usage.prompt_tokens = prompt_tokens
    usage.completion_tokens = completion_tokens

    response = MagicMock()
    response.choices = [choice]
    response.usage = usage
    response.model_dump.return_value = {"id": "fake", "object": "chat.completion"}
    return response


def _make_tool_call(name: str, arguments: str, tc_id: str = "tc-001") -> MagicMock:
    """Build a minimal tool call object."""
    fn = MagicMock()
    fn.name = name
    fn.arguments = arguments

    tc = MagicMock()
    tc.function = fn
    tc.id = tc_id
    return tc


def _make_provider(model_name: str = "gpt-4o") -> LiteLLMProvider:
    return LiteLLMProvider(
        model_name=model_name,
        retry_policy=RetryPolicy(max_retries=0, base_delay=0.0, jitter=False),
    )


def _user_msg(content: str = "hi") -> list[Message]:
    return [Message(role="user", content=content)]


# ---------------------------------------------------------------------------
# model_id()
# ---------------------------------------------------------------------------


def test_model_id_returns_model_name():
    provider = _make_provider("anthropic/claude-3-5-sonnet-20241022")
    assert provider.model_id() == "anthropic/claude-3-5-sonnet-20241022"


# ---------------------------------------------------------------------------
# _convert_tools()
# ---------------------------------------------------------------------------


def test_convert_tools_produces_openai_format():
    provider = _make_provider()
    tools = [
        ToolDefinition(
            name="read_file",
            description="Read a file",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        )
    ]
    result = provider._convert_tools(tools)
    assert len(result) == 1
    assert result[0]["type"] == "function"
    assert result[0]["function"]["name"] == "read_file"
    assert result[0]["function"]["description"] == "Read a file"
    assert "parameters" in result[0]["function"]


def test_convert_tools_empty_list_returns_empty():
    provider = _make_provider()
    assert provider._convert_tools([]) == []


def test_convert_tools_multiple_tools():
    provider = _make_provider()
    tools = [
        ToolDefinition(name="t1", description="d1", input_schema={}),
        ToolDefinition(name="t2", description="d2", input_schema={}),
        ToolDefinition(name="t3", description="d3", input_schema={}),
    ]
    result = provider._convert_tools(tools)
    assert len(result) == 3
    names = [r["function"]["name"] for r in result]
    assert names == ["t1", "t2", "t3"]


# ---------------------------------------------------------------------------
# _do_complete() — success paths
# ---------------------------------------------------------------------------


def test_complete_returns_content_and_tokens():
    provider = _make_provider()
    fake_resp = _make_litellm_response(content="The answer is 42", prompt_tokens=80, completion_tokens=20)

    with patch("litellm.completion", return_value=fake_resp):
        resp = provider.complete(_user_msg("What is the answer?"))

    assert resp.content == "The answer is 42"
    assert resp.input_tokens == 80
    assert resp.output_tokens == 20
    assert resp.model_id == "gpt-4o"


def test_complete_with_system_prompt_prepends_system_message():
    provider = _make_provider()
    fake_resp = _make_litellm_response()
    captured_kwargs: dict[str, Any] = {}

    def capture(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_resp

    with patch("litellm.completion", side_effect=capture):
        provider.complete(_user_msg(), system_prompt="You are a security expert.")

    messages = captured_kwargs["messages"]
    assert messages[0]["role"] == "system"
    assert "security expert" in messages[0]["content"]


def test_complete_no_system_prompt_omits_system_message():
    provider = _make_provider()
    fake_resp = _make_litellm_response()
    captured_kwargs: dict[str, Any] = {}

    def capture(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_resp

    with patch("litellm.completion", side_effect=capture):
        provider.complete(_user_msg())

    messages = captured_kwargs["messages"]
    roles = [m["role"] for m in messages]
    assert "system" not in roles


def test_complete_with_tools_passes_tool_choice_auto():
    provider = _make_provider()
    fake_resp = _make_litellm_response()
    captured_kwargs: dict[str, Any] = {}

    def capture(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_resp

    tools = [ToolDefinition(name="search", description="Search", input_schema={})]
    with patch("litellm.completion", side_effect=capture):
        provider.complete(_user_msg(), tools=tools)

    assert captured_kwargs.get("tool_choice") == "auto"
    assert len(captured_kwargs["tools"]) == 1


def test_complete_without_tools_omits_tool_choice():
    provider = _make_provider()
    fake_resp = _make_litellm_response()
    captured_kwargs: dict[str, Any] = {}

    def capture(**kwargs):
        captured_kwargs.update(kwargs)
        return fake_resp

    with patch("litellm.completion", side_effect=capture):
        provider.complete(_user_msg())

    assert "tool_choice" not in captured_kwargs


# ---------------------------------------------------------------------------
# Tool call normalisation
# ---------------------------------------------------------------------------


def test_tool_calls_parsed_correctly():
    provider = _make_provider()
    tc = _make_tool_call("read_file", json.dumps({"path": "/etc/passwd"}), "tc-1")
    fake_resp = _make_litellm_response(tool_calls=[tc])

    with patch("litellm.completion", return_value=fake_resp):
        resp = provider.complete(_user_msg())

    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0]["name"] == "read_file"
    assert resp.tool_calls[0]["id"] == "tc-1"
    assert resp.tool_calls[0]["input"] == {"path": "/etc/passwd"}


def test_tool_call_malformed_json_arguments_yields_empty_input():
    """If tool call arguments are not valid JSON, input should be {} (not crash)."""
    provider = _make_provider()
    tc = _make_tool_call("bad_tool", "{not valid json}", "tc-2")
    fake_resp = _make_litellm_response(tool_calls=[tc])

    with patch("litellm.completion", return_value=fake_resp):
        resp = provider.complete(_user_msg())

    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0]["input"] == {}


def test_tool_call_none_arguments_yields_empty_input():
    """If tool call arguments are None, input should be {} (not crash)."""
    provider = _make_provider()
    tc = _make_tool_call("null_tool", None, "tc-3")  # type: ignore[arg-type]
    tc.function.arguments = None
    fake_resp = _make_litellm_response(tool_calls=[tc])

    with patch("litellm.completion", return_value=fake_resp):
        resp = provider.complete(_user_msg())

    assert resp.tool_calls[0]["input"] == {}


def test_multiple_tool_calls_all_normalised():
    provider = _make_provider()
    tcs = [
        _make_tool_call("tool_a", json.dumps({"x": 1}), "id-a"),
        _make_tool_call("tool_b", json.dumps({"y": 2}), "id-b"),
    ]
    fake_resp = _make_litellm_response(tool_calls=tcs)

    with patch("litellm.completion", return_value=fake_resp):
        resp = provider.complete(_user_msg())

    assert len(resp.tool_calls) == 2
    names = {tc["name"] for tc in resp.tool_calls}
    assert names == {"tool_a", "tool_b"}


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_rate_limit_error_raises_provider_rate_limit_error():
    """RateLimitError from litellm is converted and raised (as ProviderRateLimitError
    or wrapped in MaxRetriesExceeded when max_retries=0)."""
    import litellm as _litellm
    from sec_review_framework.models.base import MaxRetriesExceeded

    provider = _make_provider()
    exc = _litellm.RateLimitError(
        message="Too many requests",
        response=MagicMock(headers={}),
        llm_provider="openai",
        model="gpt-4o",
    )

    with patch("litellm.completion", side_effect=exc):
        with pytest.raises((ProviderRateLimitError, MaxRetriesExceeded)) as exc_info:
            provider.complete(_user_msg())

    # Either the raw ProviderRateLimitError or it's the __cause__ of MaxRetriesExceeded
    raised = exc_info.value
    if isinstance(raised, MaxRetriesExceeded):
        assert isinstance(raised.__cause__, ProviderRateLimitError)
        assert raised.__cause__.status_code == 429
    else:
        assert raised.status_code == 429


def test_rate_limit_error_with_retry_after_header_parsed():
    """retry-after header is parsed and stored on the ProviderRateLimitError."""
    import litellm as _litellm
    from sec_review_framework.models.base import MaxRetriesExceeded

    provider = _make_provider()
    mock_response = MagicMock()
    mock_response.headers = {"retry-after": "60"}
    exc = _litellm.RateLimitError(
        message="rate limited",
        response=mock_response,
        llm_provider="openai",
        model="gpt-4o",
    )

    with patch("litellm.completion", side_effect=exc):
        with pytest.raises((ProviderRateLimitError, MaxRetriesExceeded)) as exc_info:
            provider.complete(_user_msg())

    raised = exc_info.value
    if isinstance(raised, MaxRetriesExceeded):
        rl_exc = raised.__cause__
        assert isinstance(rl_exc, ProviderRateLimitError)
        assert rl_exc.retry_after == pytest.approx(60.0)
    else:
        assert raised.retry_after == pytest.approx(60.0)


def test_api_error_raises_provider_error():
    import litellm as _litellm

    provider = _make_provider()
    exc = _litellm.APIError(
        status_code=500,
        message="Internal Server Error",
        llm_provider="openai",
        model="gpt-4o",
    )

    with patch("litellm.completion", side_effect=exc):
        with pytest.raises(ProviderError) as exc_info:
            provider.complete(_user_msg())

    assert exc_info.value.status_code == 500


def test_unexpected_exception_raises_provider_error():
    provider = _make_provider()

    with patch("litellm.completion", side_effect=RuntimeError("unexpected network failure")):
        with pytest.raises(ProviderError) as exc_info:
            provider.complete(_user_msg())

    assert exc_info.value.status_code == 500
    assert "unexpected network failure" in str(exc_info.value)


def test_timeout_exception_raises_provider_error():
    import httpx

    provider = _make_provider()

    with patch("litellm.completion", side_effect=httpx.TimeoutException("connect timeout")):
        with pytest.raises(ProviderError):
            provider.complete(_user_msg())


# ---------------------------------------------------------------------------
# Null/empty content
# ---------------------------------------------------------------------------


def test_null_content_coerced_to_empty_string():
    provider = _make_provider()
    fake_resp = _make_litellm_response(content=None)  # type: ignore[arg-type]
    fake_resp.choices[0].message.content = None

    with patch("litellm.completion", return_value=fake_resp):
        resp = provider.complete(_user_msg())

    assert resp.content == ""


# ---------------------------------------------------------------------------
# model_dump fallback (raw field)
# ---------------------------------------------------------------------------


def test_raw_field_uses_model_dump_when_available():
    provider = _make_provider()
    fake_resp = _make_litellm_response()
    fake_resp.model_dump.return_value = {"key": "value"}

    with patch("litellm.completion", return_value=fake_resp):
        resp = provider.complete(_user_msg())

    assert resp.raw == {"key": "value"}


def test_raw_field_falls_back_to_dict_when_no_model_dump():
    provider = _make_provider()
    fake_resp = _make_litellm_response()
    del fake_resp.model_dump  # remove model_dump to test fallback

    # Patch __iter__ to simulate dict() fallback
    fake_resp.__iter__ = MagicMock(return_value=iter([("id", "test"), ("object", "chat")]))

    with patch("litellm.completion", return_value=fake_resp):
        # Should not raise even when model_dump is absent
        resp = provider.complete(_user_msg())

    assert isinstance(resp.raw, dict)
