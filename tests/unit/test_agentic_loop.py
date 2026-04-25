"""Tests for run_agentic_loop and build_system_prompt (common.py).

Note: run_subagents was removed in Phase 4. run_agentic_loop is kept for
verification/verifier.py which runs a synchronous verification pass outside
the pydantic-ai runner context.
"""

from __future__ import annotations

import pytest

from sec_review_framework.models.base import ModelResponse
from sec_review_framework.strategies.common import (
    build_system_prompt,
    run_agentic_loop,
)
from sec_review_framework.tools.registry import Tool, ToolDefinition, ToolRegistry
from tests.conftest import FakeModelProvider


# ---------------------------------------------------------------------------
# Minimal mock tool helpers
# ---------------------------------------------------------------------------


class EchoTool(Tool):
    """Returns a fixed string for any input — useful for verifying invocation."""

    def __init__(self, name: str = "echo", response: str = "tool_result") -> None:
        self._name = name
        self._response = response
        self.calls: list[dict] = []

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name=self._name,
            description="Echo tool for tests",
            input_schema={"type": "object", "properties": {}, "required": []},
        )

    def invoke(self, input: dict) -> str:
        self.calls.append(input)
        return self._response


def _make_registry(*tools: Tool) -> ToolRegistry:
    registry = ToolRegistry()
    for t in tools:
        registry.tools[t.definition().name] = t
    return registry


def _tool_call_response(
    tool_name: str = "echo",
    tool_id: str = "call-1",
    final_content: str = "done",
    final_content_2: str | None = None,
) -> list[ModelResponse]:
    """Returns a two-response sequence: one tool-call, one terminal."""
    return [
        ModelResponse(
            content="",
            tool_calls=[{"name": tool_name, "id": tool_id, "input": {}}],
            input_tokens=10,
            output_tokens=5,
            model_id="fake",
            raw={},
        ),
        ModelResponse(
            content=final_content if final_content_2 is None else final_content,
            tool_calls=[],
            input_tokens=10,
            output_tokens=5,
            model_id="fake",
            raw={},
        ),
    ]


# ---------------------------------------------------------------------------
# run_agentic_loop tests
# ---------------------------------------------------------------------------


def test_agentic_loop_no_tool_calls_returns_content_immediately():
    """When the first response has no tool calls, content is returned directly."""
    model = FakeModelProvider([
        ModelResponse(
            content="All looks clean.",
            tool_calls=[],
            input_tokens=10,
            output_tokens=5,
            model_id="fake",
            raw={},
        )
    ])
    registry = _make_registry()
    result = run_agentic_loop(model, registry, "sys", "check code")
    assert result == "All looks clean."


def test_agentic_loop_one_tool_call_invoked_and_final_response_returned():
    """One tool call is made, tool is invoked, then the final response is returned."""
    echo = EchoTool(name="echo", response="file content here")
    registry = _make_registry(echo)
    model = FakeModelProvider(_tool_call_response("echo", "call-001", "analysis done"))

    result = run_agentic_loop(model, registry, "sys", "user msg")

    assert result == "analysis done"
    assert len(echo.calls) == 1


def test_agentic_loop_multiple_tool_calls_same_turn_all_invoked():
    """Multiple tool calls in the same turn are all invoked before continuing."""
    echo_a = EchoTool(name="read_file", response="file a")
    echo_b = EchoTool(name="grep", response="grep result")
    registry = _make_registry(echo_a, echo_b)

    model = FakeModelProvider([
        ModelResponse(
            content="",
            tool_calls=[
                {"name": "read_file", "id": "c1", "input": {"path": "a.py"}},
                {"name": "grep", "id": "c2", "input": {"pattern": "SELECT"}},
            ],
            input_tokens=10,
            output_tokens=5,
            model_id="fake",
            raw={},
        ),
        ModelResponse(
            content="two tools called",
            tool_calls=[],
            input_tokens=10,
            output_tokens=5,
            model_id="fake",
            raw={},
        ),
    ])

    result = run_agentic_loop(model, registry, "sys", "scan")
    assert result == "two tools called"
    assert len(echo_a.calls) == 1
    assert len(echo_b.calls) == 1


def test_agentic_loop_max_turns_exceeded_raises_runtime_error():
    """If the model never returns a terminal response, RuntimeError is raised."""
    tool = EchoTool(name="echo")
    registry = _make_registry(tool)

    # Every response includes a tool call — loop never terminates naturally.
    always_tool = ModelResponse(
        content="",
        tool_calls=[{"name": "echo", "id": "x", "input": {}}],
        input_tokens=5,
        output_tokens=5,
        model_id="fake",
        raw={},
    )
    # Provide more than max_turns responses (they will keep popping from the queue)
    model = FakeModelProvider([always_tool] * 5)

    with pytest.raises(RuntimeError, match="max_turns"):
        run_agentic_loop(model, registry, "sys", "msg", max_turns=3)


# ---------------------------------------------------------------------------
# build_system_prompt tests
# ---------------------------------------------------------------------------


def test_build_system_prompt_with_modifier_appends_to_base():
    """A profile with a non-empty modifier is appended after the base prompt."""
    class FakeProfile:
        system_prompt_modifier = "STRICT MODE: be precise."

    config = {"review_profile": FakeProfile()}
    result = build_system_prompt("You are a reviewer.", config)
    assert result == "You are a reviewer.\n\nSTRICT MODE: be precise."


def test_build_system_prompt_without_modifier_returns_base_unchanged():
    """A profile with an empty modifier returns the base prompt unmodified."""
    class FakeProfile:
        system_prompt_modifier = ""

    config = {"review_profile": FakeProfile()}
    result = build_system_prompt("base prompt", config)
    assert result == "base prompt"


def test_build_system_prompt_no_profile_key_returns_base_unchanged():
    """Config with no review_profile key returns the base prompt unmodified."""
    result = build_system_prompt("base only", config={})
    assert result == "base only"


