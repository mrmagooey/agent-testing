"""Tests for ModelProvider retry logic, RetryPolicy, token/conversation logs, and clone()."""

from __future__ import annotations

from collections import deque
from typing import Any
from unittest.mock import patch

import pytest

from sec_review_framework.models.base import (
    MaxRetriesExceeded,
    Message,
    ModelProvider,
    ModelResponse,
    ProviderError,
    ProviderRateLimitError,
    RetryPolicy,
    ToolDefinition,
)


# ---------------------------------------------------------------------------
# Test provider implementation
# ---------------------------------------------------------------------------


class _TestProvider(ModelProvider):
    """Returns responses or raises exceptions from a queue, in order."""

    def __init__(
        self,
        responses_or_errors: list,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        super().__init__(retry_policy=retry_policy)
        self._queue: deque = deque(responses_or_errors)

    def _do_complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None,
        system_prompt: str | None,
        max_tokens: int,
        temperature: float,
    ) -> ModelResponse:
        if not self._queue:
            raise RuntimeError("_TestProvider queue is empty")
        item = self._queue.popleft()
        if isinstance(item, BaseException):
            raise item
        return item

    def model_id(self) -> str:
        return "test-provider"


def _ok_response(content: str = "OK") -> ModelResponse:
    return ModelResponse(
        content=content,
        tool_calls=[],
        input_tokens=100,
        output_tokens=50,
        model_id="test-provider",
        raw={},
    )


def _msg(content: str = "Hello") -> list[Message]:
    return [Message(role="user", content=content)]


# ---------------------------------------------------------------------------
# RetryPolicy.compute_delay
# ---------------------------------------------------------------------------


def test_compute_delay_exponential_attempt_0():
    policy = RetryPolicy(base_delay=1.0, max_delay=60.0, jitter=False)
    assert policy.compute_delay(attempt=0) == pytest.approx(1.0)


def test_compute_delay_exponential_attempt_1():
    policy = RetryPolicy(base_delay=1.0, max_delay=60.0, jitter=False)
    assert policy.compute_delay(attempt=1) == pytest.approx(2.0)


def test_compute_delay_exponential_attempt_2():
    policy = RetryPolicy(base_delay=1.0, max_delay=60.0, jitter=False)
    assert policy.compute_delay(attempt=2) == pytest.approx(4.0)


def test_compute_delay_capped_at_max_delay():
    policy = RetryPolicy(base_delay=1.0, max_delay=5.0, jitter=False)
    delay = policy.compute_delay(attempt=10)
    assert delay == pytest.approx(5.0)


def test_compute_delay_retry_after_honors_value():
    """When retry_after is provided, that value is used as the base delay."""
    policy = RetryPolicy(base_delay=1.0, max_delay=60.0, jitter=False)
    delay = policy.compute_delay(attempt=0, retry_after=42.0)
    assert delay == pytest.approx(42.0)


def test_compute_delay_jitter_range():
    """With jitter=True, delay must be in [0.5*base, base)."""
    policy = RetryPolicy(base_delay=1.0, max_delay=60.0, jitter=True)
    for _ in range(50):
        delay = policy.compute_delay(attempt=0)
        assert 0.5 <= delay < 1.0


def test_compute_delay_no_jitter_deterministic():
    """jitter=False → same attempt always yields the same delay."""
    policy = RetryPolicy(base_delay=1.0, max_delay=60.0, jitter=False)
    delays = [policy.compute_delay(attempt=1) for _ in range(10)]
    assert all(d == delays[0] for d in delays)


# ---------------------------------------------------------------------------
# complete() success path
# ---------------------------------------------------------------------------


def test_complete_success_first_try_populates_token_log():
    provider = _TestProvider([_ok_response("result")])
    resp = provider.complete(_msg())
    assert resp.content == "result"
    assert len(provider.token_log) == 1
    assert provider.token_log[0].content == "result"


def test_complete_conversation_log_populated_after_call():
    provider = _TestProvider([_ok_response("answer")])
    provider.complete(_msg("what?"))
    # conversation_log should have the user message + assistant response
    assert len(provider.conversation_log) >= 2
    roles = [e["role"] for e in provider.conversation_log]
    assert "user" in roles
    assert "assistant" in roles


