"""DevDocs ToolExtension builder — registers doc_* tools via the MCP bridge.

At import time this module registers ``build_devdocs_tools`` against
``ToolExtension.DEVDOCS`` in the registry's extension-builder table.

The builder:
  1. Reads ``DEVDOCS_ROOT`` from the environment (default ``/data/devdocs``);
     raises ``RuntimeError`` immediately if the path does not exist (fail-fast).
  2. Constructs an ``MCPServerSpec`` that launches our custom DevDocs MCP server
     (``devdocs_server.py``) with ``--docsets-root`` and optional
     ``--allow-docsets`` arguments.
  3. Starts the ``MCPClient`` (blocks until the subprocess is ready; propagates
     startup errors immediately so misconfigured runs fail fast).
  4. Registers ``client.close`` with ``registry.add_closer`` for clean teardown.
  5. Calls ``register_mcp_tools`` with ``name_prefix="doc_"`` to expose the
     server's tools as ``doc_list_docsets``, ``doc_search``, and ``doc_fetch``.

DocLookupTool stub interaction
------------------------------
The legacy ``DocLookupTool`` stub (registered as ``"lookup_docs"`` in the core
tools list) is suppressed at the ``ToolRegistryFactory.create`` level: when
``ToolExtension.DEVDOCS`` is present in ``tool_extensions``, the factory skips
adding the stub entirely. This is the simplest approach — no ``unregister``
method needed on ``ToolRegistry``. See ``registry.py`` for the conditional.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sec_review_framework.data.experiment import ToolExtension
from sec_review_framework.tools.mcp_bridge import MCPClient, MCPServerSpec, register_mcp_tools
from sec_review_framework.tools.registry import register_extension_builder

if TYPE_CHECKING:
    from sec_review_framework.tools.registry import ToolRegistry

__all__ = ["build_devdocs_tools"]

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_DEVDOCS_ROOT = "/data/devdocs"

# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_devdocs_tools(registry: ToolRegistry, target: Any) -> None:
    """Build and register doc_* tools backed by the DevDocs offline MCP subprocess.

    Parameters
    ----------
    registry:
        The ``ToolRegistry`` for the current run. Tools are added in-place;
        a closer is registered for subprocess cleanup.
    target:
        The experiment target (not used by this extension — docs are
        target-agnostic). Accepted for consistency with the builder protocol.

    Raises
    ------
    RuntimeError
        If ``DEVDOCS_ROOT`` env var points at a path that does not exist.
        Workers must have the PVC mounted before this builder is invoked.
    TimeoutError
        If the DevDocs MCP server subprocess does not become ready within its
        startup timeout (default 20 s).
    RuntimeError
        If the subprocess exits with an error during startup.
    """
    root_str = os.environ.get("DEVDOCS_ROOT", _DEFAULT_DEVDOCS_ROOT)
    root = Path(root_str)

    if not root.exists():
        raise RuntimeError(
            f"DevDocs root not mounted at {root_str!r}. "
            "Ensure the devdocs PVC is mounted at the expected path before "
            "starting a run with ToolExtension.DEVDOCS."
        )

    # The server module is part of this package — launch via `python -m` so it
    # works regardless of how the worker image is invoked.
    server_module = "sec_review_framework.tools.extensions.devdocs_server"

    args: list[str] = ["-m", server_module, "--docsets-root", str(root)]

    # Optional allow-list from environment.
    allow_raw = os.environ.get("DEVDOCS_ALLOW_DOCSETS", "").strip()
    if allow_raw:
        args += ["--allow-docsets", allow_raw]

    spec = MCPServerSpec(
        name="devdocs",
        command=sys.executable,
        args=args,
        startup_timeout_s=20.0,
    )

    client = MCPClient(spec)
    # start() will raise TimeoutError or RuntimeError on failure — fail fast.
    client.start()

    registry.add_closer(client.close)

    register_mcp_tools(
        registry,
        client,
        name_prefix="doc_",
    )


# ---------------------------------------------------------------------------
# Self-registration at import time
# ---------------------------------------------------------------------------

register_extension_builder(ToolExtension.DEVDOCS, build_devdocs_tools)
