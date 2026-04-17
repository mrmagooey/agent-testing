"""MCP stdio transport bridge: sync adapter layer over the async MCP Python SDK."""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.types import TextContent

from sec_review_framework.models.base import ToolDefinition
from sec_review_framework.tools.registry import Tool

if TYPE_CHECKING:
    from sec_review_framework.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


@dataclass
class MCPServerSpec:
    """Descriptor for a stdio MCP server subprocess."""

    command: str
    name: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    cwd: str | None = None
    startup_timeout_s: float = 15.0


class MCPClient:
    """
    Manages a subprocess speaking MCP over stdio.

    The MCP SDK is async-first (anyio). We bridge into our sync Tool ABC by
    running a dedicated event loop in a background thread and marshalling every
    coroutine call onto it via run_coroutine_threadsafe.  The background thread
    owns the process lifetime; when close() is called we schedule a graceful
    shutdown on the same loop so the anyio context managers clean up properly.
    """

    def __init__(self, spec: MCPServerSpec) -> None:
        self._spec = spec
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: ClientSession | None = None
        self._started = False
        self._closed = False
        # Signals readiness / fatal startup error from the background thread.
        self._ready_event = threading.Event()
        self._startup_error: BaseException | None = None
        # Shutdown is coordinated through this asyncio.Event set on the loop.
        self._stop_event: asyncio.Event | None = None

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        self._started = True

        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            name=f"mcp-{self._spec.name}",
            daemon=True,
        )
        self._thread.start()

        if not self._ready_event.wait(timeout=self._spec.startup_timeout_s):
            self._closed = True
            raise TimeoutError(
                f"MCP server '{self._spec.name}' did not become ready within "
                f"{self._spec.startup_timeout_s}s"
            )

        if self._startup_error is not None:
            self._closed = True
            raise RuntimeError(
                f"MCP server '{self._spec.name}' failed to start"
            ) from self._startup_error

        logger.info("[%s] MCP server started", self._spec.name)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._loop is not None and self._stop_event is not None:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread is not None:
            self._thread.join(timeout=10)
        logger.info("[%s] MCP server closed", self._spec.name)

    # ------------------------------------------------------------------
    # Tool operations (synchronous, safe to call from any thread)
    # ------------------------------------------------------------------

    def list_tools(self) -> list[dict[str, Any]]:
        self._assert_open()

        async def _list() -> Any:
            return await self._session.list_tools()

        result = self._run_coro(_list())
        out = []
        for tool in result.tools:
            out.append(
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "inputSchema": tool.inputSchema.model_dump() if hasattr(tool.inputSchema, "model_dump") else dict(tool.inputSchema),
                }
            )
        return out

    def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        self._assert_open()

        async def _call() -> Any:
            return await self._session.call_tool(name, arguments)

        result = self._run_coro(_call())
        parts: list[str] = []
        for item in result.content:
            if isinstance(item, TextContent):
                parts.append(item.text)
            else:
                parts.append(json.dumps(item.model_dump()))
        return "".join(parts)

    # ------------------------------------------------------------------
    # Context-manager protocol
    # ------------------------------------------------------------------

    def __enter__(self) -> "MCPClient":
        self.start()
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _assert_open(self) -> None:
        if self._closed or self._session is None:
            raise RuntimeError(f"MCP client '{self._spec.name}' is closed")

    def _run_coro(self, coro: Any) -> Any:
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result()

    def _run_loop(self) -> None:
        """Background thread: owns the event loop and the subprocess lifetime."""
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._connect_and_serve())
        except Exception as exc:  # noqa: BLE001
            if not self._ready_event.is_set():
                self._startup_error = exc
                self._ready_event.set()
        finally:
            self._loop.close()

    async def _connect_and_serve(self) -> None:
        self._stop_event = asyncio.Event()
        params = StdioServerParameters(
            command=self._spec.command,
            args=self._spec.args,
            env=self._spec.env,
        )

        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                self._session = session
                try:
                    await session.initialize()
                except Exception as exc:
                    self._startup_error = exc
                    self._ready_event.set()
                    return

                self._ready_event.set()
                # Hold the session open until close() is called.
                await self._stop_event.wait()


class MCPToolAdapter(Tool):
    """Wraps a single MCP-advertised tool as a sync Tool ABC."""

    def __init__(
        self,
        client: MCPClient,
        mcp_tool_name: str,
        tool_definition: ToolDefinition,
        name_prefix: str = "",
    ) -> None:
        self._client = client
        self._mcp_tool_name = mcp_tool_name
        self._display_name = f"{name_prefix}{mcp_tool_name}" if name_prefix else mcp_tool_name
        self._definition = ToolDefinition(
            name=self._display_name,
            description=tool_definition.description,
            input_schema=tool_definition.input_schema,
        )

    def definition(self) -> ToolDefinition:
        return self._definition

    def invoke(self, input: dict[str, Any]) -> str:
        return self._client.call_tool(self._mcp_tool_name, input)


def register_mcp_tools(
    registry: "ToolRegistry",
    client: MCPClient,
    name_prefix: str = "",
    name_filter: Callable[[str], bool] | None = None,
) -> list[MCPToolAdapter]:
    """
    Enumerate the MCP server's tools and register each as an MCPToolAdapter.

    Returns the list of adapters for bookkeeping (e.g. lifecycle management).
    """
    adapters: list[MCPToolAdapter] = []
    for raw in client.list_tools():
        tool_name: str = raw["name"]
        if name_filter is not None and not name_filter(tool_name):
            continue

        defn = ToolDefinition(
            name=tool_name,
            description=raw.get("description", ""),
            input_schema=raw.get("inputSchema", {}),
        )
        adapter = MCPToolAdapter(
            client=client,
            mcp_tool_name=tool_name,
            tool_definition=defn,
            name_prefix=name_prefix,
        )
        display_name = adapter.definition().name
        registry.tools[display_name] = adapter
        adapters.append(adapter)
        logger.debug("[%s] registered MCP tool '%s'", client._spec.name, display_name)

    return adapters
