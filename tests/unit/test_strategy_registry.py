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
    registry = load_default_registry()
    expected_ids = {
        "builtin.single_agent",
        "builtin.per_file",
        "builtin.per_vuln_class",
        "builtin.sast_first",
        "builtin.diff_review",
    }
    actual_ids = {s.id for s in registry.list_all()}
    # Phase 3a adds builtin_v2.* entries; original 5 must still be present.
    assert expected_ids <= actual_ids


def test_all_7_builtins_present_after_phase3a():
    """After Phase 3a, the registry has the original 5 plus at least the 2 Phase-3a v2 entries.

    Phase 3b adds 4 more (per_file, sast_first, file_reviewer, triage_agent).
    This test uses subset-check so it does not need updating for each phase.
    """
    registry = load_default_registry()
    expected_ids = {
        "builtin.single_agent",
        "builtin.per_file",
        "builtin.per_vuln_class",
        "builtin.sast_first",
        "builtin.diff_review",
        "builtin_v2.single_agent",
        "builtin_v2.diff_review",
    }
    actual_ids = {s.id for s in registry.list_all()}
    assert expected_ids <= actual_ids


def test_all_builtins_have_is_builtin_true():
    registry = load_default_registry()
    for strategy in registry.list_all():
        assert strategy.is_builtin, f"{strategy.id} should have is_builtin=True"


def test_top_level_builtins_have_no_parent():
    """Top-level (non-subagent) builtin strategies must have no parent_strategy_id.

    Subagent strategies (e.g. builtin_v2.file_reviewer, builtin_v2.triage_agent)
    DO carry a parent_strategy_id; those are excluded from this check.
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


def test_per_vuln_class_has_16_overrides():
    """One override per VulnClass file — currently 16 classes (including 'other')."""
    registry = load_default_registry()
    pvc = registry.get("builtin.per_vuln_class")
    # VulnClass has 16 members including OTHER
    assert len(pvc.overrides) == len(VulnClass)


def test_per_vuln_class_overrides_only_system_prompt():
    """Each per_vuln_class override sets only system_prompt; all others are None."""
    registry = load_default_registry()
    pvc = registry.get("builtin.per_vuln_class")
    for rule in pvc.overrides:
        ov = rule.override
        assert ov.system_prompt is not None, f"Key {rule.key} has no system_prompt"
        assert ov.user_prompt_template is None
        assert ov.profile_modifier is None
        assert ov.model_id is None
        assert ov.tools is None
        assert ov.verification is None
        assert ov.max_turns is None
        assert ov.tool_extensions is None


def test_per_vuln_class_override_keys_are_valid_vuln_class_names():
    registry = load_default_registry()
    pvc = registry.get("builtin.per_vuln_class")
    valid_names = {vc.value for vc in VulnClass}
    for rule in pvc.overrides:
        assert rule.key in valid_names, f"Key {rule.key!r} is not a valid VulnClass"


def test_per_vuln_class_system_prompts_nonempty():
    registry = load_default_registry()
    pvc = registry.get("builtin.per_vuln_class")
    for rule in pvc.overrides:
        assert rule.override.system_prompt, (
            f"Override for {rule.key!r} has empty system_prompt"
        )


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
