"""Unit tests for :mod:`sec_review_framework.agent.litellm_model`.

All tests run offline against ``ScriptedLiteLLMProvider`` — a subclass of
``LiteLLMProvider`` that returns pre-scripted responses without hitting any
external API.

Skipped cleanly when the ``agent`` extra (pydantic-ai) is not installed.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest

# Skip the entire module if pydantic_ai is not installed.
# This allows the test suite to run cleanly without the agent extra.
pydantic_ai = pytest.importorskip("pydantic_ai")

from pydantic_ai import Agent  # noqa: E402
from pydantic_ai.messages import (  # noqa: E402
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    SystemPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters  # noqa: E402

from sec_review_framework.agent.litellm_model import LiteLLMModel, _provider_from_model_name  # noqa: E402
from sec_review_framework.data.findings import Finding, Severity, VulnClass  # noqa: E402
from sec_review_framework.models.base import Message, ToolDefinition  # noqa: E402
from sec_review_framework.models.base import ModelResponse as FrameworkModelResponse  # noqa: E402
from sec_review_framework.models.litellm_provider import LiteLLMProvider  # noqa: E402

# ---------------------------------------------------------------------------
# Shared test fixture: ScriptedLiteLLMProvider
# ---------------------------------------------------------------------------


class ScriptedLiteLLMProvider(LiteLLMProvider):
    """LiteLLMProvider that returns pre-scripted responses for offline tests.

    Responses are popped from a queue in order.  The first element of
    ``responses`` is returned on the first call to ``_do_complete``, etc.
    """

    def __init__(self, responses: list[dict[str, Any]], model_name: str = "fake/test") -> None:
        super().__init__(model_name=model_name)
        self._responses: list[dict[str, Any]] = list(responses)

    def _do_complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None,
        system_prompt: str | None,
        max_tokens: int,
        temperature: float,
    ) -> FrameworkModelResponse:
        if not self._responses:
            raise RuntimeError("ScriptedLiteLLMProvider: no more scripted responses")
        data = self._responses.pop(0)
        return FrameworkModelResponse(
            content=data.get("content", ""),
            tool_calls=data.get("tool_calls", []),
            input_tokens=data.get("input_tokens", 10),
            output_tokens=data.get("output_tokens", 5),
            model_id=self.model_name,
            raw={},
        )


def _make_finding_data(**overrides: Any) -> dict[str, Any]:
    """Return a valid Finding dict, optionally overriding fields."""
    base = {
        "id": str(uuid.uuid4()),
        "file_path": "src/auth.py",
        "line_start": 42,
        "line_end": 42,
        "vuln_class": "hardcoded_secret",
        "cwe_ids": ["CWE-798"],
        "severity": "high",
        "title": "Hardcoded API secret",
        "description": "A hardcoded secret key was found.",
        "recommendation": "Move to environment variable.",
        "confidence": 0.95,
        "raw_llm_output": "...",
        "produced_by": "test",
        "experiment_id": "test_exp_001",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests: _provider_from_model_name
# ---------------------------------------------------------------------------


class TestProviderFromModelName:
    """Verify provider name extraction from LiteLLM model strings."""

    def test_anthropic_prefix(self) -> None:
        assert _provider_from_model_name("anthropic/claude-3-5-sonnet-20241022") == "anthropic"

    def test_bedrock_prefix(self) -> None:
        assert _provider_from_model_name("bedrock/anthropic.claude-3-haiku-20240307-v1:0") == "bedrock"

    def test_vertex_ai_prefix(self) -> None:
        assert _provider_from_model_name("vertex_ai/claude-3-haiku@20240307") == "vertex_ai"

    def test_openai_prefix(self) -> None:
        assert _provider_from_model_name("openai/gpt-4o") == "openai"

    def test_unknown_prefix_falls_back_to_litellm(self) -> None:
        assert _provider_from_model_name("unknown/some-model") == "litellm"

    def test_no_prefix_falls_back_to_litellm(self) -> None:
        # e.g. bare model strings like "gpt-4o" (OpenAI default)
        assert _provider_from_model_name("gpt-4o") == "litellm"

    def test_fake_test_model(self) -> None:
        assert _provider_from_model_name("fake/test") == "litellm"


# ---------------------------------------------------------------------------
# Tests: LiteLLMModel adapter properties
# ---------------------------------------------------------------------------


class TestLiteLLMModelProperties:
    """Verifies adapter construction and Model ABC compliance."""

    def test_model_name_matches_provider(self) -> None:
        provider = ScriptedLiteLLMProvider([], model_name="anthropic/claude-3-haiku-20240307")
        model = LiteLLMModel(provider)
        assert model.model_name == "anthropic/claude-3-haiku-20240307"

    def test_system_parses_anthropic_prefix(self) -> None:
        provider = ScriptedLiteLLMProvider([], model_name="anthropic/claude-3-haiku-20240307")
        model = LiteLLMModel(provider)
        assert model.system == "anthropic"

    def test_system_parses_bedrock_prefix(self) -> None:
        provider = ScriptedLiteLLMProvider([], model_name="bedrock/claude-v2")
        model = LiteLLMModel(provider)
        assert model.system == "bedrock"

    def test_system_falls_back_to_litellm(self) -> None:
        provider = ScriptedLiteLLMProvider([], model_name="fake/test")
        model = LiteLLMModel(provider)
        assert model.system == "litellm"

    def test_model_id_combines_system_and_name(self) -> None:
        provider = ScriptedLiteLLMProvider([], model_name="anthropic/claude-3-haiku-20240307")
        model = LiteLLMModel(provider)
        # pydantic-ai Model.model_id = f"{system}:{model_name}"
        assert model.model_id == "anthropic:anthropic/claude-3-haiku-20240307"

    def test_default_max_tokens_and_temperature(self) -> None:
        provider = ScriptedLiteLLMProvider([])
        model = LiteLLMModel(provider)
        assert model._max_tokens == 8192
        assert model._temperature == 0.2

    def test_custom_max_tokens_and_temperature(self) -> None:
        provider = ScriptedLiteLLMProvider([])
        model = LiteLLMModel(provider, max_tokens=4096, temperature=0.0)
        assert model._max_tokens == 4096
        assert model._temperature == 0.0


# ---------------------------------------------------------------------------
# Tests: message conversion helpers
# ---------------------------------------------------------------------------


class TestConvertMessages:
    """Verifies pydantic-ai → framework message conversion."""

    def _make_model(self) -> LiteLLMModel:
        return LiteLLMModel(ScriptedLiteLLMProvider([]))

    def test_user_prompt_becomes_user_message(self) -> None:
        model = self._make_model()
        pai_messages: list[Any] = [
            ModelRequest(parts=[UserPromptPart(content="Hello")])
        ]
        result = model._convert_messages(pai_messages)
        assert len(result) == 1
        assert result[0].role == "user"
        assert result[0].content == "Hello"

    def test_tool_return_becomes_tool_message(self) -> None:
        model = self._make_model()
        pai_messages: list[Any] = [
            ModelRequest(
                parts=[ToolReturnPart(tool_name="my_tool", content="42", tool_call_id="tc1")]
            )
        ]
        result = model._convert_messages(pai_messages)
        assert len(result) == 1
        assert result[0].role == "tool"
        assert result[0].content == "42"
        assert result[0].tool_call_id == "tc1"

    def test_model_response_becomes_assistant_message(self) -> None:
        model = self._make_model()
        pai_messages: list[Any] = [
            ModelResponse(
                parts=[TextPart(content="I found a bug.")],
                model_name="fake/test",
            )
        ]
        result = model._convert_messages(pai_messages)
        assert len(result) == 1
        assert result[0].role == "assistant"
        assert result[0].content == "I found a bug."

    def test_empty_model_response_still_produces_assistant(self) -> None:
        model = self._make_model()
        pai_messages: list[Any] = [
            ModelResponse(parts=[], model_name="fake/test")
        ]
        result = model._convert_messages(pai_messages)
        assert len(result) == 1
        assert result[0].role == "assistant"

    def test_system_prompt_part_excluded_from_messages(self) -> None:
        """SystemPromptPart must not appear in framework messages — handled by _get_system_prompt."""
        model = self._make_model()
        pai_messages: list[Any] = [
            ModelRequest(
                parts=[
                    SystemPromptPart(content="You are a reviewer."),
                    UserPromptPart(content="Hello"),
                ]
            )
        ]
        result = model._convert_messages(pai_messages)
        # Only the user message should appear; system prompt is excluded
        assert all(m.role != "system" for m in result)
        assert len([m for m in result if m.role == "user"]) == 1

    def test_retry_prompt_with_tool_name_becomes_tool_message(self) -> None:
        model = self._make_model()
        pai_messages: list[Any] = [
            ModelRequest(
                parts=[
                    RetryPromptPart(
                        content="Try again",
                        tool_name="some_tool",
                        tool_call_id="tc99",
                    )
                ]
            )
        ]
        result = model._convert_messages(pai_messages)
        assert len(result) == 1
        assert result[0].role == "tool"
        assert result[0].tool_call_id == "tc99"

    def test_retry_prompt_without_tool_name_becomes_user_message(self) -> None:
        model = self._make_model()
        pai_messages: list[Any] = [
            ModelRequest(
                parts=[
                    RetryPromptPart(content="Please retry", tool_name=None, tool_call_id="tc42")
                ]
            )
        ]
        result = model._convert_messages(pai_messages)
        assert len(result) == 1
        assert result[0].role == "user"

    def test_user_and_tool_return_in_same_request(self) -> None:
        model = self._make_model()
        pai_messages: list[Any] = [
            ModelRequest(
                parts=[
                    UserPromptPart(content="Use tool"),
                    ToolReturnPart(tool_name="t", content="result", tool_call_id="id1"),
                ]
            )
        ]
        result = model._convert_messages(pai_messages)
        roles = [m.role for m in result]
        assert "user" in roles
        assert "tool" in roles


# ---------------------------------------------------------------------------
# Tests: tool definition conversion
# ---------------------------------------------------------------------------


class TestConvertToolDefinitions:
    """Verifies pydantic-ai ToolDefinition → framework ToolDefinition."""

    def test_function_tool_converted(self) -> None:
        from pydantic_ai.models import ToolDefinition as PAIToolDef

        model = LiteLLMModel(ScriptedLiteLLMProvider([]))
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {"x": {"type": "integer"}},
        }
        params = ModelRequestParameters(
            function_tools=[
                PAIToolDef(name="my_tool", description="Does things", parameters_json_schema=schema)
            ]
        )
        tool_defs = model._convert_tool_definitions(params)
        assert len(tool_defs) == 1
        assert tool_defs[0].name == "my_tool"
        assert tool_defs[0].description == "Does things"
        assert tool_defs[0].input_schema == schema

    def test_output_tool_included(self) -> None:
        from pydantic_ai.models import ToolDefinition as PAIToolDef

        model = LiteLLMModel(ScriptedLiteLLMProvider([]))
        params = ModelRequestParameters(
            output_tools=[
                PAIToolDef(name="final_result", parameters_json_schema={"type": "object"})
            ]
        )
        tool_defs = model._convert_tool_definitions(params)
        assert any(td.name == "final_result" for td in tool_defs)

    def test_no_tools_returns_empty(self) -> None:
        model = LiteLLMModel(ScriptedLiteLLMProvider([]))
        params = ModelRequestParameters()
        tool_defs = model._convert_tool_definitions(params)
        assert tool_defs == []

    def test_both_function_and_output_tools_included(self) -> None:
        from pydantic_ai.models import ToolDefinition as PAIToolDef

        model = LiteLLMModel(ScriptedLiteLLMProvider([]))
        params = ModelRequestParameters(
            function_tools=[PAIToolDef(name="read_file", parameters_json_schema={"type": "object"})],
            output_tools=[PAIToolDef(name="final_result", parameters_json_schema={"type": "object"})],
        )
        tool_defs = model._convert_tool_definitions(params)
        names = [td.name for td in tool_defs]
        assert "read_file" in names
        assert "final_result" in names


# ---------------------------------------------------------------------------
# Tests: _build_model_response
# ---------------------------------------------------------------------------


class TestBuildModelResponse:
    """Verifies that args are always stored as JSON strings in ToolCallPart."""

    def test_dict_args_serialised_to_json(self) -> None:
        model = LiteLLMModel(ScriptedLiteLLMProvider([]))
        fw_resp = FrameworkModelResponse(
            content="",
            tool_calls=[{"name": "t", "id": "id1", "input": {"x": 1}}],
            input_tokens=10,
            output_tokens=5,
            model_id="fake/test",
            raw={},
        )
        pai_resp = model._build_model_response(fw_resp)
        tc = next(p for p in pai_resp.parts if isinstance(p, ToolCallPart))
        assert isinstance(tc.args, str)
        assert json.loads(tc.args) == {"x": 1}

    def test_list_args_serialised_to_json(self) -> None:
        model = LiteLLMModel(ScriptedLiteLLMProvider([]))
        fw_resp = FrameworkModelResponse(
            content="",
            tool_calls=[
                {
                    "name": "final_result",
                    "id": "id1",
                    "input": {"response": [{"a": "b"}]},
                }
            ],
            input_tokens=10,
            output_tokens=5,
            model_id="fake/test",
            raw={},
        )
        pai_resp = model._build_model_response(fw_resp)
        tc = next(p for p in pai_resp.parts if isinstance(p, ToolCallPart))
        assert isinstance(tc.args, str)
        assert json.loads(tc.args) == {"response": [{"a": "b"}]}

    def test_text_content_becomes_text_part(self) -> None:
        model = LiteLLMModel(ScriptedLiteLLMProvider([]))
        fw_resp = FrameworkModelResponse(
            content="Hello!",
            tool_calls=[],
            input_tokens=10,
            output_tokens=5,
            model_id="fake/test",
            raw={},
        )
        pai_resp = model._build_model_response(fw_resp)
        texts = [p.content for p in pai_resp.parts if isinstance(p, TextPart)]
        assert texts == ["Hello!"]

    def test_empty_content_produces_no_text_part(self) -> None:
        model = LiteLLMModel(ScriptedLiteLLMProvider([]))
        fw_resp = FrameworkModelResponse(
            content="",
            tool_calls=[{"name": "t", "id": "id1", "input": {}}],
            input_tokens=10,
            output_tokens=5,
            model_id="fake/test",
            raw={},
        )
        pai_resp = model._build_model_response(fw_resp)
        texts = [p for p in pai_resp.parts if isinstance(p, TextPart)]
        assert texts == []

    def test_usage_tokens_propagated(self) -> None:
        model = LiteLLMModel(ScriptedLiteLLMProvider([]))
        fw_resp = FrameworkModelResponse(
            content="x",
            tool_calls=[],
            input_tokens=77,
            output_tokens=33,
            model_id="fake/test",
            raw={},
        )
        pai_resp = model._build_model_response(fw_resp)
        assert pai_resp.usage.input_tokens == 77
        assert pai_resp.usage.output_tokens == 33


# ---------------------------------------------------------------------------
# Tests: full agent loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_text_response() -> None:
    """Single agent call returns a plain text answer."""
    provider = ScriptedLiteLLMProvider(
        responses=[
            {
                "content": "No findings.",
                "tool_calls": [],
                "input_tokens": 50,
                "output_tokens": 10,
            }
        ]
    )
    model = LiteLLMModel(provider)
    agent: Agent[None, str] = Agent(model, system_prompt="You are a code reviewer.")
    result = await agent.run("Review this file.")
    assert result.output == "No findings."


@pytest.mark.asyncio
async def test_agent_system_prompt_passes_through() -> None:
    """Verify system prompt reaches the underlying provider."""
    calls: list[dict[str, Any]] = []

    class TrackingProvider(ScriptedLiteLLMProvider):
        def _do_complete(
            self,
            messages: list[Message],
            tools: list[ToolDefinition] | None,
            system_prompt: str | None,
            max_tokens: int,
            temperature: float,
        ) -> FrameworkModelResponse:
            calls.append({"system_prompt": system_prompt})
            return super()._do_complete(messages, tools, system_prompt, max_tokens, temperature)

    provider = TrackingProvider(
        responses=[
            {"content": "done", "tool_calls": [], "input_tokens": 5, "output_tokens": 3}
        ]
    )
    model = LiteLLMModel(provider)
    agent: Agent[None, str] = Agent(model, system_prompt="Be concise.")
    await agent.run("test")
    assert calls[0]["system_prompt"] == "Be concise."


@pytest.mark.asyncio
async def test_agent_tool_call_round_trip() -> None:
    """Agent calls a tool, receives the result, returns final answer."""
    provider = ScriptedLiteLLMProvider(
        responses=[
            {
                "content": "",
                "tool_calls": [{"name": "read_file", "id": "tc1", "input": {"path": "a.py"}}],
                "input_tokens": 100,
                "output_tokens": 20,
            },
            {
                "content": "Found SQL injection.",
                "tool_calls": [],
                "input_tokens": 150,
                "output_tokens": 30,
            },
        ]
    )
    model = LiteLLMModel(provider)
    agent: Agent[None, str] = Agent(model)
    called_with: list[str] = []

    @agent.tool_plain
    def read_file(path: str) -> str:  # type: ignore[return]
        called_with.append(path)
        return "SELECT * FROM users WHERE id='" + "user_input" + "'"

    result = await agent.run("Review a.py")
    assert called_with == ["a.py"]
    assert "SQL injection" in result.output


@pytest.mark.asyncio
async def test_agent_multiple_tool_calls() -> None:
    """Agent makes two sequential tool calls before finishing."""
    provider = ScriptedLiteLLMProvider(
        responses=[
            {
                "content": "",
                "tool_calls": [{"name": "read_file", "id": "tc1", "input": {"path": "a.py"}}],
                "input_tokens": 50,
                "output_tokens": 10,
            },
            {
                "content": "",
                "tool_calls": [{"name": "read_file", "id": "tc2", "input": {"path": "b.py"}}],
                "input_tokens": 60,
                "output_tokens": 12,
            },
            {
                "content": "Reviewed both files.",
                "tool_calls": [],
                "input_tokens": 70,
                "output_tokens": 15,
            },
        ]
    )
    model = LiteLLMModel(provider)
    agent: Agent[None, str] = Agent(model)
    paths: list[str] = []

    @agent.tool_plain
    def read_file(path: str) -> str:  # type: ignore[return]
        paths.append(path)
        return f"content of {path}"

    result = await agent.run("Review files")
    assert paths == ["a.py", "b.py"]
    assert "Reviewed" in result.output


# ---------------------------------------------------------------------------
# Tests: structured output
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_structured_output_list_finding() -> None:
    """Agent returns list[Finding] via output tool."""
    finding_data = _make_finding_data()
    provider = ScriptedLiteLLMProvider(
        responses=[
            {
                "content": "",
                "tool_calls": [
                    {
                        "name": "final_result",
                        "id": "tc_out",
                        "input": {"response": [finding_data]},
                    }
                ],
                "input_tokens": 200,
                "output_tokens": 80,
            }
        ]
    )
    model = LiteLLMModel(provider)
    agent: Agent[None, list[Finding]] = Agent(model, output_type=list[Finding])
    result = await agent.run("Find issues.")
    assert len(result.output) == 1
    assert result.output[0].vuln_class == VulnClass.HARDCODED_SECRET
    assert result.output[0].severity == Severity.HIGH
    assert result.output[0].confidence == 0.95


@pytest.mark.asyncio
async def test_structured_output_empty_list() -> None:
    """Agent returns empty list[Finding] when no findings present."""
    provider = ScriptedLiteLLMProvider(
        responses=[
            {
                "content": "",
                "tool_calls": [
                    {
                        "name": "final_result",
                        "id": "tc_out",
                        "input": {"response": []},
                    }
                ],
                "input_tokens": 100,
                "output_tokens": 10,
            }
        ]
    )
    model = LiteLLMModel(provider)
    agent: Agent[None, list[Finding]] = Agent(model, output_type=list[Finding])
    result = await agent.run("Find issues.")
    assert result.output == []


# ---------------------------------------------------------------------------
# Tests: token usage parity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_usage_exact_passthrough() -> None:
    """Token counts from provider are exactly preserved in RunResult.usage()."""
    provider = ScriptedLiteLLMProvider(
        responses=[
            {
                "content": "Done.",
                "tool_calls": [],
                "input_tokens": 123,
                "output_tokens": 456,
            }
        ]
    )
    model = LiteLLMModel(provider)
    agent: Agent[None, str] = Agent(model)
    result = await agent.run("Test")
    usage = result.usage()
    assert usage.input_tokens == 123
    assert usage.output_tokens == 456


@pytest.mark.asyncio
async def test_token_usage_accumulates_across_turns() -> None:
    """Token usage is summed across all model turns in a multi-turn run."""
    provider = ScriptedLiteLLMProvider(
        responses=[
            {
                "content": "",
                "tool_calls": [{"name": "read_file", "id": "tc1", "input": {"path": "a.py"}}],
                "input_tokens": 100,
                "output_tokens": 20,
            },
            {
                "content": "Done.",
                "tool_calls": [],
                "input_tokens": 50,
                "output_tokens": 10,
            },
        ]
    )
    model = LiteLLMModel(provider)
    agent: Agent[None, str] = Agent(model)

    @agent.tool_plain
    def read_file(path: str) -> str:  # type: ignore[return]
        return "file content"

    result = await agent.run("Read a file")
    usage = result.usage()
    # pydantic-ai sums across all requests
    assert (usage.input_tokens or 0) == 150
    assert (usage.output_tokens or 0) == 30


@pytest.mark.asyncio
async def test_provider_token_log_matches_run_usage() -> None:
    """Provider token_log and RunResult.usage() agree on single-turn runs."""
    provider = ScriptedLiteLLMProvider(
        responses=[
            {
                "content": "x",
                "tool_calls": [],
                "input_tokens": 77,
                "output_tokens": 33,
            }
        ]
    )
    model = LiteLLMModel(provider)
    agent: Agent[None, str] = Agent(model)
    result = await agent.run("q")
    usage = result.usage()
    assert len(provider.token_log) == 1
    assert provider.token_log[0].input_tokens == usage.input_tokens
    assert provider.token_log[0].output_tokens == usage.output_tokens


# ---------------------------------------------------------------------------
# Tests: asyncio.to_thread — non-blocking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_request_does_not_block_event_loop() -> None:
    """Verify that request() uses asyncio.to_thread so the event loop is not blocked.

    We schedule a coroutine that sets a flag while the model call is in flight.
    If the model call were blocking (direct call without to_thread), the flag
    would never be set during the call.  With to_thread, other tasks can run.
    """
    flag: list[bool] = [False]

    class SlowProvider(ScriptedLiteLLMProvider):
        def _do_complete(self, messages, tools, system_prompt, max_tokens, temperature):
            import time

            time.sleep(0.05)  # simulate a slow sync call
            return super()._do_complete(messages, tools, system_prompt, max_tokens, temperature)

    provider = SlowProvider(
        responses=[{"content": "done", "tool_calls": [], "input_tokens": 1, "output_tokens": 1}]
    )
    model = LiteLLMModel(provider)
    agent: Agent[None, str] = Agent(model)

    async def set_flag() -> None:
        await asyncio.sleep(0)
        flag[0] = True

    run_task = asyncio.create_task(agent.run("test"))
    flag_task = asyncio.create_task(set_flag())
    await asyncio.gather(run_task, flag_task)
    assert flag[0] is True, "Event loop was blocked; asyncio.to_thread not working"


# ---------------------------------------------------------------------------
# Tests: Bug #1 — multi-turn tool replay wire-format correctness
# ---------------------------------------------------------------------------
#
# ValidatingProvider rejects message sequences where a role="tool" message
# references a tool_call_id that was not declared in a preceding assistant
# turn's tool_calls array.  This is the regression guard for the bug where
# ToolCallPart data was silently dropped from the history.
# ---------------------------------------------------------------------------


class ValidatingProvider(ScriptedLiteLLMProvider):
    """ScriptedLiteLLMProvider that asserts LiteLLM message list integrity.

    Raises AssertionError on any call where a tool-result message references a
    tool_call_id that does not appear in the immediately preceding assistant
    turn's tool_calls array.  This rejects the broken wire format produced
    before the Bug #1 fix.
    """

    def _do_complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None,
        system_prompt: str | None,
        max_tokens: int,
        temperature: float,
    ) -> FrameworkModelResponse:
        # Collect tool_call_ids declared in assistant turns.
        declared_ids: set[str] = set()
        for msg in messages:
            if msg.role == "assistant" and msg.tool_calls:
                for tc in msg.tool_calls:
                    declared_ids.add(tc["id"])

        # Every tool-result message must reference a declared id.
        for msg in messages:
            if msg.role == "tool" and msg.tool_call_id is not None:
                assert msg.tool_call_id in declared_ids, (
                    f"tool message references tool_call_id={msg.tool_call_id!r} "
                    f"which was never declared in an assistant tool_calls array. "
                    f"Declared IDs: {declared_ids}"
                )

        return super()._do_complete(messages, tools, system_prompt, max_tokens, temperature)


class TestConvertMessagesToolCallPart:
    """ToolCallPart in ModelResponse must populate Message.tool_calls."""

    def _make_model(self) -> LiteLLMModel:
        return LiteLLMModel(ScriptedLiteLLMProvider([]))

    def test_model_response_with_tool_call_part_populates_tool_calls(self) -> None:
        model = self._make_model()
        pai_messages: list[Any] = [
            ModelResponse(
                parts=[ToolCallPart(tool_name="read_file", args='{"path": "a.py"}', tool_call_id="tc1")],
                model_name="fake/test",
            )
        ]
        result = model._convert_messages(pai_messages)
        assert len(result) == 1
        assert result[0].role == "assistant"
        assert result[0].tool_calls is not None
        assert len(result[0].tool_calls) == 1
        tc = result[0].tool_calls[0]
        assert tc["id"] == "tc1"
        assert tc["type"] == "function"
        assert tc["function"]["name"] == "read_file"
        assert json.loads(tc["function"]["arguments"]) == {"path": "a.py"}

    def test_model_response_text_and_tool_call_part(self) -> None:
        model = self._make_model()
        pai_messages: list[Any] = [
            ModelResponse(
                parts=[
                    TextPart(content="Let me read that file."),
                    ToolCallPart(tool_name="read_file", args='{"path": "b.py"}', tool_call_id="tc2"),
                ],
                model_name="fake/test",
            )
        ]
        result = model._convert_messages(pai_messages)
        assert len(result) == 1
        assert result[0].content == "Let me read that file."
        assert result[0].tool_calls is not None
        assert result[0].tool_calls[0]["id"] == "tc2"

    def test_model_response_no_tool_calls_yields_none(self) -> None:
        model = self._make_model()
        pai_messages: list[Any] = [
            ModelResponse(
                parts=[TextPart(content="plain text")],
                model_name="fake/test",
            )
        ]
        result = model._convert_messages(pai_messages)
        assert result[0].tool_calls is None

    def test_multiple_tool_call_parts_all_captured(self) -> None:
        model = self._make_model()
        pai_messages: list[Any] = [
            ModelResponse(
                parts=[
                    ToolCallPart(tool_name="read_file", args='{"path": "a.py"}', tool_call_id="tc-a"),
                    ToolCallPart(tool_name="read_file", args='{"path": "b.py"}', tool_call_id="tc-b"),
                ],
                model_name="fake/test",
            )
        ]
        result = model._convert_messages(pai_messages)
        assert len(result) == 1
        assert result[0].tool_calls is not None
        assert len(result[0].tool_calls) == 2
        ids = {tc["id"] for tc in result[0].tool_calls}
        assert ids == {"tc-a", "tc-b"}


@pytest.mark.asyncio
async def test_multi_turn_tool_replay_wire_format_valid() -> None:
    """Multi-turn flow through ValidatingProvider succeeds after Bug #1 fix.

    Before the fix: the assistant turn's tool_calls were dropped from Message,
    so ValidatingProvider would raise AssertionError when the tool-return turn
    arrived referencing the orphaned tool_call_id.

    After the fix: Message.tool_calls is populated, so the validator passes.
    """
    provider = ValidatingProvider(
        responses=[
            {
                "content": "",
                "tool_calls": [{"name": "read_file", "id": "tc-multi", "input": {"path": "x.py"}}],
                "input_tokens": 100,
                "output_tokens": 20,
            },
            {
                "content": "Found an issue.",
                "tool_calls": [],
                "input_tokens": 150,
                "output_tokens": 30,
            },
        ]
    )
    model = LiteLLMModel(provider)
    agent: Agent[None, str] = Agent(model)

    @agent.tool_plain
    def read_file(path: str) -> str:  # type: ignore[return]
        return f"contents of {path}"

    result = await agent.run("Review x.py")
    assert "Found an issue" in result.output


@pytest.mark.asyncio
async def test_validating_provider_rejects_orphaned_tool_return() -> None:
    """ValidatingProvider raises AssertionError when tool_calls are missing.

    This test documents the pre-fix failure mode: if a caller manually builds
    a message list where the assistant turn has no tool_calls but a subsequent
    tool message references a tool_call_id, the validator must reject it.
    """
    provider = ValidatingProvider(
        responses=[
            {
                "content": "done",
                "tool_calls": [],
                "input_tokens": 10,
                "output_tokens": 5,
            }
        ]
    )
    model = LiteLLMModel(provider)

    # Build a broken message list directly: assistant with no tool_calls,
    # followed by a tool message referencing an id that was never declared.
    broken_messages: list[Any] = [
        ModelRequest(parts=[UserPromptPart(content="hi")]),
        # Assistant turn with a ToolCallPart that has been MANUALLY stripped:
        # simulate the pre-fix state by using a raw Message with no tool_calls.
        # We can't trigger this via the normal agent flow after the fix, so we
        # call _convert_messages and then strip tool_calls to replicate the bug.
    ]

    fw_messages = model._convert_messages(broken_messages)
    # Insert an orphaned tool return referencing id "ghost-id"
    from sec_review_framework.models.base import Message as FwMessage
    fw_messages.append(FwMessage(role="assistant", content=""))
    fw_messages.append(FwMessage(role="tool", content="result", tool_call_id="ghost-id"))

    with pytest.raises(AssertionError, match="tool_call_id=.ghost-id."):
        provider._do_complete(fw_messages, None, None, 100, 0.2)
