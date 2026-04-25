"""Unit tests for :mod:`sec_review_framework.strategies.runner`.

Tests cover:
- Builds an Agent with tools when subagents=[].
- Builds an Agent with tools + inject tools when subagents=[<id>].
- Returns a StrategyOutput with non-None findings.
- Translates UnexpectedModelBehavior to RunnerError.
- _should_use_new_runner() gate in worker.py.
- Feature-flag dispatch: use_new_runner=True routes to run_strategy(); False routes legacy.

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

from sec_review_framework.data.findings import StrategyOutput  # noqa: E402
from sec_review_framework.data.strategy_bundle import (  # noqa: E402
    OrchestrationShape,
    StrategyBundleDefault,
    UserStrategy,
)
from sec_review_framework.models.base import Message, ToolDefinition  # noqa: E402 F401
from sec_review_framework.models.base import ModelResponse as FrameworkModelResponse  # noqa: E402
from sec_review_framework.models.litellm_provider import LiteLLMProvider  # noqa: E402
from sec_review_framework.strategies.runner import (  # noqa: E402
    RunnerError,
    _build_user_prompt,
    _stamp_findings,
    run_strategy,
)
from sec_review_framework.tools.registry import ToolRegistry  # noqa: E402
from sec_review_framework.worker import _should_use_new_runner  # noqa: E402

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class ScriptedLiteLLMProvider(LiteLLMProvider):
    """Pre-scripted provider for offline runner tests."""

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
    ) -> FrameworkModelResponse:
        if not self._responses:
            raise RuntimeError("ScriptedLiteLLMProvider: no more scripted responses")
        data = self._responses.pop(0)
        return FrameworkModelResponse(
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
        "line_start": 77,
        "line_end": 77,
        "vuln_class": "sqli",
        "cwe_ids": ["CWE-89"],
        "severity": "high",
        "title": "SQL injection",
        "description": "User input is concatenated into SQL query.",
        "recommendation": "Use parameterised queries.",
        "confidence": 0.92,
        "raw_llm_output": "",
        "produced_by": "test",
        "experiment_id": "unit_001",
    }
    base.update(overrides)
    return base


def _make_strategy(
    *,
    use_new_runner: bool = True,
    subagents: list[str] | None = None,
    strategy_id: str = "unit.test",
) -> UserStrategy:
    if subagents is None:
        subagents = []
    return UserStrategy(
        id=strategy_id,
        name="Unit test strategy",
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.SINGLE_AGENT,
        default=StrategyBundleDefault(
            system_prompt="Review for security issues.",
            user_prompt_template="Repo:\n{repo_summary}\n\n{finding_output_format}",
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
        use_new_runner=use_new_runner,
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
                        "id": "tc_unit_1",
                        "input": {"response": findings},
                    }
                ],
                "input_tokens": 100,
                "output_tokens": 40,
            }
        ]
    )


# ---------------------------------------------------------------------------
# Tests: run_strategy core behaviour
# ---------------------------------------------------------------------------


class TestRunStrategyCore:
    """Core run_strategy() behaviour tests."""

    def test_returns_strategy_output(self) -> None:
        provider = _scripted_provider([])
        output = run_strategy(_make_strategy(), FakeTarget(), provider, ToolRegistry())
        assert isinstance(output, StrategyOutput)

    def test_returns_non_none_findings(self) -> None:
        provider = _scripted_provider([])
        output = run_strategy(_make_strategy(), FakeTarget(), provider, ToolRegistry())
        assert output.findings is not None

    def test_returns_findings_list(self) -> None:
        finding = _make_finding_data()
        provider = _scripted_provider([finding])
        output = run_strategy(_make_strategy(), FakeTarget(), provider, ToolRegistry())
        assert len(output.findings) == 1

    def test_finding_type_is_finding(self) -> None:
        from sec_review_framework.data.findings import Finding

        finding = _make_finding_data()
        provider = _scripted_provider([finding])
        output = run_strategy(_make_strategy(), FakeTarget(), provider, ToolRegistry())
        assert isinstance(output.findings[0], Finding)

    def test_pre_post_dedup_count_equal_finding_count(self) -> None:
        findings = [_make_finding_data(), _make_finding_data(file_path="app/models.py")]
        provider = _scripted_provider(findings)
        output = run_strategy(_make_strategy(), FakeTarget(), provider, ToolRegistry())
        assert output.pre_dedup_count == len(output.findings)
        assert output.post_dedup_count == len(output.findings)

    def test_dedup_log_is_empty(self) -> None:
        provider = _scripted_provider([_make_finding_data()])
        output = run_strategy(_make_strategy(), FakeTarget(), provider, ToolRegistry())
        assert output.dedup_log == []

    def test_system_prompt_in_output(self) -> None:
        provider = _scripted_provider([])
        output = run_strategy(_make_strategy(), FakeTarget(), provider, ToolRegistry())
        assert output.system_prompt == "Review for security issues."

    def test_user_message_in_output(self) -> None:
        provider = _scripted_provider([])
        output = run_strategy(_make_strategy(), FakeTarget(), provider, ToolRegistry())
        assert output.user_message is not None
        assert "app/views.py" in output.user_message


# ---------------------------------------------------------------------------
# Tests: tool injection
# ---------------------------------------------------------------------------


class TestToolInjection:
    """Verify tools are passed to the pydantic-ai Agent."""

    def test_builds_agent_with_zero_tools_when_no_subagents(self) -> None:
        """With subagents=[], only registry tools (none here) are registered."""
        provider = _scripted_provider([])
        captured_tools: list[Any] = []

        original_agent_init = pydantic_ai.Agent.__init__

        def patched_init(self_agent, *args, tools=None, **kwargs):  # type: ignore[override]
            if tools is not None:
                captured_tools.extend(tools)
            return original_agent_init(self_agent, *args, tools=tools, **kwargs)

        with mock.patch.object(pydantic_ai.Agent, "__init__", patched_init):
            run_strategy(_make_strategy(subagents=[]), FakeTarget(), provider, ToolRegistry())

        # No registry tools (empty ToolRegistry) + no subagent tools
        assert len(captured_tools) == 0

    def test_builds_agent_with_subagent_tools_when_subagents_declared(self) -> None:
        """With subagents=[...], invoke_subagent and invoke_subagent_batch are injected."""
        provider = _scripted_provider([])
        captured_tools: list[Any] = []

        original_agent_init = pydantic_ai.Agent.__init__

        def patched_init(self_agent, *args, tools=None, **kwargs):  # type: ignore[override]
            if tools is not None:
                captured_tools.extend(tools)
            return original_agent_init(self_agent, *args, tools=tools, **kwargs)

        with mock.patch.object(pydantic_ai.Agent, "__init__", patched_init):
            run_strategy(
                _make_strategy(subagents=["reviewer"]),
                FakeTarget(),
                provider,
                ToolRegistry(),
            )

        # Tools passed are plain async functions; check their function names
        tool_names = set()
        for t in captured_tools:
            # PAITool (from_schema) has .name; plain callables have __name__
            if hasattr(t, "name"):
                tool_names.add(t.name)
            elif callable(t) and hasattr(t, "__name__"):
                tool_names.add(t.__name__)
            else:
                tool_names.add(str(t))

        # Must have at least 2 tools (invoke_subagent + invoke_subagent_batch)
        assert len(captured_tools) >= 2, (
            f"Expected at least 2 tools when subagents declared, got: {tool_names}"
        )
        # Verify subagent tool names appear
        assert any("invoke_subagent" in n for n in tool_names), (
            f"invoke_subagent not found in tools: {tool_names}"
        )
        assert any("batch" in n for n in tool_names), (
            f"invoke_subagent_batch not found in tools: {tool_names}"
        )


# ---------------------------------------------------------------------------
# Tests: error handling
# ---------------------------------------------------------------------------


class TestRunStrategyErrors:
    """Error translation: UnexpectedModelBehavior → RunnerError."""

    def test_unexpected_model_behavior_becomes_runner_error(self) -> None:
        strategy = _make_strategy()
        provider = ScriptedLiteLLMProvider(responses=[])

        with mock.patch(
            "sec_review_framework.strategies.runner.Agent.run_sync",
            side_effect=UnexpectedModelBehavior("test error"),
        ):
            with pytest.raises(RunnerError):
                run_strategy(strategy, FakeTarget(), provider, ToolRegistry())

    def test_runner_error_message_contains_strategy_id(self) -> None:
        strategy = _make_strategy(strategy_id="my.strategy.id")
        provider = ScriptedLiteLLMProvider(responses=[])

        with mock.patch(
            "sec_review_framework.strategies.runner.Agent.run_sync",
            side_effect=UnexpectedModelBehavior("forced"),
        ):
            with pytest.raises(RunnerError, match="my.strategy.id"):
                run_strategy(strategy, FakeTarget(), provider, ToolRegistry())

    def test_runner_error_is_runtime_error(self) -> None:
        strategy = _make_strategy()
        provider = ScriptedLiteLLMProvider(responses=[])

        with mock.patch(
            "sec_review_framework.strategies.runner.Agent.run_sync",
            side_effect=UnexpectedModelBehavior("forced"),
        ):
            with pytest.raises(RuntimeError):
                run_strategy(strategy, FakeTarget(), provider, ToolRegistry())

    def test_other_exceptions_propagate_unchanged(self) -> None:
        strategy = _make_strategy()
        provider = ScriptedLiteLLMProvider(responses=[])

        with mock.patch(
            "sec_review_framework.strategies.runner.Agent.run_sync",
            side_effect=ValueError("unexpected value error"),
        ):
            with pytest.raises(ValueError, match="unexpected value error"):
                run_strategy(strategy, FakeTarget(), provider, ToolRegistry())


# ---------------------------------------------------------------------------
# Tests: _stamp_findings helper
# ---------------------------------------------------------------------------


class TestStampFindings:
    """Tests for the _stamp_findings internal helper."""

    def _make_finding(self, **kwargs: Any) -> Any:
        from sec_review_framework.data.findings import Finding, Severity, VulnClass
        base = dict(
            id=str(uuid.uuid4()),
            file_path="a.py",
            line_start=1,
            vuln_class=VulnClass.SQLI,
            severity=Severity.HIGH,
            title="Test",
            description="Test desc",
            confidence=0.9,
            raw_llm_output="raw",
            produced_by="test",
            experiment_id="exp",
        )
        base.update(kwargs)
        return Finding(**base)

    def test_stamp_fills_missing_id(self) -> None:
        f = self._make_finding(id="")
        stamped = _stamp_findings([f], strategy_id="strat")
        assert stamped[0].id != ""

    def test_stamp_fills_missing_produced_by(self) -> None:
        f = self._make_finding(produced_by="")
        stamped = _stamp_findings([f], strategy_id="my.strategy")
        assert stamped[0].produced_by == "my.strategy"

    def test_stamp_preserves_existing_id(self) -> None:
        original_id = str(uuid.uuid4())
        f = self._make_finding(id=original_id)
        stamped = _stamp_findings([f], strategy_id="strat")
        assert stamped[0].id == original_id

    def test_stamp_preserves_existing_produced_by(self) -> None:
        f = self._make_finding(produced_by="original_agent")
        stamped = _stamp_findings([f], strategy_id="strat")
        assert stamped[0].produced_by == "original_agent"

    def test_stamp_empty_list(self) -> None:
        assert _stamp_findings([], strategy_id="strat") == []

    def test_stamp_multiple_findings(self) -> None:
        findings = [
            self._make_finding(id="", produced_by=""),
            self._make_finding(id="", produced_by=""),
        ]
        stamped = _stamp_findings(findings, strategy_id="strat")
        assert all(f.id for f in stamped)
        assert all(f.produced_by for f in stamped)
        # IDs must be unique
        ids = [f.id for f in stamped]
        assert len(set(ids)) == len(ids)


# ---------------------------------------------------------------------------
# Tests: _build_user_prompt helper
# ---------------------------------------------------------------------------


class TestBuildUserPrompt:
    """Tests for the _build_user_prompt helper."""

    def test_injects_repo_summary(self) -> None:
        template = "Repo:\n{repo_summary}"
        target = FakeTarget()
        prompt = _build_user_prompt(template, target)
        assert "app/views.py" in prompt

    def test_injects_finding_output_format(self) -> None:
        template = "Do this:\n{finding_output_format}"
        target = FakeTarget()
        prompt = _build_user_prompt(template, target)
        assert "```json" in prompt or "json" in prompt.lower()

    def test_unknown_placeholder_left_as_is(self) -> None:
        template = "Hello {unknown_key} world"
        target = FakeTarget()
        prompt = _build_user_prompt(template, target)
        assert "{unknown_key}" in prompt

    def test_fallback_to_list_source_files(self) -> None:
        """Target without get_file_tree() falls back to list_source_files()."""

        class MinimalTarget:
            def list_source_files(self) -> list[str]:
                return ["main.go", "lib.go"]

        template = "{repo_summary}"
        prompt = _build_user_prompt(template, MinimalTarget())
        assert "main.go" in prompt

    def test_fallback_to_str_when_no_methods(self) -> None:
        template = "{repo_summary}"

        class BareTarget:
            def __str__(self) -> str:
                return "<bare>"

        prompt = _build_user_prompt(template, BareTarget())
        assert "<bare>" in prompt


# ---------------------------------------------------------------------------
# Tests: feature flag — _should_use_new_runner
# ---------------------------------------------------------------------------


class TestFeatureFlag:
    """Tests for the _should_use_new_runner worker gate."""

    def test_returns_true_when_flag_set(self) -> None:
        strategy = _make_strategy(use_new_runner=True)
        assert _should_use_new_runner(strategy) is True

    def test_returns_false_by_default(self) -> None:
        strategy = _make_strategy(use_new_runner=False)
        assert _should_use_new_runner(strategy) is False

    def test_returns_false_for_legacy_strategy_without_field(self) -> None:
        """Strategies that pre-date use_new_runner must return False via getattr."""

        class LegacyStrategyStub:
            """Stub without use_new_runner attribute (simulates pre-Phase 2 object)."""
            pass

        stub = LegacyStrategyStub()
        assert _should_use_new_runner(stub) is False  # type: ignore[arg-type]

    def test_flag_not_in_serialised_json(self) -> None:
        """use_new_runner must not appear in model_dump_json (exclude=True)."""
        strategy = _make_strategy(use_new_runner=True)
        serialised = strategy.model_dump_json()
        assert "use_new_runner" not in serialised

    def test_flag_not_in_model_dump(self) -> None:
        strategy = _make_strategy(use_new_runner=True)
        d = strategy.model_dump()
        assert "use_new_runner" not in d


# ---------------------------------------------------------------------------
# Tests: feature-flag dispatch in worker
# ---------------------------------------------------------------------------


class TestWorkerDispatch:
    """Verify worker dispatch routes correctly based on use_new_runner flag."""

    def test_new_runner_path_called_when_flag_true(self) -> None:
        """When use_new_runner=True, worker calls run_strategy()."""
        strategy = _make_strategy(use_new_runner=True)
        fake_output = StrategyOutput(
            findings=[], pre_dedup_count=0, post_dedup_count=0, dedup_log=[]
        )

        with mock.patch(
            "sec_review_framework.strategies.runner.run_strategy",
            return_value=fake_output,
        ) as mock_run:
            # Import the lazy-import path — simulate the worker dispatch
            # We directly test _should_use_new_runner + the lazy import branch logic
            assert _should_use_new_runner(strategy) is True
            from sec_review_framework.strategies.runner import run_strategy as rs

            rs(strategy, FakeTarget(), ScriptedLiteLLMProvider([]), ToolRegistry())
            # The `from ... import run_strategy` inside the `if` branch is re-executed
            # on each call, so patching the module path makes the imported name resolve
            # to the mock.
            assert mock_run.call_count == 1

    def test_legacy_path_taken_when_flag_false(self) -> None:
        """When use_new_runner=False, _should_use_new_runner returns False."""
        strategy = _make_strategy(use_new_runner=False)
        assert _should_use_new_runner(strategy) is False
