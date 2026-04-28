"""Unit tests for Database user_strategies persistence layer."""

from __future__ import annotations

from datetime import datetime

from sec_review_framework.data.strategy_bundle import (
    OrchestrationShape,
    OverrideRule,
    StrategyBundleDefault,
    StrategyBundleOverride,
    UserStrategy,
    canonical_json,
)
from sec_review_framework.db import Database

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 1, 0, 0, 0)

_DEFAULT_BUNDLE = StrategyBundleDefault(
    system_prompt="You are a security expert.",
    user_prompt_template="Review {repo_summary}. Output as {finding_output_format}.",
    profile_modifier="",
    model_id="claude-opus-4-5",
    tools=frozenset(["read_file", "grep"]),
    verification="none",
    max_turns=50,
    tool_extensions=frozenset(),
)


def _make_single_agent_strategy(strategy_id: str = "user.test.abc123") -> UserStrategy:
    return UserStrategy(
        id=strategy_id,
        name="Test Single Agent",
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.SINGLE_AGENT,
        default=_DEFAULT_BUNDLE,
        overrides=[],
        created_at=_NOW,
        is_builtin=False,
    )


def _make_per_file_strategy(strategy_id: str = "user.per-file.def456") -> UserStrategy:
    return UserStrategy(
        id=strategy_id,
        name="Test Per File",
        parent_strategy_id="builtin.per_file",
        orchestration_shape=OrchestrationShape.PER_FILE,
        default=_DEFAULT_BUNDLE,
        overrides=[
            OverrideRule(
                key="**/*.py",
                override=StrategyBundleOverride(system_prompt="Python-specific prompt."),
            ),
            OverrideRule(
                key="**/*.js",
                override=StrategyBundleOverride(system_prompt="JavaScript-specific prompt."),
            ),
        ],
        created_at=_NOW,
        is_builtin=False,
    )


# ---------------------------------------------------------------------------
# Round-trip tests
# ---------------------------------------------------------------------------


async def test_insert_and_get_round_trip(temp_database: Database):
    strategy = _make_single_agent_strategy()
    await temp_database.insert_user_strategy(strategy)

    retrieved = await temp_database.get_user_strategy(strategy.id)
    assert retrieved is not None
    assert retrieved.id == strategy.id
    assert retrieved.name == strategy.name
    assert retrieved.orchestration_shape == strategy.orchestration_shape
    assert retrieved.is_builtin == strategy.is_builtin
    assert retrieved.parent_strategy_id == strategy.parent_strategy_id
    assert retrieved.default.system_prompt == strategy.default.system_prompt
    assert retrieved.default.tools == strategy.default.tools


async def test_get_nonexistent_returns_none(temp_database: Database):
    result = await temp_database.get_user_strategy("user.does-not-exist.000000")
    assert result is None


async def test_list_includes_inserted(temp_database: Database):
    s1 = _make_single_agent_strategy("user.alpha.aaa111")
    s2 = _make_single_agent_strategy("user.beta.bbb222")
    await temp_database.insert_user_strategy(s1)
    await temp_database.insert_user_strategy(s2)

    all_strategies = await temp_database.list_user_strategies()
    ids = {s.id for s in all_strategies}
    assert s1.id in ids
    assert s2.id in ids


async def test_list_empty_initially(temp_database: Database):
    result = await temp_database.list_user_strategies()
    assert result == []


async def test_delete_removes_strategy(temp_database: Database):
    strategy = _make_single_agent_strategy()
    await temp_database.insert_user_strategy(strategy)

    deleted = await temp_database.delete_user_strategy(strategy.id)
    assert deleted is True

    retrieved = await temp_database.get_user_strategy(strategy.id)
    assert retrieved is None


async def test_delete_returns_false_for_nonexistent(temp_database: Database):
    deleted = await temp_database.delete_user_strategy("user.ghost.000000")
    assert deleted is False


async def test_delete_removes_from_list(temp_database: Database):
    strategy = _make_single_agent_strategy()
    await temp_database.insert_user_strategy(strategy)

    await temp_database.delete_user_strategy(strategy.id)
    all_strategies = await temp_database.list_user_strategies()
    assert all(s.id != strategy.id for s in all_strategies)


# ---------------------------------------------------------------------------
# canonical_json round-trip: override order preserved for glob shapes
# ---------------------------------------------------------------------------


async def test_canonical_json_override_order_preserved(temp_database: Database):
    """Glob-shaped strategies must preserve override insertion order on round-trip."""
    strategy = _make_per_file_strategy()
    original_keys = [r.key for r in strategy.overrides]

    await temp_database.insert_user_strategy(strategy)
    retrieved = await temp_database.get_user_strategy(strategy.id)
    assert retrieved is not None

    retrieved_keys = [r.key for r in retrieved.overrides]
    assert retrieved_keys == original_keys, (
        "Override order was not preserved across DB round-trip"
    )


async def test_canonical_json_round_trip_full_fidelity(temp_database: Database):
    """canonical_json of original and retrieved strategy must be identical."""
    strategy = _make_per_file_strategy()
    original_json = canonical_json(strategy)

    await temp_database.insert_user_strategy(strategy)
    retrieved = await temp_database.get_user_strategy(strategy.id)
    assert retrieved is not None

    retrieved_json = canonical_json(retrieved)
    assert retrieved_json == original_json


# ---------------------------------------------------------------------------
# Builtin handling — DB layer does not enforce read-only; that is API's job
# ---------------------------------------------------------------------------


async def test_delete_builtin_at_db_layer_succeeds(temp_database: Database):
    """The DB layer itself has no restriction on deleting builtins.

    Builtin protection is enforced at the API layer (DELETE /strategies/{id}
    returns 403 for builtins). The DB layer just hard-deletes whatever it is
    asked to delete.
    """
    builtin = UserStrategy(
        id="builtin.test_builtin",
        name="Test Builtin",
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.SINGLE_AGENT,
        default=_DEFAULT_BUNDLE,
        overrides=[],
        created_at=_NOW,
        is_builtin=True,
    )
    await temp_database.insert_user_strategy(builtin)
    deleted = await temp_database.delete_user_strategy(builtin.id)
    assert deleted is True
    assert await temp_database.get_user_strategy(builtin.id) is None


# ---------------------------------------------------------------------------
# strategy_is_referenced_by_runs — always False until follow-up agent lands
# ---------------------------------------------------------------------------


async def test_strategy_is_referenced_by_runs_returns_false(temp_database: Database):
    """Placeholder: always returns False until the runs table gains strategy_id."""
    result = await temp_database.strategy_is_referenced_by_runs("builtin.single_agent")
    assert result is False
