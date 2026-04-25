"""Smoke parity test: single_agent strategy through run_strategy() vs SingleAgentStrategy.

This is the Phase 2 calibration baseline.  It verifies that the pydantic-ai
runner produces output of the same shape as the legacy agentic loop for the
``single_agent`` shape, using fully offline scripted providers.

Parity tolerance:
- Same ``len(findings)`` (exact match for single-agent).
- Same set of ``(file_path, vuln_class)`` tuples.
- Token-cost drift bound: ±5% (total tokens from both runs).

Skipped cleanly when the ``agent`` extra (pydantic-ai) is not installed.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

import pytest

pydantic_ai = pytest.importorskip("pydantic_ai")

from sec_review_framework.data.findings import Finding, Severity, VulnClass  # noqa: E402
from sec_review_framework.data.strategy_bundle import (  # noqa: E402
    OrchestrationShape,
    StrategyBundleDefault,
    UserStrategy,
)
from sec_review_framework.models.base import Message, ToolDefinition  # noqa: E402
from sec_review_framework.models.base import ModelResponse as FrameworkModelResponse  # noqa: E402
from sec_review_framework.models.litellm_provider import LiteLLMProvider  # noqa: E402
from sec_review_framework.strategies.runner import RunnerError, run_strategy  # noqa: E402
from sec_review_framework.strategies.single_agent import SingleAgentStrategy  # noqa: E402
from sec_review_framework.tools.registry import ToolRegistry  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_finding_data(**overrides: Any) -> dict[str, Any]:
    """Return a valid Finding dict."""
    base = {
        "id": str(uuid.uuid4()),
        "file_path": "src/auth.py",
        "line_start": 42,
        "line_end": 42,
        "vuln_class": "hardcoded_secret",
        "cwe_ids": ["CWE-798"],
        "severity": "high",
        "title": "Hardcoded API secret",
        "description": "A hardcoded secret key was found in the source.",
        "recommendation": "Move to environment variable.",
        "confidence": 0.95,
        "raw_llm_output": "",
        "produced_by": "test",
        "experiment_id": "smoke_001",
    }
    base.update(overrides)
    return base


class ScriptedLiteLLMProvider(LiteLLMProvider):
    """LiteLLMProvider returning pre-scripted responses for offline tests."""

    def __init__(self, responses: list[dict[str, Any]], model_name: str = "fake/test") -> None:
        super().__init__(model_name=model_name)
        self._responses: list[dict[str, Any]] = list(responses)
        self._call_count = 0

    def _do_complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None,
        system_prompt: str | None,
        max_tokens: int,
        temperature: float,
    ) -> FrameworkModelResponse:
        if not self._responses:
            raise RuntimeError("ScriptedLiteLLMProvider: no more scripted responses")
        self._call_count += 1
        data = self._responses.pop(0)
        return FrameworkModelResponse(
            content=data.get("content", ""),
            tool_calls=data.get("tool_calls", []),
            input_tokens=data.get("input_tokens", 100),
            output_tokens=data.get("output_tokens", 50),
            model_id=self.model_name,
            raw={},
        )


class FakeTarget:
    """Minimal target stub for smoke tests."""

    def get_file_tree(self) -> str:
        return "src/\n  auth.py\n  utils.py\n"

    def list_source_files(self) -> list[str]:
        return ["src/auth.py", "src/utils.py"]


def _make_single_agent_strategy(*, use_new_runner: bool = False) -> UserStrategy:
    """Construct a minimal single-agent UserStrategy for smoke testing."""
    return UserStrategy(
        id="smoke.single_agent",
        name="Smoke test single agent",
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.SINGLE_AGENT,
        default=StrategyBundleDefault(
            system_prompt="You are a security code reviewer.",
            user_prompt_template=(
                "Review the following repository:\n{repo_summary}\n\n"
                "{finding_output_format}"
            ),
            model_id="fake/test",
            tools=frozenset(),
            verification="none",
            max_turns=5,
            tool_extensions=frozenset(),
        ),
        overrides=[],
        created_at=datetime(2026, 1, 1),
        is_builtin=False,
        use_new_runner=use_new_runner,
    )


# ---------------------------------------------------------------------------
# Smoke test: run_strategy produces valid StrategyOutput
# ---------------------------------------------------------------------------


class TestRunStrategyBasic:
    """Basic validation that run_strategy produces a well-formed StrategyOutput."""

    def _make_provider_for_runner(self, findings: list[dict[str, Any]]) -> ScriptedLiteLLMProvider:
        """Script one response: a final_result tool call with findings."""
        return ScriptedLiteLLMProvider(
            responses=[
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "final_result",
                            "id": "tc_smoke_1",
                            "input": {"response": findings},
                        }
                    ],
                    "input_tokens": 200,
                    "output_tokens": 80,
                }
            ]
        )

    def test_run_strategy_returns_strategy_output(self) -> None:
        finding_data = _make_finding_data()
        provider = self._make_provider_for_runner([finding_data])
        strategy = _make_single_agent_strategy(use_new_runner=True)
        target = FakeTarget()

        output = run_strategy(strategy, target, provider, ToolRegistry())

        assert output is not None
        assert isinstance(output.findings, list)

    def test_run_strategy_non_empty_findings(self) -> None:
        finding_data = _make_finding_data()
        provider = self._make_provider_for_runner([finding_data])
        strategy = _make_single_agent_strategy(use_new_runner=True)
        target = FakeTarget()

        output = run_strategy(strategy, target, provider, ToolRegistry())

        assert len(output.findings) == 1, "Expected one finding from scripted response"

    def test_run_strategy_finding_fields(self) -> None:
        finding_data = _make_finding_data(
            file_path="src/utils.py",
            vuln_class="sqli",
            severity="critical",
            confidence=0.88,
        )
        provider = self._make_provider_for_runner([finding_data])
        strategy = _make_single_agent_strategy(use_new_runner=True)
        target = FakeTarget()

        output = run_strategy(strategy, target, provider, ToolRegistry())

        f = output.findings[0]
        assert isinstance(f, Finding)
        assert f.file_path == "src/utils.py"
        assert f.vuln_class == VulnClass.SQLI
        assert f.severity == Severity.CRITICAL
        assert abs(f.confidence - 0.88) < 0.01

    def test_run_strategy_stamps_id(self) -> None:
        """Findings must have non-empty id, produced_by, experiment_id."""
        finding_data = _make_finding_data(id="", produced_by="", experiment_id="")
        provider = self._make_provider_for_runner([finding_data])
        strategy = _make_single_agent_strategy(use_new_runner=True)
        target = FakeTarget()

        output = run_strategy(strategy, target, provider, ToolRegistry())

        f = output.findings[0]
        assert f.id, "id must be non-empty after stamping"
        assert f.produced_by, "produced_by must be non-empty after stamping"

    def test_run_strategy_empty_findings(self) -> None:
        provider = self._make_provider_for_runner([])
        strategy = _make_single_agent_strategy(use_new_runner=True)
        target = FakeTarget()

        output = run_strategy(strategy, target, provider, ToolRegistry())

        assert output.findings == []
        assert output.pre_dedup_count == 0
        assert output.post_dedup_count == 0

    def test_run_strategy_captures_system_prompt(self) -> None:
        provider = self._make_provider_for_runner([])
        strategy = _make_single_agent_strategy(use_new_runner=True)
        target = FakeTarget()

        output = run_strategy(strategy, target, provider, ToolRegistry())

        assert output.system_prompt == "You are a security code reviewer."

    def test_run_strategy_captures_user_message(self) -> None:
        provider = self._make_provider_for_runner([])
        strategy = _make_single_agent_strategy(use_new_runner=True)
        target = FakeTarget()

        output = run_strategy(strategy, target, provider, ToolRegistry())

        assert output.user_message is not None
        assert "src/" in output.user_message  # repo_summary injected

    def test_run_strategy_profile_modifier_appended(self) -> None:
        """profile_modifier should be appended to system_prompt."""
        provider = self._make_provider_for_runner([])
        strategy = UserStrategy(
            id="smoke.with_modifier",
            name="Smoke with modifier",
            parent_strategy_id=None,
            orchestration_shape=OrchestrationShape.SINGLE_AGENT,
            default=StrategyBundleDefault(
                system_prompt="Base prompt.",
                user_prompt_template="Review {repo_summary}",
                profile_modifier="Focus on authentication.",
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
        target = FakeTarget()

        output = run_strategy(strategy, target, provider, ToolRegistry())

        assert output.system_prompt == "Base prompt.\n\nFocus on authentication."


# ---------------------------------------------------------------------------
# Parity test: run_strategy vs SingleAgentStrategy
# ---------------------------------------------------------------------------


class TestSingleAgentParity:
    """Compare outputs of run_strategy vs SingleAgentStrategy.run().

    Both runners are given the same strategy configuration and scripted to
    return the same set of findings.  The test verifies:
    - Same number of findings.
    - Same (file_path, vuln_class) tuple set.
    - Token counts within ±5% (smoke baseline; Phase 3 enforces ±20%).
    """

    # Two findings with distinct (file_path, vuln_class) pairs
    _FINDING_A = {
        "id": str(uuid.uuid4()),
        "file_path": "src/auth.py",
        "line_start": 10,
        "line_end": 10,
        "vuln_class": "hardcoded_secret",
        "cwe_ids": ["CWE-798"],
        "severity": "high",
        "title": "Hardcoded credential",
        "description": "Hardcoded password in auth module.",
        "recommendation": "Use env var.",
        "confidence": 0.90,
        "raw_llm_output": "",
        "produced_by": "test",
        "experiment_id": "parity_001",
    }
    _FINDING_B = {
        "id": str(uuid.uuid4()),
        "file_path": "src/db.py",
        "line_start": 55,
        "line_end": 57,
        "vuln_class": "sqli",
        "cwe_ids": ["CWE-89"],
        "severity": "critical",
        "title": "SQL injection",
        "description": "Unsanitised user input in SQL query.",
        "recommendation": "Use parameterised queries.",
        "confidence": 0.95,
        "raw_llm_output": "",
        "produced_by": "test",
        "experiment_id": "parity_001",
    }

    def _legacy_output_format(self, findings: list[dict]) -> str:
        """Render findings as the ``json fenced block that FindingParser expects."""
        return "Here are my findings:\n\n```json\n" + json.dumps(findings) + "\n```\n"

    def _run_new_runner(self, findings: list[dict]) -> Any:
        """Run find_strategy() scripted to return *findings*."""
        provider = ScriptedLiteLLMProvider(
            responses=[
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "final_result",
                            "id": "tc_parity_new",
                            "input": {"response": findings},
                        }
                    ],
                    "input_tokens": 250,
                    "output_tokens": 100,
                }
            ]
        )
        strategy = _make_single_agent_strategy(use_new_runner=True)
        return run_strategy(strategy, FakeTarget(), provider, ToolRegistry()), provider

    def _run_legacy_runner(self, findings: list[dict]) -> Any:
        """Run SingleAgentStrategy.run() scripted to return *findings*."""
        legacy_text = self._legacy_output_format(findings)
        provider = ScriptedLiteLLMProvider(
            responses=[
                {
                    "content": legacy_text,
                    "tool_calls": [],
                    "input_tokens": 250,
                    "output_tokens": 100,
                }
            ]
        )
        strategy = _make_single_agent_strategy(use_new_runner=False)
        output = SingleAgentStrategy().run(FakeTarget(), provider, ToolRegistry(), strategy)
        return output, provider

    def test_parity_finding_count(self) -> None:
        findings = [self._FINDING_A, self._FINDING_B]
        new_output, _ = self._run_new_runner(findings)
        legacy_output, _ = self._run_legacy_runner(findings)

        assert len(new_output.findings) == len(legacy_output.findings), (
            f"Finding count mismatch: new={len(new_output.findings)}, "
            f"legacy={len(legacy_output.findings)}"
        )

    def test_parity_file_vuln_pairs(self) -> None:
        findings = [self._FINDING_A, self._FINDING_B]
        new_output, _ = self._run_new_runner(findings)
        legacy_output, _ = self._run_legacy_runner(findings)

        def _key_set(output):
            return {(f.file_path, str(f.vuln_class)) for f in output.findings}

        assert _key_set(new_output) == _key_set(legacy_output), (
            f"(file_path, vuln_class) pairs differ:\n"
            f"  new={_key_set(new_output)}\n"
            f"  legacy={_key_set(legacy_output)}"
        )

    def test_parity_token_drift_within_5_percent(self) -> None:
        """Token usage within ±5% (smoke baseline calibration).

        Both runners are scripted with the same token counts so the
        delta should be 0% in Phase 2.  The bound is documented here as the
        calibration baseline that Phase 3 must not regress beyond ±20%.
        """
        findings = [self._FINDING_A]
        new_output, new_provider = self._run_new_runner(findings)
        legacy_output, legacy_provider = self._run_legacy_runner(findings)

        def _total_tokens(provider: ScriptedLiteLLMProvider) -> int:
            return sum(t.input_tokens + t.output_tokens for t in provider.token_log)

        new_tokens = _total_tokens(new_provider)
        legacy_tokens = _total_tokens(legacy_provider)

        # Both scripted at 250 in + 100 out = 350 total; drift must be ≤5%
        if legacy_tokens > 0:
            drift = abs(new_tokens - legacy_tokens) / legacy_tokens
            assert drift <= 0.05, (
                f"Token drift {drift:.1%} exceeds ±5% bound "
                f"(new={new_tokens}, legacy={legacy_tokens})"
            )

    def test_parity_single_finding(self) -> None:
        """Single-finding case: file_path and vuln_class match exactly."""
        findings = [self._FINDING_A]
        new_output, _ = self._run_new_runner(findings)
        legacy_output, _ = self._run_legacy_runner(findings)

        assert len(new_output.findings) == 1
        assert len(legacy_output.findings) == 1
        assert new_output.findings[0].file_path == legacy_output.findings[0].file_path
        assert new_output.findings[0].vuln_class == legacy_output.findings[0].vuln_class


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestRunStrategyErrors:
    """Test RunnerError propagation."""

    def test_unexpected_model_behavior_raises_runner_error(self) -> None:
        """UnexpectedModelBehavior from pydantic-ai becomes RunnerError."""
        import unittest.mock as mock

        from pydantic_ai.exceptions import UnexpectedModelBehavior

        # Provider that raises UnexpectedModelBehavior by returning no output
        # tool call — pydantic-ai will raise UnexpectedModelBehavior after
        # exhausting retries.  We simulate this more directly by patching.
        strategy = _make_single_agent_strategy(use_new_runner=True)
        provider_stub = ScriptedLiteLLMProvider(responses=[])
        target = FakeTarget()

        # Patch agent.run_sync to raise UnexpectedModelBehavior directly
        with mock.patch(
            "sec_review_framework.strategies.runner.Agent.run_sync",
            side_effect=UnexpectedModelBehavior("forced test error"),
        ):
            with pytest.raises(RunnerError, match="unexpected response"):
                run_strategy(strategy, target, provider_stub, ToolRegistry())
