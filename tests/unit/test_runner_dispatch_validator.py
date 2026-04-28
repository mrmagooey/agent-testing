"""Unit tests for the dispatch-completeness validator in runner.py.

Tests cover:
- _validate_dispatch: returns empty list when all inputs dispatched.
- _validate_dispatch: returns missing inputs when parent forgets some.
- run_strategy with expected_dispatch: re-prompts when missing inputs found.
- re-prompt only happens once (bound to 1).
- Clear failure when re-prompt still misses inputs (run completes but missing inputs logged).

Skipped cleanly when the ``agent`` extra (pydantic-ai) is not installed.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any
from unittest import mock

import pytest

pydantic_ai = pytest.importorskip("pydantic_ai")

from pydantic_ai.exceptions import UnexpectedModelBehavior  # noqa: E402

from sec_review_framework.agent.subagent import SubagentDeps  # noqa: E402
from sec_review_framework.data.findings import StrategyOutput  # noqa: E402
from sec_review_framework.data.strategy_bundle import (  # noqa: E402
    OrchestrationShape,
    StrategyBundleDefault,
    UserStrategy,
)
from sec_review_framework.models.base import Message, ModelResponse, ToolDefinition  # noqa: E402
from sec_review_framework.models.litellm_provider import LiteLLMProvider  # noqa: E402
from sec_review_framework.strategies.runner import (  # noqa: E402
    RunnerError,
    _programmatic_fallback,
    _validate_dispatch,
    run_strategy,
)
from sec_review_framework.tools.registry import ToolRegistry  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class ScriptedLiteLLMProvider(LiteLLMProvider):
    """Pre-scripted provider for offline tests."""

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
            input_tokens=data.get("input_tokens", 10),
            output_tokens=data.get("output_tokens", 5),
            model_id=self.model_name,
            raw={},
        )


def _make_finding_data(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": str(uuid.uuid4()),
        "file_path": "app/views.py",
        "line_start": 42,
        "line_end": 42,
        "vuln_class": "sqli",
        "cwe_ids": ["CWE-89"],
        "severity": "high",
        "title": "SQL injection",
        "description": "User input concatenated into SQL.",
        "recommendation": "Use parameterised queries.",
        "confidence": 0.9,
        "raw_llm_output": "",
        "produced_by": "test",
        "experiment_id": "validator_001",
    }
    base.update(overrides)
    return base


def _make_strategy(
    strategy_id: str = "test.dispatch",
    subagents: list[str] | None = None,
) -> UserStrategy:
    if subagents is None:
        subagents = ["test.file_reviewer"]
    return UserStrategy(
        id=strategy_id,
        name="Test dispatch strategy",
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.PER_FILE,
        default=StrategyBundleDefault(
            system_prompt="Review files.",
            user_prompt_template="Review: {repo_summary}\n{finding_output_format}",
            model_id="fake/test",
            tools=frozenset(),
            verification="none",
            max_turns=5,
            tool_extensions=frozenset(),
            subagents=subagents,
        ),
        overrides=[],
        created_at=datetime(2026, 1, 1),
        is_builtin=False,
    )


class FakeTarget:
    def get_file_tree(self) -> str:
        return "app/views.py\napp/models.py"

    def list_source_files(self) -> list[str]:
        return ["app/views.py", "app/models.py"]


def _scripted_provider(findings: list[dict]) -> ScriptedLiteLLMProvider:
    return ScriptedLiteLLMProvider(
        responses=[
            {
                "content": "",
                "tool_calls": [
                    {
                        "name": "final_result",
                        "id": "tc_val_1",
                        "input": {"response": findings},
                    }
                ],
                "input_tokens": 100,
                "output_tokens": 40,
            }
        ]
    )


def _make_mock_deps(batch_calls: list[tuple[str, list[dict]]] | None = None) -> SubagentDeps:
    """Build SubagentDeps with optional pre-populated batch_call_log."""
    deps = SubagentDeps(
        depth=0,
        max_depth=3,
        invocations=0,
        max_invocations=100,
        max_batch_size=32,
        available_roles={"test.file_reviewer"},
        subagent_strategies={},
        tool_registry=ToolRegistry(),
    )
    if batch_calls:
        deps.batch_call_log = list(batch_calls)
    return deps


# ---------------------------------------------------------------------------
# Tests: _validate_dispatch unit tests
# ---------------------------------------------------------------------------


class TestValidateDispatch:
    """Direct unit tests for _validate_dispatch."""

    def test_returns_empty_when_all_dispatched(self) -> None:
        expected = [
            {"file_path": "a.py"},
            {"file_path": "b.py"},
        ]
        actual_calls = [
            ("file_reviewer", [{"file_path": "a.py"}, {"file_path": "b.py"}]),
        ]
        missing = _validate_dispatch("test", expected, actual_calls, "file_path")
        assert missing == []

    def test_returns_missing_when_one_not_dispatched(self) -> None:
        expected = [
            {"file_path": "a.py"},
            {"file_path": "b.py"},
            {"file_path": "c.py"},
        ]
        actual_calls = [
            ("file_reviewer", [{"file_path": "a.py"}, {"file_path": "c.py"}]),
        ]
        missing = _validate_dispatch("test", expected, actual_calls, "file_path")
        assert len(missing) == 1
        assert missing[0]["file_path"] == "b.py"

    def test_returns_all_when_none_dispatched(self) -> None:
        expected = [
            {"file_path": "a.py"},
            {"file_path": "b.py"},
        ]
        missing = _validate_dispatch("test", expected, [], "file_path")
        assert len(missing) == 2

    def test_empty_expected_returns_empty(self) -> None:
        missing = _validate_dispatch("test", [], [], "file_path")
        assert missing == []

    def test_multiple_batch_calls_counted(self) -> None:
        """Multiple invoke_subagent_batch calls combine their dispatched inputs."""
        expected = [
            {"file_path": "a.py"},
            {"file_path": "b.py"},
            {"file_path": "c.py"},
        ]
        actual_calls = [
            ("file_reviewer", [{"file_path": "a.py"}]),
            ("file_reviewer", [{"file_path": "b.py"}, {"file_path": "c.py"}]),
        ]
        missing = _validate_dispatch("test", expected, actual_calls, "file_path")
        assert missing == []

    def test_different_roles_combined(self) -> None:
        """Dispatches to different roles are combined for coverage check."""
        expected = [
            {"file_path": "a.py"},
            {"file_path": "b.py"},
        ]
        actual_calls = [
            ("role_a", [{"file_path": "a.py"}]),
            ("role_b", [{"file_path": "b.py"}]),
        ]
        missing = _validate_dispatch("test", expected, actual_calls, "file_path")
        assert missing == []

    def test_custom_match_key(self) -> None:
        """Validator works with a non-file_path match key."""
        expected = [{"vuln_class": "sqli"}, {"vuln_class": "xss"}]
        actual_calls = [("agent", [{"vuln_class": "sqli"}])]
        missing = _validate_dispatch("test", expected, actual_calls, "vuln_class")
        assert len(missing) == 1
        assert missing[0]["vuln_class"] == "xss"

    def test_inputs_without_match_key_treated_as_missing(self) -> None:
        """Inputs in actual_calls lacking the match key are effectively invisible."""
        expected = [{"file_path": "a.py"}]
        actual_calls = [("agent", [{"other_key": "value"}])]
        missing = _validate_dispatch("test", expected, actual_calls, "file_path")
        assert len(missing) == 1


# ---------------------------------------------------------------------------
# Tests: run_strategy with expected_dispatch — re-prompt behaviour
# ---------------------------------------------------------------------------


class TestRunStrategyDispatchValidator:
    """Integration tests for the dispatch validator within run_strategy."""

    def test_no_reprompt_when_dispatch_fallback_none(self) -> None:
        """With dispatch_fallback='none', validator is disabled and run finishes in 1 turn.

        PER_FILE strategies auto-derive expected_dispatch since fix #2, so the
        old 'no expected_dispatch → no validator' assumption no longer holds.
        Setting dispatch_fallback='none' is the correct way to opt out.
        """
        finding = _make_finding_data()
        provider = _scripted_provider([finding])
        mock_deps = _make_mock_deps()

        strategy = UserStrategy(
            id="test.dispatch.none",
            name="Test dispatch strategy (fallback=none)",
            parent_strategy_id=None,
            orchestration_shape=OrchestrationShape.PER_FILE,
            default=StrategyBundleDefault(
                system_prompt="Review files.",
                user_prompt_template="Review: {repo_summary}\n{finding_output_format}",
                model_id="fake/test",
                tools=frozenset(),
                verification="none",
                max_turns=5,
                tool_extensions=frozenset(),
                subagents=["test.file_reviewer"],
                dispatch_fallback="none",
            ),
            overrides=[],
            created_at=datetime(2026, 1, 1),
            is_builtin=False,
        )

        output = run_strategy(
            strategy,
            FakeTarget(),
            provider,
            ToolRegistry(),
            deps_factory=lambda: mock_deps,
        )
        assert isinstance(output, StrategyOutput)
        assert len(output.findings) == 1
        # Validator did not run — dispatch_completeness stays None
        assert output.dispatch_completeness is None

    def test_no_reprompt_when_all_dispatched(self) -> None:
        """When all expected inputs are in batch_call_log, no re-prompt occurs."""
        finding = _make_finding_data()
        # Script only one response (no re-prompt response needed)
        provider = _scripted_provider([finding])
        mock_deps = _make_mock_deps(
            batch_calls=[
                ("test.file_reviewer", [{"file_path": "app/views.py"}, {"file_path": "app/models.py"}])
            ]
        )

        expected_dispatch = [
            {"file_path": "app/views.py"},
            {"file_path": "app/models.py"},
        ]

        output = run_strategy(
            _make_strategy(),
            FakeTarget(),
            provider,
            ToolRegistry(),
            deps_factory=lambda: mock_deps,
            expected_dispatch=expected_dispatch,
            dispatch_match_key="file_path",
        )
        # Only one response consumed — no re-prompt
        assert isinstance(output, StrategyOutput)
        # Confirm no extra responses were requested (provider drained)
        assert not provider._responses

    def test_reprompt_triggered_when_dispatch_incomplete(self) -> None:
        """When dispatch is incomplete, a second run_sync call is made (re-prompt)."""
        finding1 = _make_finding_data()
        finding2 = _make_finding_data(file_path="app/models.py", vuln_class="xss")

        # Script two responses: initial turn + re-prompt turn
        provider = ScriptedLiteLLMProvider(
            responses=[
                # Initial turn: parent misses app/models.py dispatch
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "final_result",
                            "id": "tc_val_2",
                            "input": {"response": [finding1]},
                        }
                    ],
                    "input_tokens": 100,
                    "output_tokens": 40,
                },
                # Re-prompt turn: parent dispatches the missed file and returns its finding
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "final_result",
                            "id": "tc_val_3",
                            "input": {"response": [finding2]},
                        }
                    ],
                    "input_tokens": 80,
                    "output_tokens": 20,
                },
            ]
        )

        # Deps log only one file dispatched (models.py was missed)
        mock_deps = _make_mock_deps(
            batch_calls=[
                ("test.file_reviewer", [{"file_path": "app/views.py"}])
            ]
        )

        expected_dispatch = [
            {"file_path": "app/views.py"},
            {"file_path": "app/models.py"},  # this one was missed
        ]

        output = run_strategy(
            _make_strategy(),
            FakeTarget(),
            provider,
            ToolRegistry(),
            deps_factory=lambda: mock_deps,
            expected_dispatch=expected_dispatch,
            dispatch_match_key="file_path",
        )

        # Both turns were consumed
        assert not provider._responses, "Both scripted responses should have been used"
        # Findings from both turns combined
        assert len(output.findings) == 2

    def test_reprompt_bounded_to_one(self) -> None:
        """Re-prompt only happens once even if dispatch is still incomplete after re-prompt."""
        finding1 = _make_finding_data()

        # Script two responses (initial + re-prompt); a 3rd would be an error
        provider = ScriptedLiteLLMProvider(
            responses=[
                # Initial turn
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "final_result",
                            "id": "tc_val_4",
                            "input": {"response": [finding1]},
                        }
                    ],
                    "input_tokens": 100,
                    "output_tokens": 40,
                },
                # Re-prompt turn — still missing c.py but we don't re-prompt again
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "final_result",
                            "id": "tc_val_5",
                            "input": {"response": []},
                        }
                    ],
                    "input_tokens": 80,
                    "output_tokens": 10,
                },
            ]
        )

        mock_deps = _make_mock_deps(
            batch_calls=[
                ("test.file_reviewer", [{"file_path": "a.py"}])
            ]
        )

        # Two expected, one dispatched — validator re-prompts
        expected_dispatch = [
            {"file_path": "a.py"},
            {"file_path": "b.py"},  # missed initially
            {"file_path": "c.py"},  # missed initially
        ]

        # Should complete without error — no 3rd run_sync call
        output = run_strategy(
            _make_strategy(),
            FakeTarget(),
            provider,
            ToolRegistry(),
            deps_factory=lambda: mock_deps,
            expected_dispatch=expected_dispatch,
            dispatch_match_key="file_path",
        )

        # Run completed (did not crash)
        assert isinstance(output, StrategyOutput)
        # Only two responses were consumed (initial + 1 re-prompt)
        assert not provider._responses, "Both scripted responses should have been consumed"

    def test_reprompt_failure_does_not_raise(self) -> None:
        """If the re-prompt itself raises UnexpectedModelBehavior, it is logged but not raised."""
        finding1 = _make_finding_data()
        provider = _scripted_provider([finding1])

        # Deps with incomplete dispatch
        mock_deps = _make_mock_deps(
            batch_calls=[
                ("test.file_reviewer", [{"file_path": "app/views.py"}])
            ]
        )

        expected_dispatch = [
            {"file_path": "app/views.py"},
            {"file_path": "app/models.py"},
        ]

        with mock.patch(
            "sec_review_framework.strategies.runner.Agent.run_sync",
            side_effect=[
                # First call: normal result (mocked)
                mock.MagicMock(
                    output=[],
                    all_messages=lambda: [],
                ),
                # Re-prompt call raises UnexpectedModelBehavior
                UnexpectedModelBehavior("re-prompt failed"),
            ],
        ):
            # Should NOT raise — error is logged and run completes
            try:
                run_strategy(
                    _make_strategy(),
                    FakeTarget(),
                    provider,
                    ToolRegistry(),
                    deps_factory=lambda: mock_deps,
                    expected_dispatch=expected_dispatch,
                    dispatch_match_key="file_path",
                )
            except RunnerError:
                pytest.fail("run_strategy should not raise RunnerError for re-prompt failure")


# ---------------------------------------------------------------------------
# Tests: _validate_dispatch logging
# ---------------------------------------------------------------------------


class TestValidateDispatchLogging:
    """Verify _validate_dispatch logs warnings for missing inputs."""

    def test_warning_logged_for_missing_inputs(self) -> None:
        """_validate_dispatch must log a warning when inputs are missing."""
        expected = [{"file_path": "a.py"}, {"file_path": "b.py"}]
        actual_calls: list[tuple[str, list[dict]]] = []

        with mock.patch("sec_review_framework.strategies.runner.logging") as mock_logging:
            _validate_dispatch("my.strategy", expected, actual_calls, "file_path")
            mock_logging.warning.assert_called_once()
            warning_args = str(mock_logging.warning.call_args)
            assert "my.strategy" in warning_args or "2" in warning_args

    def test_no_warning_logged_when_complete(self) -> None:
        """_validate_dispatch must NOT log a warning when all inputs are dispatched."""
        expected = [{"file_path": "a.py"}]
        actual_calls = [("agent", [{"file_path": "a.py"}])]

        with mock.patch("sec_review_framework.strategies.runner.logging") as mock_logging:
            _validate_dispatch("my.strategy", expected, actual_calls, "file_path")
            mock_logging.warning.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: _programmatic_fallback role resolution
# ---------------------------------------------------------------------------


class TestProgrammaticFallbackRoleResolution:
    """Test _programmatic_fallback resolves both bare and namespaced roles via shared helper."""

    def test_resolve_bare_suffix_role(self) -> None:
        """_programmatic_fallback must resolve bare role suffix (e.g. 'sqli_specialist')."""
        from sec_review_framework.data.findings import Finding, Severity

        # Create a fake specialist strategy
        specialist_strategy = _make_strategy(
            strategy_id="builtin_v2.sqli_specialist",
            subagents=[],
        )

        # Create deps with the specialist registered
        deps = SubagentDeps(
            depth=0,
            max_depth=3,
            invocations=0,
            max_invocations=100,
            max_batch_size=32,
            available_roles={"builtin_v2.sqli_specialist"},
            subagent_strategies={"builtin_v2.sqli_specialist": specialist_strategy},
            tool_registry=ToolRegistry(),
        )

        # Create a proper Finding instance that will be returned by _run_child_sync
        finding = Finding(
            id=str(uuid.uuid4()),
            file_path="test.py",
            vuln_class="sqli",
            severity=Severity.HIGH,
            title="SQL injection",
            description="Test finding",
            confidence=0.9,
            raw_llm_output="",
            produced_by="sqli_specialist",
            experiment_id="test_001",
        )

        with mock.patch(
            "sec_review_framework.strategies.runner._run_child_sync"
        ) as mock_run:
            mock_run.return_value = mock.Mock(output=[finding])

            # Call with bare suffix (no namespace) — role resolution should handle it
            missing_inputs = [{"vuln_class": "sqli"}]
            results = _programmatic_fallback(
                "test.strategy",
                missing_inputs,
                "vuln_class",
                deps,
            )

            # Should find and invoke the specialist
            assert mock_run.called
            assert len(results) == 1
            assert results[0].vuln_class == "sqli"

    def test_resolve_namespaced_role(self) -> None:
        """_programmatic_fallback must correctly resolve fully-namespaced roles."""
        from sec_review_framework.data.findings import Finding, Severity

        specialist_strategy = _make_strategy(
            strategy_id="builtin_v2.xss_specialist",
            subagents=[],
        )

        deps = SubagentDeps(
            depth=0,
            max_depth=3,
            invocations=0,
            max_invocations=100,
            max_batch_size=32,
            available_roles={"builtin_v2.xss_specialist"},
            subagent_strategies={"builtin_v2.xss_specialist": specialist_strategy},
            tool_registry=ToolRegistry(),
        )

        finding = Finding(
            id=str(uuid.uuid4()),
            file_path="test.py",
            vuln_class="xss",
            severity=Severity.MEDIUM,
            title="XSS vulnerability",
            description="Test finding",
            confidence=0.8,
            raw_llm_output="",
            produced_by="xss_specialist",
            experiment_id="test_001",
        )

        with mock.patch(
            "sec_review_framework.strategies.runner._run_child_sync"
        ) as mock_run:
            mock_run.return_value = mock.Mock(output=[finding])

            missing_inputs = [{"vuln_class": "xss"}]
            results = _programmatic_fallback(
                "test.strategy",
                missing_inputs,
                "vuln_class",
                deps,
            )

            assert mock_run.called
            assert len(results) == 1
            assert results[0].vuln_class == "xss"


# ---------------------------------------------------------------------------
# Regression tests: Bug #2 — dispatch validator auto-derivation
# ---------------------------------------------------------------------------


def _make_per_vuln_class_strategy(dispatch_fallback: str = "programmatic") -> UserStrategy:
    """Build a minimal PER_VULN_CLASS strategy with 16 specialists."""
    from sec_review_framework.data.findings import VulnClass

    specialist_ids = [f"test.{vc.value}_specialist" for vc in VulnClass]
    return UserStrategy(
        id="test.per_vuln_class",
        name="Test per_vuln_class",
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.PER_VULN_CLASS,
        default=StrategyBundleDefault(
            system_prompt="Dispatch specialists.",
            user_prompt_template="Repo: {repo_summary}\n{finding_output_format}",
            model_id="fake/test",
            tools=frozenset(),
            verification="none",
            max_turns=10,
            tool_extensions=frozenset(),
            subagents=specialist_ids,
            dispatch_fallback=dispatch_fallback,
        ),
        overrides=[],
        created_at=datetime(2026, 1, 1),
        is_builtin=False,
    )


class TestBug2AutoDerivePERVULN:
    """Regression for bug #2: PER_VULN_CLASS auto-derives expected_dispatch.

    Simulates a supervisor that dispatches only 8/16 specialists; asserts that
    programmatic fallback fires and the remaining 8 are invoked.
    """

    def test_programmatic_fallback_fires_for_missed_specialists(self) -> None:
        """8/16 dispatched by supervisor → programmatic fallback covers remaining 8."""
        from unittest.mock import Mock, patch

        from sec_review_framework.data.findings import Finding, Severity, VulnClass

        strategy = _make_per_vuln_class_strategy(dispatch_fallback="programmatic")

        # Script the parent to return an empty findings list immediately
        provider = ScriptedLiteLLMProvider(
            responses=[
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "final_result",
                            "id": "tc_pvc_1",
                            "input": {"response": []},
                        }
                    ],
                    "input_tokens": 100,
                    "output_tokens": 10,
                }
            ]
        )

        all_vuln_classes = list(VulnClass)
        dispatched_8 = all_vuln_classes[:8]
        missed_8 = all_vuln_classes[8:]

        # Deps pre-populated with 8 dispatched — 8 are missing
        dispatched_calls = [
            (f"test.{vc.value}_specialist", [{"vuln_class": vc.value}])
            for vc in dispatched_8
        ]
        deps = SubagentDeps(
            depth=0,
            max_depth=3,
            invocations=0,
            max_invocations=200,
            max_batch_size=32,
            available_roles={f"test.{vc.value}_specialist" for vc in all_vuln_classes},
            subagent_strategies={},
            tool_registry=ToolRegistry(),
        )
        deps.batch_call_log = dispatched_calls

        def make_finding(vc_value: str) -> Finding:
            return Finding(
                id=str(uuid.uuid4()),
                file_path="test.py",
                line_start=1,
                vuln_class=vc_value,
                severity=Severity.HIGH,
                title=f"{vc_value} issue",
                description="Test finding",
                confidence=0.8,
                raw_llm_output="",
                produced_by=f"{vc_value}_specialist",
                experiment_id="test_pvc",
            )

        # _run_child_sync returns one finding per specialist
        def fake_run_child(strategy_obj, inp, parent_deps):
            vc = inp.get("vuln_class", "sqli")
            return Mock(output=[make_finding(vc)])

        with patch("sec_review_framework.strategies.runner._run_child_sync", side_effect=fake_run_child):
            # Register specialist strategies in deps so resolution works
            for vc in missed_8:
                specialist_id = f"test.{vc.value}_specialist"
                deps.subagent_strategies[specialist_id] = UserStrategy(
                    id=specialist_id,
                    name=f"{vc.value} specialist",
                    parent_strategy_id="test.per_vuln_class",
                    orchestration_shape=OrchestrationShape.SINGLE_AGENT,
                    default=StrategyBundleDefault(
                        system_prompt="Find vulnerabilities.",
                        user_prompt_template="{vuln_class}: {repo_summary}",
                        model_id="fake/test",
                        tools=frozenset(),
                        verification="none",
                        max_turns=5,
                        tool_extensions=frozenset(),
                    ),
                    overrides=[],
                    created_at=datetime(2026, 1, 1),
                )

            output = run_strategy(
                strategy,
                FakeTarget(),
                provider,
                ToolRegistry(),
                deps_factory=lambda: deps,
            )

        # Programmatic fallback must have covered the 8 missed specialists
        assert len(output.findings) == 8, (
            f"Expected 8 findings from programmatic fallback, got {len(output.findings)}"
        )
        # dispatch_completeness is 1.0 after programmatic fill
        assert output.dispatch_completeness == 1.0


class TestBug2AutoDerivePERFILE:
    """Regression for bug #2: PER_FILE auto-derives expected_dispatch from target files.

    Supervisor dispatches 1/3 files; validator catches the miss and re-prompts.
    """

    def test_reprompt_fires_when_supervisor_misses_files(self) -> None:
        """Supervisor dispatches 1/3 files → reprompt fires for the 2 missed files."""

        class ThreeFileTarget:
            def get_file_tree(self) -> str:
                return "a.py\nb.py\nc.py"

            def list_source_files(self) -> list[str]:
                return ["a.py", "b.py", "c.py"]

        strategy = UserStrategy(
            id="test.per_file_reprompt",
            name="Test per_file reprompt",
            parent_strategy_id=None,
            orchestration_shape=OrchestrationShape.PER_FILE,
            default=StrategyBundleDefault(
                system_prompt="Dispatch file reviewers.",
                user_prompt_template="Repo: {repo_summary}\n{finding_output_format}",
                model_id="fake/test",
                tools=frozenset(),
                verification="none",
                max_turns=5,
                tool_extensions=frozenset(),
                subagents=["test.file_reviewer"],
                dispatch_fallback="reprompt",
            ),
            overrides=[],
            created_at=datetime(2026, 1, 1),
        )

        finding_a = _make_finding_data(file_path="a.py", vuln_class="sqli")
        finding_b = _make_finding_data(file_path="b.py", vuln_class="xss")

        # Two responses: initial turn (1 finding) + re-prompt turn (1 finding)
        provider = ScriptedLiteLLMProvider(
            responses=[
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "final_result",
                            "id": "tc_pf_1",
                            "input": {"response": [finding_a]},
                        }
                    ],
                    "input_tokens": 100,
                    "output_tokens": 30,
                },
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "final_result",
                            "id": "tc_pf_2",
                            "input": {"response": [finding_b]},
                        }
                    ],
                    "input_tokens": 80,
                    "output_tokens": 20,
                },
            ]
        )

        # Deps: only a.py was dispatched by the supervisor
        deps = SubagentDeps(
            depth=0,
            max_depth=3,
            invocations=0,
            max_invocations=100,
            max_batch_size=32,
            available_roles={"test.file_reviewer"},
            subagent_strategies={},
            tool_registry=ToolRegistry(),
        )
        deps.batch_call_log = [("test.file_reviewer", [{"file_path": "a.py"}])]

        output = run_strategy(
            strategy,
            ThreeFileTarget(),
            provider,
            ToolRegistry(),
            deps_factory=lambda: deps,
        )

        # Both scripted responses consumed — re-prompt fired
        assert not provider._responses, "Both responses should have been consumed"
        # Combined findings from both turns (deduplicated — no overlap here)
        assert len(output.findings) == 2


# ---------------------------------------------------------------------------
# Regression tests: Bug #5 — output_type_name honoured on parent strategy
# ---------------------------------------------------------------------------


class TestBug5OutputTypeName:
    """Regression for bug #5: parent strategy with non-finding output_type_name."""

    def test_verifier_verdict_output_goes_to_non_finding_output(self) -> None:
        """Strategy with output_type_name='verifier_verdict' populates non_finding_output."""
        from sec_review_framework.data.verification import VerifierVerdict

        strategy = UserStrategy(
            id="test.verifier_parent",
            name="Test verifier parent",
            parent_strategy_id=None,
            orchestration_shape=OrchestrationShape.SINGLE_AGENT,
            default=StrategyBundleDefault(
                system_prompt="Verify findings.",
                user_prompt_template="Repo: {repo_summary}",
                model_id="fake/test",
                tools=frozenset(),
                verification="none",
                max_turns=5,
                tool_extensions=frozenset(),
                output_type_name="verifier_verdict",
            ),
            overrides=[],
            created_at=datetime(2026, 1, 1),
        )

        # For Pydantic BaseModel output_type, pydantic-ai expects the model's
        # fields directly as the tool call input (not wrapped in {"response": ...}).
        verdict_data = {
            "status": "confirmed",
            "evidence": "The code is definitely vulnerable on line 42.",
        }

        provider = ScriptedLiteLLMProvider(
            responses=[
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "final_result",
                            "id": "tc_vv_1",
                            "input": verdict_data,
                        }
                    ],
                    "input_tokens": 100,
                    "output_tokens": 20,
                }
            ]
        )

        output = run_strategy(strategy, FakeTarget(), provider, ToolRegistry())

        # findings must be empty — this is a non-finding strategy
        assert output.findings == []
        # non_finding_output must contain the verdict
        assert output.non_finding_output is not None
        assert isinstance(output.non_finding_output, VerifierVerdict)
        assert output.non_finding_output.status == "confirmed"


