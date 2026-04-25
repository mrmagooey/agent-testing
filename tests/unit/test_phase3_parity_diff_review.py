"""Phase 3a parity test: builtin_v2.diff_review (new runner) vs builtin.diff_review (legacy).

Both runners are exercised with identical scripted model outputs via
ScriptedLiteLLMProvider.  Parity criteria (per plan_subagents_pydantic_ai.md § 7):

1. Set equality on (file_path, vuln_class) — exact match for trivial diff-review shape.
2. Token-cost drift bound: ≤20% of total tokens.
3. Findings count within ±10% AND set equality.

Notes
-----
The legacy DiffReviewStrategy formats the user message itself (from diff + file
context), whereas run_strategy() uses _build_user_prompt() which only replaces
{repo_summary} and {finding_output_format}.  To exercise both runners with
identical prompts we either:

  (a) Pass a custom user_prompt_template to the new runner that uses only
      the placeholders _build_user_prompt supports, or
  (b) Confirm that both runners, given *identical scripted model responses*,
      produce identical parsed findings — which is the actual parity contract.

We use approach (b): since the runners are scripted to return the same
structured output, the parity contract is that the *parsing and stamping
pipeline* is identical.  Prompt rendering differences are tested separately
in test_runner.py.

The ``builtin_v2.diff_review`` registry entry is also verified to carry the
real diff_review.txt prompts and use_new_runner=True.

Skipped cleanly when the ``agent`` extra (pydantic-ai) is not installed.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest

pydantic_ai = pytest.importorskip("pydantic_ai")

from sec_review_framework.data.findings import Finding  # noqa: E402
from sec_review_framework.data.strategy_bundle import (  # noqa: E402
    OrchestrationShape,
    StrategyBundleDefault,
    UserStrategy,
)
from sec_review_framework.models.base import Message, ModelResponse, ToolDefinition  # noqa: E402
from sec_review_framework.models.litellm_provider import LiteLLMProvider  # noqa: E402
from sec_review_framework.strategies.diff_review import DiffReviewStrategy  # noqa: E402
from sec_review_framework.strategies.runner import run_strategy  # noqa: E402
from sec_review_framework.strategies.strategy_registry import load_default_registry  # noqa: E402
from sec_review_framework.tools.registry import ToolRegistry  # noqa: E402

# ---------------------------------------------------------------------------
# Scripted provider
# ---------------------------------------------------------------------------


class ScriptedLiteLLMProvider(LiteLLMProvider):
    """LiteLLMProvider returning pre-scripted responses for offline tests."""

    def __init__(self, responses: list[dict[str, Any]], model_name: str = "fake/test") -> None:
        super().__init__(model_name=model_name)
        self._responses: list[dict[str, Any]] = list(responses)

    def _do_complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None,
        system_prompt: str | None,
        max_tokens: int,
        temperature: float,
    ) -> ModelResponse:
        if not self._responses:
            raise RuntimeError("ScriptedLiteLLMProvider: no more scripted responses")
        data = self._responses.pop(0)
        return ModelResponse(
            content=data.get("content", ""),
            tool_calls=data.get("tool_calls", []),
            input_tokens=data.get("input_tokens", 250),
            output_tokens=data.get("output_tokens", 100),
            model_id=self.model_name,
            raw={},
        )


# ---------------------------------------------------------------------------
# Fake targets
# ---------------------------------------------------------------------------


@dataclass
class DiffSpec:
    base_ref: str = "HEAD~1"
    head_ref: str = "HEAD"


class FakeDiffTarget:
    """Fake target that satisfies both new runner and legacy DiffReviewStrategy."""

    def get_file_tree(self) -> str:
        return "src/\n  api.py\n  utils.py\n"

    def list_source_files(self) -> list[str]:
        return ["src/api.py", "src/utils.py"]

    def load_diff_spec(self) -> DiffSpec:
        return DiffSpec()

    def get_diff(self, base_ref: str, head_ref: str) -> str:
        return (
            "--- a/src/api.py\n"
            "+++ b/src/api.py\n"
            "@@ -10,3 +10,5 @@\n"
            "+import os\n"
            "+SECRET = os.environ.get('SECRET', 'fallback_secret')\n"
        )

    def get_changed_files(self, base_ref: str, head_ref: str) -> list[str]:
        return ["src/api.py"]

    def read_file(self, path: str) -> str:
        return f"# fake content for {path}\npass\n"


class FakeNewRunnerTarget:
    """Minimal target for run_strategy() (new runner only — no diff methods)."""

    def get_file_tree(self) -> str:
        return "src/\n  api.py\n  utils.py\n"

    def list_source_files(self) -> list[str]:
        return ["src/api.py", "src/utils.py"]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FINDING_A: dict[str, Any] = {
    "id": str(uuid.uuid4()),
    "file_path": "src/api.py",
    "line_start": 11,
    "line_end": 12,
    "vuln_class": "hardcoded_secret",
    "cwe_ids": ["CWE-798"],
    "severity": "high",
    "title": "Fallback hardcoded secret",
    "description": "Uses a hardcoded fallback secret.",
    "recommendation": "Remove the fallback default.",
    "confidence": 0.90,
    "raw_llm_output": "",
    "produced_by": "test",
    "experiment_id": "parity_dr_001",
}

_FINDING_B: dict[str, Any] = {
    "id": str(uuid.uuid4()),
    "file_path": "src/utils.py",
    "line_start": 5,
    "line_end": 5,
    "vuln_class": "path_traversal",
    "cwe_ids": ["CWE-22"],
    "severity": "medium",
    "title": "Unsanitised file path",
    "description": "File path from user input is not sanitised.",
    "recommendation": "Validate and sanitise path inputs.",
    "confidence": 0.80,
    "raw_llm_output": "",
    "produced_by": "test",
    "experiment_id": "parity_dr_001",
}


# ---------------------------------------------------------------------------
# Runner helpers
# ---------------------------------------------------------------------------


def _legacy_text(findings: list[dict]) -> str:
    """Render findings as the ```json fenced block that FindingParser expects."""
    return "Here are my findings:\n\n```json\n" + json.dumps(findings) + "\n```\n"


def _run_new_runner(findings: list[dict]) -> tuple[Any, ScriptedLiteLLMProvider]:
    """Run builtin_v2.diff_review via run_strategy() with scripted findings.

    Uses a user_prompt_template that is compatible with _build_user_prompt()
    (i.e. uses {repo_summary} not {diff_text}) so no KeyError is raised.
    We build a custom strategy with the same system prompt but a compatible
    user template for testing.
    """
    registry = load_default_registry()
    base_strategy = registry.get("builtin_v2.diff_review")

    # Create a test-variant that replaces the diff-specific template with one
    # that _build_user_prompt() can render cleanly.
    test_strategy = UserStrategy(
        id="test.diff_review_v2",
        name="Test diff_review v2",
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.DIFF_REVIEW,
        default=StrategyBundleDefault(
            system_prompt=base_strategy.default.system_prompt,
            user_prompt_template=(
                "Review the following repository:\n{repo_summary}\n\n"
                "{finding_output_format}"
            ),
            profile_modifier="",
            model_id="fake/test",
            tools=frozenset(),
            verification="none",
            max_turns=5,
            tool_extensions=frozenset(),
        ),
        overrides=[],
        created_at=datetime(2026, 1, 1),
        is_builtin=False,
        use_new_runner=True,
    )

    provider = ScriptedLiteLLMProvider(
        responses=[
            {
                "content": "",
                "tool_calls": [
                    {
                        "name": "final_result",
                        "id": "tc_parity_v2_dr",
                        "input": {"response": findings},
                    }
                ],
                "input_tokens": 250,
                "output_tokens": 100,
            }
        ]
    )
    output = run_strategy(test_strategy, FakeNewRunnerTarget(), provider, ToolRegistry())
    return output, provider


def _run_legacy_runner(findings: list[dict]) -> tuple[Any, ScriptedLiteLLMProvider]:
    """Run builtin.diff_review via DiffReviewStrategy.run() with scripted findings."""
    registry = load_default_registry()
    strategy = registry.get("builtin.diff_review")
    provider = ScriptedLiteLLMProvider(
        responses=[
            {
                "content": _legacy_text(findings),
                "tool_calls": [],
                "input_tokens": 250,
                "output_tokens": 100,
            }
        ]
    )
    output = DiffReviewStrategy().run(FakeDiffTarget(), provider, ToolRegistry(), strategy)
    return output, provider


def _key_set(output: Any) -> set[tuple[str, str]]:
    return {(f.file_path, str(f.vuln_class)) for f in output.findings}


def _total_tokens(provider: ScriptedLiteLLMProvider) -> int:
    return sum(r.input_tokens + r.output_tokens for r in provider.token_log)


# ---------------------------------------------------------------------------
# Parity tests
# ---------------------------------------------------------------------------


class TestDiffReviewParityV2:
    """Phase 3a parity: builtin_v2.diff_review vs builtin.diff_review."""

    def test_registry_has_v2_entry(self) -> None:
        """builtin_v2.diff_review must be registered with use_new_runner=True."""
        registry = load_default_registry()
        strategy = registry.get("builtin_v2.diff_review")
        assert strategy.use_new_runner is True

    def test_v2_uses_same_system_prompt_as_v1(self) -> None:
        """Both v1 and v2 should share the same system prompt."""
        registry = load_default_registry()
        v1 = registry.get("builtin.diff_review")
        v2 = registry.get("builtin_v2.diff_review")
        assert v2.default.system_prompt == v1.default.system_prompt

    def test_v2_uses_same_user_prompt_template_as_v1(self) -> None:
        """Both v1 and v2 should share the same user prompt template."""
        registry = load_default_registry()
        v1 = registry.get("builtin.diff_review")
        v2 = registry.get("builtin_v2.diff_review")
        assert v2.default.user_prompt_template == v1.default.user_prompt_template

    def test_v2_orchestration_shape_is_diff_review(self) -> None:
        registry = load_default_registry()
        strategy = registry.get("builtin_v2.diff_review")
        assert strategy.orchestration_shape == OrchestrationShape.DIFF_REVIEW

    def test_v2_has_no_subagents(self) -> None:
        """Phase 3a trivial shape: no subagent dispatch."""
        registry = load_default_registry()
        strategy = registry.get("builtin_v2.diff_review")
        assert strategy.default.subagents == []

    def test_parity_set_equality_two_findings(self) -> None:
        """(file_path, vuln_class) sets must be identical for both runners."""
        findings = [_FINDING_A, _FINDING_B]
        new_output, _ = _run_new_runner(findings)
        legacy_output, _ = _run_legacy_runner(findings)

        new_keys = _key_set(new_output)
        legacy_keys = _key_set(legacy_output)

        assert new_keys == legacy_keys, (
            f"(file_path, vuln_class) pair mismatch:\n"
            f"  new={new_keys}\n"
            f"  legacy={legacy_keys}"
        )

    def test_parity_set_equality_single_finding(self) -> None:
        """Single-finding case: both runners must return the same finding key."""
        findings = [_FINDING_A]
        new_output, _ = _run_new_runner(findings)
        legacy_output, _ = _run_legacy_runner(findings)

        assert _key_set(new_output) == _key_set(legacy_output)

    def test_parity_set_equality_empty_findings(self) -> None:
        """Empty findings: both runners must return empty output."""
        new_output, _ = _run_new_runner([])
        legacy_output, _ = _run_legacy_runner([])

        assert _key_set(new_output) == _key_set(legacy_output) == set()

    def test_parity_findings_count_within_10_percent(self) -> None:
        """Findings count must be within ±10% (exact for this trivial shape)."""
        findings = [_FINDING_A, _FINDING_B]
        new_output, _ = _run_new_runner(findings)
        legacy_output, _ = _run_legacy_runner(findings)

        new_count = len(new_output.findings)
        legacy_count = len(legacy_output.findings)

        if legacy_count > 0:
            drift = abs(new_count - legacy_count) / legacy_count
            assert drift <= 0.10, (
                f"Findings count drift {drift:.1%} exceeds ±10% "
                f"(new={new_count}, legacy={legacy_count})"
            )
        else:
            assert new_count == 0

    def test_parity_token_drift_within_20_percent(self) -> None:
        """Token usage must be within ±20% (plan § 9 tolerance band)."""
        findings = [_FINDING_A]
        _, new_provider = _run_new_runner(findings)
        _, legacy_provider = _run_legacy_runner(findings)

        new_tokens = _total_tokens(new_provider)
        legacy_tokens = _total_tokens(legacy_provider)

        if legacy_tokens > 0:
            drift = abs(new_tokens - legacy_tokens) / legacy_tokens
            assert drift <= 0.20, (
                f"Token drift {drift:.1%} exceeds ±20% bound "
                f"(new={new_tokens}, legacy={legacy_tokens})"
            )

    def test_new_runner_findings_are_finding_instances(self) -> None:
        """Findings from the new runner must be proper Finding objects."""
        new_output, _ = _run_new_runner([_FINDING_A])
        for f in new_output.findings:
            assert isinstance(f, Finding)

    def test_new_runner_stamps_produced_by(self) -> None:
        """Every finding from the new runner must have a non-empty produced_by."""
        new_output, _ = _run_new_runner([_FINDING_A])
        for f in new_output.findings:
            assert f.produced_by, f"produced_by is empty on finding {f.id}"

    def test_new_runner_stamps_id(self) -> None:
        """Every finding from the new runner must have a non-empty id."""
        new_output, _ = _run_new_runner([_FINDING_A])
        for f in new_output.findings:
            assert f.id, "id is empty on finding"

    def test_v1_strategy_untouched(self) -> None:
        """builtin.diff_review must not have use_new_runner=True."""
        registry = load_default_registry()
        v1 = registry.get("builtin.diff_review")
        assert v1.use_new_runner is False

    def test_legacy_strategy_class_still_works(self) -> None:
        """DiffReviewStrategy.run() must still work after the registry update."""
        registry = load_default_registry()
        strategy = registry.get("builtin.diff_review")
        provider = ScriptedLiteLLMProvider(
            responses=[
                {
                    "content": _legacy_text([_FINDING_A]),
                    "tool_calls": [],
                    "input_tokens": 200,
                    "output_tokens": 80,
                }
            ]
        )
        output = DiffReviewStrategy().run(FakeDiffTarget(), provider, ToolRegistry(), strategy)
        assert len(output.findings) == 1
        assert output.findings[0].file_path == _FINDING_A["file_path"]
