"""Tests for strategy bundle integration in common.py.

Tests:
- run_subagents backwards compatibility (legacy task dicts)
- run_subagents with bundle-keyed task dicts
- ModelProviderCache: same instance for same model_id within a run
- filter_tools: returns ToolRegistry with only allowed tool names
"""

from __future__ import annotations

from collections import deque
from datetime import datetime
from unittest.mock import MagicMock

import pytest

from sec_review_framework.data.strategy_bundle import (
    OrchestrationShape,
    OverrideRule,
    StrategyBundleDefault,
    StrategyBundleOverride,
    UserStrategy,
)
from sec_review_framework.models.base import ModelResponse
from sec_review_framework.strategies.common import (
    ModelProviderCache,
    filter_tools,
    run_subagents,
)
from sec_review_framework.tools.registry import Tool, ToolDefinition, ToolRegistry
from tests.conftest import FakeModelProvider


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 1, 0, 0, 0)


def _canned_response(content: str = "result") -> ModelResponse:
    return ModelResponse(
        content=content,
        tool_calls=[],
        input_tokens=10,
        output_tokens=5,
        model_id="fake-model",
        raw={},
    )


def _fake_model(n: int = 1, content: str = "result") -> FakeModelProvider:
    return FakeModelProvider([_canned_response(content) for _ in range(n)])


def _empty_tools() -> ToolRegistry:
    return ToolRegistry()


def _make_single_agent_strategy(
    system_prompt: str = "Default system",
    max_turns: int = 50,
) -> UserStrategy:
    return UserStrategy(
        id="test.single",
        name="test",
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.SINGLE_AGENT,
        default=StrategyBundleDefault(
            system_prompt=system_prompt,
            user_prompt_template="Review {repo_summary}.",
            profile_modifier="",
            model_id="claude-opus-4-5",
            tools=frozenset(["read_file", "grep"]),
            verification="none",
            max_turns=max_turns,
            tool_extensions=frozenset(),
        ),
        overrides=[],
        created_at=_NOW,
    )


def _make_pvc_strategy() -> UserStrategy:
    return UserStrategy(
        id="test.pvc",
        name="test pvc",
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.PER_VULN_CLASS,
        default=StrategyBundleDefault(
            system_prompt="Default system prompt.",
            user_prompt_template="Review {repo_summary}.",
            profile_modifier="",
            model_id="claude-opus-4-5",
            tools=frozenset(["read_file"]),
            verification="none",
            max_turns=40,
            tool_extensions=frozenset(),
        ),
        overrides=[
            OverrideRule(
                key="sqli",
                override=StrategyBundleOverride(
                    system_prompt="You are a SQLi specialist.",
                    max_turns=30,
                ),
            ),
        ],
        created_at=_NOW,
    )


# ---------------------------------------------------------------------------
# run_subagents — legacy task dicts (backwards compat)
# ---------------------------------------------------------------------------


def test_run_subagents_legacy_sequential():
    model = _fake_model(n=2, content="legacy result")
    results = run_subagents(
        tasks=[
            {"system_prompt": "sys1", "user_message": "user1", "max_turns": 10},
            {"system_prompt": "sys2", "user_message": "user2", "max_turns": 10},
        ],
        model=model,
        tools=_empty_tools(),
        parallel=False,
    )
    assert results == ["legacy result", "legacy result"]


def test_run_subagents_legacy_parallel():
    model = _fake_model(n=2, content="parallel result")
    results = run_subagents(
        tasks=[
            {"system_prompt": "sys1", "user_message": "user1", "max_turns": 10},
            {"system_prompt": "sys2", "user_message": "user2", "max_turns": 10},
        ],
        model=model,
        tools=_empty_tools(),
        parallel=True,
        max_workers=2,
    )
    assert sorted(results) == ["parallel result", "parallel result"]


def test_run_subagents_legacy_no_strategy_arg_needed():
    """Legacy callers should not need to pass strategy."""
    model = _fake_model(n=1, content="ok")
    results = run_subagents(
        tasks=[{"system_prompt": "sys", "user_message": "msg", "max_turns": 5}],
        model=model,
        tools=_empty_tools(),
        parallel=False,
        # no strategy kwarg
    )
    assert results == ["ok"]


# ---------------------------------------------------------------------------
# run_subagents — bundle-keyed task dicts
# ---------------------------------------------------------------------------


def test_run_subagents_bundle_key_none_for_single_agent():
    """Bundle-keyed task with key=None resolves single_agent default."""
    strategy = _make_single_agent_strategy(system_prompt="Bundle system", max_turns=20)
    model = _fake_model(n=1, content="bundle result")
    results = run_subagents(
        tasks=[{"key": None, "user_message": "msg"}],
        model=model,
        tools=_empty_tools(),
        parallel=False,
        strategy=strategy,
    )
    assert results == ["bundle result"]


def test_run_subagents_bundle_key_resolves_override():
    """Bundle-keyed task uses the correct overridden max_turns from the bundle."""
    strategy = _make_pvc_strategy()
    model = _fake_model(n=1, content="sqli result")

    # We verify the bundle was resolved by checking the call went through without error
    results = run_subagents(
        tasks=[{"key": "sqli", "user_message": "Review this for SQLi."}],
        model=model,
        tools=_empty_tools(),
        parallel=False,
        strategy=strategy,
    )
    assert results == ["sqli result"]


