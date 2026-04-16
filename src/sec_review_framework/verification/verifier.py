"""Verification pass — filters candidate findings before evaluation."""

from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from sec_review_framework.data.evaluation import (
    VerificationOutcome,
    VerificationResult,
    VerifiedFinding,
)
from sec_review_framework.data.findings import Finding
from sec_review_framework.strategies.common import run_agentic_loop

if TYPE_CHECKING:
    from sec_review_framework.ground_truth.models import TargetCodebase
    from sec_review_framework.models.base import ModelProvider
    from sec_review_framework.tools.registry import ToolRegistry


VERIFICATION_PROMPT = """
You are a security finding verifier. For each candidate finding below,
determine whether it is a genuine vulnerability by examining the source code.

For each finding, you must:
1. Read the cited file and line range
2. Trace the data flow to determine if the vulnerability is exploitable
3. Check if any existing mitigations (input validation, framework guards, etc.) prevent exploitation
4. Cite specific lines of code as evidence for your decision

Respond with a JSON array where each entry has:
{
  "finding_id": "...",
  "outcome": "verified" | "rejected" | "uncertain",
  "evidence": "Your detailed reasoning with file:line citations",
  "cited_lines": ["src/auth.py:42", "src/auth.py:55"]
}
"""


class Verifier(ABC):
    """Receives candidate findings and verifies each one against source code."""

    @abstractmethod
    def verify(
        self,
        candidates: list[Finding],
        target: "TargetCodebase",
        model: "ModelProvider",
        tools: "ToolRegistry",
    ) -> VerificationResult: ...


class LLMVerifier(Verifier):
    """Uses an LLM to verify each candidate finding against the source code."""

    def verify(
        self,
        candidates: list[Finding],
        target: "TargetCodebase",
        model: "ModelProvider",
        tools: "ToolRegistry",
    ) -> VerificationResult:
        if not candidates:
            return VerificationResult(
                verified=[], rejected=[], uncertain=[],
                total_candidates=0, verification_tokens=0,
            )

        findings_summary = self._format_candidates(candidates)
        user_message = (
            f"Verify the following {len(candidates)} candidate security findings:\n\n"
            f"{findings_summary}\n\n"
            f"Use your tools to read the source code and verify each finding.\n"
            f"{VERIFICATION_PROMPT}"
        )

        input_before = sum(r.input_tokens for r in model.token_log)
        output_before = sum(r.output_tokens for r in model.token_log)

        raw_output = run_agentic_loop(
            model, tools,
            system_prompt="You are a precise security finding verifier.",
            initial_user_message=user_message,
            max_turns=40,
        )

        input_after = sum(r.input_tokens for r in model.token_log)
        output_after = sum(r.output_tokens for r in model.token_log)
        verification_tokens = (input_after - input_before) + (output_after - output_before)

        decisions = self._parse_verification_output(raw_output)

        verified: list[VerifiedFinding] = []
        rejected: list[VerifiedFinding] = []
        uncertain: list[VerifiedFinding] = []

        for candidate in candidates:
            decision = decisions.get(candidate.id)
            if decision is None:
                candidate.verified = None
                uncertain.append(VerifiedFinding(
                    finding=candidate,
                    outcome=VerificationOutcome.UNCERTAIN,
                    evidence="Not addressed by verifier",
                    cited_lines=[],
                ))
                continue

            outcome_str = decision.get("outcome", "uncertain")
            candidate.verified = outcome_str == "verified"
            candidate.verification_evidence = decision.get("evidence", "")
            if outcome_str == "rejected":
                candidate.verification_rejected_reason = decision.get("evidence", "")

            vf = VerifiedFinding(
                finding=candidate,
                outcome=VerificationOutcome(outcome_str),
                evidence=decision.get("evidence", ""),
                cited_lines=decision.get("cited_lines", []),
            )
            if outcome_str == "verified":
                verified.append(vf)
            elif outcome_str == "rejected":
                rejected.append(vf)
            else:
                uncertain.append(vf)

        return VerificationResult(
            verified=verified,
            rejected=rejected,
            uncertain=uncertain,
            total_candidates=len(candidates),
            verification_tokens=verification_tokens,
        )

    def _format_candidates(self, candidates: list[Finding]) -> str:
        lines: list[str] = []
        for f in candidates:
            loc = f"{f.file_path}:{f.line_start or '?'}-{f.line_end or '?'}"
            lines.append(
                f"- ID: {f.id}\n"
                f"  Location: {loc}\n"
                f"  Type: {f.vuln_class.value} ({', '.join(f.cwe_ids) or 'no CWE'})\n"
                f"  Severity: {f.severity.value}\n"
                f"  Title: {f.title}\n"
                f"  Description: {f.description}\n"
            )
        return "\n".join(lines)

    def _parse_verification_output(self, raw_output: str) -> dict[str, dict[str, Any]]:
        """Extract JSON decisions from verifier output."""
        match = re.search(r"```json\s*(.*?)\s*```", raw_output, re.DOTALL)
        text = match.group(1) if match else raw_output

        try:
            items = json.loads(text)
        except json.JSONDecodeError:
            return {}

        if not isinstance(items, list):
            return {}

        decisions: dict[str, dict[str, Any]] = {}
        for item in items:
            if isinstance(item, dict) and "finding_id" in item:
                decisions[item["finding_id"]] = item
        return decisions
