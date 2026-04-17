"""PerFileStrategy — one subagent per source file, optional parallel execution."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sec_review_framework.data.findings import StrategyOutput
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


class PerFileStrategy(ScanStrategy):
    """Assign one subagent per source file; merge and deduplicate all findings.

    Each subagent receives a single file's content and may use tools for
    cross-file context (e.g. to follow imports).  Supports parallel execution
    via ``config["parallel"]``.
    """

    def name(self) -> str:
        return "per_file"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _base_system_prompt(self) -> str:
        return load_system_prompt("per_file.txt")

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
        source_files = target.list_source_files()
        system_prompt = build_system_prompt(self._base_system_prompt(), config)
        experiment_id = config.get("experiment_id", "")
        max_turns_per_file = config.get("max_turns_per_file", 20)
        parallel = config.get("parallel", False)

        user_template = load_user_prompt("per_file.txt")
        tasks = []
        for file_path in source_files:
            file_content = target.read_file(file_path)
            tasks.append(
                {
                    "system_prompt": system_prompt,
                    "user_message": user_template.format(
                        file_path=file_path,
                        file_content=file_content,
                        finding_output_format=FINDING_OUTPUT_FORMAT,
                    ),
                    "max_turns": max_turns_per_file,
                }
            )

        first_system_prompt = tasks[0]["system_prompt"] if tasks else None
        first_user_message = tasks[0]["user_message"] if tasks else None

        outputs = run_subagents(tasks, model, tools, parallel=parallel)

        all_findings = []
        for file_path, raw_output in zip(source_files, outputs):
            findings = FindingParser().parse(
                raw_output,
                experiment_id=experiment_id,
                produced_by=f"per_file:{file_path}",
            )
            all_findings.extend(findings)

        result = deduplicate(all_findings)
        result.system_prompt = first_system_prompt
        result.user_message = first_user_message
        return result