# ---------------------------------------------------------------------------
# Retry logic
# ---------------------------------------------------------------------------


def test_complete_retries_on_rate_limit_error():
    """ProviderRateLimitError → retried; succeeds on second attempt."""
    responses = [
        ProviderRateLimitError("rate limited", status_code=429),
        _ok_response("success after retry"),
    ]
    policy = RetryPolicy(max_retries=3, base_delay=0.0, jitter=False)
    provider = _TestProvider(responses, retry_policy=policy)

    with patch("time.sleep"):
        resp = provider.complete(_msg())

    assert resp.content == "success after retry"


def test_complete_raises_max_retries_exceeded_after_all_retries_fail():
    """All retry attempts fail with rate limit → MaxRetriesExceeded."""
    policy = RetryPolicy(max_retries=2, base_delay=0.0, jitter=False)
    responses = [
        ProviderRateLimitError("rl", status_code=429),
        ProviderRateLimitError("rl", status_code=429),
        ProviderRateLimitError("rl", status_code=429),
    ]
    provider = _TestProvider(responses, retry_policy=policy)

    with patch("time.sleep"):
        with pytest.raises(MaxRetriesExceeded):
            provider.complete(_msg())


def test_complete_non_retryable_provider_error_raised_immediately():
    """A ProviderError with a non-retryable status code is raised without retrying."""
    policy = RetryPolicy(
        max_retries=3,
        base_delay=0.0,
        jitter=False,
        retryable_status_codes=[429, 503],
    )
    responses = [
        ProviderError("bad request", status_code=400),
        _ok_response(),  # should never be reached
    ]
    provider = _TestProvider(responses, retry_policy=policy)

    with pytest.raises(ProviderError) as exc_info:
        provider.complete(_msg())

    assert exc_info.value.status_code == 400
    # Only one attempt made — queue still has the success response
    assert len(provider._queue) == 1


def test_complete_retryable_status_code_503_retried():
    """ProviderError with status 503 (retryable) is retried."""
    policy = RetryPolicy(
        max_retries=3,
        base_delay=0.0,
        jitter=False,
        retryable_status_codes=[429, 503],
    )
    responses = [
        ProviderError("service unavailable", status_code=503),
        _ok_response("recovered"),
    ]
    provider = _TestProvider(responses, retry_policy=policy)

    with patch("time.sleep"):
        resp = provider.complete(_msg())

    assert resp.content == "recovered"


def test_complete_rate_limit_with_retry_after_uses_provided_delay():
    """retry_after from ProviderRateLimitError is passed to compute_delay (via sleep)."""
    policy = RetryPolicy(max_retries=2, base_delay=1.0, jitter=False)
    responses = [
        ProviderRateLimitError("rl", status_code=429, retry_after=30.0),
        _ok_response("ok"),
    ]
    provider = _TestProvider(responses, retry_policy=policy)

    sleep_calls = []
    with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
        provider.complete(_msg())

    assert len(sleep_calls) == 1
    assert sleep_calls[0] == pytest.approx(30.0)


# ---------------------------------------------------------------------------
# clone()
# ---------------------------------------------------------------------------


def test_clone_produces_independent_empty_logs():
    """clone() gives a new provider with empty token_log and conversation_log."""
    provider = _TestProvider([_ok_response("a"), _ok_response("b")])
    provider.complete(_msg())          # populates original logs

    cloned = provider.clone()
    assert cloned.token_log == []
    assert cloned.conversation_log == []


def test_clone_original_logs_unaffected_by_clone_usage():
    """Using the clone doesn't pollute the original's logs."""
    provider = _TestProvider([_ok_response("original"), _ok_response("clone")])
    provider.complete(_msg("first"))

    cloned = provider.clone()
    cloned._queue = deque([_ok_response("from clone")])
    cloned.complete(_msg("second"))

    assert len(provider.token_log) == 1
    assert provider.token_log[0].content == "original"
