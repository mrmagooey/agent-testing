"""Unit tests for :mod:`sec_review_framework.agent.subagent`.

Tests cover:
- SubagentDeps construction and child_deps()
- SubagentOutput Pydantic model
- invoke_subagent: caps enforcement (depth, invocations, unknown role)
- invoke_subagent_batch: caps enforcement (batch size, invocations, unknown role)
- Batch parallel dispatch (independence of results)
- Child agent isolation (no shared state between invocations)

Tests that exercise _run_child_sync use a patched LiteLLMProvider so no
network calls are made.

Skipped cleanly when the ``agent`` extra (pydantic-ai) is not installed.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Skip the entire module if pydantic_ai is not installed.
pydantic_ai = pytest.importorskip("pydantic_ai")

from pydantic_ai import RunContext  # noqa: E402
from pydantic_ai.exceptions import ModelRetry  # noqa: E402

from sec_review_framework.agent.subagent import (  # noqa: E402
    SubagentDeps,
    SubagentOutput,
    _check_caps,
    resolve_role,
    _run_child_sync,
    make_invoke_subagent_batch_tool,
    make_invoke_subagent_tool,
)
from sec_review_framework.data.strategy_bundle import (  # noqa: E402
    OrchestrationShape,
    StrategyBundleDefault,
    UserStrategy,
)
from sec_review_framework.models.base import Message, ToolDefinition  # noqa: E402
from sec_review_framework.models.base import ModelResponse as FrameworkModelResponse  # noqa: E402
from sec_review_framework.models.litellm_provider import LiteLLMProvider  # noqa: E402
from sec_review_framework.tools.registry import ToolRegistry  # noqa: E402

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_strategy(
    model_id: str = "fake/test",
    strategy_id: str = "test.strategy",
    system_prompt: str = "You are a reviewer.",
) -> UserStrategy:
    """Construct a minimal single-agent UserStrategy for testing."""
    return UserStrategy(
        id=strategy_id,
        name=f"Test strategy: {strategy_id}",
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.SINGLE_AGENT,
        default=StrategyBundleDefault(
            system_prompt=system_prompt,
            user_prompt_template="Review this.",
            model_id=model_id,
            tools=frozenset(),
            verification="none",
            max_turns=10,
            tool_extensions=frozenset(),
        ),
        overrides=[],
        created_at=datetime(2026, 1, 1),
        is_builtin=False,
    )


def _make_deps(
    available_roles: set[str] | None = None,
    strategies: dict[str, UserStrategy] | None = None,
    depth: int = 0,
    max_depth: int = 3,
    invocations: int = 0,
    max_invocations: int = 100,
    max_batch_size: int = 32,
) -> SubagentDeps:
    """Build a SubagentDeps with sensible test defaults."""
    if available_roles is None:
        available_roles = set()
    if strategies is None:
        strategies = {}
    return SubagentDeps(
        depth=depth,
        max_depth=max_depth,
        invocations=invocations,
        max_invocations=max_invocations,
        max_batch_size=max_batch_size,
        available_roles=available_roles,
        subagent_strategies=strategies,
        tool_registry=ToolRegistry(),
    )


def _make_run_context(deps: SubagentDeps) -> RunContext[SubagentDeps]:
    """Build a minimal RunContext wrapping *deps*."""
    ctx = MagicMock(spec=RunContext)
    ctx.deps = deps
    ctx.run_id = "test-run-id"
    return ctx


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


# ---------------------------------------------------------------------------
# Tests: SubagentDeps
# ---------------------------------------------------------------------------


class TestSubagentDeps:
    """Tests for SubagentDeps construction and child_deps()."""

    def test_default_construction(self) -> None:
        deps = SubagentDeps()
        assert deps.depth == 0
        assert deps.max_depth == 3
        assert deps.invocations == 0
        assert deps.max_invocations == 100
        assert deps.max_batch_size == 32
        assert deps.available_roles == set()
        assert deps.subagent_strategies == {}

    def test_child_deps_depth_incremented(self) -> None:
        deps = SubagentDeps(depth=0, max_depth=5)
        child = deps.child_deps()
        assert child.depth == 1

    def test_child_deps_max_depth_inherited(self) -> None:
        deps = SubagentDeps(depth=0, max_depth=7)
        child = deps.child_deps()
        assert child.max_depth == 7

    def test_child_deps_caps_inherited(self) -> None:
        deps = SubagentDeps(max_invocations=50, max_batch_size=16)
        child = deps.child_deps()
        assert child.max_invocations == 50
        assert child.max_batch_size == 16

    def test_child_deps_strategies_copied(self) -> None:
        strategy = _make_strategy()
        deps = SubagentDeps(
            available_roles={"reviewer"},
            subagent_strategies={"reviewer": strategy},
        )
        child = deps.child_deps()
        assert "reviewer" in child.available_roles
        assert "reviewer" in child.subagent_strategies

    def test_child_deps_tool_registry_cloned(self) -> None:
        registry = ToolRegistry()
        deps = SubagentDeps(tool_registry=registry)
        child = deps.child_deps()
        # Child registry is a distinct object (clone)
        assert child.tool_registry is not registry

    def test_child_deps_registry_has_independent_audit_log(self) -> None:
        registry = ToolRegistry()
        deps = SubagentDeps(tool_registry=registry)
        child = deps.child_deps()
        assert child.tool_registry.audit_log is not registry.audit_log


# ---------------------------------------------------------------------------
# Tests: SubagentOutput
# ---------------------------------------------------------------------------


class TestSubagentOutput:
    """Tests for SubagentOutput Pydantic model."""

    def test_basic_construction(self) -> None:
        output = SubagentOutput(
            role="reviewer",
            output={"findings": []},
            usage={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )
        assert output.role == "reviewer"
        assert output.output == {"findings": []}
        assert output.usage["total_tokens"] == 15

    def test_output_accepts_any_type(self) -> None:
        # output field is Any
        output_str = SubagentOutput(role="r", output="plain string", usage={})
        output_list = SubagentOutput(role="r", output=[1, 2, 3], usage={})
        output_none = SubagentOutput(role="r", output=None, usage={})
        assert output_str.output == "plain string"
        assert output_list.output == [1, 2, 3]
        assert output_none.output is None

    def test_usage_can_be_empty_dict(self) -> None:
        output = SubagentOutput(role="r", output="x", usage={})
        assert output.usage == {}


# ---------------------------------------------------------------------------
# Tests: _check_caps
# ---------------------------------------------------------------------------


class TestCheckCaps:
    """Tests for the internal _check_caps helper."""

    def test_no_violation_does_not_raise(self) -> None:
        deps = _make_deps(depth=0, max_depth=3, invocations=0, max_invocations=10)
        ctx = _make_run_context(deps)
        # Should not raise
        _check_caps(ctx, count=1)

    def test_depth_at_max_raises_model_retry(self) -> None:
        deps = _make_deps(depth=3, max_depth=3)
        ctx = _make_run_context(deps)
        with pytest.raises(ModelRetry, match="depth cap"):
            _check_caps(ctx, count=1)

    def test_depth_above_max_raises_model_retry(self) -> None:
        deps = _make_deps(depth=5, max_depth=3)
        ctx = _make_run_context(deps)
        with pytest.raises(ModelRetry):
            _check_caps(ctx, count=1)

    def test_invocation_cap_exceeded_raises_model_retry(self) -> None:
        deps = _make_deps(invocations=9, max_invocations=10)
        ctx = _make_run_context(deps)
        with pytest.raises(ModelRetry, match="invocation cap"):
            _check_caps(ctx, count=2)  # 9 + 2 = 11 > 10

    def test_invocation_cap_exact_boundary_raises(self) -> None:
        deps = _make_deps(invocations=10, max_invocations=10)
        ctx = _make_run_context(deps)
        with pytest.raises(ModelRetry):
            _check_caps(ctx, count=1)

    def test_invocation_cap_just_under_boundary_does_not_raise(self) -> None:
        deps = _make_deps(invocations=9, max_invocations=10)
        ctx = _make_run_context(deps)
        # 9 + 1 = 10 == 10, not > 10, so should pass
        _check_caps(ctx, count=1)


# ---------------------------------------------------------------------------
# Tests: invoke_subagent tool — caps and role validation
# ---------------------------------------------------------------------------


class TestInvokeSubagentTool:
    """Tests for make_invoke_subagent_tool."""

    @pytest.mark.asyncio
    async def test_unknown_role_raises_model_retry(self) -> None:
        invoke_subagent = make_invoke_subagent_tool()
        deps = _make_deps(available_roles={"known_role"})
        ctx = _make_run_context(deps)

        with pytest.raises(ModelRetry, match="Unknown subagent role"):
            await invoke_subagent(ctx, role="unknown_role", input={})

    @pytest.mark.asyncio
    async def test_depth_cap_raises_model_retry(self) -> None:
        invoke_subagent = make_invoke_subagent_tool()
        strategy = _make_strategy()
        deps = _make_deps(
            available_roles={"reviewer"},
            strategies={"reviewer": strategy},
            depth=3,
            max_depth=3,
        )
        ctx = _make_run_context(deps)

        with pytest.raises(ModelRetry, match="depth cap"):
            await invoke_subagent(ctx, role="reviewer", input={"task": "review"})

    @pytest.mark.asyncio
    async def test_invocation_cap_raises_model_retry(self) -> None:
        invoke_subagent = make_invoke_subagent_tool()
        strategy = _make_strategy()
        deps = _make_deps(
            available_roles={"reviewer"},
            strategies={"reviewer": strategy},
            invocations=100,
            max_invocations=100,
        )
        ctx = _make_run_context(deps)

        with pytest.raises(ModelRetry, match="invocation cap"):
            await invoke_subagent(ctx, role="reviewer", input={})

    @pytest.mark.asyncio
    async def test_invocation_counter_incremented_on_success(self) -> None:
        """Successful invocation increments deps.invocations by 1."""
        strategy = _make_strategy(model_id="fake/test")

        scripted = ScriptedLiteLLMProvider(
            responses=[{"content": "review complete", "tool_calls": [], "input_tokens": 5, "output_tokens": 3}]
        )

        invoke_subagent = make_invoke_subagent_tool()
        deps = _make_deps(
            available_roles={"reviewer"},
            strategies={"reviewer": strategy},
            invocations=0,
        )
        ctx = _make_run_context(deps)

        with patch(
            "sec_review_framework.agent.subagent.LiteLLMProvider",
            return_value=scripted,
        ):
            await invoke_subagent(ctx, role="reviewer", input={"code": "x = 1"})

        assert deps.invocations == 1

    @pytest.mark.asyncio
    async def test_returns_subagent_output_on_success(self) -> None:
        """Successful invocation returns a SubagentOutput with the role name."""
        strategy = _make_strategy(model_id="fake/test")
        scripted = ScriptedLiteLLMProvider(
            responses=[
                {"content": "no issues found", "tool_calls": [], "input_tokens": 5, "output_tokens": 3}
            ]
        )

        invoke_subagent = make_invoke_subagent_tool()
        deps = _make_deps(
            available_roles={"reviewer"},
            strategies={"reviewer": strategy},
        )
        ctx = _make_run_context(deps)

        with patch(
            "sec_review_framework.agent.subagent.LiteLLMProvider",
            return_value=scripted,
        ):
            result = await invoke_subagent(ctx, role="reviewer", input={"code": "pass"})

        assert isinstance(result, SubagentOutput)
        assert result.role == "test.strategy"  # strategy.id
        assert "usage" in result.__dict__ or result.usage is not None


# ---------------------------------------------------------------------------
# Tests: invoke_subagent_batch tool — caps and parallel dispatch
# ---------------------------------------------------------------------------


class TestInvokeSubagentBatchTool:
    """Tests for make_invoke_subagent_batch_tool."""

    @pytest.mark.asyncio
    async def test_unknown_role_raises_model_retry(self) -> None:
        invoke_batch = make_invoke_subagent_batch_tool()
        deps = _make_deps(available_roles={"known_role"})
        ctx = _make_run_context(deps)

        with pytest.raises(ModelRetry, match="Unknown subagent role"):
            await invoke_batch(ctx, role="unknown_role", inputs=[{}])

    @pytest.mark.asyncio
    async def test_batch_too_large_raises_model_retry(self) -> None:
        invoke_batch = make_invoke_subagent_batch_tool()
        strategy = _make_strategy()
        deps = _make_deps(
            available_roles={"reviewer"},
            strategies={"reviewer": strategy},
            max_batch_size=3,
        )
        ctx = _make_run_context(deps)

        with pytest.raises(ModelRetry, match="Batch too large"):
            await invoke_batch(ctx, role="reviewer", inputs=[{}] * 4)

    @pytest.mark.asyncio
    async def test_invocation_cap_exceeded_raises_model_retry(self) -> None:
        invoke_batch = make_invoke_subagent_batch_tool()
        strategy = _make_strategy()
        deps = _make_deps(
            available_roles={"reviewer"},
            strategies={"reviewer": strategy},
            invocations=8,
            max_invocations=10,
        )
        ctx = _make_run_context(deps)

        # 8 + 4 = 12 > 10
        with pytest.raises(ModelRetry, match="invocation cap"):
            await invoke_batch(ctx, role="reviewer", inputs=[{}] * 4)

    @pytest.mark.asyncio
    async def test_depth_cap_raises_model_retry(self) -> None:
        invoke_batch = make_invoke_subagent_batch_tool()
        strategy = _make_strategy()
        deps = _make_deps(
            available_roles={"reviewer"},
            strategies={"reviewer": strategy},
            depth=3,
            max_depth=3,
        )
        ctx = _make_run_context(deps)

        with pytest.raises(ModelRetry, match="depth cap"):
            await invoke_batch(ctx, role="reviewer", inputs=[{}])

    @pytest.mark.asyncio
    async def test_invocation_counter_incremented_by_batch_size(self) -> None:
        """Batch invocation increments deps.invocations by len(inputs)."""
        strategy = _make_strategy(model_id="fake/test")

        def _make_scripted() -> ScriptedLiteLLMProvider:
            return ScriptedLiteLLMProvider(
                responses=[{"content": "done", "tool_calls": [], "input_tokens": 5, "output_tokens": 3}]
            )

        invoke_batch = make_invoke_subagent_batch_tool()
        deps = _make_deps(
            available_roles={"reviewer"},
            strategies={"reviewer": strategy},
            invocations=0,
        )
        ctx = _make_run_context(deps)

        with patch(
            "sec_review_framework.agent.subagent.LiteLLMProvider",
            side_effect=[_make_scripted(), _make_scripted(), _make_scripted()],
        ):
            await invoke_batch(ctx, role="reviewer", inputs=[{}, {}, {}])

        assert deps.invocations == 3

    @pytest.mark.asyncio
    async def test_batch_returns_list_of_subagent_outputs(self) -> None:
        """Batch invocation returns one SubagentOutput per input."""
        strategy = _make_strategy(model_id="fake/test")

        def _make_scripted() -> ScriptedLiteLLMProvider:
            return ScriptedLiteLLMProvider(
                responses=[{"content": "result", "tool_calls": [], "input_tokens": 5, "output_tokens": 3}]
            )

        invoke_batch = make_invoke_subagent_batch_tool()
        deps = _make_deps(
            available_roles={"reviewer"},
            strategies={"reviewer": strategy},
        )
        ctx = _make_run_context(deps)

        with patch(
            "sec_review_framework.agent.subagent.LiteLLMProvider",
            side_effect=[_make_scripted(), _make_scripted()],
        ):
            results = await invoke_batch(ctx, role="reviewer", inputs=[{"a": 1}, {"a": 2}])

        assert isinstance(results, list)
        assert len(results) == 2
        assert all(isinstance(r, SubagentOutput) for r in results)

    @pytest.mark.asyncio
    async def test_empty_batch_is_accepted(self) -> None:
        """Empty inputs list is valid (0 invocations dispatched)."""
        invoke_batch = make_invoke_subagent_batch_tool()
        strategy = _make_strategy()
        deps = _make_deps(
            available_roles={"reviewer"},
            strategies={"reviewer": strategy},
        )
        ctx = _make_run_context(deps)

        results = await invoke_batch(ctx, role="reviewer", inputs=[])
        assert results == []
        assert deps.invocations == 0


# ---------------------------------------------------------------------------
# Tests: child isolation
# ---------------------------------------------------------------------------


class TestChildIsolation:
    """Verify that child agents run in isolation — no shared state."""

    def test_each_child_gets_fresh_provider(self) -> None:
        """Each _run_child_sync call builds a new LiteLLMProvider.

        _run_child_sync must be called from a thread with no running event
        loop — this test uses asyncio.run in its own thread to simulate the
        production ThreadPoolExecutor environment.
        """
        import concurrent.futures

        strategy = _make_strategy()
        calls: list[str] = []

        def _fake_provider(model_name: str) -> ScriptedLiteLLMProvider:
            calls.append(model_name)
            return ScriptedLiteLLMProvider(
                responses=[{"content": "done", "tool_calls": [], "input_tokens": 5, "output_tokens": 3}]
            )

        deps = SubagentDeps(
            depth=0,
            max_depth=3,
            invocations=0,
            max_invocations=100,
            max_batch_size=32,
            available_roles={"reviewer"},
            subagent_strategies={"reviewer": strategy},
            tool_registry=ToolRegistry(),
        )

        def run_in_thread(inp: dict) -> SubagentOutput:
            with patch(
                "sec_review_framework.agent.subagent.LiteLLMProvider",
                side_effect=_fake_provider,
            ):
                return _run_child_sync(strategy, inp, deps)

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(run_in_thread, {"input": "a"}), pool.submit(run_in_thread, {"input": "b"})]
            [f.result() for f in futures]

        # Each call creates a fresh provider
        assert len(calls) == 2

    def test_child_registry_is_cloned(self) -> None:
        """Child receives a clone of the parent registry, not the same object.

        _run_child_sync must be called from a thread with no running event loop.
        """
        import concurrent.futures

        strategy = _make_strategy()
        parent_registry = ToolRegistry()
        deps = SubagentDeps(
            depth=0,
            max_depth=3,
            invocations=0,
            max_invocations=100,
            max_batch_size=32,
            available_roles={"reviewer"},
            subagent_strategies={"reviewer": strategy},
            tool_registry=parent_registry,
        )

        clones_used: list[ToolRegistry] = []
        original_clone = parent_registry.clone

        def _capture_clone() -> ToolRegistry:
            clone = original_clone()
            clones_used.append(clone)
            return clone

        def run_in_thread() -> SubagentOutput:
            scripted = ScriptedLiteLLMProvider(
                responses=[{"content": "done", "tool_calls": [], "input_tokens": 5, "output_tokens": 3}]
            )
            with patch.object(parent_registry, "clone", side_effect=_capture_clone), patch(
                "sec_review_framework.agent.subagent.LiteLLMProvider",
                return_value=scripted,
            ):
                return _run_child_sync(strategy, {}, deps)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(run_in_thread).result()

        # clone() was called at least once (child_deps + make_tool_callables)
        assert len(clones_used) >= 1


# ---------------------------------------------------------------------------
# Tests: resolve_role helper
# ---------------------------------------------------------------------------


class TestResolveRole:
    """Tests for the resolve_role helper function."""

    def test_exact_match_returns_role(self) -> None:
        available = {"builtin_v2.sqli_specialist", "builtin_v2.xss_specialist"}
        assert resolve_role("builtin_v2.sqli_specialist", available) == "builtin_v2.sqli_specialist"

    def test_bare_suffix_resolves_to_namespaced(self) -> None:
        available = {"builtin_v2.sqli_specialist"}
        assert resolve_role("sqli_specialist", available) == "builtin_v2.sqli_specialist"

    def test_ambiguous_bare_raises_model_retry(self) -> None:
        available = {"builtin_v2.sqli_specialist", "experimental.sqli_specialist"}
        with pytest.raises(ModelRetry, match="Ambiguous role"):
            resolve_role("sqli_specialist", available)

    def test_unknown_role_returns_none(self) -> None:
        available = {"builtin_v2.sqli_specialist"}
        assert resolve_role("nonexistent_role", available) is None

    def test_empty_available_returns_none(self) -> None:
        assert resolve_role("sqli_specialist", set()) is None

    def test_exact_match_preferred_over_suffix_match(self) -> None:
        """If the bare name is itself in available_roles, exact match wins."""
        available = {"sqli_specialist", "builtin_v2.sqli_specialist"}
        # "sqli_specialist" is an exact match — should not be ambiguous
        assert resolve_role("sqli_specialist", available) == "sqli_specialist"

    def test_multiple_suffix_matches_raises(self) -> None:
        available = {"ns1.foo_role", "ns2.foo_role", "ns3.foo_role"}
        with pytest.raises(ModelRetry, match="Ambiguous role"):
            resolve_role("foo_role", available)


# ---------------------------------------------------------------------------
# Tests: invoke_subagent role resolution
# ---------------------------------------------------------------------------


class TestInvokeSubagentRoleResolution:
    """Tests that invoke_subagent resolves bare and namespaced roles."""

    @pytest.mark.asyncio
    async def test_resolves_bare_role_name(self) -> None:
        """Bare role name 'sqli_specialist' resolves to 'builtin_v2.sqli_specialist'."""
        strategy = _make_strategy(strategy_id="builtin_v2.sqli_specialist")
        scripted = ScriptedLiteLLMProvider(
            responses=[{"content": "done", "tool_calls": [], "input_tokens": 5, "output_tokens": 3}]
        )

        invoke_subagent = make_invoke_subagent_tool()
        deps = _make_deps(
            available_roles={"builtin_v2.sqli_specialist"},
            strategies={"builtin_v2.sqli_specialist": strategy},
        )
        ctx = _make_run_context(deps)

        with patch(
            "sec_review_framework.agent.subagent.LiteLLMProvider",
            return_value=scripted,
        ):
            await invoke_subagent(ctx, role="sqli_specialist", input={"vuln_class": "sqli"})

        # Log must record the fully-namespaced role
        assert len(deps.single_call_log) == 1
        logged_role, _ = deps.single_call_log[0]
        assert logged_role == "builtin_v2.sqli_specialist"

    @pytest.mark.asyncio
    async def test_resolves_full_namespaced_role(self) -> None:
        """Full namespaced role 'builtin_v2.sqli_specialist' is accepted directly."""
        strategy = _make_strategy(strategy_id="builtin_v2.sqli_specialist")
        scripted = ScriptedLiteLLMProvider(
            responses=[{"content": "done", "tool_calls": [], "input_tokens": 5, "output_tokens": 3}]
        )

        invoke_subagent = make_invoke_subagent_tool()
        deps = _make_deps(
            available_roles={"builtin_v2.sqli_specialist"},
            strategies={"builtin_v2.sqli_specialist": strategy},
        )
        ctx = _make_run_context(deps)

        with patch(
            "sec_review_framework.agent.subagent.LiteLLMProvider",
            return_value=scripted,
        ):
            await invoke_subagent(
                ctx, role="builtin_v2.sqli_specialist", input={"vuln_class": "sqli"}
            )

        assert len(deps.single_call_log) == 1
        logged_role, _ = deps.single_call_log[0]
        assert logged_role == "builtin_v2.sqli_specialist"

    @pytest.mark.asyncio
    async def test_ambiguous_bare_role_raises_model_retry(self) -> None:
        """Bare name matching two namespaces raises ModelRetry with ambiguity message."""
        strategy = _make_strategy(strategy_id="builtin_v2.sqli_specialist")
        invoke_subagent = make_invoke_subagent_tool()
        deps = _make_deps(
            available_roles={"builtin_v2.sqli_specialist", "experimental.sqli_specialist"},
            strategies={
                "builtin_v2.sqli_specialist": strategy,
                "experimental.sqli_specialist": strategy,
            },
        )
        ctx = _make_run_context(deps)

        with pytest.raises(ModelRetry, match="Ambiguous role"):
            await invoke_subagent(ctx, role="sqli_specialist", input={})

    @pytest.mark.asyncio
    async def test_unknown_role_raises_model_retry_with_available(self) -> None:
        """Completely unknown role raises ModelRetry listing available roles."""
        invoke_subagent = make_invoke_subagent_tool()
        deps = _make_deps(available_roles={"builtin_v2.sqli_specialist"})
        ctx = _make_run_context(deps)

        with pytest.raises(ModelRetry, match="Unknown subagent role"):
            await invoke_subagent(ctx, role="totally_unknown_role", input={})


# ---------------------------------------------------------------------------
# Tests: invoke_subagent_batch role resolution
# ---------------------------------------------------------------------------


class TestInvokeSubagentBatchRoleResolution:
    """Tests that invoke_subagent_batch resolves bare and namespaced roles."""

    @pytest.mark.asyncio
    async def test_resolves_bare_role_name(self) -> None:
        """Bare role name resolves to full namespaced ID in batch_call_log."""
        strategy = _make_strategy(strategy_id="builtin_v2.sqli_specialist")

        def _make_scripted() -> ScriptedLiteLLMProvider:
            return ScriptedLiteLLMProvider(
                responses=[{"content": "done", "tool_calls": [], "input_tokens": 5, "output_tokens": 3}]
            )

        invoke_batch = make_invoke_subagent_batch_tool()
        deps = _make_deps(
            available_roles={"builtin_v2.sqli_specialist"},
            strategies={"builtin_v2.sqli_specialist": strategy},
        )
        ctx = _make_run_context(deps)

        with patch(
            "sec_review_framework.agent.subagent.LiteLLMProvider",
            side_effect=[_make_scripted(), _make_scripted()],
        ):
            await invoke_batch(
                ctx,
                role="sqli_specialist",
                inputs=[{"vuln_class": "sqli"}, {"vuln_class": "sqli"}],
            )

        assert len(deps.batch_call_log) == 1
        logged_role, _ = deps.batch_call_log[0]
        assert logged_role == "builtin_v2.sqli_specialist"

    @pytest.mark.asyncio
    async def test_resolves_full_namespaced_role(self) -> None:
        """Full namespaced role is recorded as-is in batch_call_log."""
        strategy = _make_strategy(strategy_id="builtin_v2.sqli_specialist")

        def _make_scripted() -> ScriptedLiteLLMProvider:
            return ScriptedLiteLLMProvider(
                responses=[{"content": "done", "tool_calls": [], "input_tokens": 5, "output_tokens": 3}]
            )

        invoke_batch = make_invoke_subagent_batch_tool()
        deps = _make_deps(
            available_roles={"builtin_v2.sqli_specialist"},
            strategies={"builtin_v2.sqli_specialist": strategy},
        )
        ctx = _make_run_context(deps)

        with patch(
            "sec_review_framework.agent.subagent.LiteLLMProvider",
            side_effect=[_make_scripted()],
        ):
            await invoke_batch(
                ctx,
                role="builtin_v2.sqli_specialist",
                inputs=[{"vuln_class": "sqli"}],
            )

        assert len(deps.batch_call_log) == 1
        logged_role, _ = deps.batch_call_log[0]
        assert logged_role == "builtin_v2.sqli_specialist"

    @pytest.mark.asyncio
    async def test_ambiguous_bare_role_raises_model_retry(self) -> None:
        """Bare name matching two namespaces raises ModelRetry."""
        strategy = _make_strategy(strategy_id="builtin_v2.sqli_specialist")
        invoke_batch = make_invoke_subagent_batch_tool()
        deps = _make_deps(
            available_roles={"builtin_v2.sqli_specialist", "experimental.sqli_specialist"},
            strategies={
                "builtin_v2.sqli_specialist": strategy,
                "experimental.sqli_specialist": strategy,
            },
        )
        ctx = _make_run_context(deps)

        with pytest.raises(ModelRetry, match="Ambiguous role"):
            await invoke_batch(ctx, role="sqli_specialist", inputs=[{}])

    @pytest.mark.asyncio
    async def test_unknown_role_raises_model_retry_with_available(self) -> None:
        """Completely unknown role raises ModelRetry listing available roles."""
        invoke_batch = make_invoke_subagent_batch_tool()
        deps = _make_deps(available_roles={"builtin_v2.sqli_specialist"})
        ctx = _make_run_context(deps)

        with pytest.raises(ModelRetry, match="Unknown subagent role"):
            await invoke_batch(ctx, role="totally_unknown_role", inputs=[{}])


# ---------------------------------------------------------------------------
# Regression tests for bugs #4, #7, #10, #11
# ---------------------------------------------------------------------------


class TestModelProviderCache:
    """Regression tests for bugs #10 and #4/#11 (provider sharing via cache)."""

    def test_two_children_same_model_id_share_provider(self) -> None:
        """Bug #10 regression: two children with same model_id share one ModelProvider.

        When model_provider_cache is set on SubagentDeps, _run_child fetches the
        provider from the cache keyed by model_id.  Two invocations for the same
        model_id must return the *same object*, not construct separate instances.
        """
        from sec_review_framework.strategies.common import ModelProviderCache

        # One scripted provider that can serve two responses sequentially.
        shared_provider = ScriptedLiteLLMProvider(
            responses=[
                {"content": "child-a", "tool_calls": [], "input_tokens": 7, "output_tokens": 3},
                {"content": "child-b", "tool_calls": [], "input_tokens": 8, "output_tokens": 4},
            ],
            model_name="fake/shared",
        )
        cache = ModelProviderCache()
        cache.put("fake/shared", shared_provider)

        strategy = _make_strategy(model_id="fake/shared")
        deps = SubagentDeps(
            depth=0,
            max_depth=3,
            invocations=0,
            max_invocations=100,
            max_batch_size=32,
            available_roles={"reviewer"},
            subagent_strategies={"reviewer": strategy},
            tool_registry=ToolRegistry(),
            model_provider_cache=cache,
        )

        # Run two children sequentially; both must use the shared provider.
        import asyncio

        async def _run_two():
            await _run_child(strategy, {"input": "a"}, deps)
            await _run_child(strategy, {"input": "b"}, deps)

        asyncio.run(_run_two())

        # The shared provider should have accumulated both invocations.
        assert len(shared_provider.token_log) == 2
        # No new provider was constructed outside the cache.
        assert "fake/shared" in cache

    def test_child_token_log_accumulates_on_shared_provider(self) -> None:
        """Bug #4 regression: child token usage accumulates on the shared provider.

        After two child invocations, the shared provider's token_log must contain
        entries for every child call so callers can sum the total spend.
        """
        from sec_review_framework.strategies.common import ModelProviderCache

        shared_provider = ScriptedLiteLLMProvider(
            responses=[
                {"content": "r1", "tool_calls": [], "input_tokens": 10, "output_tokens": 5},
                {"content": "r2", "tool_calls": [], "input_tokens": 20, "output_tokens": 8},
            ],
            model_name="fake/billing",
        )
        cache = ModelProviderCache()
        cache.put("fake/billing", shared_provider)

        strategy = _make_strategy(model_id="fake/billing")
        deps = SubagentDeps(
            depth=0,
            max_depth=3,
            invocations=0,
            max_invocations=100,
            max_batch_size=32,
            available_roles={"specialist"},
            subagent_strategies={"specialist": strategy},
            tool_registry=ToolRegistry(),
            model_provider_cache=cache,
        )

        import asyncio

        async def _run_both():
            await asyncio.gather(
                _run_child(strategy, {"x": 1}, deps),
                _run_child(strategy, {"x": 2}, deps),
            )

        asyncio.run(_run_both())

        # Both child calls contributed token entries.
        assert len(shared_provider.token_log) == 2
        total_input = sum(e.input_tokens for e in shared_provider.token_log)
        assert total_input == 30  # 10 + 20

    def test_child_conversation_log_accumulates_on_shared_provider(self) -> None:
        """Bug #11 regression: child conversation turns accumulate on shared provider.

        conversation_log must contain entries from every child invocation so
        worker.py can write a complete conversation.jsonl artifact.
        """
        from sec_review_framework.strategies.common import ModelProviderCache

        shared_provider = ScriptedLiteLLMProvider(
            responses=[
                {"content": "turn1", "tool_calls": [], "input_tokens": 5, "output_tokens": 3},
            ],
            model_name="fake/conv",
        )
        cache = ModelProviderCache()
        cache.put("fake/conv", shared_provider)

        strategy = _make_strategy(model_id="fake/conv")
        deps = SubagentDeps(
            depth=0,
            max_depth=3,
            invocations=0,
            max_invocations=100,
            max_batch_size=32,
            available_roles={"specialist"},
            subagent_strategies={"specialist": strategy},
            tool_registry=ToolRegistry(),
            model_provider_cache=cache,
        )

        import asyncio

        asyncio.run(_run_child(strategy, {"q": "hello"}, deps))

        # The shared provider's conversation_log must contain entries from the child.
        assert len(shared_provider.conversation_log) > 0

    def test_child_deps_inherits_cache(self) -> None:
        """Bug #10 regression: child_deps passes the cache by reference."""
        from sec_review_framework.strategies.common import ModelProviderCache

        cache = ModelProviderCache()
        deps = SubagentDeps(
            depth=0,
            max_depth=3,
            invocations=0,
            max_invocations=100,
            max_batch_size=32,
            available_roles=set(),
            subagent_strategies={},
            tool_registry=ToolRegistry(),
            model_provider_cache=cache,
        )
        child = deps.child_deps()
        assert child.model_provider_cache is cache

    def test_no_cache_falls_back_to_fresh_provider(self) -> None:
        """Without a cache, _run_child still constructs a fresh provider (legacy behaviour)."""
        strategy = _make_strategy(model_id="fake/nocache")
        constructed: list[str] = []

        def _fake_provider(model_name: str) -> ScriptedLiteLLMProvider:
            constructed.append(model_name)
            return ScriptedLiteLLMProvider(
                responses=[{"content": "done", "tool_calls": [], "input_tokens": 2, "output_tokens": 1}]
            )

        deps = SubagentDeps(
            depth=0,
            max_depth=3,
            invocations=0,
            max_invocations=100,
            max_batch_size=32,
            available_roles={"reviewer"},
            subagent_strategies={"reviewer": strategy},
            tool_registry=ToolRegistry(),
            model_provider_cache=None,
        )

        import asyncio

        with patch(
            "sec_review_framework.agent.subagent.LiteLLMProvider",
            side_effect=_fake_provider,
        ):
            asyncio.run(_run_child(strategy, {}, deps))

        assert len(constructed) == 1
        assert constructed[0] == "fake/nocache"


