"""Tests for sec_review_framework.strategies.strategy_registry."""

from __future__ import annotations

from datetime import datetime

import pytest

from sec_review_framework.data.findings import VulnClass
from sec_review_framework.data.strategy_bundle import (
    OrchestrationShape,
    StrategyBundleDefault,
    UserStrategy,
)
from sec_review_framework.strategies.strategy_registry import (
    StrategyRegistry,
    build_registry_from_db,
    load_default_registry,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 1, 0, 0, 0)

_SAMPLE_STRATEGY = UserStrategy(
    id="user.test-strategy",
    name="Test Strategy",
    parent_strategy_id=None,
    orchestration_shape=OrchestrationShape.SINGLE_AGENT,
    default=StrategyBundleDefault(
        system_prompt="You are a security reviewer.",
        user_prompt_template="Review {repo_summary}.",
        profile_modifier="",
        model_id="gpt-4o",
        tools=frozenset(["read_file"]),
        verification="none",
        max_turns=50,
        tool_extensions=frozenset(),
    ),
    overrides=[],
    created_at=_NOW,
)


# ---------------------------------------------------------------------------
# StrategyRegistry basics
# ---------------------------------------------------------------------------


def test_register_and_retrieve():
    registry = StrategyRegistry()
    registry.register(_SAMPLE_STRATEGY)
    result = registry.get("user.test-strategy")
    assert result is _SAMPLE_STRATEGY


def test_get_nonexistent_raises_key_error():
    registry = StrategyRegistry()
    with pytest.raises(KeyError, match="nonexistent.id"):
        registry.get("nonexistent.id")


def test_get_error_message_lists_available_ids():
    registry = StrategyRegistry()
    registry.register(_SAMPLE_STRATEGY)
    with pytest.raises(KeyError, match="user.test-strategy"):
        registry.get("missing")


def test_list_all_empty():
    registry = StrategyRegistry()
    assert registry.list_all() == []


def test_list_all_sorted_by_id():
    registry = StrategyRegistry()
    for strategy_id in ["c.strategy", "a.strategy", "b.strategy"]:
        s = UserStrategy(
            id=strategy_id,
            name=strategy_id,
            parent_strategy_id=None,
            orchestration_shape=OrchestrationShape.SINGLE_AGENT,
            default=_SAMPLE_STRATEGY.default,
            overrides=[],
            created_at=_NOW,
        )
        registry.register(s)
    ids = [s.id for s in registry.list_all()]
    assert ids == sorted(ids)


def test_register_replaces_existing():
    registry = StrategyRegistry()
    registry.register(_SAMPLE_STRATEGY)
    updated = _SAMPLE_STRATEGY.model_copy(update={"name": "Updated Name"})
    registry.register(updated)
    assert registry.get("user.test-strategy").name == "Updated Name"


# ---------------------------------------------------------------------------
# load_default_registry — builtin strategies
# ---------------------------------------------------------------------------


def test_load_default_registry_returns_registry():
    registry = load_default_registry()
    assert isinstance(registry, StrategyRegistry)


def test_all_5_builtins_present():
    """After Phase 4, the 5 top-level builtin IDs are still present under builtin.*."""
    registry = load_default_registry()
    expected_ids = {
        "builtin.single_agent",
        "builtin.per_file",
        "builtin.per_vuln_class",
        "builtin.sast_first",
        "builtin.diff_review",
    }
    actual_ids = {s.id for s in registry.list_all()}
    assert expected_ids <= actual_ids


def test_specialists_all_present():
    """All 16 per-vuln-class specialist subagents are registered under builtin.*."""
    from sec_review_framework.data.findings import VulnClass

    registry = load_default_registry()
    actual_ids = {s.id for s in registry.list_all()}

    # Parent strategy
    assert "builtin.per_vuln_class" in actual_ids

    # All 16 specialists (now under builtin.* namespace)
    for vc in VulnClass:
        specialist_id = f"builtin.{vc.value}_specialist"
        assert specialist_id in actual_ids, f"Missing specialist: {specialist_id}"


