"""Documentation lookup tool stub for the security review framework."""

from __future__ import annotations

from typing import Any

from sec_review_framework.models.base import ToolDefinition
from sec_review_framework.tools.registry import Tool


class DocLookupTool(Tool):
    """
    Stub implementation of the documentation lookup tool.

    In production this would call a designated internal documentation endpoint.
    No external URLs are accepted as inputs; the query is dispatched only to the
    pre-configured internal service (per Layer 3 tool constraints in the
    exfiltration risk model).
    """

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="lookup_docs",
            description=(
                "Look up security-relevant documentation for a language feature, "
                "library, or API. Returns guidance on known vulnerability patterns "
                "and safe usage."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": (
                            "The topic or API to look up "
                            "(e.g. 'Python subprocess shell=True', 'SQL parameterised queries')."
                        ),
                    },
                    "language": {
                        "type": "string",
                        "description": (
                            "Programming language context for the query "
                            "(e.g. 'python', 'javascript', 'java')."
                        ),
                    },
                },
                "required": ["query"],
            },
        )

    def invoke(self, input: dict[str, Any]) -> str:
        query: str = input.get("query", "")
        language: str | None = input.get("language")

        lang_suffix = f" ({language})" if language else ""
        return (
            f"[DocLookupTool stub] Documentation lookup for: {query!r}{lang_suffix}. "
            "This tool is not yet connected to a documentation backend. "
            "Configure the internal docs endpoint in the deployment environment to enable it."
        )
