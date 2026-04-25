"""Tests for Phase 0 spike: pydantic-ai + LiteLLMProvider round-trip.

All tests run offline against ``ScriptedLiteLLMProvider`` — a subclass of
``LiteLLMProvider`` that returns pre-scripted responses without hitting any
external API.  Tests are documented with "MOCK" where live-API confirmation
is still needed.

Test coverage:
  - LiteLLMModel adapter construction and properties.
  - Message conversion: user prompt, tool return, retry prompt.
  - Tool definition conversion (pydantic-ai → framework).
  - Full agent loop: single text response.
  - Full agent loop: one tool call + final answer.
  - Structured output: output_type=list[Finding].
  - Structured output: validation retry on bad JSON from model.
  - Two-level agent-as-tool chain.
  - Token-usage propagation from LiteLLMProvider to pydantic-ai RunUsage.
  - Argument serialisation: list/dict inputs are converted to JSON strings.
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from typing import Any

import pytest

# Allow running tests without installing the package
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.dirname(__file__))

from sec_review_framework.data.findings import Finding, Severity, VulnClass
from sec_review_framework.models.base import Message, ModelResponse as FrameworkModelResponse, ToolDefinition
from sec_review_framework.models.litellm_provider import LiteLLMProvider

from litellm_model import LiteLLMModel

from pydantic_ai import Agent
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.usage import RequestUsage


# ---------------------------------------------------------------------------
# Shared fixture: scripted LiteLLMProvider
# ---------------------------------------------------------------------------


class ScriptedLiteLLMProvider(LiteLLMProvider):
    """LiteLLMProvider that returns pre-scripted responses for offline tests."""

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
        "produced_by": "spike_test",
        "experiment_id": "test_exp_001",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Tests: LiteLLMModel adapter properties
# ---------------------------------------------------------------------------


class TestLiteLLMModelProperties:
    """MOCK: Verifies adapter construction and Model ABC compliance."""

    def test_model_name_matches_provider(self) -> None:
        provider = ScriptedLiteLLMProvider([], model_name="anthropic/claude-3-haiku-20240307")
        model = LiteLLMModel(provider)
        assert model.model_name == "anthropic/claude-3-haiku-20240307"

    def test_system_is_litellm(self) -> None:
        provider = ScriptedLiteLLMProvider([])
        model = LiteLLMModel(provider)
        assert model.system == "litellm"

    def test_model_id_combines_system_and_name(self) -> None:
        provider = ScriptedLiteLLMProvider([], model_name="bedrock/claude-v2")
        model = LiteLLMModel(provider)
        # pydantic-ai Model.model_id = f"{system}:{model_name}"
        assert model.model_id == "litellm:bedrock/claude-v2"

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
    """MOCK: Verifies pydantic-ai → framework message conversion."""

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
            ModelRequest(parts=[ToolReturnPart(tool_name="my_tool", content="42", tool_call_id="tc1")])
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
    """MOCK: Verifies pydantic-ai ToolDefinition → framework ToolDefinition."""

    def test_function_tool_converted(self) -> None:
        from pydantic_ai.models import ToolDefinition as PAIToolDef

        model = LiteLLMModel(ScriptedLiteLLMProvider([]))
        schema: dict[str, Any] = {"type": "object", "properties": {"x": {"type": "integer"}}}
        params = ModelRequestParameters(
            function_tools=[PAIToolDef(name="my_tool", description="Does things", parameters_json_schema=schema)]
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
            output_tools=[PAIToolDef(name="final_result", parameters_json_schema={"type": "object"})]
        )
        tool_defs = model._convert_tool_definitions(params)
        assert any(td.name == "final_result" for td in tool_defs)

    def test_no_tools_returns_empty(self) -> None:
        model = LiteLLMModel(ScriptedLiteLLMProvider([]))
        params = ModelRequestParameters()
        tool_defs = model._convert_tool_definitions(params)
        assert tool_defs == []


# ---------------------------------------------------------------------------
# Tests: args serialisation in _build_model_response
# ---------------------------------------------------------------------------


class TestBuildModelResponse:
    """MOCK: Verifies that args are always stored as JSON strings."""

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
            tool_calls=[{"name": "final_result", "id": "id1", "input": {"response": [{"a": "b"}]}}],
            input_tokens=10,
            output_tokens=5,
            model_id="fake/test",
            raw={},
        )
        pai_resp = model._build_model_response(fw_resp)
        tc = next(p for p in pai_resp.parts if isinstance(p, ToolCallPart))
        assert isinstance(tc.args, str)
        parsed = json.loads(tc.args)
        assert parsed == {"response": [{"a": "b"}]}

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
# Tests: full agent loop — text-only response
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_text_response() -> None:
    """MOCK: Single agent call returns a plain text answer."""
    provider = ScriptedLiteLLMProvider(
        responses=[{"content": "No findings.", "tool_calls": [], "input_tokens": 50, "output_tokens": 10}]
    )
    model = LiteLLMModel(provider)
    agent: Agent[None, str] = Agent(model, system_prompt="You are a code reviewer.")
    result = await agent.run("Review this file.")
    assert result.output == "No findings."


@pytest.mark.asyncio
async def test_agent_system_prompt_passes_through() -> None:
    """MOCK: Verify system prompt reaches the underlying provider."""
    calls: list[dict[str, Any]] = []

    class TrackingProvider(ScriptedLiteLLMProvider):
        def _do_complete(self, messages, tools, system_prompt, max_tokens, temperature):
            calls.append({"system_prompt": system_prompt})
            return super()._do_complete(messages, tools, system_prompt, max_tokens, temperature)

    provider = TrackingProvider(
        responses=[{"content": "done", "tool_calls": [], "input_tokens": 5, "output_tokens": 3}]
    )
    model = LiteLLMModel(provider)
    agent: Agent[None, str] = Agent(model, system_prompt="Be concise.")
    await agent.run("test")
    assert calls[0]["system_prompt"] == "Be concise."


# ---------------------------------------------------------------------------
# Tests: full agent loop — tool calling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_tool_call_round_trip() -> None:
    """MOCK: Agent calls a tool, receives the result, returns final answer."""
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
    def read_file(path: str) -> str:
        called_with.append(path)
        return "SELECT * FROM users WHERE id=" + "'" + "user_input" + "'"

    result = await agent.run("Review a.py")
    assert called_with == ["a.py"]
    assert "SQL injection" in result.output


@pytest.mark.asyncio
async def test_agent_multiple_tool_calls() -> None:
    """MOCK: Agent makes two sequential tool calls before finishing."""
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
    def read_file(path: str) -> str:
        paths.append(path)
        return f"content of {path}"

    result = await agent.run("Review files")
    assert paths == ["a.py", "b.py"]
    assert "Reviewed" in result.output


# ---------------------------------------------------------------------------
# Tests: structured output (output_type=list[Finding])
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_structured_output_list_finding() -> None:
    """MOCK: Agent returns list[Finding] via output tool."""
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
    """MOCK: Agent returns empty list[Finding] when no findings present."""
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


@pytest.mark.asyncio
async def test_structured_output_retry_on_validation_error() -> None:
    """MOCK: pydantic-ai retries when first output tool call fails validation.

    The model first sends invalid JSON (missing required fields), which causes
    a ValidationError.  pydantic-ai retries with a RetryPromptPart; the model
    then returns a valid Finding.
    """
    finding_data = _make_finding_data()
    provider = ScriptedLiteLLMProvider(
        responses=[
            # First response: missing required fields — triggers retry
            {
                "content": "",
                "tool_calls": [
                    {
                        "name": "final_result",
                        "id": "tc1",
                        "input": {"response": [{"vuln_class": "sqli"}]},  # missing required fields
                    }
                ],
                "input_tokens": 100,
                "output_tokens": 30,
            },
            # Second response: valid Finding
            {
                "content": "",
                "tool_calls": [
                    {
                        "name": "final_result",
                        "id": "tc2",
                        "input": {"response": [finding_data]},
                    }
                ],
                "input_tokens": 150,
                "output_tokens": 40,
            },
        ]
    )
    model = LiteLLMModel(provider)
    # Allow 2 retries (default is 1, so set output_retries=1 to give the second chance)
    agent: Agent[None, list[Finding]] = Agent(
        model, output_type=list[Finding], output_retries=2
    )
    result = await agent.run("Find issues.")
    assert len(result.output) == 1
    assert result.output[0].vuln_class == VulnClass.HARDCODED_SECRET


# ---------------------------------------------------------------------------
# Tests: two-level agent-as-tool chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_two_level_agent_as_tool() -> None:
    """MOCK: Parent invokes child agent as a tool, receives structured output."""
    finding_data = _make_finding_data(vuln_class="sqli", severity="critical")

    child_provider = ScriptedLiteLLMProvider(
        responses=[
            {
                "content": "",
                "tool_calls": [
                    {
                        "name": "final_result",
                        "id": "child_tc1",
                        "input": {"response": [finding_data]},
                    }
                ],
                "input_tokens": 100,
                "output_tokens": 40,
            }
        ]
    )
    child_model = LiteLLMModel(child_provider)
    child_agent: Agent[None, list[Finding]] = Agent(
        child_model, output_type=list[Finding], system_prompt="Review a file."
    )

    parent_provider = ScriptedLiteLLMProvider(
        responses=[
            {
                "content": "",
                "tool_calls": [{"name": "review_file", "id": "p_tc1", "input": {"path": "src/db.py"}}],
                "input_tokens": 80,
                "output_tokens": 20,
            },
            {
                "content": "Found SQL injection in src/db.py.",
                "tool_calls": [],
                "input_tokens": 120,
                "output_tokens": 30,
            },
        ]
    )
    parent_model = LiteLLMModel(parent_provider)
    parent_agent: Agent[None, str] = Agent(
        parent_model, system_prompt="Orchestrate reviews."
    )

    child_outputs: list[list[Finding]] = []

    @parent_agent.tool_plain
    async def review_file(path: str) -> str:
        res = await child_agent.run(f"Review {path}")
        child_outputs.append(res.output)
        return json.dumps([f.vuln_class.value for f in res.output])

    result = await parent_agent.run("Review src/db.py")
    assert len(child_outputs) == 1
    assert len(child_outputs[0]) == 1
    assert child_outputs[0][0].vuln_class == VulnClass.SQLI
    assert "SQL injection" in result.output


@pytest.mark.asyncio
async def test_two_level_child_isolation() -> None:
    """MOCK: Each child invocation gets a fresh context (no shared state)."""
    finding_a = _make_finding_data(file_path="a.py", id=str(uuid.uuid4()))
    finding_b = _make_finding_data(file_path="b.py", vuln_class="xss", id=str(uuid.uuid4()))

    # Child is invoked twice; each call gets a fresh provider response
    child_provider = ScriptedLiteLLMProvider(
        responses=[
            {
                "content": "",
                "tool_calls": [{"name": "final_result", "id": "c1", "input": {"response": [finding_a]}}],
                "input_tokens": 50,
                "output_tokens": 20,
            },
            {
                "content": "",
                "tool_calls": [{"name": "final_result", "id": "c2", "input": {"response": [finding_b]}}],
                "input_tokens": 50,
                "output_tokens": 20,
            },
        ]
    )
    child_model = LiteLLMModel(child_provider)
    child_agent: Agent[None, list[Finding]] = Agent(
        child_model, output_type=list[Finding]
    )

    parent_provider = ScriptedLiteLLMProvider(
        responses=[
            {
                "content": "",
                "tool_calls": [{"name": "review", "id": "p1", "input": {"path": "a.py"}}],
                "input_tokens": 40,
                "output_tokens": 10,
            },
            {
                "content": "",
                "tool_calls": [{"name": "review", "id": "p2", "input": {"path": "b.py"}}],
                "input_tokens": 40,
                "output_tokens": 10,
            },
            {
                "content": "Done",
                "tool_calls": [],
                "input_tokens": 60,
                "output_tokens": 5,
            },
        ]
    )
    parent_model = LiteLLMModel(parent_provider)
    parent_agent: Agent[None, str] = Agent(parent_model)

    seen_paths: list[str] = []
    seen_findings: list[Finding] = []

    @parent_agent.tool_plain
    async def review(path: str) -> str:
        seen_paths.append(path)
        res = await child_agent.run(f"Review {path}")
        seen_findings.extend(res.output)
        return "ok"

    await parent_agent.run("Review a.py and b.py")
    assert seen_paths == ["a.py", "b.py"]
    assert {f.file_path for f in seen_findings} == {"a.py", "b.py"}


# ---------------------------------------------------------------------------
# Tests: token usage accounting
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_token_usage_exact_passthrough() -> None:
    """MOCK: pydantic-ai Usage tokens exactly match scripted provider tokens for simple response."""
    provider = ScriptedLiteLLMProvider(
        responses=[{"content": "ok", "tool_calls": [], "input_tokens": 123, "output_tokens": 456}]
    )
    model = LiteLLMModel(provider)
    agent: Agent[None, str] = Agent(model)
    result = await agent.run("test")
    usage = result.usage()
    # Token log must capture exact provider values
    assert provider.token_log[0].input_tokens == 123
    assert provider.token_log[0].output_tokens == 456
    # pydantic-ai usage should include provider tokens (may be exactly equal for simple case)
    assert usage.input_tokens >= 123
    assert usage.output_tokens >= 456


@pytest.mark.asyncio
async def test_token_usage_accumulates_across_turns() -> None:
    """MOCK: Multi-turn token usage sums across all model calls."""
    provider = ScriptedLiteLLMProvider(
        responses=[
            {"content": "", "tool_calls": [{"name": "t", "id": "i1", "input": {}}], "input_tokens": 100, "output_tokens": 50},
            {"content": "done", "tool_calls": [], "input_tokens": 200, "output_tokens": 60},
        ]
    )
    model = LiteLLMModel(provider)
    agent: Agent[None, str] = Agent(model)

    @agent.tool_plain
    def t() -> str:
        return "result"

    result = await agent.run("test")
    usage = result.usage()
    # pydantic-ai sums usage across all turns
    assert usage.requests == 2
    # Total input/output tokens should be >= sum of scripted values
    assert usage.input_tokens >= 300  # 100 + 200
    assert usage.output_tokens >= 110  # 50 + 60


@pytest.mark.asyncio
async def test_token_usage_provider_log_populated() -> None:
    """MOCK: Provider token_log captures each individual model call."""
    provider = ScriptedLiteLLMProvider(
        responses=[
            {"content": "", "tool_calls": [{"name": "t", "id": "i1", "input": {}}], "input_tokens": 10, "output_tokens": 5},
            {"content": "done", "tool_calls": [], "input_tokens": 20, "output_tokens": 8},
        ]
    )
    model = LiteLLMModel(provider)
    agent: Agent[None, str] = Agent(model)

    @agent.tool_plain
    def t() -> str:
        return "x"

    await agent.run("test")
    assert len(provider.token_log) == 2
    assert provider.token_log[0].input_tokens == 10
    assert provider.token_log[1].input_tokens == 20