def test_specialist_count():
    """Exactly 16 specialist subagents exist (one per VulnClass)."""
    from sec_review_framework.data.findings import VulnClass

    registry = load_default_registry()
    specialist_ids = {
        s.id for s in registry.list_all()
        if s.id.startswith("builtin.") and s.id.endswith("_specialist")
    }
    assert len(specialist_ids) == len(VulnClass), (
        f"Expected {len(VulnClass)} specialists, got {len(specialist_ids)}: {sorted(specialist_ids)}"
    )


def test_per_vuln_class_parent_has_16_subagents():
    """builtin.per_vuln_class must declare exactly 16 subagents."""
    from sec_review_framework.data.findings import VulnClass

    registry = load_default_registry()
    parent = registry.get("builtin.per_vuln_class")
    assert len(parent.default.subagents) == len(VulnClass)


def test_per_vuln_class_parent_dispatch_fallback_programmatic():
    """builtin.per_vuln_class must use programmatic dispatch fallback."""
    registry = load_default_registry()
    parent = registry.get("builtin.per_vuln_class")
    assert parent.default.dispatch_fallback == "programmatic"


def test_specialists_have_correct_parent():
    """All specialists must reference builtin.per_vuln_class as parent."""
    from sec_review_framework.data.findings import VulnClass

    registry = load_default_registry()
    for vc in VulnClass:
        specialist_id = f"builtin.{vc.value}_specialist"
        specialist = registry.get(specialist_id)
        assert specialist.parent_strategy_id == "builtin.per_vuln_class", (
            f"{specialist_id} has unexpected parent_strategy_id: {specialist.parent_strategy_id}"
        )


def test_specialists_have_non_empty_system_prompts():
    """All specialist subagents must have non-empty system prompts."""
    from sec_review_framework.data.findings import VulnClass

    registry = load_default_registry()
    for vc in VulnClass:
        specialist_id = f"builtin.{vc.value}_specialist"
        specialist = registry.get(specialist_id)
        assert specialist.default.system_prompt, (
            f"{specialist_id} has empty system_prompt"
        )


def test_total_builtin_count():
    """Registry must have exactly 34 builtin entries after Phase 5.

    Phase 4 baseline (23):
      5 top-level strategies: single_agent, diff_review, per_file, sast_first, per_vuln_class
      2 top-level subagents: file_reviewer (for per_file), triage_agent (for sast_first)
      16 per-vuln-class specialist subagents (one per VulnClass)

    Phase 5 additions (11):
      4 top-level strategies: single_agent_with_verifier, classifier_dispatch,
                               taint_pipeline, diff_blast_radius
      7 subagents: verifier, classifier, source_finder, sink_tracer,
                   sanitization_checker, blast_radius_finder, caller_checker
    Total: 23 + 11 = 34
    """
    from sec_review_framework.data.findings import VulnClass

    registry = load_default_registry()
    all_strategies = registry.list_all()
    # Phase 4 base: 5 top-level + 2 subagents + 16 specialists
    phase4_count = 5 + 2 + len(VulnClass)
    # Phase 5 additions: 4 top-level + 7 subagents
    phase5_count = 4 + 7
    expected_count = phase4_count + phase5_count
    assert len(all_strategies) == expected_count, (
        f"Expected {expected_count} builtin entries, got {len(all_strategies)}: "
        + str(sorted(s.id for s in all_strategies))
    )


def test_all_builtins_have_is_builtin_true():
    registry = load_default_registry()
    for strategy in registry.list_all():
        assert strategy.is_builtin, f"{strategy.id} should have is_builtin=True"


