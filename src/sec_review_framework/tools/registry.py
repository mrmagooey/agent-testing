"""Tool registry, audit logging, and base abstractions for the security review framework."""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from sec_review_framework.data.experiment import ToolExtension, ToolVariant
from sec_review_framework.models.base import ToolDefinition

logger = logging.getLogger(__name__)

class ToolCallRecord(BaseModel):
    call_id: str
    tool_name: str
    input: dict[str, Any]       # full input, not truncated
    timestamp: datetime
    duration_ms: int
    output_truncated: bool


class ToolCallAuditLog:
    """Append-only log of every tool invocation during a run."""

    def __init__(self) -> None:
        self._entries: list[ToolCallRecord] = []

    def record(
        self,
        name: str,
        input: dict[str, Any],
        call_id: str,
        duration_ms: int = 0,
        output_truncated: bool = False,
    ) -> ToolCallRecord:
        """Create a timestamped record and append it to the log."""
        entry = ToolCallRecord(
            call_id=call_id,
            tool_name=name,
            input=input,
            timestamp=datetime.utcnow(),
            duration_ms=duration_ms,
            output_truncated=output_truncated,
        )
        self._entries.append(entry)
        return entry

    @property
    def entries(self) -> list[ToolCallRecord]:
        return list(self._entries)


class Tool(ABC):
    """Abstract base class for all tools exposed to agents."""

    @abstractmethod
    def definition(self) -> ToolDefinition:
        """Return the tool's JSON-Schema–backed definition for provider APIs."""
        ...

    @abstractmethod
    def invoke(self, input: dict[str, Any]) -> str:
        """Execute the tool and return a string result."""
        ...


@dataclass
class ToolRegistry:
    """Holds a set of Tool instances and an audit log for the current run."""

    tools: dict[str, Tool] = field(default_factory=dict)
    audit_log: ToolCallAuditLog = field(default_factory=ToolCallAuditLog)
    _closers: list[Callable[[], None]] = field(default_factory=list, repr=False)
    _closed: bool = field(default=False, repr=False)

    def get_tool_definitions(self) -> list[ToolDefinition]:
        return [t.definition() for t in self.tools.values()]

    def invoke(self, name: str, input: dict[str, Any], call_id: str) -> str:
        tool = self.tools.get(name)
        if tool is None:
            raise ValueError(f"Unknown tool: {name}")

        start = time.monotonic()
        result = tool.invoke(input)
        duration_ms = int((time.monotonic() - start) * 1000)

        # Detect truncation: repo_access tools embed a sentinel suffix when they truncate.
        output_truncated = result.endswith("[output truncated]")

        self.audit_log.record(
            name=name,
            input=input,
            call_id=call_id,
            duration_ms=duration_ms,
            output_truncated=output_truncated,
        )
        return result

    def clone(self) -> ToolRegistry:
        """Return a registry sharing the same tool instances but with a fresh audit log."""
        return ToolRegistry(tools=dict(self.tools), audit_log=ToolCallAuditLog())

    def add_closer(self, fn: Callable[[], None]) -> None:
        """Register a cleanup callback invoked by close() in LIFO order."""
        self._closers.append(fn)

    def close(self) -> None:
        """Run all registered closers in LIFO order. Idempotent; exceptions are logged."""
        if self._closed:
            return
        self._closed = True
        for fn in reversed(self._closers):
            try:
                fn()
            except Exception:
                logger.exception("Error in registry closer %r", fn)


# Dispatch table for optional tool extensions.  Chunks 3-5 populate this via
# register_extension_builder(); left empty here so Chunk 2 ships a clean scaffold.
_EXTENSION_BUILDERS: dict[ToolExtension, Callable[["ToolRegistry", Any], None]] = {}


def register_extension_builder(
    ext: ToolExtension,
    builder: Callable[["ToolRegistry", Any], None],
) -> None:
    """Register a builder for a ToolExtension so create() can invoke it.

    Called by extension modules (Chunks 3-5) at import time — keeps the factory
    body free of per-extension conditionals.
    """
    _EXTENSION_BUILDERS[ext] = builder