# ---------------------------------------------------------------------------
# Regression tests: Bug #8 — re-prompt deduplication
# ---------------------------------------------------------------------------


class TestBug8RepromptDeduplication:
    """Regression for bug #8: re-prompt findings are deduplicated."""

    def test_duplicate_finding_on_reprompt_collapses_to_one(self) -> None:
        """If supervisor re-emits a previously-dispatched finding, dedup collapses it."""

        # Finding that will appear in BOTH initial and re-prompt turns
        dup_finding = _make_finding_data(
            id=str(uuid.uuid4()),
            file_path="app/views.py",
            line_start=42,
            line_end=42,
            vuln_class="sqli",
        )

        # Two responses: initial turn returns the finding, re-prompt also returns it
        provider = ScriptedLiteLLMProvider(
            responses=[
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "final_result",
                            "id": "tc_dup_1",
                            "input": {"response": [dup_finding]},
                        }
                    ],
                    "input_tokens": 100,
                    "output_tokens": 30,
                },
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "final_result",
                            "id": "tc_dup_2",
                            # Supervisor re-emits the SAME finding on re-prompt
                            "input": {"response": [dup_finding]},
                        }
                    ],
                    "input_tokens": 80,
                    "output_tokens": 20,
                },
            ]
        )

        # Deps: only a.py dispatched; b.py was missed → triggers re-prompt
        deps = SubagentDeps(
            depth=0,
            max_depth=3,
            invocations=0,
            max_invocations=100,
            max_batch_size=32,
            available_roles={"test.file_reviewer"},
            subagent_strategies={},
            tool_registry=ToolRegistry(),
        )
        deps.batch_call_log = [("test.file_reviewer", [{"file_path": "app/views.py"}])]

        strategy = UserStrategy(
            id="test.dedup_reprompt",
            name="Test dedup reprompt",
            parent_strategy_id=None,
            orchestration_shape=OrchestrationShape.PER_FILE,
            default=StrategyBundleDefault(
                system_prompt="Review.",
                user_prompt_template="Repo: {repo_summary}\n{finding_output_format}",
                model_id="fake/test",
                tools=frozenset(),
                verification="none",
                max_turns=5,
                tool_extensions=frozenset(),
                subagents=["test.file_reviewer"],
                dispatch_fallback="reprompt",
            ),
            overrides=[],
            created_at=datetime(2026, 1, 1),
        )

        output = run_strategy(
            strategy,
            FakeTarget(),
            provider,
            ToolRegistry(),
            deps_factory=lambda: deps,
        )

        # Both responses consumed (re-prompt fired)
        assert not provider._responses
        # The duplicate finding was deduplicated — only 1 finding in output
        assert len(output.findings) == 1, (
            f"Duplicate finding from re-prompt should have been deduplicated, "
            f"got {len(output.findings)} findings"
        )
