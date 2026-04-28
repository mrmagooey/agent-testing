"""Semgrep ToolExtension builder — registers run_semgrep as an opt-in tool.

At import time this module registers ``build_semgrep_tools`` against
``ToolExtension.SEMGREP`` in the registry's extension-builder table.

The builder instantiates ``SemgrepTool`` (moved here from the former
``tools/semgrep.py`` location) and adds it directly to the registry.
Unlike the MCP-backed extensions (TREE_SITTER, LSP, DEVDOCS) semgrep
runs in-process via a subprocess call to the binary that is baked into
the worker image via pipx — no MCP server subprocess is needed.

The binary is installed at image-build time; the extension merely controls
whether the tool is present in the agent's ToolRegistry for a given run.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from sec_review_framework.data.experiment import ToolExtension
from sec_review_framework.tools.registry import register_extension_builder
from sec_review_framework.tools.semgrep import SemgrepTool

if TYPE_CHECKING:
    from sec_review_framework.tools.registry import ToolRegistry

__all__ = ["build_semgrep_tools"]


def build_semgrep_tools(registry: ToolRegistry, target: Any) -> None:
    """Build and register run_semgrep backed by the semgrep binary.

    Parameters
    ----------
    registry:
        The ``ToolRegistry`` for the current run. The tool is added in-place.
    target:
        The experiment target. Must expose a ``repo_path`` attribute (Path or
        str) pointing at the repository root to analyse.
    """
    repo_path: Path = Path(getattr(target, "repo_path", str(target))).resolve()
    tool = SemgrepTool(repo_path=repo_path)
    registry.tools[tool.definition().name] = tool


# ---------------------------------------------------------------------------
# Self-registration at import time
# ---------------------------------------------------------------------------

register_extension_builder(ToolExtension.SEMGREP, build_semgrep_tools)