# ---------------------------------------------------------------------------
# Extension auto-registration
#
# These imports MUST appear after the Tool class and register_extension_builder
# are both fully defined. Each extension module imports Tool transitively via
# mcp_bridge; if these imports ran before Tool was defined (as they did at the
# top of this file) Python's partial-initialisation state caused a false
# ImportError that silently swallowed all three extensions.
#
# Error handling: genuine missing-optional-dependency errors (e.g.
# tree-sitter-language-pack wheel not installed) should log a warning and
# continue. Circular-import errors — "partially initialized module" — indicate
# a structural problem that must not be silenced; we re-raise them so they are
# immediately visible.
# ---------------------------------------------------------------------------

def _import_extension(module_name: str, ext_label: str) -> None:
    """Import one extension module, differentiating real dep-missing from cycle bugs."""
    try:
        from importlib import import_module
        import_module(f"sec_review_framework.tools.extensions.{module_name}")
    except ImportError as _ext_import_err:
        msg = str(_ext_import_err)
        if "partially initialized module" in msg or "circular import" in msg:
            raise ImportError(
                f"Circular import detected while loading {module_name}: {msg}. "
                "This is a structural bug — the extension import must not run "
                "before the Tool class is fully defined in registry.py."
            ) from _ext_import_err
        logger.warning(
            "%s could not be imported; ToolExtension.%s will not be "
            "available. Install worker extras to enable it. (%s)",
            module_name,
            ext_label,
            _ext_import_err,
        )


_import_extension("lsp_ext", "LSP")
_import_extension("tree_sitter_ext", "TREE_SITTER")
_import_extension("devdocs_ext", "DEVDOCS")


class ToolRegistryFactory:
    """Constructs a ToolRegistry appropriate for the given ToolVariant."""

    @staticmethod
    def create(
        tool_variant: ToolVariant,
        target: Any,
        tool_extensions: frozenset[ToolExtension] | Iterable[ToolExtension] = frozenset(),
    ) -> ToolRegistry:
        """
        Build and return a configured ToolRegistry.

        Parameters
        ----------
        tool_variant:
            WITH_TOOLS populates the registry with all standard tools.
            WITHOUT_TOOLS returns an empty registry (pure-text runs).
        target:
            The repository/target being reviewed. Expected to expose a
            ``repo_path`` attribute (Path) used by file-access tools.
        tool_extensions:
            Optional extension dimensions (tree-sitter, LSP, DevDocs). Each
            extension's builder must have been registered via
            register_extension_builder before create() is called.  Passing an
            unregistered extension raises ValueError immediately so misconfigured
            runs fail fast rather than silently running without the extension.
        """
        extensions: frozenset[ToolExtension] = (
            tool_extensions
            if isinstance(tool_extensions, frozenset)
            else frozenset(tool_extensions)
        )

        registry = ToolRegistry()

        if tool_variant != ToolVariant.WITHOUT_TOOLS:
            # WITH_TOOLS — import here to avoid circular imports at module level.
            from pathlib import Path

            from sec_review_framework.tools.doc_lookup import DocLookupTool
            from sec_review_framework.tools.repo_access import (
                GrepTool,
                ListDirectoryTool,
                ReadFileTool,
            )
            from sec_review_framework.tools.semgrep import SemgrepTool

            repo_path: Path = getattr(target, "repo_path", Path(str(target)))

            core_tools: list[Tool] = [
                ReadFileTool(repo_root=repo_path),
                ListDirectoryTool(repo_root=repo_path),
                GrepTool(repo_root=repo_path),
                SemgrepTool(repo_path=repo_path),
            ]

            # When DEVDOCS is active the MCP-backed doc_* tools replace the stub.
            # Suppress the stub so the two don't coexist in the registry.
            if ToolExtension.DEVDOCS not in extensions:
                core_tools.append(DocLookupTool())

            for tool in core_tools:
                defn = tool.definition()
                registry.tools[defn.name] = tool

        for ext in extensions:
            builder = _EXTENSION_BUILDERS.get(ext)
            if builder is None:
                raise ValueError(
                    f"ToolExtension {ext.value!r} is not yet implemented"
                )
            builder(registry, target)

        return registry
