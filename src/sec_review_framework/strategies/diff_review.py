"""DiffReviewStrategy — PR-style review of a unified diff."""

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


class DiffReviewStrategy(ScanStrategy):
    """Simulate a PR-time security review against a unified diff.

    Requires the dataset to contain a ``diff_spec.yaml`` that specifies
    ``base_ref`` and ``head_ref``.  The runner skips this strategy and logs a
    warning for any dataset that lacks ``diff_spec.yaml``.

    The agent receives:
    - The unified diff between base and head refs
    - Full file content for each changed file (for context)
    - Tool access to the full repo if ``tool_variant=WITH_TOOLS``

    Findings are expected to focus on changed code, but the agent is also
    instructed to flag pre-existing issues in touched files and issues in
    unchanged code that interact with the changes.  The evaluator uses the
    ``introduced_in_diff`` label field to measure whether the model correctly
    distinguishes new vs pre-existing issues.

    No deduplication is applied (single agentic loop, no subagent overlap).
    """

    def name(self) -> str:
        return "diff_review"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _base_system_prompt(self) -> str:
        return (
            "You are an expert security code reviewer performing a pull request review. "
            "Your primary focus is on security issues introduced or exposed by the code change, "
            "but you should also flag pre-existing issues in the files touched by the diff "
            "and any issues in unchanged code that interact with the changes. "
            "Be precise — report only genuine findings with evidence from the code."
        )

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
        # Load the diff spec — raises if no diff_spec.yaml (runner handles this)
        diff_spec = target.load_diff_spec()
        diff_text = target.get_diff(diff_spec.base_ref, diff_spec.head_ref)
        changed_files = target.get_changed_files(diff_spec.base_ref, diff_spec.head_ref)

        system_prompt = build_system_prompt(self._base_system_prompt(), config)
        experiment_id = config.get("experiment_id", "")

        # Build full-file context for each changed file
        file_context = ""
        for fp in changed_files:
            content = target.read_file(fp)
            file_context += f"\n--- {fp} (full file) ---\n{content}\n"

        user_message = (
            "You are reviewing a code change (pull request). Focus your security analysis\n"
            "on the changed code, but consider the full file context for understanding.\n\n"
            "Flag issues in three categories:\n"
            "- Bugs introduced by this change\n"
            "- Pre-existing bugs in the touched files that should be addressed\n"
            "- Bugs in unchanged code that interact with the changes\n\n"
            f"Unified diff:\n"
            f"```diff\n{diff_text}\n```\n\n"
            f"Full content of changed files:\n{file_context}\n"
            "Use your tools to read any other files needed for context.\n"
            f"{FINDING_OUTPUT_FORMAT}"
        )

        raw_output = run_agentic_loop(
            model,
            tools,
            system_prompt,
            user_message,
            max_turns=config.get("max_turns", 60),
        )

        findings = FindingParser().parse(
            raw_output,
            experiment_id=experiment_id,
            produced_by="diff_review",
        )

        # No dedup — single agentic loop, no subagent overlap
        return StrategyOutput(
            findings=findings,
            pre_dedup_count=len(findings),
            post_dedup_count=len(findings),
            dedup_log=[],
        )
