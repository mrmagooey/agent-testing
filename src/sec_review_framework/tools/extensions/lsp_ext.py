"""LSP ToolExtension builder — registers lsp_* tools via the MCP bridge.

At import time this module registers ``build_lsp_tools`` against
``ToolExtension.LSP`` in the registry's extension-builder table.

The builder:
  1. Constructs an ``MCPServerSpec`` that launches our custom LSP multiplexer MCP
     server (``lsp_server.py``) as a subprocess with ``--workspace <repo_path>``.
  2. Starts the ``MCPClient`` (blocks until the subprocess is ready; propagates
     startup errors immediately so misconfigured runs fail fast).
  3. Registers ``client.close`` with ``registry.add_closer`` for clean teardown.
  4. Calls ``register_mcp_tools`` with ``name_prefix="lsp_"`` to expose the
     server's tools as ``lsp_definition``, ``lsp_references``, ``lsp_hover``,
     ``lsp_document_symbols``, and ``lsp_workspace_symbols``.

Note on fail-fast behaviour: the multiplexer process itself must start
successfully (MCP handshake must complete). Individual language servers are
started lazily on demand and may fail per-language without bringing down the
whole extension.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sec_review_framework.data.experiment import ToolExtension
from sec_review_framework.tools.mcp_bridge import MCPClient, MCPServerSpec, register_mcp_tools
from sec_review_framework.tools.registry import register_extension_builder

if TYPE_CHECKING:
    from sec_review_framework.tools.registry import ToolRegistry

__all__ = ["build_lsp_tools"]

# ---------------------------------------------------------------------------
# Tools advertised by the LSP multiplexer MCP server
# ---------------------------------------------------------------------------

_ALLOWED_TOOLS = frozenset({
    "definition",
    "references",
    "hover",
    "document_symbols",
    "workspace_symbols",
})


def _lsp_tool_filter(tool_name: str) -> bool:
    """Accept only the LSP tools relevant to security analysis."""
    return tool_name in _ALLOWED_TOOLS


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_lsp_tools(registry: ToolRegistry, target: Any) -> None:
    """Build and register lsp_* tools backed by the LSP multiplexer MCP subprocess.

    Parameters
    ----------
    registry:
        The ``ToolRegistry`` for the current run. Tools are added in-place;
        a closer is registered for subprocess cleanup.
    target:
        The experiment target. Must expose a ``repo_path`` attribute (Path or
        str) pointing at the repository root to analyse.

    Raises
    ------
    TimeoutError
        If the LSP multiplexer MCP server subprocess does not become ready within
        its startup timeout (default 30 s). The multiplexer itself must start
        unconditionally; per-language server failures are handled lazily.
    RuntimeError
        If the subprocess exits with an error during startup.
    """
    repo_path: Path = Path(getattr(target, "repo_path", str(target))).resolve()

    # The server module is part of this package — launch via `python -m` so it
    # works regardless of how the worker image is invoked.
    server_module = "sec_review_framework.tools.extensions.lsp_server"

    spec = MCPServerSpec(
        name="lsp",
        command=sys.executable,
        args=["-m", server_module, "--workspace", str(repo_path)],
        cwd=str(repo_path),
        # The multiplexer itself starts quickly (no language servers spawned yet);
        # 30 s is generous.
        startup_timeout_s=30.0,
    )

    client = MCPClient(spec)
    # start() will raise TimeoutError or RuntimeError on failure — fail fast
    # so the run doesn't proceed without the declared extension.
    client.start()

    registry.add_closer(client.close)

    register_mcp_tools(
        registry,
        client,
        name_prefix="lsp_",
        name_filter=_lsp_tool_filter,
    )


# ---------------------------------------------------------------------------
# Self-registration at import time
# ---------------------------------------------------------------------------

register_extension_builder(ToolExtension.LSP, build_lsp_tools)
