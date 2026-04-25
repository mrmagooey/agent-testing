"""Phase 3b parity test: builtin_v2.sast_first (new runner) vs builtin.sast_first (legacy).

Both runners are exercised with identical scripted inputs via ScriptedLiteLLMProvider.
Parity criteria (per plan_subagents_pydantic_ai.md § 7):

1. Set equality on (file_path, vuln_class) — exact match for identical inputs.
2. Token-cost drift bound: ≤20% of total tokens.
3. Findings count within ±10% AND set equality.
4. When run_semgrep returns an error string (binary missing), the strategy must
   produce empty findings, not crash.

Architecture of the new runner for sast_first:
- Parent calls run_semgrep tool
- If error string returned, parent emits empty findings
- Parent groups semgrep results by file and calls invoke_subagent_batch
  with role="triage_agent"
- Each triage_agent subagent returns list[Finding] for one file
- Parent aggregates

Skipped cleanly when the ``agent`` extra (pydantic-ai) is not installed.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

pydantic_ai = pytest.importorskip("pydantic_ai")

from sec_review_framework.agent.subagent import SubagentDeps  # noqa: E402
from sec_review_framework.data.findings import Finding  # noqa: E402
from sec_review_framework.data.strategy_bundle import (  # noqa: E402
    OrchestrationShape,
    StrategyBundleDefault,
    UserStrategy,
)
from sec_review_framework.models.base import Message, ModelResponse, ToolDefinition  # noqa: E402
from sec_review_framework.models.litellm_provider import LiteLLMProvider  # noqa: E402
from sec_review_framework.strategies.runner import run_strategy  # noqa: E402
from sec_review_framework.strategies.sast_first import SASTFirstStrategy  # noqa: E402
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
            input_tokens=data.get("input_tokens", 200),
            output_tokens=data.get("output_tokens", 80),
            model_id=self.model_name,
            raw={},
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FINDING_SQLI: dict[str, Any] = {
    "id": str(uuid.uuid4()),
    "file_path": "app/views.py",
    "line_start": 55,
    "line_end": 55,
    "vuln_class": "sqli",
    "cwe_ids": ["CWE-89"],
    "severity": "critical",
    "title": "SQL injection in search endpoint",
    "description": "Unsanitised user input is concatenated into SQL.",
    "recommendation": "Use parameterised queries.",
    "confidence": 0.93,
    "raw_llm_output": "",
    "produced_by": "test",
    "experiment_id": "parity_sf_001",
}

_FINDING_XSS: dict[str, Any] = {
    "id": str(uuid.uuid4()),
    "file_path": "app/templates.py",
    "line_start": 12,
    "line_end": 14,
    "vuln_class": "xss",
    "cwe_ids": ["CWE-79"],
    "severity": "high",
    "title": "Reflected XSS in template rendering",
    "description": "User input reflected without escaping.",
    "recommendation": "Escape output.",
    "confidence": 0.88,
    "raw_llm_output": "",
    "produced_by": "test",
    "experiment_id": "parity_sf_001",
}


class FakeTarget:
    """Minimal target stub sufficient for sast_first testing."""

    def __init__(self, repo_path: Path | None = None) -> None:
        self.repo_path = repo_path or Path("/tmp/fake_repo")

    def get_file_tree(self) -> str:
        return "app/\n  views.py\n  templates.py\n"

    def list_source_files(self) -> list[str]:
        return ["app/views.py", "app/templates.py"]

    def read_file(self, path: str) -> str:
        return f"# content of {path}\n"


# ---------------------------------------------------------------------------
# Mock SubagentDeps
# ---------------------------------------------------------------------------


def _make_mock_deps(
    per_file_findings: dict[str, list[dict[str, Any]]] | None = None,
) -> SubagentDeps:
    """Build a SubagentDeps where triage_agent is a mock subagent."""

    mock_subagent_strategy = UserStrategy(
        id="builtin_v2.triage_agent",
        name="Triage Agent subagent (mock)",
        parent_strategy_id="builtin_v2.sast_first",
        orchestration_shape=OrchestrationShape.SINGLE_AGENT,
        default=StrategyBundleDefault(
            system_prompt="Mock triage agent",
            user_prompt_template="Triage {file_path}. Findings: {sast_findings}. {finding_output_format}",
            model_id="fake/test",
            tools=frozenset(),
            verification="none",
            max_turns=5,
            tool_extensions=frozenset(),
        ),
        overrides=[],
        created_at=datetime(2026, 1, 1),
        is_builtin=False,
    )

    return SubagentDeps(
        depth=0,
        max_depth=3,
        invocations=0,
        max_invocations=100,
        max_batch_size=32,
        available_roles={"builtin_v2.triage_agent"},
        subagent_strategies={"builtin_v2.triage_agent": mock_subagent_strategy},
        tool_registry=ToolRegistry(),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _legacy_text(findings: list[dict]) -> str:
    """Render findings as the ```json fenced block that FindingParser expects."""
    return "Here are my findings:\n\n```json\n" + json.dumps(findings) + "\n```\n"


def _key_set(output: Any) -> set[tuple[str, str]]:
    return {
        (f.file_path, f.vuln_class.value if hasattr(f.vuln_class, "value") else str(f.vuln_class))
        for f in output.findings
    }


def _total_tokens(provider: ScriptedLiteLLMProvider) -> int:
    return sum(r.input_tokens + r.output_tokens for r in provider.token_log)


# ---------------------------------------------------------------------------
# Tests: builtin_v2.sast_first registration
# ---------------------------------------------------------------------------


class TestSastFirstV2Registry:
    """Verify the new registry entries exist and have expected structure."""

    def test_registry_has_sast_first_v2_entry(self) -> None:
        registry = load_default_registry()
        strategy = registry.get("builtin_v2.sast_first")
        assert strategy.use_new_runner is True

    def test_sast_first_v2_has_triage_agent_subagent(self) -> None:
        registry = load_default_registry()
        strategy = registry.get("builtin_v2.sast_first")
        assert "builtin_v2.triage_agent" in strategy.default.subagents

    def test_sast_first_v2_orchestration_shape(self) -> None:
        registry = load_default_registry()
        strategy = registry.get("builtin_v2.sast_first")
        assert strategy.orchestration_shape == OrchestrationShape.SAST_FIRST

    def test_triage_agent_subagent_registered(self) -> None:
        registry = load_default_registry()
        subagent = registry.get("builtin_v2.triage_agent")
        assert subagent.use_new_runner is True
        assert subagent.parent_strategy_id == "builtin_v2.sast_first"

    def test_sast_first_v2_has_non_empty_prompts(self) -> None:
        registry = load_default_registry()
        strategy = registry.get("builtin_v2.sast_first")
        assert strategy.default.system_prompt
        assert strategy.default.user_prompt_template

    def test_triage_agent_has_non_empty_prompts(self) -> None:
        registry = load_default_registry()
        subagent = registry.get("builtin_v2.triage_agent")
        assert subagent.default.system_prompt
        assert subagent.default.user_prompt_template

    def test_sast_first_v1_unchanged(self) -> None:
        """builtin.sast_first must not have use_new_runner=True."""
        registry = load_default_registry()
        v1 = registry.get("builtin.sast_first")
        assert v1.use_new_runner is False


# ---------------------------------------------------------------------------
# Tests: sast_first v2 — semgrep binary missing (error-string handling)
# ---------------------------------------------------------------------------


class TestSastFirstV2SemgrepMissing:
    """When run_semgrep returns an error string, parent must return empty findings."""

    def _run_v2_with_semgrep_error(self, error_response: str) -> Any:
        """Script the parent to call run_semgrep (tool call), receive error, then emit empty results."""
        registry = load_default_registry()
        strategy = registry.get("builtin_v2.sast_first")

        # The parent agent will:
        # Turn 1: call run_semgrep tool → receives error string
        # Turn 2: return empty findings (final_result with empty list)
        from sec_review_framework.tools.semgrep import SemgrepTool

        # Build a ToolRegistry with a SemgrepTool that returns error
        tool_registry = ToolRegistry()
        semgrep_tool = SemgrepTool(repo_path=Path("/tmp/fake_repo"))
        tool_registry.tools["run_semgrep"] = semgrep_tool

        # Script: first turn calls run_semgrep via tool, gets error;
        # second turn returns empty findings
        provider = ScriptedLiteLLMProvider(
            responses=[
                # Turn 1: parent calls run_semgrep tool
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "run_semgrep",
                            "id": "tc_semgrep_1",
                            "input": {"path": "."},
                        }
                    ],
                    "input_tokens": 150,
                    "output_tokens": 30,
                },
                # Turn 2: parent sees error, returns empty findings
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "final_result",
                            "id": "tc_final_1",
                            "input": {"response": []},
                        }
                    ],
                    "input_tokens": 200,
                    "output_tokens": 10,
                },
            ]
        )

        mock_deps = _make_mock_deps()

        with patch("subprocess.run", side_effect=FileNotFoundError):
            output = run_strategy(
                strategy,
                FakeTarget(),
                provider,
                tool_registry,
                deps_factory=lambda: mock_deps,
            )
        return output

    def test_binary_missing_returns_empty_findings(self) -> None:
        """When semgrep binary is absent, strategy must return [] findings, not crash."""
        output = self._run_v2_with_semgrep_error("Error: semgrep binary not found on PATH")
        assert output.findings == []

    def test_binary_missing_does_not_raise(self) -> None:
        """When semgrep binary is absent, run_strategy must not raise."""
        try:
            output = self._run_v2_with_semgrep_error("Error: semgrep binary not found on PATH")
            assert output is not None
        except Exception as exc:
            pytest.fail(f"run_strategy raised an exception when semgrep is missing: {exc!r}")

    def test_binary_missing_returns_strategy_output(self) -> None:
        """Output must be a StrategyOutput instance even when semgrep is missing."""
        from sec_review_framework.data.findings import StrategyOutput

        output = self._run_v2_with_semgrep_error("Error: semgrep binary not found on PATH")
        assert isinstance(output, StrategyOutput)


# ---------------------------------------------------------------------------
# Tests: sast_first v2 new runner — scripted end-to-end
# ---------------------------------------------------------------------------


class TestSastFirstV2Runner:
    """Test the sast_first v2 strategy end-to-end with mocked subagent dispatch."""

    def _run_v2_with_findings(self, parent_findings: list[dict[str, Any]]) -> Any:
        """Script parent to return *parent_findings* directly as final_result."""
        registry = load_default_registry()
        strategy = registry.get("builtin_v2.sast_first")

        provider = ScriptedLiteLLMProvider(
            responses=[
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "final_result",
                            "id": "tc_sf_v2_1",
                            "input": {"response": parent_findings},
                        }
                    ],
                    "input_tokens": 200,
                    "output_tokens": 80,
                }
            ]
        )

        mock_deps = _make_mock_deps()

        output = run_strategy(
            strategy,
            FakeTarget(),
            provider,
            ToolRegistry(),
            deps_factory=lambda: mock_deps,
        )
        return output

    def test_returns_strategy_output(self) -> None:
        from sec_review_framework.data.findings import StrategyOutput

        output = self._run_v2_with_findings([])
        assert isinstance(output, StrategyOutput)

    def test_returns_findings_from_parent(self) -> None:
        output = self._run_v2_with_findings([_FINDING_SQLI, _FINDING_XSS])
        assert len(output.findings) == 2

    def test_finding_keys_match_expected(self) -> None:
        output = self._run_v2_with_findings([_FINDING_SQLI, _FINDING_XSS])
        keys = _key_set(output)
        assert ("app/views.py", "sqli") in keys
        assert ("app/templates.py", "xss") in keys

    def test_empty_findings_returns_empty(self) -> None:
        output = self._run_v2_with_findings([])
        assert output.findings == []

    def test_findings_are_finding_instances(self) -> None:
        output = self._run_v2_with_findings([_FINDING_SQLI])
        for f in output.findings:
            assert isinstance(f, Finding)

    def test_findings_have_stamped_ids(self) -> None:
        output = self._run_v2_with_findings([_FINDING_SQLI])
        for f in output.findings:
            assert f.id

    def test_findings_have_stamped_produced_by(self) -> None:
        output = self._run_v2_with_findings([_FINDING_SQLI])
        for f in output.findings:
            assert f.produced_by


# ---------------------------------------------------------------------------
# Tests: parity with legacy SASTFirstStrategy
# ---------------------------------------------------------------------------


class TestSastFirstParityV2:
    """Phase 3b parity: builtin_v2.sast_first vs builtin.sast_first (legacy)."""

    def _run_legacy_sast_first(
        self, all_findings: list[dict], tmp_path: Path
    ) -> tuple[Any, ScriptedLiteLLMProvider]:
        """Run builtin.sast_first via SASTFirstStrategy.run() with scripted findings."""
        import json as _json

        registry = load_default_registry()
        strategy = registry.get("builtin.sast_first")
        target = FakeTarget(repo_path=tmp_path)

        # Build a mock semgrep result so the legacy strategy dispatches subagents
        views_findings = [f for f in all_findings if f["file_path"] == "app/views.py"]
        templates_findings = [f for f in all_findings if f["file_path"] == "app/templates.py"]

        # Create fake semgrep JSON output
        semgrep_results = []
        for f in all_findings:
            semgrep_results.append({
                "path": f["file_path"],
                "start": {"line": f.get("line_start", 1), "col": 1},
                "end": {"line": f.get("line_end", 1), "col": 10},
                "check_id": f"python.{f['vuln_class']}",
                "extra": {
                    "message": f["description"],
                    "severity": f["severity"].upper(),
                },
            })

        semgrep_json = _json.dumps({"results": semgrep_results, "errors": []})

        from unittest.mock import MagicMock

        mock_proc = MagicMock()
        mock_proc.stdout = semgrep_json
        mock_proc.stderr = ""
        mock_proc.returncode = 0

        # Script responses for each flagged file
        responses = []
        if views_findings:
            responses.append({
                "content": _legacy_text(views_findings),
                "tool_calls": [],
                "input_tokens": 200,
                "output_tokens": 80,
            })
        if templates_findings:
            responses.append({
                "content": _legacy_text(templates_findings),
                "tool_calls": [],
                "input_tokens": 200,
                "output_tokens": 80,
            })

        provider = ScriptedLiteLLMProvider(responses=responses)

        with patch("subprocess.run", return_value=mock_proc):
            output = SASTFirstStrategy().run(target, provider, ToolRegistry(), strategy)
        return output, provider

    def _run_v2_sast_first(
        self, all_findings: list[dict]
    ) -> tuple[Any, ScriptedLiteLLMProvider]:
        """Run builtin_v2.sast_first via run_strategy() with scripted findings."""
        registry = load_default_registry()
        strategy = registry.get("builtin_v2.sast_first")

        provider = ScriptedLiteLLMProvider(
            responses=[
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "final_result",
                            "id": "tc_sf_par",
                            "input": {"response": all_findings},
                        }
                    ],
                    "input_tokens": 200,
                    "output_tokens": 80,
                }
            ]
        )

        mock_deps = _make_mock_deps()
        output = run_strategy(
            strategy,
            FakeTarget(),
            provider,
            ToolRegistry(),
            deps_factory=lambda: mock_deps,
        )
        return output, provider

    def test_parity_set_equality_two_files(self, tmp_path: Path) -> None:
        """(file_path, vuln_class) sets must be identical for both runners."""
        all_findings = [_FINDING_SQLI, _FINDING_XSS]

        legacy_output, _ = self._run_legacy_sast_first(all_findings, tmp_path)
        v2_output, _ = self._run_v2_sast_first(all_findings)

        legacy_keys = _key_set(legacy_output)
        v2_keys = _key_set(v2_output)

        assert v2_keys == legacy_keys, (
            f"(file_path, vuln_class) pair mismatch:\n"
            f"  v2={v2_keys}\n"
            f"  legacy={legacy_keys}"
        )

    def test_parity_set_equality_empty_findings(self, tmp_path: Path) -> None:
        """Empty findings: both runners must return empty output."""
        legacy_output, _ = self._run_legacy_sast_first([], tmp_path)
        v2_output, _ = self._run_v2_sast_first([])

        assert _key_set(legacy_output) == _key_set(v2_output) == set()

    def test_parity_findings_count_within_10_percent(self, tmp_path: Path) -> None:
        all_findings = [_FINDING_SQLI, _FINDING_XSS]
        legacy_output, _ = self._run_legacy_sast_first(all_findings, tmp_path)
        v2_output, _ = self._run_v2_sast_first(all_findings)

        legacy_count = len(legacy_output.findings)
        v2_count = len(v2_output.findings)

        if legacy_count > 0:
            drift = abs(v2_count - legacy_count) / legacy_count
            assert drift <= 0.10, (
                f"Findings count drift {drift:.1%} exceeds ±10% "
                f"(v2={v2_count}, legacy={legacy_count})"
            )
        else:
            assert v2_count == 0

    def test_parity_token_drift_within_20_percent(self, tmp_path: Path) -> None:
        all_findings = [_FINDING_SQLI]
        _, v2_provider = self._run_v2_sast_first(all_findings)
        _, legacy_provider = self._run_legacy_sast_first(all_findings, tmp_path)

        v2_tokens = _total_tokens(v2_provider)
        legacy_tokens = _total_tokens(legacy_provider)

        if legacy_tokens > 0:
            drift = abs(v2_tokens - legacy_tokens) / legacy_tokens
            assert drift <= 0.20, (
                f"Token drift {drift:.1%} exceeds ±20% bound "
                f"(v2={v2_tokens}, legacy={legacy_tokens})"
            )

    def test_v2_strategy_uses_new_runner(self) -> None:
        registry = load_default_registry()
        v2 = registry.get("builtin_v2.sast_first")
        assert v2.use_new_runner is True

    def test_v1_strategy_uses_legacy_runner(self) -> None:
        registry = load_default_registry()
        v1 = registry.get("builtin.sast_first")
        assert v1.use_new_runner is False
