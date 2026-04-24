"""PerVulnClassStrategy — specialist subagents per vulnerability class."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sec_review_framework.data.findings import StrategyOutput, VulnClass
from sec_review_framework.strategies.base import ScanStrategy
from sec_review_framework.strategies.common import (
    FINDING_OUTPUT_FORMAT,
    FindingParser,
    deduplicate,
    run_subagents,
)

if TYPE_CHECKING:
    from sec_review_framework.data.strategy_bundle import UserStrategy
    from sec_review_framework.models.base import ModelProvider
    from sec_review_framework.tools.registry import ToolRegistry


class PerVulnClassStrategy(ScanStrategy):
    """Assign one specialist subagent per vulnerability class, scanning the full repo.

    Each subagent focuses exclusively on its assigned vulnerability class and
    scans the entire repository.  All findings are merged and deduplicated.
    Supports parallel execution.

    The per-class system prompt comes from the strategy's overrides keyed by
    VulnClass name (e.g. ``"sqli"``).  The default bundle's system_prompt is
    used as a fallback when no override exists for a class.
    """

    def name(self) -> str:
        return "per_vuln_class"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_repo_summary(self, target) -> str:
        """Return a text representation of the repository file tree."""
        try:
            return target.get_file_tree()
        except AttributeError:
            files = target.list_source_files()
            return "\n".join(files)

    # ------------------------------------------------------------------
    # ScanStrategy.run()
    # ------------------------------------------------------------------

    def run(
        self,
        target,
        model: "ModelProvider",
        tools: "ToolRegistry",
        strategy: "UserStrategy",
        active_classes: list[VulnClass] | None = None,
        parallel: bool = False,
    ) -> StrategyOutput:
        if active_classes is None:
            active_classes = list(VulnClass)

        repo_summary = self._build_repo_summary(target)
        experiment_id = ""  # experiment_id is not in UserStrategy; use empty string

        tasks = []
        for vuln_class in active_classes:
            tasks.append(
                {
                    "key": vuln_class.value,
                    "user_message": strategy.default.user_prompt_template.format(
                        vuln_class=vuln_class,
                        repo_summary=repo_summary,
                        finding_output_format=FINDING_OUTPUT_FORMAT,
                    ),
                }
            )

        outputs = run_subagents(tasks, model, tools, parallel=parallel, strategy=strategy)

        all_findings = []
        for vuln_class, raw_output in zip(active_classes, outputs):
            findings = FindingParser().parse(
                raw_output,
                experiment_id=experiment_id,
                produced_by=f"specialist:{vuln_class}",
            )
            all_findings.extend(findings)

        result = deduplicate(all_findings)
        return result
