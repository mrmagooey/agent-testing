"""Phase 3c parity test: builtin_v2.per_vuln_class (new runner) vs builtin.per_vuln_class (legacy).

Both runners are exercised with identical scripted inputs via ScriptedLiteLLMProvider.
Parity criteria (per plan_subagents_pydantic_ai.md § 7):

1. Set equality on (file_path, vuln_class) — exact match for identical inputs.
2. Token-cost drift bound: ≤20% per call.
3. Dispatch completeness: when supervisor dispatches only 10 of 16 specialists,
   the programmatic fallback must invoke the remaining 6 so all 16 run.
4. Dispatch validator: deps.single_call_log records what was dispatched.

Architecture of the new runner for per_vuln_class:
- Parent agent calls invoke_subagent once per specialist role
- Each specialist (sqli_specialist, etc.) returns list[Finding]
- Parent aggregates all findings
- dispatch_fallback="programmatic" ensures missing specialists run regardless of
  supervisor LLM variance

Skipped cleanly when the ``agent`` extra (pydantic-ai) is not installed.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any
from unittest import mock

import pytest

pydantic_ai = pytest.importorskip("pydantic_ai")

from sec_review_framework.agent.subagent import SubagentDeps  # noqa: E402
from sec_review_framework.data.findings import Finding, Severity, VulnClass  # noqa: E402
from sec_review_framework.data.strategy_bundle import (  # noqa: E402
    OrchestrationShape,
    StrategyBundleDefault,
    UserStrategy,
)
from sec_review_framework.models.base import Message, ModelResponse, ToolDefinition  # noqa: E402
from sec_review_framework.models.litellm_provider import LiteLLMProvider  # noqa: E402
from sec_review_framework.strategies.per_vuln_class import PerVulnClassStrategy  # noqa: E402
from sec_review_framework.strategies.runner import _programmatic_fallback, run_strategy  # noqa: E402
from sec_review_framework.strategies.strategy_registry import load_default_registry  # noqa: E402
from sec_review_framework.tools.registry import ToolRegistry  # noqa: E402

# ---------------------------------------------------------------------------
# Scripted provider
# ---------------------------------------------------------------------------


class ScriptedLiteLLMProvider(LiteLLMProvider):
    """LiteLLMProvider returning pre-scripted responses for offline tests.

    Also maintains a ``token_log`` list of all :class:`ModelResponse` objects
    returned, so tests can inspect per-call token costs.
    """

    def __init__(self, responses: list[dict[str, Any]], model_name: str = "fake/test") -> None:
        super().__init__(model_name=model_name)
        self._responses: list[dict[str, Any]] = list(responses)
        self.token_log: list[ModelResponse] = []

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
        response = ModelResponse(
            content=data.get("content", ""),
            tool_calls=data.get("tool_calls", []),
            input_tokens=data.get("input_tokens", 200),
            output_tokens=data.get("output_tokens", 80),
            model_id=self.model_name,
            raw={},
        )
        self.token_log.append(response)
        return response


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# One finding per vuln class — 16 findings total
_ALL_VULN_CLASS_FINDINGS: list[dict[str, Any]] = [
    {
        "id": str(uuid.uuid4()),
        "file_path": f"src/module_{vc.value}.py",
        "line_start": 10 * i + 1,
        "line_end": 10 * i + 1,
        "vuln_class": vc.value,
        "cwe_ids": [],
        "severity": "high",
        "title": f"{vc.value} finding",
        "description": f"A {vc.value} vulnerability was found.",
        "recommendation": "Fix it.",
        "confidence": 0.9,
        "raw_llm_output": "",
        "produced_by": "test",
        "experiment_id": "parity_pvc_001",
    }
    for i, vc in enumerate(VulnClass)
]


class FakeTarget:
    """Minimal target stub for per_vuln_class testing."""

    def get_file_tree(self) -> str:
        return "src/\n" + "\n".join(
            f"  module_{vc.value}.py" for vc in VulnClass
        )

    def list_source_files(self) -> list[str]:
        return [f"src/module_{vc.value}.py" for vc in VulnClass]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_finding(vc: VulnClass, file_path: str = "app/main.py") -> Finding:
    return Finding(
        id=str(uuid.uuid4()),
        file_path=file_path,
        line_start=1,
        line_end=1,
        vuln_class=vc,
        cwe_ids=[],
        severity=Severity.HIGH,
        title=f"{vc.value} finding",
        description=f"A {vc.value} vulnerability.",
        recommendation="Fix it.",
        confidence=0.9,
        raw_llm_output="",
        produced_by="test",
        experiment_id="test",
    )


def _key_set(output: Any) -> set[tuple[str, str]]:
    return {
        (f.file_path, f.vuln_class.value if hasattr(f.vuln_class, "value") else str(f.vuln_class))
        for f in output.findings
    }


def _legacy_text(findings: list[dict]) -> str:
    """Render findings as the ```json fenced block that FindingParser expects."""
    return "Here are my findings:\n\n```json\n" + json.dumps(findings) + "\n```\n"


def _total_tokens(provider: ScriptedLiteLLMProvider) -> int:
    return sum(r.input_tokens + r.output_tokens for r in provider.token_log)


def _make_mock_specialist_strategy(vc: VulnClass, findings: list[Finding]) -> UserStrategy:
    """Build a minimal specialist strategy that returns pre-scripted findings."""
    # We patch _run_child_sync, so the actual prompts don't matter for mock tests
    return UserStrategy(
        id=f"builtin_v2.{vc.value}_specialist",
        name=f"{vc.value} specialist (mock)",
        parent_strategy_id="builtin_v2.per_vuln_class",
        orchestration_shape=OrchestrationShape.SINGLE_AGENT,
        default=StrategyBundleDefault(
            system_prompt=f"You are the {vc.value} specialist.",
            user_prompt_template="Scan for {vuln_class}. {finding_output_format}",
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


def _make_mock_deps(
    dispatched_roles: set[str],
    per_role_findings: dict[str, list[Finding]],
) -> SubagentDeps:
    """Build SubagentDeps with pre-populated specialist strategies.

    dispatched_roles: the roles that will appear in single_call_log (simulating
        that the supervisor already dispatched them).
    per_role_findings: findings returned when _run_child_sync is called for
        each role (used by the programmatic fallback).
    """
    all_roles = {f"builtin_v2.{vc.value}_specialist" for vc in VulnClass}
    subagent_strategies = {
        role: _make_mock_specialist_strategy(
            VulnClass(role.removeprefix("builtin_v2.").removesuffix("_specialist")),
            per_role_findings.get(role, []),
        )
        for role in all_roles
    }

    deps = SubagentDeps(
        depth=0,
        max_depth=3,
        invocations=0,
        max_invocations=100,
        max_batch_size=32,
        available_roles=all_roles,
        subagent_strategies=subagent_strategies,
        tool_registry=ToolRegistry(),
    )
    # Pre-populate single_call_log to simulate the supervisor having dispatched
    # some (but possibly not all) specialists
    deps.single_call_log = [
        (role, {"vuln_class": role.removeprefix("builtin_v2.").removesuffix("_specialist")})
        for role in dispatched_roles
    ]
    return deps


# ---------------------------------------------------------------------------
# Tests: builtin_v2.per_vuln_class registration
# ---------------------------------------------------------------------------


class TestPerVulnClassV2Registry:
    """Verify new registry entries exist with expected structure."""

    def test_registry_has_per_vuln_class_v2_entry(self) -> None:
        registry = load_default_registry()
        strategy = registry.get("builtin_v2.per_vuln_class")
        assert strategy.use_new_runner is True

    def test_per_vuln_class_v2_has_16_subagents(self) -> None:
        registry = load_default_registry()
        strategy = registry.get("builtin_v2.per_vuln_class")
        assert len(strategy.default.subagents) == len(VulnClass)

    def test_per_vuln_class_v2_orchestration_shape(self) -> None:
        registry = load_default_registry()
        strategy = registry.get("builtin_v2.per_vuln_class")
        assert strategy.orchestration_shape == OrchestrationShape.PER_VULN_CLASS

    def test_per_vuln_class_v2_dispatch_fallback_programmatic(self) -> None:
        registry = load_default_registry()
        strategy = registry.get("builtin_v2.per_vuln_class")
        assert strategy.default.dispatch_fallback == "programmatic"

    def test_all_16_specialists_registered(self) -> None:
        registry = load_default_registry()
        for vc in VulnClass:
            specialist_id = f"builtin_v2.{vc.value}_specialist"
            specialist = registry.get(specialist_id)
            assert specialist.use_new_runner is False
            assert specialist.parent_strategy_id == "builtin_v2.per_vuln_class"

    def test_per_vuln_class_v1_unchanged(self) -> None:
        """builtin.per_vuln_class must not have use_new_runner=True."""
        registry = load_default_registry()
        v1 = registry.get("builtin.per_vuln_class")
        assert v1.use_new_runner is False

    def test_per_vuln_class_v2_has_non_empty_prompts(self) -> None:
        registry = load_default_registry()
        strategy = registry.get("builtin_v2.per_vuln_class")
        assert strategy.default.system_prompt
        assert strategy.default.user_prompt_template

    def test_specialists_have_non_empty_prompts(self) -> None:
        registry = load_default_registry()
        for vc in VulnClass:
            specialist_id = f"builtin_v2.{vc.value}_specialist"
            specialist = registry.get(specialist_id)
            assert specialist.default.system_prompt, f"{specialist_id} has empty system_prompt"
            assert specialist.default.user_prompt_template, (
                f"{specialist_id} has empty user_prompt_template"
            )

    def test_specialist_ids_match_vuln_class_enum_exactly(self) -> None:
        """No typos: every VulnClass value produces a valid specialist ID."""
        registry = load_default_registry()
        expected_ids = {f"builtin_v2.{vc.value}_specialist" for vc in VulnClass}
        actual_specialist_ids = {
            s.id for s in registry.list_all()
            if s.id.startswith("builtin_v2.") and s.id.endswith("_specialist")
        }
        assert actual_specialist_ids == expected_ids


# ---------------------------------------------------------------------------
# Tests: per_vuln_class v2 new runner — scripted end-to-end
# ---------------------------------------------------------------------------


class TestPerVulnClassV2Runner:
    """Test the per_vuln_class v2 strategy end-to-end with mocked subagent dispatch."""

    def _run_v2_with_mocked_dispatch(
        self,
        parent_findings: list[dict[str, Any]],
        pre_dispatched_roles: set[str] | None = None,
    ) -> Any:
        """Run builtin_v2.per_vuln_class with a mocked subagent deps.

        The parent agent receives a scripted final_result response that
        returns *parent_findings*.  The mock deps object has single_call_log
        pre-populated for *pre_dispatched_roles*.
        """
        registry = load_default_registry()
        strategy = registry.get("builtin_v2.per_vuln_class")

        provider = ScriptedLiteLLMProvider(
            responses=[
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "final_result",
                            "id": "tc_pvc_v2_1",
                            "input": {"response": parent_findings},
                        }
                    ],
                    "input_tokens": 200,
                    "output_tokens": 80,
                }
            ]
        )

        if pre_dispatched_roles is None:
            pre_dispatched_roles = {f"builtin_v2.{vc.value}_specialist" for vc in VulnClass}

        mock_deps = _make_mock_deps(
            dispatched_roles=pre_dispatched_roles,
            per_role_findings={},
        )

        # Build the expected_dispatch list (all 16 vuln classes)
        expected_dispatch = [{"vuln_class": vc.value} for vc in VulnClass]

        output = run_strategy(
            strategy,
            FakeTarget(),
            provider,
            ToolRegistry(),
            deps_factory=lambda: mock_deps,
            expected_dispatch=expected_dispatch,
            dispatch_match_key="vuln_class",
        )
        return output

    def test_returns_strategy_output(self) -> None:
        from sec_review_framework.data.findings import StrategyOutput

        output = self._run_v2_with_mocked_dispatch(parent_findings=[])
        assert isinstance(output, StrategyOutput)

    def test_returns_findings_from_parent(self) -> None:
        output = self._run_v2_with_mocked_dispatch(
            parent_findings=_ALL_VULN_CLASS_FINDINGS,
        )
        assert len(output.findings) == len(VulnClass)

    def test_empty_findings_returns_empty(self) -> None:
        output = self._run_v2_with_mocked_dispatch(parent_findings=[])
        assert output.findings == []

    def test_findings_are_finding_instances(self) -> None:
        output = self._run_v2_with_mocked_dispatch(
            parent_findings=[_ALL_VULN_CLASS_FINDINGS[0]],
        )
        for f in output.findings:
            assert isinstance(f, Finding)

    def test_findings_have_stamped_ids(self) -> None:
        output = self._run_v2_with_mocked_dispatch(
            parent_findings=[_ALL_VULN_CLASS_FINDINGS[0]],
        )
        for f in output.findings:
            assert f.id

    def test_findings_have_stamped_produced_by(self) -> None:
        output = self._run_v2_with_mocked_dispatch(
            parent_findings=[_ALL_VULN_CLASS_FINDINGS[0]],
        )
        for f in output.findings:
            assert f.produced_by


# ---------------------------------------------------------------------------
# Tests: Dispatch completeness — programmatic fallback
# ---------------------------------------------------------------------------


class TestDispatchCompleteness:
    """Phase 3c: programmatic fallback must invoke missing specialists directly."""

    def _make_deps_with_partial_dispatch(
        self,
        dispatched_vc_values: list[str],
        findings_per_vc: dict[str, list[Finding]] | None = None,
    ) -> SubagentDeps:
        """Build deps where only *dispatched_vc_values* appear in single_call_log."""
        dispatched_roles = {f"builtin_v2.{v}_specialist" for v in dispatched_vc_values}
        per_role_findings: dict[str, list[Finding]] = {}
        if findings_per_vc:
            for vc_val, fs in findings_per_vc.items():
                per_role_findings[f"builtin_v2.{vc_val}_specialist"] = fs
        return _make_mock_deps(
            dispatched_roles=dispatched_roles,
            per_role_findings=per_role_findings,
        )

    def test_programmatic_fallback_invokes_missing_specialists(self) -> None:
        """When supervisor dispatches only 10/16, fallback invokes the remaining 6."""
        all_vc_values = [vc.value for vc in VulnClass]
        dispatched_10 = all_vc_values[:10]
        missing_6 = all_vc_values[10:]

        # Build per-role findings for the missing 6
        per_vc_findings = {
            vc_val: [_make_finding(VulnClass(vc_val), f"src/module_{vc_val}.py")]
            for vc_val in missing_6
        }

        deps = self._make_deps_with_partial_dispatch(
            dispatched_vc_values=dispatched_10,
            findings_per_vc=per_vc_findings,
        )

        # Build missing_inputs list (same format as _validate_dispatch would produce)
        missing_inputs = [{"vuln_class": vc_val} for vc_val in missing_6]

        # Patch _run_child_sync to return pre-scripted findings
        from sec_review_framework.agent.subagent import SubagentOutput

        def _mock_run_child_sync(strategy, input_data, parent_deps):
            vc_val = input_data.get("vuln_class", "")
            role = f"builtin_v2.{vc_val}_specialist"
            findings = per_vc_findings.get(vc_val, [])
            return SubagentOutput(role=role, output=findings, usage={})

        with mock.patch(
            "sec_review_framework.strategies.runner._run_child_sync",
            side_effect=_mock_run_child_sync,
        ):
            extra_findings = _programmatic_fallback(
                strategy_id="builtin_v2.per_vuln_class",
                missing_inputs=missing_inputs,
                dispatch_match_key="vuln_class",
                deps=deps,
            )

        # Should have exactly 6 findings — one from each missing specialist
        assert len(extra_findings) == 6, (
            f"Expected 6 findings from 6 missing specialists, got {len(extra_findings)}"
        )
        # Verify the vc values match the missing specialists
        returned_vc_values = {f.vuln_class.value for f in extra_findings}
        assert returned_vc_values == set(missing_6), (
            f"Expected vuln classes {set(missing_6)}, got {returned_vc_values}"
        )

    def test_programmatic_fallback_full_run_all_16_complete(self) -> None:
        """Full run: supervisor dispatches 10/16, fallback fills the remaining 6.

        Total findings from the run must equal the all-16 case.
        """
        all_vc_values = [vc.value for vc in VulnClass]
        dispatched_10 = all_vc_values[:10]
        missing_6 = all_vc_values[10:]

        # Parent returns 10 findings (one per dispatched specialist)
        parent_findings = [
            {
                "id": str(uuid.uuid4()),
                "file_path": f"src/module_{vc_val}.py",
                "line_start": 1,
                "line_end": 1,
                "vuln_class": vc_val,
                "cwe_ids": [],
                "severity": "high",
                "title": f"{vc_val} finding",
                "description": f"A {vc_val} finding.",
                "recommendation": "Fix it.",
                "confidence": 0.9,
                "raw_llm_output": "",
                "produced_by": "test",
                "experiment_id": "test",
            }
            for vc_val in dispatched_10
        ]

        # Fallback specialists produce 1 finding each
        per_vc_findings = {
            vc_val: [_make_finding(VulnClass(vc_val), f"src/module_{vc_val}.py")]
            for vc_val in missing_6
        }

        registry = load_default_registry()
        strategy = registry.get("builtin_v2.per_vuln_class")

        provider = ScriptedLiteLLMProvider(
            responses=[
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "final_result",
                            "id": "tc_pvc_full",
                            "input": {"response": parent_findings},
                        }
                    ],
                    "input_tokens": 300,
                    "output_tokens": 100,
                }
            ]
        )

        # Build deps with only 10 specialists in single_call_log
        mock_deps_10 = self._make_deps_with_partial_dispatch(
            dispatched_vc_values=dispatched_10,
            findings_per_vc=per_vc_findings,
        )

        expected_dispatch = [{"vuln_class": vc.value} for vc in VulnClass]

        from sec_review_framework.agent.subagent import SubagentOutput

        def _mock_run_child_sync(strat, input_data, parent_deps):
            vc_val = input_data.get("vuln_class", "")
            role = f"builtin_v2.{vc_val}_specialist"
            findings = per_vc_findings.get(vc_val, [])
            return SubagentOutput(role=role, output=findings, usage={})

        with mock.patch(
            "sec_review_framework.strategies.runner._run_child_sync",
            side_effect=_mock_run_child_sync,
        ):
            output = run_strategy(
                strategy,
                FakeTarget(),
                provider,
                ToolRegistry(),
                deps_factory=lambda: mock_deps_10,
                expected_dispatch=expected_dispatch,
                dispatch_match_key="vuln_class",
            )

        # Total findings: 10 from parent + 6 from fallback = 16
        assert len(output.findings) == 16, (
            f"Expected 16 findings (10 from parent + 6 from fallback), "
            f"got {len(output.findings)}"
        )

        # All 16 vuln classes must be covered
        returned_vc_values = {f.vuln_class.value for f in output.findings}
        expected_vc_values = {vc.value for vc in VulnClass}
        assert returned_vc_values == expected_vc_values, (
            f"Missing vuln classes: {expected_vc_values - returned_vc_values}"
        )

    def test_programmatic_fallback_all_16_dispatched_no_fallback_needed(self) -> None:
        """When all 16 are dispatched, no fallback invocations occur."""
        all_vc_values = [vc.value for vc in VulnClass]
        parent_findings = [
            {
                "id": str(uuid.uuid4()),
                "file_path": f"src/module_{vc_val}.py",
                "line_start": 1,
                "line_end": 1,
                "vuln_class": vc_val,
                "cwe_ids": [],
                "severity": "high",
                "title": f"{vc_val} finding",
                "description": f"A {vc_val} finding.",
                "recommendation": "Fix it.",
                "confidence": 0.9,
                "raw_llm_output": "",
                "produced_by": "test",
                "experiment_id": "test",
            }
            for vc_val in all_vc_values
        ]

        registry = load_default_registry()
        strategy = registry.get("builtin_v2.per_vuln_class")

        provider = ScriptedLiteLLMProvider(
            responses=[
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "final_result",
                            "id": "tc_pvc_all16",
                            "input": {"response": parent_findings},
                        }
                    ],
                    "input_tokens": 300,
                    "output_tokens": 100,
                }
            ]
        )

        # All 16 dispatched — no fallback needed
        mock_deps_all = self._make_deps_with_partial_dispatch(
            dispatched_vc_values=all_vc_values,
            findings_per_vc={},
        )

        expected_dispatch = [{"vuln_class": vc.value} for vc in VulnClass]

        fallback_call_count = [0]

        original_fallback = _programmatic_fallback

        def _counting_fallback(*args, **kwargs):
            fallback_call_count[0] += 1
            return original_fallback(*args, **kwargs)

        with mock.patch(
            "sec_review_framework.strategies.runner._programmatic_fallback",
            side_effect=_counting_fallback,
        ):
            output = run_strategy(
                strategy,
                FakeTarget(),
                provider,
                ToolRegistry(),
                deps_factory=lambda: mock_deps_all,
                expected_dispatch=expected_dispatch,
                dispatch_match_key="vuln_class",
            )

        # Fallback was never called (all dispatched)
        assert fallback_call_count[0] == 0, (
            f"Fallback should not have been called when all 16 were dispatched, "
            f"but it was called {fallback_call_count[0]} time(s)"
        )
        # All 16 findings present
        assert len(output.findings) == 16


# ---------------------------------------------------------------------------
# Tests: Dispatch validator — single_call_log tracking
# ---------------------------------------------------------------------------


class TestDispatchValidatorSingleCallLog:
    """Verify that invoke_subagent calls are recorded in single_call_log."""

    def test_single_call_log_populated_after_invoke_subagent(self) -> None:
        """After invoke_subagent is called, single_call_log contains the entry."""
        from sec_review_framework.agent.subagent import SubagentOutput, make_invoke_subagent_tool

        # Create a minimal deps with one role
        mock_strategy = _make_mock_specialist_strategy(VulnClass.SQLI, [])
        deps = SubagentDeps(
            depth=0,
            max_depth=3,
            invocations=0,
            max_invocations=100,
            max_batch_size=32,
            available_roles={"builtin_v2.sqli_specialist"},
            subagent_strategies={"builtin_v2.sqli_specialist": mock_strategy},
            tool_registry=ToolRegistry(),
        )

        # Confirm single_call_log starts empty
        assert deps.single_call_log == []

        # Simulate what _run_child_sync would do — patch it to return an output
        def _mock_child_sync(strategy, input_data, parent_deps):
            return SubagentOutput(role="builtin_v2.sqli_specialist", output=[], usage={})

        with mock.patch(
            "sec_review_framework.agent.subagent._run_child_sync",
            side_effect=_mock_child_sync,
        ):
            import asyncio

            tool_fn = make_invoke_subagent_tool()

            # Build a minimal RunContext mock
            ctx = mock.MagicMock()
            ctx.deps = deps

            asyncio.run(
                tool_fn(ctx, role="builtin_v2.sqli_specialist", input={"vuln_class": "sqli"})
            )

        # single_call_log should now have one entry
        assert len(deps.single_call_log) == 1
        role, inp = deps.single_call_log[0]
        assert role == "builtin_v2.sqli_specialist"
        assert inp["vuln_class"] == "sqli"

    def test_single_call_log_tracks_all_roles_dispatched(self) -> None:
        """Each invoke_subagent call appends to single_call_log."""
        from sec_review_framework.agent.subagent import SubagentOutput, make_invoke_subagent_tool

        # Two roles
        deps = SubagentDeps(
            depth=0,
            max_depth=3,
            invocations=0,
            max_invocations=100,
            max_batch_size=32,
            available_roles={
                "builtin_v2.sqli_specialist",
                "builtin_v2.xss_specialist",
            },
            subagent_strategies={
                "builtin_v2.sqli_specialist": _make_mock_specialist_strategy(VulnClass.SQLI, []),
                "builtin_v2.xss_specialist": _make_mock_specialist_strategy(VulnClass.XSS, []),
            },
            tool_registry=ToolRegistry(),
        )

        def _mock_child_sync(strategy, input_data, parent_deps):
            return SubagentOutput(role=strategy.id, output=[], usage={})

        with mock.patch(
            "sec_review_framework.agent.subagent._run_child_sync",
            side_effect=_mock_child_sync,
        ):
            import asyncio

            tool_fn = make_invoke_subagent_tool()
            ctx = mock.MagicMock()
            ctx.deps = deps

            asyncio.run(
                tool_fn(ctx, role="builtin_v2.sqli_specialist", input={"vuln_class": "sqli"})
            )
            asyncio.run(
                tool_fn(ctx, role="builtin_v2.xss_specialist", input={"vuln_class": "xss"})
            )

        # Both calls should be logged
        assert len(deps.single_call_log) == 2
        logged_roles = {role for role, _ in deps.single_call_log}
        assert "builtin_v2.sqli_specialist" in logged_roles
        assert "builtin_v2.xss_specialist" in logged_roles


# ---------------------------------------------------------------------------
# Tests: parity with legacy PerVulnClassStrategy
# ---------------------------------------------------------------------------


class TestPerVulnClassParityV2:
    """Phase 3c parity: builtin_v2.per_vuln_class vs builtin.per_vuln_class (legacy)."""

    def _run_legacy_per_vuln_class(
        self,
        all_findings: list[dict],
    ) -> tuple[Any, ScriptedLiteLLMProvider]:
        """Run builtin.per_vuln_class via PerVulnClassStrategy.run() with scripted findings."""
        registry = load_default_registry()
        strategy = registry.get("builtin.per_vuln_class")
        target = FakeTarget()

        # Legacy runner calls each vuln class's subagent sequentially
        # Build one scripted response per VulnClass
        all_vc_values = [vc.value for vc in VulnClass]
        responses = []
        for vc_val in all_vc_values:
            vc_findings = [f for f in all_findings if f["vuln_class"] == vc_val]
            responses.append(
                {
                    "content": _legacy_text(vc_findings),
                    "tool_calls": [],
                    "input_tokens": 200,
                    "output_tokens": 80,
                }
            )

        provider = ScriptedLiteLLMProvider(responses=responses)
        output = PerVulnClassStrategy().run(target, provider, ToolRegistry(), strategy)
        return output, provider

    def _run_v2_per_vuln_class(
        self,
        all_findings: list[dict],
    ) -> tuple[Any, ScriptedLiteLLMProvider]:
        """Run builtin_v2.per_vuln_class via run_strategy() with scripted findings."""
        registry = load_default_registry()
        strategy = registry.get("builtin_v2.per_vuln_class")

        provider = ScriptedLiteLLMProvider(
            responses=[
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "final_result",
                            "id": "tc_pvc_v2_par",
                            "input": {"response": all_findings},
                        }
                    ],
                    "input_tokens": 200,
                    "output_tokens": 80,
                }
            ]
        )

        # Pre-populate single_call_log with all 16 roles dispatched
        all_roles = {f"builtin_v2.{vc.value}_specialist" for vc in VulnClass}
        mock_deps = _make_mock_deps(
            dispatched_roles=all_roles,
            per_role_findings={},
        )

        expected_dispatch = [{"vuln_class": vc.value} for vc in VulnClass]

        output = run_strategy(
            strategy,
            FakeTarget(),
            provider,
            ToolRegistry(),
            deps_factory=lambda: mock_deps,
            expected_dispatch=expected_dispatch,
            dispatch_match_key="vuln_class",
        )
        return output, provider

    def test_parity_set_equality_all_16_findings(self) -> None:
        """(file_path, vuln_class) sets must be identical for both runners."""
        all_findings = list(_ALL_VULN_CLASS_FINDINGS)

        legacy_output, _ = self._run_legacy_per_vuln_class(all_findings)
        v2_output, _ = self._run_v2_per_vuln_class(all_findings)

        legacy_keys = _key_set(legacy_output)
        v2_keys = _key_set(v2_output)

        assert v2_keys == legacy_keys, (
            f"(file_path, vuln_class) pair mismatch:\n"
            f"  v2={v2_keys}\n"
            f"  legacy={legacy_keys}"
        )

    def test_parity_set_equality_empty_findings(self) -> None:
        legacy_output, _ = self._run_legacy_per_vuln_class([])
        v2_output, _ = self._run_v2_per_vuln_class([])

        assert _key_set(legacy_output) == _key_set(v2_output) == set()

    def test_parity_findings_count_within_10_percent(self) -> None:
        all_findings = list(_ALL_VULN_CLASS_FINDINGS)
        legacy_output, _ = self._run_legacy_per_vuln_class(all_findings)
        v2_output, _ = self._run_v2_per_vuln_class(all_findings)

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
        """Per-call token cost must be within ±20%.

        v2 makes 1 parent call; legacy makes 16 subagent calls (1 per vuln class).
        We measure per-call cost to compare meaningfully.
        """
        all_findings = [_ALL_VULN_CLASS_FINDINGS[0]]  # one finding for simplicity

        _, v2_provider = self._run_v2_per_vuln_class(all_findings)
        _, legacy_provider = self._run_legacy_per_vuln_class(all_findings)

        v2_calls = len(v2_provider.token_log)
        legacy_calls = len(legacy_provider.token_log)

        v2_tokens = _total_tokens(v2_provider)
        legacy_tokens = _total_tokens(legacy_provider)

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
        v2 = registry.get("builtin_v2.per_vuln_class")
        assert v2.use_new_runner is True

    def test_v1_strategy_uses_legacy_runner(self) -> None:
        registry = load_default_registry()
        v1 = registry.get("builtin.per_vuln_class")
        assert v1.use_new_runner is False


# ---------------------------------------------------------------------------
# Tests: bare-role dispatch — integration test for role resolution fix
# ---------------------------------------------------------------------------


class TestBareRoleResolutionIntegration:
    """Integration test: supervisor dispatches using bare role names (no namespace).

    Verifies that the role-resolution fix in invoke_subagent lets the supervisor
    call ``invoke_subagent(role="sqli_specialist", ...)`` even when
    ``available_roles`` contains only ``"builtin_v2.sqli_specialist"``.

    The test asserts:
    - No ModelRetry is raised during the parent agent run.
    - ``single_call_log`` records the *resolved* (namespaced) role.
    - The programmatic fallback does NOT re-invoke the specialist.
    """

    def test_bare_role_dispatch_records_namespaced_id_no_fallback(self) -> None:
        """Supervisor uses bare 'sqli_specialist'; log records 'builtin_v2.sqli_specialist'.

        This simulates the exact bug scenario from Phase 3c: the parent prompt
        says ``invoke_subagent(role="sqli_specialist")`` but available_roles has
        the namespaced form.  Without the fix, ModelRetry fires each turn and the
        run wastes its full turn budget.

        With the fix, the call succeeds on the first try and the resolved role
        appears in single_call_log — so the dispatch validator counts it as
        dispatched and does NOT trigger the programmatic fallback.
        """
        from unittest import mock

        # Build a minimal deps with only the sqli_specialist
        sqli_strategy = _make_mock_specialist_strategy(VulnClass.SQLI, [])
        deps = SubagentDeps(
            depth=0,
            max_depth=3,
            invocations=0,
            max_invocations=100,
            max_batch_size=32,
            # available_roles uses full namespaced ID
            available_roles={"builtin_v2.sqli_specialist"},
            subagent_strategies={"builtin_v2.sqli_specialist": sqli_strategy},
            tool_registry=ToolRegistry(),
        )

        # Simulate the supervisor dispatching with the bare name
        from sec_review_framework.agent.subagent import SubagentOutput, make_invoke_subagent_tool

        def _mock_child_sync(strategy, input_data, parent_deps):
            return SubagentOutput(role="builtin_v2.sqli_specialist", output=[], usage={})

        with mock.patch(
            "sec_review_framework.agent.subagent._run_child_sync",
            side_effect=_mock_child_sync,
        ):
            import asyncio

            tool_fn = make_invoke_subagent_tool()
            ctx = mock.MagicMock()
            ctx.deps = deps

            # Must NOT raise ModelRetry — bare role resolves to namespaced ID
            asyncio.run(
                tool_fn(ctx, role="sqli_specialist", input={"vuln_class": "sqli"})
            )

        # The log records the RESOLVED (namespaced) role — not the bare alias
        assert len(deps.single_call_log) == 1
        logged_role, logged_input = deps.single_call_log[0]
        assert logged_role == "builtin_v2.sqli_specialist", (
            f"Expected 'builtin_v2.sqli_specialist' in log, got {logged_role!r}"
        )
        assert logged_input == {"vuln_class": "sqli"}

    def test_bare_role_dispatch_counts_as_dispatched_no_fallback(self) -> None:
        """After bare-role dispatch, the dispatch validator sees it as dispatched.

        Runs a full run_strategy call where:
        - The supervisor dispatches with bare name "sqli_specialist" (via pre-populated
          single_call_log to avoid needing a real LLM for the parent agent turn).
        - expected_dispatch includes one entry for sqli.
        - Fallback mode is "programmatic".

        Assert: programmatic fallback is NOT triggered (the validator finds the
        namespaced role already in the log).
        """
        from unittest import mock

        from sec_review_framework.strategies.runner import _programmatic_fallback, run_strategy

        registry = load_default_registry()
        strategy = registry.get("builtin_v2.per_vuln_class")

        # Pre-populate single_call_log with the NAMESPACED role (as stored by
        # the fixed invoke_subagent after resolving the bare name).
        all_roles = {f"builtin_v2.{vc.value}_specialist" for vc in VulnClass}
        # Simulate: all 16 roles were dispatched using bare names, which got
        # resolved and logged as namespaced IDs.
        mock_deps = SubagentDeps(
            depth=0,
            max_depth=3,
            invocations=0,
            max_invocations=100,
            max_batch_size=32,
            available_roles=all_roles,
            subagent_strategies={
                role: _make_mock_specialist_strategy(
                    VulnClass(role.removeprefix("builtin_v2.").removesuffix("_specialist")), []
                )
                for role in all_roles
            },
            tool_registry=ToolRegistry(),
        )
        # Simulate having dispatched all 16 via bare names (stored as namespaced)
        mock_deps.single_call_log = [
            (role, {"vuln_class": role.removeprefix("builtin_v2.").removesuffix("_specialist")})
            for role in all_roles
        ]

        all_findings_dicts = [
            {
                "id": str(__import__("uuid").uuid4()),
                "file_path": f"src/module_{vc.value}.py",
                "line_start": 1,
                "line_end": 1,
                "vuln_class": vc.value,
                "cwe_ids": [],
                "severity": "high",
                "title": f"{vc.value} finding",
                "description": f"A {vc.value} vulnerability.",
                "recommendation": "Fix it.",
                "confidence": 0.9,
                "raw_llm_output": "",
                "produced_by": "test",
                "experiment_id": "test",
            }
            for vc in VulnClass
        ]

        provider = ScriptedLiteLLMProvider(
            responses=[
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "final_result",
                            "id": "tc_bare_integration",
                            "input": {"response": all_findings_dicts},
                        }
                    ],
                    "input_tokens": 300,
                    "output_tokens": 100,
                }
            ]
        )

        expected_dispatch = [{"vuln_class": vc.value} for vc in VulnClass]

        fallback_call_count = [0]
        original_fallback = _programmatic_fallback

        def _counting_fallback(*args, **kwargs):
            fallback_call_count[0] += 1
            return original_fallback(*args, **kwargs)

        with mock.patch(
            "sec_review_framework.strategies.runner._programmatic_fallback",
            side_effect=_counting_fallback,
        ):
            output = run_strategy(
                strategy,
                FakeTarget(),
                provider,
                ToolRegistry(),
                deps_factory=lambda: mock_deps,
                expected_dispatch=expected_dispatch,
                dispatch_match_key="vuln_class",
            )

        # Fallback should NOT have been called — all 16 were dispatched
        assert fallback_call_count[0] == 0, (
            f"Programmatic fallback was triggered {fallback_call_count[0]} time(s) "
            "but should have been 0 — all roles were already in single_call_log"
        )
        assert len(output.findings) == 16
