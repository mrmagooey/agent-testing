"""SingleAgentStrategy — one agent scans the full repository."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sec_review_framework.data.findings import StrategyOutput
from sec_review_framework.strategies.base import ScanStrategy
from sec_review_framework.strategies.common import (
    FINDING_OUTPUT_FORMAT,
    FindingParser,
    build_system_prompt,
    run_agentic_loop,
)

if TYPE_CHECKING:
    from sec_review_framework.models.base import ModelProvider
    from sec_review_framework.tools.registry import ToolRegistry


class SingleAgentStrategy(ScanStrategy):
    """Scan the full repository with a single agentic loop.

    One agent receives a repo structure summary and uses tools to read any
    files it needs.  Because only one agent runs, there is no overlap between
    findings and deduplication is unnecessary.
    """

    def name(self) -> str:
        return "single_agent"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _base_system_prompt(self) -> str:
        return (
            "You are an expert security code reviewer. "
            "Your task is to perform a thorough security audit of the provided codebase. "
            "Look for vulnerabilities across all classes: injection flaws, authentication "
            "issues, cryptographic weaknesses, insecure dependencies, logic bugs, and more. "
            "Be precise — report only genuine findings with evidence from the code."
        )

    def _build_repo_summary(self, target) -> str:
        """Return a text representation of the repository file tree."""
        try:
            return target.get_file_tree()
        except AttributeError:
            # Fallback: list source files if get_file_tree() is not available
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
        system_prompt = build_system_prompt(self._base_system_prompt(), config)
        repo_summary = self._build_repo_summary(target)
        experiment_id = config.get("experiment_id", "")

        user_message = f"""You are a security code reviewer. Perform a comprehensive security audit \
of the following codebase.

Repository structure:
{repo_summary}

Use your tools to read any files you need. Cover all vulnerability classes.
{FINDING_OUTPUT_FORMAT}"""

        raw_output = run_agentic_loop(
            model,
            tools,
            system_prompt,
            user_message,
            max_turns=config.get("max_turns", 80),
        )

        findings = FindingParser().parse(
            raw_output,
            experiment_id=experiment_id,
            produced_by="single_agent",
        )

        # No dedup for single agent — one pass, no overlap between subagents
        return StrategyOutput(
            findings=findings,
            pre_dedup_count=len(findings),
            post_dedup_count=len(findings),
            dedup_log=[],
        )
