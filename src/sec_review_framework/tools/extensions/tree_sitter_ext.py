"""Tree-sitter ToolExtension builder — registers ts_* tools via the MCP bridge.

At import time this module registers ``build_tree_sitter_tools`` against
``ToolExtension.TREE_SITTER`` in the registry's extension-builder table.

The builder:
  1. Constructs an ``MCPServerSpec`` that launches our custom tree-sitter MCP
     server (``tree_sitter_server.py``) as a subprocess with ``repo_path`` as
     its workspace root.
  2. Starts the ``MCPClient`` (blocks until the subprocess is ready; propagates
     startup errors immediately so misconfigured runs fail fast).
  3. Registers ``client.close`` with ``registry.add_closer`` for clean teardown.
  4. Calls ``register_mcp_tools`` with ``name_prefix="ts_"`` to expose the
     server's tools as ``ts_find_symbol``, ``ts_get_ast``, ``ts_list_functions``,
     and ``ts_query``.
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

__all__ = ["build_tree_sitter_tools"]

# ---------------------------------------------------------------------------
# Tools that are safe / useful for security review (read-only, no side effects)
# ---------------------------------------------------------------------------

_ALLOWED_TOOLS = frozenset({"find_symbol", "get_ast", "list_functions", "query"})


def _security_filter(tool_name: str) -> bool:
    """Accept only the tools relevant to security analysis."""
    return tool_name in _ALLOWED_TOOLS


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

def build_tree_sitter_tools(registry: ToolRegistry, target: Any) -> None:
    """Build and register ts_* tools backed by a tree-sitter MCP subprocess.

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
        If the tree-sitter MCP server subprocess does not become ready within
        its startup timeout (default 15 s).
    RuntimeError
        If the subprocess exits with an error during startup (e.g. missing
        ``tree-sitter-language-pack`` dependency).
    """
    repo_path: Path = Path(getattr(target, "repo_path", str(target))).resolve()

    # The server module is part of this package — launch via `python -m` so it
    # works regardless of how the worker image is invoked.
    server_module = "sec_review_framework.tools.extensions.tree_sitter_server"

    spec = MCPServerSpec(
        name="tree-sitter",
        command=sys.executable,
        args=["-m", server_module, str(repo_path)],
        cwd=str(repo_path),
        startup_timeout_s=20.0,
    )

    client = MCPClient(spec)
    # start() will raise TimeoutError or RuntimeError on failure — fail fast.
    client.start()

    registry.add_closer(client.close)

    register_mcp_tools(
        registry,
        client,
        name_prefix="ts_",
        name_filter=_security_filter,
    )


# ---------------------------------------------------------------------------
# Self-registration at import time
# ---------------------------------------------------------------------------

register_extension_builder(ToolExtension.TREE_SITTER, build_tree_sitter_tools)
