"""Tool registry, audit logging, and base abstractions for the security review framework."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sec_review_framework.data.experiment import ToolVariant
from sec_review_framework.models.base import ToolDefinition


@dataclass
class ToolCallRecord:
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


class ToolRegistryFactory:
    """Constructs a ToolRegistry appropriate for the given ToolVariant."""

    @staticmethod
    def create(tool_variant: ToolVariant, target: Any) -> ToolRegistry:
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
        """
        registry = ToolRegistry()

        if tool_variant == ToolVariant.WITHOUT_TOOLS:
            return registry

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

        tools: list[Tool] = [
            ReadFileTool(repo_root=repo_path),
            ListDirectoryTool(repo_root=repo_path),
            GrepTool(repo_root=repo_path),
            SemgrepTool(repo_path=repo_path),
            DocLookupTool(),
        ]

        for tool in tools:
            defn = tool.definition()
            registry.tools[defn.name] = tool

        return registry
