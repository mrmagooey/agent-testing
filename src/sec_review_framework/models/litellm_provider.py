from __future__ import annotations

import json
from typing import Any

import litellm

from .base import (
    MaxRetriesExceeded,  # noqa: F401 — re-exported for convenience
    Message,
    ModelProvider,
    ModelResponse,
    ProviderError,
    ProviderRateLimitError,
    RetryPolicy,
    ToolDefinition,
)


class LiteLLMProvider(ModelProvider):
    """Single concrete provider that covers all backends via a model string.

    LiteLLM routes the call to the appropriate SDK (OpenAI, Anthropic,
    Gemini, Mistral, Cohere, …) based on the model name prefix.

    Args:
        model_name: LiteLLM model string, e.g. ``"gpt-4o"``,
            ``"anthropic/claude-3-5-sonnet-20241022"``, ``"gemini/gemini-1.5-pro"``.
        api_key: Optional API key.  When provided it is written to
            ``litellm.api_key`` before the first call.
        api_base: Optional base URL for self-hosted / proxied endpoints.
        retry_policy: Forwarded to :class:`ModelProvider`.
    """

    def __init__(
        self,
        model_name: str,
        api_key: str | None = None,
        api_base: str | None = None,
        retry_policy: RetryPolicy | None = None,
    ) -> None:
        super().__init__(retry_policy=retry_policy)
        self.model_name = model_name
        if api_key is not None:
            litellm.api_key = api_key
        if api_base is not None:
            litellm.api_base = api_base

    # ------------------------------------------------------------------
    # ModelProvider interface
    # ------------------------------------------------------------------

    def model_id(self) -> str:
        """Canonical identifier used in experiment records."""
        return self.model_name

    def _convert_tools(self, tools: list[ToolDefinition]) -> list[dict[str, Any]]:
        """Convert ToolDefinition list to OpenAI function-calling format."""
        return [
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.input_schema,
                },
            }
            for t in tools
        ]

    def _do_complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None,
        system_prompt: str | None,
        max_tokens: int,
        temperature: float,
    ) -> ModelResponse:
        """Call litellm.completion() and normalise the response."""
        # Build message list, optionally prepending a system prompt
        litellm_messages: list[dict[str, Any]] = []
        if system_prompt is not None:
            litellm_messages.append({"role": "system", "content": system_prompt})
        for msg in messages:
            entry: dict[str, Any] = {"role": msg.role, "content": msg.content}
            if msg.tool_call_id is not None:
                entry["tool_call_id"] = msg.tool_call_id
            if msg.tool_calls is not None:
                entry["tool_calls"] = msg.tool_calls
            litellm_messages.append(entry)

        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": litellm_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = self._convert_tools(tools)
            kwargs["tool_choice"] = "auto"

        try:
            response = litellm.completion(**kwargs)
        except litellm.RateLimitError as exc:
            status_code: int = getattr(exc, "status_code", 429)
            retry_after_val: float | None = None
            if hasattr(exc, "response") and exc.response is not None:
                header = exc.response.headers.get(
                    "retry-after"
                ) or exc.response.headers.get("Retry-After")
                if header is not None:
                    try:
                        retry_after_val = float(header)
                    except ValueError:
                        pass
            raise ProviderRateLimitError(
                str(exc), status_code=status_code, retry_after=retry_after_val
            ) from exc
        except litellm.APIError as exc:
            status_code = getattr(exc, "status_code", 500)
            raise ProviderError(str(exc), status_code=status_code) from exc
        except Exception as exc:
            raise ProviderError(str(exc), status_code=500) from exc

        # Extract content
        choice = response.choices[0]
        content: str = choice.message.content or ""

        # Normalise tool calls to [{name, id, input}]
        raw_tcs = choice.message.tool_calls or []
        tool_calls: list[dict[str, Any]] = []
        for tc in raw_tcs:
            try:
                input_data: Any = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                input_data = {}
            tool_calls.append(
                {
                    "name": tc.function.name,
                    "id": tc.id,
                    "input": input_data,
                }
            )

        input_tokens: int = response.usage.prompt_tokens
        output_tokens: int = response.usage.completion_tokens

        raw: dict[str, Any] = (
            response.model_dump()
            if hasattr(response, "model_dump")
            else dict(response)
        )

        return ModelResponse(
            content=content,
            tool_calls=tool_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model_id=self.model_name,
            raw=raw,
        )