def test_top_level_builtins_have_no_parent():
    """Top-level (non-subagent) builtin strategies must have no parent_strategy_id.

    Subagent strategies (e.g. builtin.file_reviewer, builtin.triage_agent,
    builtin.*_specialist) DO carry a parent_strategy_id; those are excluded
    from this check.
    """
    registry = load_default_registry()
    # Dynamically derive the set of subagent IDs (those with a parent_strategy_id)
    # This ensures the test survives future additions (Phase 3c, etc.)
    subagent_ids = {
        s.id for s in registry.list_all() if s.parent_strategy_id is not None
    }
    for strategy in registry.list_all():
        if strategy.id not in subagent_ids:
            assert strategy.parent_strategy_id is None, (
                f"{strategy.id} unexpectedly has parent_strategy_id set"
            )


def test_builtin_orchestration_shapes():
    registry = load_default_registry()
    shapes = {s.id: s.orchestration_shape for s in registry.list_all()}
    assert shapes["builtin.single_agent"] == OrchestrationShape.SINGLE_AGENT
    assert shapes["builtin.per_file"] == OrchestrationShape.PER_FILE
    assert shapes["builtin.per_vuln_class"] == OrchestrationShape.PER_VULN_CLASS
    assert shapes["builtin.sast_first"] == OrchestrationShape.SAST_FIRST
    assert shapes["builtin.diff_review"] == OrchestrationShape.DIFF_REVIEW


def test_per_vuln_class_has_no_overrides():
    """builtin.per_vuln_class (Phase 4) uses subagents, not overrides."""
    registry = load_default_registry()
    pvc = registry.get("builtin.per_vuln_class")
    # The new implementation dispatches via subagents list, not overrides
    assert pvc.overrides == []


def test_single_agent_has_nonempty_prompts():
    registry = load_default_registry()
    sa = registry.get("builtin.single_agent")
    assert sa.default.system_prompt
    assert sa.default.user_prompt_template


def test_single_agent_has_no_overrides():
    registry = load_default_registry()
    sa = registry.get("builtin.single_agent")
    assert sa.overrides == []


def test_diff_review_has_no_overrides():
    registry = load_default_registry()
    dr = registry.get("builtin.diff_review")
    assert dr.overrides == []


def test_builtins_have_sensible_max_turns():
    registry = load_default_registry()
    for strategy in registry.list_all():
        assert strategy.default.max_turns > 0, (
            f"{strategy.id} has max_turns <= 0"
        )


def test_builtins_have_model_id():
    registry = load_default_registry()
    for strategy in registry.list_all():
        assert strategy.default.model_id, (
            f"{strategy.id} has empty model_id"
        )


# ---------------------------------------------------------------------------
# build_registry_from_db — user strategies must be resolvable alongside builtins
# ---------------------------------------------------------------------------


class _FakeDB:
    def __init__(self, user_strategies: list[UserStrategy]) -> None:
        self._user_strategies = user_strategies

    async def list_user_strategies(self) -> list[UserStrategy]:
        return list(self._user_strategies)


@pytest.mark.asyncio
async def test_build_registry_from_db_merges_builtins_with_user_strategies():
    """Coordinator path: registry must see both builtins and DB user strategies."""
    custom = UserStrategy(
        id="user.my-custom",
        name="My Custom",
        parent_strategy_id="builtin.single_agent",
        orchestration_shape=OrchestrationShape.SINGLE_AGENT,
        default=StrategyBundleDefault(
            system_prompt="Custom sys",
            user_prompt_template="Custom {repo_summary}",
            model_id="claude-opus-4-5",
            tools=frozenset(),
            verification="none",
            max_turns=5,
            tool_extensions=frozenset(),
        ),
        overrides=[],
        created_at=_NOW,
        is_builtin=False,
    )
    db = _FakeDB([custom])
    registry = await build_registry_from_db(db)

    # Builtins still present
    assert registry.get("builtin.single_agent").is_builtin is True
    # User strategy resolvable
    got = registry.get("user.my-custom")
    assert got.id == "user.my-custom"
    assert got.default.system_prompt == "Custom sys"
