"""ScanStrategy abstract base class."""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

from sec_review_framework.data.findings import StrategyOutput

if TYPE_CHECKING:
    from sec_review_framework.models.base import ModelProvider
    from sec_review_framework.tools.registry import ToolRegistry


class ScanStrategy(ABC):
    """
    A strategy receives a target codebase and a tool registry,
    and produces a flat list of findings.

    Strategies are stateless — all context comes in via arguments.
    The runner instantiates a fresh strategy per experiment run.
    """

    @abstractmethod
    def name(self) -> str:
        """Return the canonical strategy name (matches StrategyName enum value)."""
        ...

    @abstractmethod
    def run(
        self,
        target: "TargetCodebase",
        model: "ModelProvider",
        tools: "ToolRegistry",
        config: dict,
    ) -> StrategyOutput:
        """
        Execute the scan strategy.

        Args:
            target: The target codebase to scan.
            model: The model provider to use for LLM calls.
            tools: The tool registry (may be empty if tool_variant=WITHOUT_TOOLS).
            config: Strategy configuration dict (may include review_profile,
                    parallel, max_turns, vuln_classes, etc.).

        Returns:
            StrategyOutput with findings and dedup metadata.
        """
        ...