class TestRunChildAsync:
    """Bug #7 regression: _run_child is async; no asyncio.run in the hot path."""

    def test_run_child_is_coroutine(self) -> None:
        """_run_child must be an async coroutine function, not a sync function."""
        import inspect

        assert inspect.iscoroutinefunction(_run_child)

    def test_run_child_sync_works_from_thread_pool(self) -> None:
        """_run_child_sync (the thin wrapper) works from a thread pool worker.

        Simulates the _programmatic_fallback execution context: a
        ThreadPoolExecutor thread with no running event loop calls
        _run_child_sync, which internally uses asyncio.run.
        """
        import concurrent.futures

        strategy = _make_strategy(model_id="fake/thread-test")
        provider = ScriptedLiteLLMProvider(
            responses=[{"content": "ok", "tool_calls": [], "input_tokens": 3, "output_tokens": 2}],
            model_name="fake/thread-test",
        )
        deps = SubagentDeps(
            depth=0,
            max_depth=3,
            invocations=0,
            max_invocations=100,
            max_batch_size=32,
            available_roles={"reviewer"},
            subagent_strategies={"reviewer": strategy},
            tool_registry=ToolRegistry(),
            model_provider_cache=None,
        )

        with patch(
            "sec_review_framework.agent.subagent.LiteLLMProvider",
            return_value=provider,
        ):
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                fut = pool.submit(_run_child_sync, strategy, {}, deps)
                result = fut.result()

        assert isinstance(result, SubagentOutput)

    @pytest.mark.asyncio
    async def test_invoke_subagent_awaits_child_directly(self) -> None:
        """invoke_subagent awaits _run_child directly without to_thread.

        Verify it works inside an already-running event loop (the very situation
        that caused 'asyncio.run() cannot be called from a running event loop').
        """
        strategy = _make_strategy(model_id="fake/async-single")
        provider = ScriptedLiteLLMProvider(
            responses=[{"content": "response", "tool_calls": [], "input_tokens": 4, "output_tokens": 2}],
            model_name="fake/async-single",
        )
        deps = SubagentDeps(
            depth=0,
            max_depth=3,
            invocations=0,
            max_invocations=100,
            max_batch_size=32,
            available_roles={"specialist"},
            subagent_strategies={"specialist": strategy},
            tool_registry=ToolRegistry(),
            model_provider_cache=None,
        )
        ctx = _make_run_context(deps)
        invoke_subagent = make_invoke_subagent_tool()

        with patch(
            "sec_review_framework.agent.subagent.LiteLLMProvider",
            return_value=provider,
        ):
            # This runs inside an already-running event loop (pytest-asyncio).
            # If _run_child used asyncio.run(), it would raise
            # "cannot be called from a running event loop".
            result = await invoke_subagent(ctx, role="specialist", input={"task": "check"})

        assert isinstance(result, SubagentOutput)

    @pytest.mark.asyncio
    async def test_invoke_subagent_batch_gather_works_in_event_loop(self) -> None:
        """invoke_subagent_batch uses asyncio.gather, works inside a running loop."""
        strategy = _make_strategy(model_id="fake/async-batch")

        def _make_provider() -> ScriptedLiteLLMProvider:
            return ScriptedLiteLLMProvider(
                responses=[{"content": "r", "tool_calls": [], "input_tokens": 3, "output_tokens": 1}],
                model_name="fake/async-batch",
            )

        deps = SubagentDeps(
            depth=0,
            max_depth=3,
            invocations=0,
            max_invocations=100,
            max_batch_size=32,
            available_roles={"specialist"},
            subagent_strategies={"specialist": strategy},
            tool_registry=ToolRegistry(),
            model_provider_cache=None,
        )
        ctx = _make_run_context(deps)
        invoke_batch = make_invoke_subagent_batch_tool()

        with patch(
            "sec_review_framework.agent.subagent.LiteLLMProvider",
            side_effect=[_make_provider(), _make_provider()],
        ):
            results = await invoke_batch(
                ctx,
                role="specialist",
                inputs=[{"a": 1}, {"a": 2}],
            )

        assert len(results) == 2
        assert all(isinstance(r, SubagentOutput) for r in results)


# Import needed for TestRunChildAsync tests
from sec_review_framework.agent.subagent import _run_child  # noqa: E402