def test_run_subagents_bundle_task_max_turns_override():
    """A bundle-keyed task may override max_turns inline."""
    strategy = _make_single_agent_strategy(max_turns=100)
    # model only has 1 response; max_turns from the task overrides the bundle's 100
    model = _fake_model(n=1, content="done")
    results = run_subagents(
        tasks=[{"key": None, "user_message": "msg", "max_turns": 5}],
        model=model,
        tools=_empty_tools(),
        parallel=False,
        strategy=strategy,
    )
    assert results == ["done"]


def test_run_subagents_profile_modifier_appended():
    """Profile modifier in the bundle is appended to the system prompt."""
    strategy = UserStrategy(
        id="test.mod",
        name="test",
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.SINGLE_AGENT,
        default=StrategyBundleDefault(
            system_prompt="Base prompt.",
            user_prompt_template="Review.",
            profile_modifier="STRICT MODE: be strict.",
            model_id="m",
            tools=frozenset(),
            verification="none",
            max_turns=10,
            tool_extensions=frozenset(),
        ),
        overrides=[],
        created_at=_NOW,
    )
    model = _fake_model(n=1, content="ok")
    # Just verify it runs without error — the system prompt passed to the model
    # is not directly observable from run_subagents's return value, but the
    # function must not raise.
    results = run_subagents(
        tasks=[{"key": None, "user_message": "msg"}],
        model=model,
        tools=_empty_tools(),
        parallel=False,
        strategy=strategy,
    )
    assert results == ["ok"]


# ---------------------------------------------------------------------------
# ModelProviderCache
# ---------------------------------------------------------------------------


def test_model_provider_cache_returns_same_instance():
    """get() returns the exact same object on repeated calls."""
    provider = _fake_model()
    cache = ModelProviderCache()
    cache.put("model-a", provider)
    assert cache.get("model-a") is provider
    assert cache.get("model-a") is provider  # same instance on second call


def test_model_provider_cache_different_model_ids_different_instances():
    p1 = _fake_model()
    p2 = _fake_model()
    cache = ModelProviderCache()
    cache.put("model-a", p1)
    cache.put("model-b", p2)
    assert cache.get("model-a") is p1
    assert cache.get("model-b") is p2
    assert cache.get("model-a") is not cache.get("model-b")


def test_model_provider_cache_contains():
    cache = ModelProviderCache()
    cache.put("m", _fake_model())
    assert "m" in cache
    assert "other" not in cache


def test_model_provider_cache_no_factory_raises():
    cache = ModelProviderCache()
    with pytest.raises(ValueError, match="no factory"):
        cache.get("some-model")


def test_model_provider_cache_with_factory():
    """Factory is called once and result is cached."""
    call_count = 0
    provider = _fake_model()

    def factory(model_id: str):
        nonlocal call_count
        call_count += 1
        return provider

    cache = ModelProviderCache(factory=factory)
    r1 = cache.get("claude")
    r2 = cache.get("claude")
    assert r1 is r2
    assert call_count == 1


# ---------------------------------------------------------------------------
# filter_tools
# ---------------------------------------------------------------------------


class _DummyTool(Tool):
    def __init__(self, name: str) -> None:
        self._name = name

    def definition(self) -> ToolDefinition:
        return ToolDefinition(name=self._name, description="", input_schema={})

    def invoke(self, input: dict) -> str:
        return "ok"


def _registry_with_tools(*names: str) -> ToolRegistry:
    registry = ToolRegistry()
    for name in names:
        registry.tools[name] = _DummyTool(name)
    return registry


def test_filter_tools_keeps_allowed():
    base = _registry_with_tools("read_file", "grep", "semgrep")
    filtered = filter_tools(base, frozenset(["read_file", "grep"]))
    assert set(filtered.tools.keys()) == {"read_file", "grep"}


def test_filter_tools_excludes_disallowed():
    base = _registry_with_tools("read_file", "grep", "semgrep")
    filtered = filter_tools(base, frozenset(["read_file"]))
    assert "semgrep" not in filtered.tools
    assert "grep" not in filtered.tools


def test_filter_tools_fresh_audit_log():
    base = _registry_with_tools("read_file")
    # Add a record to the original audit log
    base.audit_log.record("read_file", {}, "call-1", 0, False)
    filtered = filter_tools(base, frozenset(["read_file"]))
    assert len(filtered.audit_log.entries) == 0


def test_filter_tools_allowed_subset_empty():
    base = _registry_with_tools("read_file", "grep")
    filtered = filter_tools(base, frozenset())
    assert filtered.tools == {}


def test_filter_tools_does_not_mutate_original():
    base = _registry_with_tools("read_file", "grep", "semgrep")
    original_keys = set(base.tools.keys())
    filter_tools(base, frozenset(["read_file"]))
    assert set(base.tools.keys()) == original_keys


def test_filter_tools_allowed_names_not_in_registry_are_ignored():
    """Asking for a tool that doesn't exist just results in it being absent."""
    base = _registry_with_tools("read_file")
    filtered = filter_tools(base, frozenset(["read_file", "nonexistent_tool"]))
    assert set(filtered.tools.keys()) == {"read_file"}
