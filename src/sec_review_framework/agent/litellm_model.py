"""pydantic-ai ``Model`` adapter backed by :class:`~sec_review_framework.models.litellm_provider.LiteLLMProvider`.

This module is the production version of the Phase 0 spike at
``scripts/spike_pydantic_ai/litellm_model.py``.  Improvements over the spike:

- :meth:`LiteLLMModel.request` wraps the synchronous
  :meth:`~sec_review_framework.models.base.ModelProvider.complete` call in
  :func:`asyncio.to_thread` so the async ``request()`` method does not block
  the event loop.
- Proper module-level docstring, typing, and docstrings consistent with the
  rest of ``src/sec_review_framework/``.
- ``system`` property parses the model-name prefix to return the actual
  provider string (e.g. ``"anthropic"``, ``"bedrock"``, ``"vertex_ai"``)
  for correct OpenTelemetry attribute tagging.

Requires the ``agent`` extra::

    uv pip install -e ".[agent]"

Attempting to import this module without the ``agent`` extra will raise
:exc:`ImportError` immediately.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

# Fail loudly if pydantic-ai is not installed.  Do NOT add a try/except guard
# here — the caller must know that the "agent" extra is required.
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

from sec_review_framework.models.base import Message, ModelProvider, ToolDefinition

# ---------------------------------------------------------------------------
# Provider name extraction helper
# ---------------------------------------------------------------------------

# Map of known LiteLLM provider prefixes to canonical OTel system names.
# Prefixes are checked in order; the first match wins.  "litellm" is the
# fallback for unknown prefixes.
_PROVIDER_PREFIXES: list[tuple[str, str]] = [
    ("anthropic/", "anthropic"),
    ("bedrock/", "bedrock"),
    ("vertex_ai/", "vertex_ai"),
    ("openai/", "openai"),
    ("azure/", "azure"),
    ("gemini/", "google"),
    ("cohere/", "cohere"),
    ("mistral/", "mistral"),
    ("ollama/", "ollama"),
    ("groq/", "groq"),
]


def _provider_from_model_name(model_name: str) -> str:
    """Return the provider system string for *model_name*.

    Parses the LiteLLM model-name prefix (e.g. ``"anthropic/"`` → ``"anthropic"``).
    Falls back to ``"litellm"`` for unrecognised prefixes so OTel attributes are
    always populated.

    Parameters
    ----------
    model_name:
        Full LiteLLM model string, e.g. ``"anthropic/claude-3-5-sonnet-20241022"``.

    Returns
    -------
    str
        Short provider name suitable for OTel ``gen_ai.system`` attributes.
    """
    for prefix, name in _PROVIDER_PREFIXES:
        if model_name.startswith(prefix):
            return name
    return "litellm"


# ---------------------------------------------------------------------------
# Main adapter
# ---------------------------------------------------------------------------


class LiteLLMModel(Model):
    """pydantic-ai ``Model`` adapter backed by :class:`~sec_review_framework.models.base.ModelProvider`.

    Translates between pydantic-ai's message/tool types and the framework's
    existing :class:`~sec_review_framework.models.base.ModelProvider` interface.
    All LiteLLM routing, authentication, and retry logic stays inside the
    provider; this adapter only converts message formats.

    Design notes
    ------------
    - Only :meth:`request` is implemented (no streaming); streaming is
      deferred to a later phase.
    - The synchronous :meth:`~sec_review_framework.models.base.ModelProvider.complete`
      call is dispatched via :func:`asyncio.to_thread` so the async ``request()``
      method does not block the event loop under a running asyncio loop.
    - Retry logic is delegated entirely to the provider (exponential back-off is
      already built in).  This adapter adds no additional retry loop.
    - The constructor accepts any :class:`~sec_review_framework.models.base.ModelProvider`
      (not just :class:`LiteLLMProvider`) so tests can inject fakes without
      subclassing the concrete provider.  The provider identifier is obtained
      via ``provider.model_id()`` — the abstract method on the base class.

    Parameters
    ----------
    provider:
        Any :class:`~sec_review_framework.models.base.ModelProvider`.
    max_tokens:
        Maximum tokens per call (default 8192).
    temperature:
        Sampling temperature (default 0.2, matching the framework default).
    settings:
        Optional :class:`~pydantic_ai.settings.ModelSettings` forwarded to the
        pydantic-ai :class:`~pydantic_ai.models.Model` base class.
    """

    _provider_instance: ModelProvider
    _max_tokens: int
    _temperature: float

    def __init__(
        self,
        provider: ModelProvider,
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
        """The model identifier string (e.g. ``"anthropic/claude-3-5-sonnet-20241022"``)."""
        return self._provider_instance.model_id()

    @property
    def system(self) -> str:
        """Provider system identifier for OpenTelemetry ``gen_ai.system`` attributes.

        Parses the LiteLLM model-name prefix to return a short provider name
        (e.g. ``"anthropic"``).  Falls back to ``"litellm"`` for unknown
        prefixes so OTel spans are always populated.
        """
        return _provider_from_model_name(self._provider_instance.model_id())

    # ------------------------------------------------------------------
    # Core request method
    # ------------------------------------------------------------------

    async def request(
        self,
        messages: list[ModelMessage],
        model_settings: ModelSettings | None,
        model_request_parameters: ModelRequestParameters,
    ) -> ModelResponse:
        """Translate pydantic-ai messages → LiteLLMProvider → pydantic-ai response.

        The synchronous :meth:`~LiteLLMProvider.complete` call is wrapped in
        :func:`asyncio.to_thread` to avoid blocking the event loop.

        Parameters
        ----------
        messages:
            pydantic-ai message history for the current run.
        model_settings:
            Optional per-request settings overrides (e.g. ``max_tokens``,
            ``temperature``).
        model_request_parameters:
            Tool definitions and instruction parts for this request.

        Returns
        -------
        ModelResponse
            pydantic-ai response containing text/tool-call parts and token usage.
        """
        model_settings, model_request_parameters = self.prepare_request(
            model_settings, model_request_parameters
        )

        # Extract system prompt from instruction parts and/or SystemPromptPart
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

        # Delegate to the provider inside a thread so the event loop is not blocked.
        # LiteLLMProvider.complete() is synchronous (calls litellm.completion() which
        # is a blocking HTTP call); wrapping it in to_thread allows other async tasks
        # to continue while the HTTP round-trip is in flight.
        provider_response = await asyncio.to_thread(
            self._provider_instance.complete,
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
        """Extract and merge all system prompt content into a single string.

        pydantic-ai delivers system prompts in two ways:

        1. Via ``model_request_parameters.instruction_parts`` — used when the
           agent's ``instructions=`` kwarg or ``@agent.instructions`` decorator
           is active.
        2. Via :class:`~pydantic_ai.messages.SystemPromptPart` inside
           :class:`~pydantic_ai.messages.ModelRequest` — used when the agent's
           ``system_prompt=`` kwarg or ``@agent.system_prompt`` decorator is active.

        Both paths are handled and joined with double-newlines so a single string
        is always passed to :meth:`~LiteLLMProvider.complete`.

        Parameters
        ----------
        messages:
            Full pydantic-ai message history.
        model_request_parameters:
            Contains ``instruction_parts`` for the instructions path.

        Returns
        -------
        str | None
            Combined system prompt, or ``None`` if no system prompt was found.
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
        """Convert pydantic-ai :class:`ModelMessage` list to framework :class:`Message` list.

        System/instruction parts are skipped here — they are handled separately
        by :meth:`_get_system_prompt` and passed as the ``system_prompt`` parameter
        to :meth:`~LiteLLMProvider.complete`.

        Parameters
        ----------
        messages:
            pydantic-ai message history.

        Returns
        -------
        list[Message]
            Framework messages ready to pass to :meth:`~LiteLLMProvider.complete`.
        """
        result: list[Message] = []

        for msg in messages:
            if isinstance(msg, ModelRequest):
                user_parts: list[str] = []
                tool_returns: list[tuple[str, str]] = []  # (tool_call_id, content)
                retry_prompts: list[tuple[str | None, str, str]] = []  # (tool_name, call_id, content)

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
                    # SystemPromptPart and instruction parts are handled by
                    # _get_system_prompt; skip them here.

                # Emit user text
                if user_parts:
                    result.append(Message(role="user", content="\n".join(user_parts)))

                # Emit tool-return messages
                for call_id, content in tool_returns:
                    result.append(Message(role="tool", content=content, tool_call_id=call_id))

                # Emit retry prompts as tool messages (or user messages when no tool name)
                for tool_name, call_id, content in retry_prompts:
                    if tool_name is not None:
                        result.append(Message(role="tool", content=content, tool_call_id=call_id))
                    else:
                        result.append(Message(role="user", content=content))

            elif isinstance(msg, ModelResponse):
                text_content = ""
                wire_tool_calls: list[dict[str, Any]] = []
                for part in msg.parts:
                    if isinstance(part, TextPart):
                        text_content = part.content
                    elif isinstance(part, ToolCallPart):
                        # Reconstruct the OpenAI/LiteLLM wire format expected by
                        # providers so that subsequent tool-return messages referencing
                        # tool_call_id can be matched to an existing tool call.
                        args: str = (
                            part.args
                            if isinstance(part.args, str)
                            else json.dumps(part.args)
                        )
                        wire_tool_calls.append({
                            "id": part.tool_call_id,
                            "type": "function",
                            "function": {"name": part.tool_name, "arguments": args},
                        })
                result.append(Message(
                    role="assistant",
                    content=text_content,
                    tool_calls=wire_tool_calls if wire_tool_calls else None,
                ))

        return result

    def _convert_tool_definitions(
        self, model_request_parameters: ModelRequestParameters
    ) -> list[ToolDefinition]:
        """Convert pydantic-ai ``ToolDefinition`` objects to framework ``ToolDefinition`` objects.

        Includes both regular ``function_tools`` and pydantic-ai's synthetic
        ``output_tools`` (the ``final_result`` tool used for structured output).

        Parameters
        ----------
        model_request_parameters:
            Contains ``function_tools`` and ``output_tools`` lists.

        Returns
        -------
        list[ToolDefinition]
            Framework tool definitions ready for :meth:`~LiteLLMProvider.complete`.
        """
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
        """Convert a framework ``ModelResponse`` to a pydantic-ai :class:`ModelResponse`.

        Converts :class:`~sec_review_framework.models.base.ModelResponse` (the framework type).

        Tool call arguments are always serialised to a JSON string.
        pydantic-ai's :meth:`~pydantic_ai.messages.ToolCallPart.args_as_dict`
        internally calls :func:`pydantic_core.from_json`, which only accepts
        ``str | bytes``, not a plain ``dict`` or ``list``.

        Parameters
        ----------
        provider_response:
            Framework :class:`~sec_review_framework.models.base.ModelResponse`
            returned by :meth:`~LiteLLMProvider.complete`.

        Returns
        -------
        ModelResponse
            pydantic-ai response with :class:`~pydantic_ai.messages.TextPart`,
            :class:`~pydantic_ai.messages.ToolCallPart`, and
            :class:`~pydantic_ai.models.RequestUsage`.
        """
        parts: list[Any] = []

        # Text part (may be absent when there are only tool calls)
        if provider_response.content:
            parts.append(TextPart(content=provider_response.content))

        # Tool call parts — args must be a JSON string, not a dict
        for tc in provider_response.tool_calls:
            raw_input = tc["input"]
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
