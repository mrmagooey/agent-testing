"""PerFileStrategy — one subagent per source file, optional parallel execution."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sec_review_framework.data.findings import StrategyOutput
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


class PerFileStrategy(ScanStrategy):
    """Assign one subagent per source file; merge and deduplicate all findings.

    Each subagent receives a single file's content and may use tools for
    cross-file context (e.g. to follow imports).  Supports parallel execution
    via the strategy default's config.
    """

    def name(self) -> str:
        return "per_file"

    # ------------------------------------------------------------------
    # ScanStrategy.run()
    # ------------------------------------------------------------------

    def run(
        self,
        target,
        model: "ModelProvider",
        tools: "ToolRegistry",
        strategy: "UserStrategy",
        parallel: bool = False,
    ) -> StrategyOutput:
        source_files = target.list_source_files()
        experiment_id = ""  # experiment_id is not in UserStrategy; use empty string

        tasks = []
        for file_path in source_files:
            file_content = target.read_file(file_path)
            tasks.append(
                {
                    "key": file_path,
                    "user_message": strategy.default.user_prompt_template.format(
                        file_path=file_path,
                        file_content=file_content,
                        finding_output_format=FINDING_OUTPUT_FORMAT,
                    ),
                }
            )

        outputs = run_subagents(tasks, model, tools, parallel=parallel, strategy=strategy)

        all_findings = []
        for file_path, raw_output in zip(source_files, outputs):
            findings = FindingParser().parse(
                raw_output,
                experiment_id=experiment_id,
                produced_by=f"per_file:{file_path}",
            )
            all_findings.extend(findings)

        result = deduplicate(all_findings)
        return result
