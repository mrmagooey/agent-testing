"""pydantic-ai Model adapter that delegates to LiteLLMProvider.

This module provides ``LiteLLMModel``, a pydantic-ai ``Model`` subclass whose
``request()`` implementation translates between pydantic-ai's message/tool
types and the framework's existing ``LiteLLMProvider``.

Design notes
------------
- We only implement ``request()`` (no streaming); streaming is Phase 1+ scope.
- ``system`` is "litellm" — a non-standard value; Phase 1 may want to map to
  the underlying provider name (e.g. "anthropic", "bedrock").
- Token usage flows through ``RequestUsage`` so pydantic-ai's ``Usage``
  aggregation works correctly.
- The adapter is intentionally thin: it delegates retry logic to
  ``LiteLLMProvider.complete()`` (which already has exponential back-off) and
  does NOT add its own retry loop.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import Model, ModelRequestParameters, RequestUsage
from pydantic_ai.settings import ModelSettings

from sec_review_framework.models.base import Message, ToolDefinition
from sec_review_framework.models.litellm_provider import LiteLLMProvider


class LiteLLMModel(Model):
    """pydantic-ai ``Model`` adapter backed by ``LiteLLMProvider``.

    Parameters
    ----------
    provider:
        A pre-constructed ``LiteLLMProvider``.  All LiteLLM routing / auth is
        handled there; this adapter only converts message formats.
    max_tokens:
        Maximum tokens to request per call (default 8192).
    temperature:
        Sampling temperature (default 0.2, matching the framework's default).
    """

    _provider_instance: LiteLLMProvider
    _max_tokens: int
    _temperature: float

    def __init__(
        self,
        provider: LiteLLMProvider,
        *,
        max_tokens: int = 8192,
        temperature: float = 0.2,
        settings: ModelSettings | None = None,
    ) -> None:
        super().__init__(settings=settings)
        self._provider_instance = provider
        self._max_tokens = max_tokens
        self._temperature = temperature

    # ------------------------------------------------------------------
    # Abstract property implementations (required by Model ABC)
    # ------------------------------------------------------------------

    @property
    def model_name(self) -> str:
        """The model name string (e.g. ``"anthropic/claude-3-5-sonnet-20241022"``)."""
        return self._provider_instance.model_name

    @property
    def system(self) -> str:
        """Provider system identifier for OTel attributes."""
        # Use "litellm" as the system identifier — the underlying provider
        # is encoded in model_name.  Phase 1 can parse the prefix if needed.
        return "litellm"

    # ------------------------------------------------------------------
    # Core request method
    # ------------------------------------------------------------------

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """Translate pydantic-ai messages → LiteLLMProvider → pydantic-ai response."""
        model_settings, model_request_parameters = self.prepare_request(
            model_settings, model_request_parameters
        )

        # Extract system prompt from instruction parts
        system_prompt = self._get_system_prompt(messages, model_request_parameters)

        # Convert pydantic-ai messages to framework Message objects
        framework_messages = self._convert_messages(messages)

        # Convert pydantic-ai tool definitions to framework ToolDefinition objects
        tool_defs = self._convert_tool_definitions(model_request_parameters)

        # Determine effective max_tokens / temperature from ModelSettings override
        max_tokens = self._max_tokens
        temperature = self._temperature
        if model_settings:
            if "max_tokens" in model_settings:
                max_tokens = model_settings["max_tokens"]  # type: ignore[literal-required]
            if "temperature" in model_settings:
                temperature = model_settings["temperature"]  # type: ignore[literal-required]

        # Delegate to the existing provider (includes retry logic)
        provider_response = self._provider_instance.complete(
            messages=framework_messages,
            tools=tool_defs or None,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            temperature=temperature,
        )

        # Convert provider response back to pydantic-ai ModelResponse
        return self._build_model_response(provider_response)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_system_prompt(
        self,
        messages: list[ModelMessage],
        model_request_parameters: ModelRequestParameters,
    ) -> str | None:
        """Extract the system prompt from instruction parts or ModelRequest.

        pydantic-ai delivers system prompts in two ways:
        1. Via ``model_request_parameters.instruction_parts`` (used when the
           agent's ``instructions=`` kwarg or ``@agent.instructions`` decorator
           is used — these go through the ``InstructionPart`` path).
        2. Via ``SystemPromptPart`` inside ``ModelRequest.parts`` (used when
           the agent's ``system_prompt=`` kwarg or ``@agent.system_prompt``
           decorator is used).

        We handle both and join them with double-newlines.
        """
        system_parts: list[str] = []

        # Path 1: instruction_parts (from instructions= kwarg)
        instruction_parts = self._get_instruction_parts(messages, model_request_parameters)
        if instruction_parts:
            system_parts.extend(part.content for part in instruction_parts)

        # Path 2: SystemPromptPart inside ModelRequest.parts (from system_prompt= kwarg)
        for msg in messages:
            if isinstance(msg, ModelRequest):
                for part in msg.parts:
                    if isinstance(part, SystemPromptPart):
                        system_parts.append(part.content)

        return "\n\n".join(system_parts) if system_parts else None

    def _convert_messages(self, messages: list[ModelMessage]) -> list[Message]:
        """Convert pydantic-ai ModelMessages to framework Message objects.

        Skips the instructions/system prompt (handled separately via system_prompt
        parameter to complete()).
        """
        result: list[Message] = []

        for msg in messages:
            if isinstance(msg, ModelRequest):
                # Collect all non-system parts from the request
                user_parts: list[str] = []
                tool_returns: list[tuple[str, str]] = []  # (tool_call_id, content)
                retry_prompts: list[tuple[str | None, str, str]] = []  # (tool_name, tool_call_id, content)

                for part in msg.parts:
                    if isinstance(part, UserPromptPart):
                        content = part.content
                        if isinstance(content, str):
                            user_parts.append(content)
                        else:
                            # List of content parts — extract text
                            texts = [p if isinstance(p, str) else str(p) for p in content]
                            user_parts.append(" ".join(texts))
                    elif isinstance(part, ToolReturnPart):
                        tool_returns.append((part.tool_call_id, str(part.content)))
                    elif isinstance(part, RetryPromptPart):
                        retry_prompts.append(
                            (part.tool_name, part.tool_call_id, part.model_response())
                        )
                    # SystemPromptPart and other instruction-only parts are skipped;
                    # system prompt is handled via _get_system_prompt.

                # Emit user text parts
                if user_parts:
                    result.append(Message(role="user", content="\n".join(user_parts)))

                # Emit tool return parts
                for call_id, content in tool_returns:
                    result.append(
                        Message(role="tool", content=content, tool_call_id=call_id)
                    )

                # Emit retry prompts as tool messages (or user messages if no tool name)
                for tool_name, call_id, content in retry_prompts:
                    if tool_name is not None:
                        result.append(
                            Message(role="tool", content=content, tool_call_id=call_id)
                        )
                    else:
                        result.append(Message(role="user", content=content))

            elif isinstance(msg, ModelResponse):
                # Build a plain assistant message.  The framework's Message has
                # no tool_calls field; the provider re-reads tool calls from its
                # own history on replay.  Tool call details live in the following
                # tool-return messages so we emit text content only here.
                # TODO Phase 1: if the framework Message gains a tool_calls field,
                # populate it from ToolCallPart entries in msg.parts.
                text_content = ""
                for part in msg.parts:
                    if isinstance(part, TextPart):
                        text_content = part.content

                result.append(Message(role="assistant", content=text_content))

        return result

    def _convert_tool_definitions(
        self, model_request_parameters: ModelRequestParameters
    ) -> list[ToolDefinition]:
        """Convert pydantic-ai ToolDefinitions to framework ToolDefinitions."""
        all_tools = [
            *model_request_parameters.function_tools,
            *model_request_parameters.output_tools,
        ]
        return [
            ToolDefinition(
                name=td.name,
                description=td.description or "",
                input_schema=dict(td.parameters_json_schema),
            )
            for td in all_tools
        ]

    def _build_model_response(self, provider_response: Any) -> ModelResponse:
        """Convert a framework ModelResponse to a pydantic-ai ModelResponse."""
        parts: list[Any] = []

        # Text part (may be empty string when there are only tool calls)
        if provider_response.content:
            parts.append(TextPart(content=provider_response.content))

        # Tool call parts
        for tc in provider_response.tool_calls:
            raw_input = tc["input"]
            # pydantic-ai's ToolCallPart.args_as_dict() calls pydantic_core.from_json()
            # which only accepts str/bytes — not a plain dict or list.
            # Always serialise to a JSON string so replay works correctly.
            args: str = json.dumps(raw_input) if not isinstance(raw_input, str) else raw_input
            parts.append(
                ToolCallPart(
                    tool_name=tc["name"],
                    args=args,
                    tool_call_id=tc["id"],
                )
            )

        usage = RequestUsage(
            input_tokens=provider_response.input_tokens,
            output_tokens=provider_response.output_tokens,
        )

        return ModelResponse(
            parts=parts,
            usage=usage,
            model_name=provider_response.model_id,
        )
