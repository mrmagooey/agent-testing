"""Unit tests for :mod:`sec_review_framework.agent.tool_adapter`.

Tests cover:
- Schema derivation from ToolDefinition.input_schema
- Audit-log preservation via registry.invoke
- Correct kwargs routing from pydantic-ai to registry
- Parallel-cloning: each clone gets an independent audit log
- Empty registry returns empty list

Skipped cleanly when the ``agent`` extra (pydantic-ai) is not installed.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

# Skip the entire module if pydantic_ai is not installed.
pydantic_ai = pytest.importorskip("pydantic_ai")

from pydantic_ai import Agent  # noqa: E402
from pydantic_ai.tools import Tool as PAITool  # noqa: E402

from sec_review_framework.agent.tool_adapter import _translate_schema, make_tool_callables  # noqa: E402
from sec_review_framework.models.base import Message, ToolDefinition  # noqa: E402
from sec_review_framework.models.base import ModelResponse as FrameworkModelResponse  # noqa: E402
from sec_review_framework.models.litellm_provider import LiteLLMProvider  # noqa: E402
from sec_review_framework.tools.registry import Tool, ToolRegistry  # noqa: E402

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class EchoTool(Tool):
    """A simple tool that echoes its inputs as a JSON-formatted string."""

    def __init__(
        self,
        name: str = "echo",
        description: str = "Echo inputs",
        schema: dict[str, Any] | None = None,
    ) -> None:
        self._name = name
        self._description = description
        self._schema = schema or {
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Message to echo"},
            },
            "required": ["message"],
        }

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self._name,
            description=self._description,
            input_schema=self._schema,
        )

    def invoke(self, input: dict[str, Any]) -> str:
        import json

        return json.dumps({"echoed": input})


class CountingTool(Tool):
    """Tool that counts how many times it has been invoked."""

    def __init__(self) -> None:
        self.call_count = 0
        self.last_input: dict[str, Any] | None = None

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="counter",
            description="Counts calls",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
            },
        )

    def invoke(self, input: dict[str, Any]) -> str:
        self.call_count += 1
        self.last_input = input
        return f"count={self.call_count}"


def _make_registry(*tools: Tool) -> ToolRegistry:
    """Build a ToolRegistry populated with the given tools."""
    registry = ToolRegistry()
    for tool in tools:
        defn = tool.definition()
        registry.tools[defn.name] = tool
    return registry


class ScriptedLiteLLMProvider(LiteLLMProvider):
    """Pre-scripted provider for offline tests."""

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


# ---------------------------------------------------------------------------
# Tests: _translate_schema
# ---------------------------------------------------------------------------


class TestTranslateSchema:
    """Verify schema translation logic."""

    def test_pass_through_for_standard_schema(self) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
        }
        result = _translate_schema(schema)
        assert result == schema

    def test_type_object_added_when_missing(self) -> None:
        schema: dict[str, Any] = {
            "properties": {"x": {"type": "integer"}},
        }
        result = _translate_schema(schema)
        assert result["type"] == "object"

    def test_original_schema_not_mutated(self) -> None:
        schema: dict[str, Any] = {"properties": {"x": {"type": "integer"}}}
        _translate_schema(schema)
        assert "type" not in schema  # original unchanged

    def test_empty_schema_gets_type_object(self) -> None:
        result = _translate_schema({})
        assert result["type"] == "object"


# ---------------------------------------------------------------------------
# Tests: make_tool_callables — schema derivation
# ---------------------------------------------------------------------------


class TestMakeToolCallablesSchema:
    """Verify schema derivation from ToolDefinition.input_schema."""

    def test_empty_registry_returns_empty_list(self) -> None:
        registry = ToolRegistry()
        tools = make_tool_callables(registry)
        assert tools == []

    def test_returns_one_tool_per_registry_entry(self) -> None:
        registry = _make_registry(EchoTool("echo1"), EchoTool("echo2"))
        tools = make_tool_callables(registry)
        assert len(tools) == 2

    def test_tool_names_match_registry(self) -> None:
        registry = _make_registry(EchoTool("alpha"), EchoTool("beta"))
        tools = make_tool_callables(registry)
        names = {t.name for t in tools}
        assert names == {"alpha", "beta"}

    def test_tool_description_preserved(self) -> None:
        tool = EchoTool("my_tool", description="Custom description")
        registry = _make_registry(tool)
        pai_tools = make_tool_callables(registry)
        assert len(pai_tools) == 1
        assert pai_tools[0].description == "Custom description"

    def test_schema_properties_preserved(self) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
                "limit": {"type": "integer", "description": "Max lines"},
            },
            "required": ["path"],
        }
        tool = EchoTool("read_file", schema=schema)
        registry = _make_registry(tool)
        pai_tools = make_tool_callables(registry)
        td = pai_tools[0].tool_def
        assert td.parameters_json_schema["properties"]["path"]["type"] == "string"
        assert td.parameters_json_schema["properties"]["limit"]["type"] == "integer"

    def test_returns_pai_tool_instances(self) -> None:
        registry = _make_registry(EchoTool())
        tools = make_tool_callables(registry)
        assert all(isinstance(t, PAITool) for t in tools)


# ---------------------------------------------------------------------------
# Tests: make_tool_callables — audit-log preservation
# ---------------------------------------------------------------------------


class TestAuditLogPreservation:
    """Verify that every tool invocation is recorded in the audit log."""

    @pytest.mark.asyncio
    async def test_single_invocation_logged(self) -> None:
        counting_tool = CountingTool()
        registry = _make_registry(counting_tool)

        provider = ScriptedLiteLLMProvider(
            responses=[
                {
                    "content": "",
                    "tool_calls": [{"name": "counter", "id": "tc1", "input": {"value": 42}}],
                    "input_tokens": 10,
                    "output_tokens": 5,
                },
                {
                    "content": "done",
                    "tool_calls": [],
                    "input_tokens": 5,
                    "output_tokens": 3,
                },
            ]
        )
        from sec_review_framework.agent.litellm_model import LiteLLMModel

        model = LiteLLMModel(provider)
        agent: Agent[None, str] = Agent(model, tools=make_tool_callables(registry))
        await agent.run("count something")

        assert len(registry.audit_log.entries) == 1
        entry = registry.audit_log.entries[0]
        assert entry.tool_name == "counter"
        assert entry.input == {"value": 42}

    @pytest.mark.asyncio
    async def test_two_invocations_both_logged(self) -> None:
        echo = EchoTool("echo")
        registry = _make_registry(echo)

        provider = ScriptedLiteLLMProvider(
            responses=[
                {
                    "content": "",
                    "tool_calls": [
                        {"name": "echo", "id": "tc1", "input": {"message": "hello"}}
                    ],
                    "input_tokens": 10,
                    "output_tokens": 5,
                },
                {
                    "content": "",
                    "tool_calls": [
                        {"name": "echo", "id": "tc2", "input": {"message": "world"}}
                    ],
                    "input_tokens": 10,
                    "output_tokens": 5,
                },
                {
                    "content": "done",
                    "tool_calls": [],
                    "input_tokens": 5,
                    "output_tokens": 3,
                },
            ]
        )
        from sec_review_framework.agent.litellm_model import LiteLLMModel

        model = LiteLLMModel(provider)
        agent: Agent[None, str] = Agent(model, tools=make_tool_callables(registry))
        await agent.run("echo twice")

        assert len(registry.audit_log.entries) == 2
        messages = [e.input["message"] for e in registry.audit_log.entries]
        assert "hello" in messages
        assert "world" in messages

    @pytest.mark.asyncio
    async def test_audit_log_records_duration(self) -> None:
        registry = _make_registry(EchoTool())
        provider = ScriptedLiteLLMProvider(
            responses=[
                {
                    "content": "",
                    "tool_calls": [{"name": "echo", "id": "tc1", "input": {"message": "hi"}}],
                    "input_tokens": 5,
                    "output_tokens": 3,
                },
                {"content": "done", "tool_calls": [], "input_tokens": 3, "output_tokens": 2},
            ]
        )
        from sec_review_framework.agent.litellm_model import LiteLLMModel

        model = LiteLLMModel(provider)
        agent: Agent[None, str] = Agent(model, tools=make_tool_callables(registry))
        await agent.run("test")

        entry = registry.audit_log.entries[0]
        assert entry.duration_ms >= 0


# ---------------------------------------------------------------------------
# Tests: parallel-cloning
# ---------------------------------------------------------------------------


class TestParallelCloning:
    """Verify that cloned registries have independent audit logs."""

    def test_clone_has_independent_audit_log(self) -> None:
        original = _make_registry(EchoTool())
        clone = original.clone()

        # Directly invoke via registry to populate logs
        original.invoke("echo", {"message": "original"}, "call-orig")
        clone.invoke("echo", {"message": "clone"}, "call-clone")

        orig_entries = original.audit_log.entries
        clone_entries = clone.audit_log.entries

        assert len(orig_entries) == 1
        assert len(clone_entries) == 1
        assert orig_entries[0].input["message"] == "original"
        assert clone_entries[0].input["message"] == "clone"

    def test_clone_shares_tool_instances(self) -> None:
        counting = CountingTool()
        original = _make_registry(counting)
        clone = original.clone()

        # Both registries share the same CountingTool instance
        assert original.tools["counter"] is clone.tools["counter"]

    @pytest.mark.asyncio
    async def test_parallel_agents_audit_logs_do_not_interleave(self) -> None:
        """Two agents running in parallel with cloned registries maintain independent logs."""
        echo = EchoTool()
        registry_a = _make_registry(echo)
        registry_b = registry_a.clone()

        def _make_provider(message: str) -> ScriptedLiteLLMProvider:
            return ScriptedLiteLLMProvider(
                responses=[
                    {
                        "content": "",
                        "tool_calls": [
                            {"name": "echo", "id": f"tc-{message}", "input": {"message": message}}
                        ],
                        "input_tokens": 5,
                        "output_tokens": 3,
                    },
                    {"content": "done", "tool_calls": [], "input_tokens": 3, "output_tokens": 2},
                ]
            )

        from sec_review_framework.agent.litellm_model import LiteLLMModel

        model_a = LiteLLMModel(_make_provider("from_a"))
        model_b = LiteLLMModel(_make_provider("from_b"))

        agent_a: Agent[None, str] = Agent(model_a, tools=make_tool_callables(registry_a))
        agent_b: Agent[None, str] = Agent(model_b, tools=make_tool_callables(registry_b))

        await asyncio.gather(agent_a.run("run a"), agent_b.run("run b"))

        entries_a = registry_a.audit_log.entries
        entries_b = registry_b.audit_log.entries

        assert len(entries_a) == 1
        assert len(entries_b) == 1
        assert entries_a[0].input["message"] == "from_a"
        assert entries_b[0].input["message"] == "from_b"
