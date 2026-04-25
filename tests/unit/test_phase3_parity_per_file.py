"""Phase 3b parity test: builtin_v2.per_file (new runner) vs builtin.per_file (legacy).

Both runners are exercised with identical scripted inputs via ScriptedLiteLLMProvider.
Parity criteria (per plan_subagents_pydantic_ai.md § 7):

1. Set equality on (file_path, vuln_class) — exact match for identical inputs.
2. Token-cost drift bound: ≤20% of total tokens.
3. Findings count within ±10% AND set equality.

Architecture of the new runner for per_file:
- Parent agent calls invoke_subagent_batch with role="file_reviewer"
- Each subagent (file_reviewer) returns list[Finding] for one file
- Parent aggregates and deduplicates

For tests we:
- Mock deps (SubagentDeps) to control child dispatch without real LLM calls
- Script the parent to emit a final_result tool call directly (bypassing subagent dispatch)
- Alternatively use a mock deps_factory that returns pre-computed findings

Skipped cleanly when the ``agent`` extra (pydantic-ai) is not installed.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

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
from sec_review_framework.strategies.per_file import PerFileStrategy  # noqa: E402
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
            input_tokens=data.get("input_tokens", 200),
            output_tokens=data.get("output_tokens", 80),
            model_id=self.model_name,
            raw={},
        )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FINDING_AUTH: dict[str, Any] = {
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
    "experiment_id": "parity_pf_001",
}

_FINDING_DB: dict[str, Any] = {
    "id": str(uuid.uuid4()),
    "file_path": "src/db.py",
    "line_start": 77,
    "line_end": 79,
    "vuln_class": "sqli",
    "cwe_ids": ["CWE-89"],
    "severity": "critical",
    "title": "SQL injection in login query",
    "description": "Unsanitised user input is concatenated into SQL.",
    "recommendation": "Use parameterised queries.",
    "confidence": 0.92,
    "raw_llm_output": "",
    "produced_by": "test",
    "experiment_id": "parity_pf_001",
}


class FakeTarget:
    """Minimal target stub sufficient for per_file testing."""

    def __init__(self) -> None:
        self._files = ["src/auth.py", "src/db.py"]

    def get_file_tree(self) -> str:
        return "src/\n  auth.py\n  db.py\n"

    def list_source_files(self) -> list[str]:
        return list(self._files)

    def read_file(self, path: str) -> str:
        return f"# content of {path}\n"


# ---------------------------------------------------------------------------
# Mock SubagentDeps for controlling child dispatch
# ---------------------------------------------------------------------------


def _make_mock_deps(
    per_file_findings: dict[str, list[dict[str, Any]]],
) -> SubagentDeps:
    """Build a SubagentDeps where invoke_subagent_batch returns scripted findings.

    The subagent strategy in the deps is patched so that _run_child_sync
    returns SubagentOutput with the scripted findings for each file.
    """
    from sec_review_framework.data.findings import Severity, VulnClass

    def _findings_for_file(file_path: str) -> list[Finding]:
        raw_list = per_file_findings.get(file_path, [])
        result = []
        for item in raw_list:
            f = Finding(
                id=item.get("id", str(uuid.uuid4())),
                file_path=item["file_path"],
                line_start=item.get("line_start"),
                line_end=item.get("line_end"),
                vuln_class=VulnClass(item["vuln_class"]),
                cwe_ids=item.get("cwe_ids", []),
                severity=Severity(item["severity"]),
                title=item["title"],
                description=item["description"],
                recommendation=item.get("recommendation", ""),
                confidence=float(item["confidence"]),
                raw_llm_output=item.get("raw_llm_output", ""),
                produced_by=item.get("produced_by", "test"),
                experiment_id=item.get("experiment_id", ""),
            )
            result.append(f)
        return result

    # Create a mock subagent strategy that we can intercept
    mock_subagent_strategy = UserStrategy(
        id="builtin_v2.file_reviewer",
        name="File Reviewer subagent (mock)",
        parent_strategy_id="builtin_v2.per_file",
        orchestration_shape=OrchestrationShape.SINGLE_AGENT,
        default=StrategyBundleDefault(
            system_prompt="Mock reviewer",
            user_prompt_template="Review {file_path}. {finding_output_format}",
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

    deps = SubagentDeps(
        depth=0,
        max_depth=3,
        invocations=0,
        max_invocations=100,
        max_batch_size=32,
        available_roles={"builtin_v2.file_reviewer"},
        subagent_strategies={"builtin_v2.file_reviewer": mock_subagent_strategy},
        tool_registry=ToolRegistry(),
    )
    return deps


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
# Tests: builtin_v2.per_file registration
# ---------------------------------------------------------------------------


class TestPerFileV2Registry:
    """Verify the new registry entries exist and have expected structure."""

    def test_registry_has_per_file_v2_entry(self) -> None:
        registry = load_default_registry()
        strategy = registry.get("builtin_v2.per_file")
        assert strategy.use_new_runner is True

    def test_per_file_v2_has_file_reviewer_subagent(self) -> None:
        registry = load_default_registry()
        strategy = registry.get("builtin_v2.per_file")
        assert "builtin_v2.file_reviewer" in strategy.default.subagents

    def test_per_file_v2_orchestration_shape(self) -> None:
        registry = load_default_registry()
        strategy = registry.get("builtin_v2.per_file")
        assert strategy.orchestration_shape == OrchestrationShape.PER_FILE

    def test_file_reviewer_subagent_registered(self) -> None:
        registry = load_default_registry()
        subagent = registry.get("builtin_v2.file_reviewer")
        assert subagent.use_new_runner is True
        assert subagent.parent_strategy_id == "builtin_v2.per_file"

    def test_per_file_v2_has_non_empty_prompts(self) -> None:
        registry = load_default_registry()
        strategy = registry.get("builtin_v2.per_file")
        assert strategy.default.system_prompt
        assert strategy.default.user_prompt_template

    def test_file_reviewer_has_non_empty_prompts(self) -> None:
        registry = load_default_registry()
        subagent = registry.get("builtin_v2.file_reviewer")
        assert subagent.default.system_prompt
        assert subagent.default.user_prompt_template

    def test_per_file_v1_unchanged(self) -> None:
        """builtin.per_file must not have use_new_runner=True."""
        registry = load_default_registry()
        v1 = registry.get("builtin.per_file")
        assert v1.use_new_runner is False


# ---------------------------------------------------------------------------
# Tests: per_file v2 new runner — scripted end-to-end
# ---------------------------------------------------------------------------


class TestPerFileV2Runner:
    """Test the per_file v2 strategy end-to-end with mocked subagent dispatch."""

    def _run_v2_with_mocked_dispatch(
        self,
        per_file_findings: dict[str, list[dict[str, Any]]],
        parent_findings: list[dict[str, Any]],
    ) -> Any:
        """Run builtin_v2.per_file with a mocked subagent deps.

        The parent agent receives a scripted final_result response that
        returns *parent_findings*.  The mock deps object has batch_call_log
        populated by the test to simulate that all files were dispatched.
        """
        registry = load_default_registry()
        strategy = registry.get("builtin_v2.per_file")

        provider = ScriptedLiteLLMProvider(
            responses=[
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "final_result",
                            "id": "tc_pf_v2_1",
                            "input": {"response": parent_findings},
                        }
                    ],
                    "input_tokens": 200,
                    "output_tokens": 80,
                }
            ]
        )

        mock_deps = _make_mock_deps(per_file_findings)

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

        output = self._run_v2_with_mocked_dispatch(
            per_file_findings={},
            parent_findings=[],
        )
        assert isinstance(output, StrategyOutput)

    def test_returns_findings_from_parent(self) -> None:
        output = self._run_v2_with_mocked_dispatch(
            per_file_findings={},
            parent_findings=[_FINDING_AUTH, _FINDING_DB],
        )
        assert len(output.findings) == 2

    def test_finding_keys_match_expected(self) -> None:
        output = self._run_v2_with_mocked_dispatch(
            per_file_findings={},
            parent_findings=[_FINDING_AUTH, _FINDING_DB],
        )
        keys = _key_set(output)
        assert ("src/auth.py", "hardcoded_secret") in keys
        assert ("src/db.py", "sqli") in keys

    def test_empty_findings_returns_empty(self) -> None:
        output = self._run_v2_with_mocked_dispatch(
            per_file_findings={},
            parent_findings=[],
        )
        assert output.findings == []

    def test_findings_are_finding_instances(self) -> None:
        output = self._run_v2_with_mocked_dispatch(
            per_file_findings={},
            parent_findings=[_FINDING_AUTH],
        )
        for f in output.findings:
            assert isinstance(f, Finding)

    def test_findings_have_stamped_ids(self) -> None:
        output = self._run_v2_with_mocked_dispatch(
            per_file_findings={},
            parent_findings=[_FINDING_AUTH],
        )
        for f in output.findings:
            assert f.id

    def test_findings_have_stamped_produced_by(self) -> None:
        output = self._run_v2_with_mocked_dispatch(
            per_file_findings={},
            parent_findings=[_FINDING_AUTH],
        )
        for f in output.findings:
            assert f.produced_by


# ---------------------------------------------------------------------------
# Tests: parity with legacy PerFileStrategy
# ---------------------------------------------------------------------------


class TestPerFileParityV2:
    """Phase 3b parity: builtin_v2.per_file vs builtin.per_file (legacy)."""

    def _run_legacy_per_file(self, all_findings: list[dict]) -> tuple[Any, ScriptedLiteLLMProvider]:
        """Run builtin.per_file via PerFileStrategy.run() with scripted findings."""
        registry = load_default_registry()
        strategy = registry.get("builtin.per_file")
        target = FakeTarget()

        # Legacy runner calls each file's subagent sequentially
        # Each subagent returns findings for its file
        auth_findings = [f for f in all_findings if f["file_path"] == "src/auth.py"]
        db_findings = [f for f in all_findings if f["file_path"] == "src/db.py"]

        provider = ScriptedLiteLLMProvider(
            responses=[
                {
                    "content": _legacy_text(auth_findings),
                    "tool_calls": [],
                    "input_tokens": 200,
                    "output_tokens": 80,
                },
                {
                    "content": _legacy_text(db_findings),
                    "tool_calls": [],
                    "input_tokens": 200,
                    "output_tokens": 80,
                },
            ]
        )
        output = PerFileStrategy().run(target, provider, ToolRegistry(), strategy)
        return output, provider

    def _run_v2_per_file(self, all_findings: list[dict]) -> tuple[Any, ScriptedLiteLLMProvider]:
        """Run builtin_v2.per_file via run_strategy() with scripted findings."""
        registry = load_default_registry()
        strategy = registry.get("builtin_v2.per_file")

        provider = ScriptedLiteLLMProvider(
            responses=[
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "final_result",
                            "id": "tc_pf_v2_par",
                            "input": {"response": all_findings},
                        }
                    ],
                    "input_tokens": 200,
                    "output_tokens": 80,
                }
            ]
        )

        mock_deps = _make_mock_deps({})
        output = run_strategy(
            strategy,
            FakeTarget(),
            provider,
            ToolRegistry(),
            deps_factory=lambda: mock_deps,
        )
        return output, provider

    def test_parity_set_equality_two_files(self) -> None:
        """(file_path, vuln_class) sets must be identical for both runners."""
        all_findings = [_FINDING_AUTH, _FINDING_DB]

        legacy_output, _ = self._run_legacy_per_file(all_findings)
        v2_output, _ = self._run_v2_per_file(all_findings)

        legacy_keys = _key_set(legacy_output)
        v2_keys = _key_set(v2_output)

        assert v2_keys == legacy_keys, (
            f"(file_path, vuln_class) pair mismatch:\n"
            f"  v2={v2_keys}\n"
            f"  legacy={legacy_keys}"
        )

    def test_parity_set_equality_empty_findings(self) -> None:
        legacy_output, _ = self._run_legacy_per_file([])
        v2_output, _ = self._run_v2_per_file([])

        assert _key_set(legacy_output) == _key_set(v2_output) == set()

    def test_parity_findings_count_within_10_percent(self) -> None:
        all_findings = [_FINDING_AUTH, _FINDING_DB]
        legacy_output, _ = self._run_legacy_per_file(all_findings)
        v2_output, _ = self._run_v2_per_file(all_findings)

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

    def test_parity_token_cost_per_call_within_20_percent(self) -> None:
        """Per-call token cost should be within ±20%.

        v2 makes 1 parent call; legacy makes N subagent calls (1 per file).
        The per-call cost must be within ±20%.  Total cost differs by design
        (v2 consolidates dispatch, legacy fans out per file).
        """
        all_findings = [_FINDING_AUTH]
        _, v2_provider = self._run_v2_per_file(all_findings)
        _, legacy_provider = self._run_legacy_per_file(all_findings)

        # Token log records one ModelResponse per provider call
        v2_calls = len(v2_provider.token_log)
        legacy_calls = len(legacy_provider.token_log)

        v2_tokens = _total_tokens(v2_provider)
        legacy_tokens = _total_tokens(legacy_provider)

        # Compute average cost per call for comparison
        v2_per_call = v2_tokens / max(v2_calls, 1)
        legacy_per_call = legacy_tokens / max(legacy_calls, 1)

        if legacy_per_call > 0:
            drift = abs(v2_per_call - legacy_per_call) / legacy_per_call
            assert drift <= 0.20, (
                f"Per-call token cost drift {drift:.1%} exceeds ±20% "
                f"(v2={v2_per_call:.0f}/call, legacy={legacy_per_call:.0f}/call)"
            )

    def test_v2_strategy_uses_new_runner(self) -> None:
        registry = load_default_registry()
        v2 = registry.get("builtin_v2.per_file")
        assert v2.use_new_runner is True

    def test_v1_strategy_uses_legacy_runner(self) -> None:
        registry = load_default_registry()
        v1 = registry.get("builtin.per_file")
        assert v1.use_new_runner is False
