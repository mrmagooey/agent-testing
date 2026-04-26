from __future__ import annotations

import copy
import random
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Message:
    role: str  # "user" | "assistant" | "tool"
    content: str
    tool_call_id: str | None = None
    # OpenAI/LiteLLM tool_calls array for assistant turns that made tool calls.
    # Each element: {"id": "...", "type": "function", "function": {"name": "...", "arguments": "..."}}
    tool_calls: list[dict[str, Any]] | None = None


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict[str, Any]  # JSON Schema


@dataclass
class ModelResponse:
    content: str
    tool_calls: list[dict[str, Any]]  # [{name, id, input}]
    input_tokens: int
    output_tokens: int
    model_id: str
    raw: dict[str, Any]  # full provider response, for debugging


@dataclass
class RetryPolicy:
    """Per-provider retry configuration. Loaded from config/retry.yaml."""

    max_retries: int = 3
    base_delay: float = 1.0       # seconds
    max_delay: float = 60.0       # seconds
    jitter: bool = True           # randomize delay to avoid thundering herd
    retryable_status_codes: list[int] = field(
        default_factory=lambda: [429, 529, 503]
    )

    def compute_delay(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            delay = retry_after   # honour provider's Retry-After header
        else:
            delay = min(self.base_delay * (2 ** attempt), self.max_delay)
        if self.jitter:
            delay *= (0.5 + random.random() * 0.5)
        return delay


class ProviderError(Exception):
    """Base error for provider API failures."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class ProviderRateLimitError(ProviderError):
    """Raised when the provider signals a rate limit (HTTP 429 / 529)."""

    def __init__(
        self,
        message: str,
        status_code: int,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message, status_code)
        self.retry_after = retry_after


class MaxRetriesExceeded(Exception):
    """Raised after all retry attempts are exhausted."""


class ModelProvider(ABC):
    """Unified interface over any LLM API. Includes retry wrapping."""

    def __init__(self, retry_policy: RetryPolicy | None = None) -> None:
        self.retry_policy: RetryPolicy = retry_policy or RetryPolicy()
        self.token_log: list[ModelResponse] = []
        self.conversation_log: list[dict[str, Any]] = []
        # Serialises the multi-step append sequence in complete() so concurrent
        # callers sharing a provider (e.g. asyncio.gather over to_thread) cannot
        # interleave token_log / conversation_log entries from different turns.
        self._log_lock: threading.Lock = threading.Lock()

    def complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None = None,
        system_prompt: str | None = None,
        max_tokens: int = 8192,
        temperature: float = 0.2,
    ) -> ModelResponse:
        """Calls _do_complete() with retry logic around provider rate limits."""
        policy = self.retry_policy
        last_exc: Exception | None = None

        for attempt in range(policy.max_retries + 1):
            try:
                response = self._do_complete(
                    messages, tools, system_prompt, max_tokens, temperature
                )

                with self._log_lock:
                    self.token_log.append(response)
                    for msg in messages:
                        entry: dict[str, Any] = {"role": msg.role, "content": msg.content}
                        if msg.tool_call_id is not None:
                            entry["tool_call_id"] = msg.tool_call_id
                        if msg.tool_calls is not None:
                            entry["tool_calls"] = msg.tool_calls
                        self.conversation_log.append(entry)
                    self.conversation_log.append(
                        {
                            "role": "assistant",
                            "content": response.content,
                            "tool_calls": response.tool_calls,
                        }
                    )

                return response

            except ProviderRateLimitError as exc:
                last_exc = exc
                if attempt == policy.max_retries:
                    break
                delay = policy.compute_delay(attempt, retry_after=exc.retry_after)
                time.sleep(delay)

            except ProviderError as exc:
                if exc.status_code not in policy.retryable_status_codes:
                    raise
                last_exc = exc
                if attempt == policy.max_retries:
                    break
                delay = policy.compute_delay(attempt)
                time.sleep(delay)

        raise MaxRetriesExceeded(
            f"Provider failed after {policy.max_retries} retries"
        ) from last_exc

    @abstractmethod
    def _do_complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None,
        system_prompt: str | None,
        max_tokens: int,
        temperature: float,
    ) -> ModelResponse:
        """Provider-specific implementation. Called by complete() with retry wrapping."""
        ...

    @abstractmethod
    def model_id(self) -> str:
        """Canonical identifier used in experiment records."""
        ...

    def supports_tools(self) -> bool:
        """Override to False for providers that don't support function calling."""
        return True

    def clone(self) -> ModelProvider:
        """Return a shallow copy with independent token_log and conversation_log.

        Intended for parallel subagent execution where each clone tracks its
        own token usage and conversation history independently.
        """
        cloned = copy.copy(self)
        cloned.token_log = []
        cloned.conversation_log = []
        cloned._log_lock = threading.Lock()
        return cloned
