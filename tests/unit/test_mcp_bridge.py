"""Tests for MCPClient, MCPToolAdapter, register_mcp_tools, and ToolRegistry lifecycle."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from sec_review_framework.models.base import ToolDefinition
from sec_review_framework.tools.mcp_bridge import (
    MCPClient,
    MCPServerSpec,
    MCPToolAdapter,
    register_mcp_tools,
)
from sec_review_framework.tools.registry import ToolRegistry

FAKE_SERVER = Path(__file__).parent.parent / "fixtures" / "fake_mcp_server.py"


# ---------------------------------------------------------------------------
# MCPClient — subprocess integration test
# ---------------------------------------------------------------------------


@pytest.mark.slow
class TestMCPClientSubprocess:
    """End-to-end test using the fake MCP server subprocess."""

    def _make_spec(self) -> MCPServerSpec:
        return MCPServerSpec(
            command=sys.executable,
            args=[str(FAKE_SERVER)],
            name="fake-test-server",
            startup_timeout_s=20.0,
        )

    def test_list_tools_returns_echo(self) -> None:
        spec = self._make_spec()
        with MCPClient(spec) as client:
            tools = client.list_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "echo"

    def test_call_tool_echo_round_trip(self) -> None:
        spec = self._make_spec()
        with MCPClient(spec) as client:
            result = client.call_tool("echo", {"text": "hello"})
        assert "hello" in result

    def test_context_manager_close_is_idempotent(self) -> None:
        spec = self._make_spec()
        client = MCPClient(spec)
        client.start()
        client.close()
        client.close()  # second call must not raise

    def test_call_after_close_raises(self) -> None:
        spec = self._make_spec()
        client = MCPClient(spec)
        client.start()
        client.close()
        with pytest.raises(RuntimeError, match="closed"):
            client.call_tool("echo", {"text": "x"})


# ---------------------------------------------------------------------------
# MCPToolAdapter
# ---------------------------------------------------------------------------


class TestMCPToolAdapter:
    def _make_adapter(
        self,
        name_prefix: str = "",
        tool_name: str = "mytool",
    ) -> MCPToolAdapter:
        client = MagicMock(spec=MCPClient)
        client.call_tool.return_value = "result-text"
        defn = ToolDefinition(
            name=tool_name,
            description="A test tool",
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
        )
        return MCPToolAdapter(client, tool_name, defn, name_prefix=name_prefix)

    def test_definition_uses_prefixed_name(self) -> None:
        adapter = self._make_adapter(name_prefix="ts_", tool_name="parse")
        defn = adapter.definition()
        assert defn.name == "ts_parse"
        assert defn.description == "A test tool"

    def test_definition_no_prefix(self) -> None:
        adapter = self._make_adapter(name_prefix="", tool_name="grep")
        assert adapter.definition().name == "grep"

    def test_invoke_delegates_to_client(self) -> None:
        adapter = self._make_adapter(tool_name="echo")
        result = adapter.invoke({"x": "hello"})
        assert result == "result-text"
        adapter._client.call_tool.assert_called_once_with("echo", {"x": "hello"})

    def test_definition_preserves_input_schema(self) -> None:
        adapter = self._make_adapter(tool_name="t")
        assert "properties" in adapter.definition().input_schema


# ---------------------------------------------------------------------------
# register_mcp_tools
# ---------------------------------------------------------------------------


class TestRegisterMCPTools:
    def _make_client(self, tools: list[dict]) -> MCPClient:
        client = MagicMock(spec=MCPClient)
        client.list_tools.return_value = tools
        client._spec = MagicMock()
        client._spec.name = "mock-server"
        return client

    def test_registers_all_tools_by_default(self) -> None:
        registry = ToolRegistry()
        client = self._make_client(
            [
                {"name": "alpha", "description": "tool a", "inputSchema": {}},
                {"name": "beta", "description": "tool b", "inputSchema": {}},
            ]
        )
        adapters = register_mcp_tools(registry, client)
        assert len(adapters) == 2
        assert "alpha" in registry.tools
        assert "beta" in registry.tools

    def test_name_prefix_applied(self) -> None:
        registry = ToolRegistry()
        client = self._make_client(
            [{"name": "parse", "description": "", "inputSchema": {}}]
        )
        register_mcp_tools(registry, client, name_prefix="ts_")
        assert "ts_parse" in registry.tools
        assert registry.tools["ts_parse"].definition().name == "ts_parse"

    def test_name_filter_excludes_tools(self) -> None:
        registry = ToolRegistry()
        client = self._make_client(
            [
                {"name": "allowed", "description": "", "inputSchema": {}},
                {"name": "blocked", "description": "", "inputSchema": {}},
            ]
        )
        register_mcp_tools(registry, client, name_filter=lambda n: n == "allowed")
        assert "allowed" in registry.tools
        assert "blocked" not in registry.tools

    def test_returns_adapters_list(self) -> None:
        registry = ToolRegistry()
        client = self._make_client(
            [{"name": "x", "description": "", "inputSchema": {}}]
        )
        adapters = register_mcp_tools(registry, client)
        assert len(adapters) == 1
        assert isinstance(adapters[0], MCPToolAdapter)


# ---------------------------------------------------------------------------
# ToolRegistry.close() lifecycle
# ---------------------------------------------------------------------------


class TestToolRegistryClose:
    def test_closers_called_in_lifo_order(self) -> None:
        registry = ToolRegistry()
        order: list[int] = []
        registry.add_closer(lambda: order.append(1))
        registry.add_closer(lambda: order.append(2))
        registry.add_closer(lambda: order.append(3))
        registry.close()
        assert order == [3, 2, 1]

    def test_close_is_idempotent(self) -> None:
        registry = ToolRegistry()
        called = []
        registry.add_closer(lambda: called.append(1))
        registry.close()
        registry.close()
        assert called == [1]

    def test_closer_exception_does_not_abort_remaining(self) -> None:
        registry = ToolRegistry()
        completed = []

        def bad_closer() -> None:
            raise RuntimeError("boom")

        registry.add_closer(lambda: completed.append("last"))
        registry.add_closer(bad_closer)
        registry.add_closer(lambda: completed.append("first"))

        registry.close()  # must not raise
        # Both non-failing closers ran despite the exception in the middle one.
        assert "first" in completed
        assert "last" in completed

    def test_no_closers_close_is_safe(self) -> None:
        registry = ToolRegistry()
        registry.close()  # must not raise
