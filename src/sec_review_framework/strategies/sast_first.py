"""SASTFirstStrategy — Semgrep triage then LLM deep-dive per flagged file."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING

from sec_review_framework.data.findings import StrategyOutput
from sec_review_framework.strategies.base import ScanStrategy
from sec_review_framework.strategies.common import (
    FINDING_OUTPUT_FORMAT,
    FindingParser,
    build_system_prompt,
    run_subagents,
)
from sec_review_framework.tools.semgrep import SemgrepTool

if TYPE_CHECKING:
    from sec_review_framework.models.base import ModelProvider
    from sec_review_framework.tools.registry import ToolRegistry


class SASTFirstStrategy(ScanStrategy):
    """Run Semgrep first, then have the LLM triage and deepen each flagged file.

    Phase 1: Semgrep runs unconditionally over the full repository.
    Phase 2: For each file flagged by Semgrep, one LLM subagent confirms,
             rejects, or escalates the SAST findings and looks for issues
             Semgrep missed.

    The ``tool_variant`` dimension controls whether Phase-2 subagents have
    tool access for cross-file context — the strategy itself does not branch
    on this; it is handled transparently by the ToolRegistry.

    No deduplication is applied because each file is processed exactly once.
    """

    def name(self) -> str:
        return "sast_first"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _base_system_prompt(self) -> str:
        return (
            "You are an expert security code reviewer working in a SAST triage role. "
            "You will be shown automated static analysis findings for a source file. "
            "For each finding: confirm it (true positive), reject it (false positive), "
            "or escalate it (needs further investigation). "
            "Also look for security issues the automated scanner may have missed. "
            "Be precise — report only genuine findings with evidence from the code."
        )

    def _format_sast_matches(self, matches: list) -> str:
        """Format a list of SASTMatch objects into a readable summary."""
        lines = []
        for i, match in enumerate(matches, start=1):
            rule = getattr(match, "rule_id", "unknown-rule")
            msg = getattr(match, "message", "")
            line_start = getattr(match, "line_start", "?")
            line_end = getattr(match, "line_end", "?")
            severity = getattr(match, "severity", "")
            lines.append(
                f"[{i}] Rule: {rule} | Severity: {severity} | "
                f"Lines {line_start}-{line_end}\n    {msg}"
            )
        return "\n".join(lines)

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
        # ----------------------------------------------------------------
        # Phase 1: Run Semgrep unconditionally
        # ----------------------------------------------------------------
        semgrep_tool = SemgrepTool(target.repo_path)
        sast_results = semgrep_tool.run_full_scan()

        if not sast_results:
            return StrategyOutput(
                findings=[],
                pre_dedup_count=0,
                post_dedup_count=0,
                dedup_log=[],
            )

        # Group SAST matches by file
        by_file: dict[str, list] = defaultdict(list)
        for match in sast_results:
            by_file[match.file_path].append(match)

        # ----------------------------------------------------------------
        # Phase 2: LLM triage — one subagent per flagged file
        # ----------------------------------------------------------------
        system_prompt = build_system_prompt(self._base_system_prompt(), config)
        experiment_id = config.get("experiment_id", "")
        max_turns_per_file = config.get("max_turns_per_file", 25)
        parallel = config.get("parallel", False)

        tasks = []
        file_paths = []
        for file_path, matches in by_file.items():
            file_content = target.read_file(file_path)
            sast_summary = self._format_sast_matches(matches)
            tasks.append(
                {
                    "system_prompt": system_prompt,
                    "user_message": (
                        f"Semgrep flagged the following issues in {file_path}.\n"
                        f"Confirm, reject, or escalate each. Also look for issues Semgrep missed.\n\n"
                        f"SAST findings:\n{sast_summary}\n\n"
                        f"File content:\n"
                        f"```\n{file_content}\n```\n"
                        f"{FINDING_OUTPUT_FORMAT}"
                    ),
                    "max_turns": max_turns_per_file,
                }
            )
            file_paths.append(file_path)

        outputs = run_subagents(tasks, model, tools, parallel=parallel)

        all_findings = []
        for file_path, raw_output in zip(file_paths, outputs):
            findings = FindingParser().parse(
                raw_output,
                experiment_id=experiment_id,
                produced_by=f"sast_first:{file_path}",
            )
            all_findings.extend(findings)

        # No dedup — each file is processed exactly once
        return StrategyOutput(
            findings=all_findings,
            pre_dedup_count=len(all_findings),
            post_dedup_count=len(all_findings),
            dedup_log=[],
        )
