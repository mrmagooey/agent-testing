"""PerVulnClassStrategy — specialist subagents per vulnerability class."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sec_review_framework.data.findings import StrategyOutput, VulnClass
from sec_review_framework.prompts.loader import load_system_prompt, load_user_prompt
from sec_review_framework.strategies.base import ScanStrategy
from sec_review_framework.strategies.common import (
    FINDING_OUTPUT_FORMAT,
    FindingParser,
    build_system_prompt,
    deduplicate,
    run_subagents,
)

if TYPE_CHECKING:
    from sec_review_framework.models.base import ModelProvider
    from sec_review_framework.tools.registry import ToolRegistry


# ---------------------------------------------------------------------------
# Specialist system prompts — one per VulnClass, loaded from
# prompts/system/per_vuln_class/{value}.txt
# ---------------------------------------------------------------------------

VULN_CLASS_SYSTEM_PROMPTS: dict[VulnClass, str] = {
    vc: load_system_prompt("per_vuln_class", f"{vc.value}.txt") for vc in VulnClass
}


class PerVulnClassStrategy(ScanStrategy):
    """Assign one specialist subagent per vulnerability class, scanning the full repo.

    Each subagent focuses exclusively on its assigned vulnerability class and
    scans the entire repository.  All findings are merged and deduplicated.
    Supports parallel execution via ``config["parallel"]``.
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
        config: dict,
    ) -> StrategyOutput:
        active_classes: list[VulnClass] = config.get("vuln_classes", list(VulnClass))
        repo_summary = self._build_repo_summary(target)
        experiment_id = config.get("experiment_id", "")
        max_turns_per_class = config.get("max_turns_per_class", 40)
        parallel = config.get("parallel", False)

        user_template = load_user_prompt("per_vuln_class.txt")
        tasks = []
        for vuln_class in active_classes:
            base_prompt = VULN_CLASS_SYSTEM_PROMPTS[vuln_class]
            system_prompt = build_system_prompt(base_prompt, config)
            tasks.append(
                {
                    "system_prompt": system_prompt,
                    "user_message": user_template.format(
                        vuln_class=vuln_class,
                        repo_summary=repo_summary,
                        finding_output_format=FINDING_OUTPUT_FORMAT,
                    ),
                    "max_turns": max_turns_per_class,
                }
            )

        first_system_prompt = tasks[0]["system_prompt"] if tasks else None
        first_user_message = tasks[0]["user_message"] if tasks else None

        outputs = run_subagents(tasks, model, tools, parallel=parallel)

        all_findings = []
        for vuln_class, raw_output in zip(active_classes, outputs):
            findings = FindingParser().parse(
                raw_output,
                experiment_id=experiment_id,
                produced_by=f"specialist:{vuln_class}",
            )
            all_findings.extend(findings)

        result = deduplicate(all_findings)
        result.system_prompt = first_system_prompt
        result.user_message = first_user_message
        return result
