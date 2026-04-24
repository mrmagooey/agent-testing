"""Shared test helper utilities.

Import from test files as::

    from tests.helpers import make_test_bundle_snapshot
"""

from __future__ import annotations

from datetime import datetime

from sec_review_framework.data.experiment import BundleSnapshot
from sec_review_framework.data.strategy_bundle import (
    OrchestrationShape,
    StrategyBundleDefault,
    UserStrategy,
)


def make_test_bundle_snapshot() -> BundleSnapshot:
    """Return a minimal BundleSnapshot backed by a stub UserStrategy.

    Use this wherever tests previously called ``PromptSnapshot.capture(...)``.
    The snapshot is stable across calls (same inputs → same snapshot_id).
    """
    strategy = UserStrategy(
        id="test.stub",
        name="Test Stub",
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.SINGLE_AGENT,
        default=StrategyBundleDefault(
            system_prompt="sys",
            user_prompt_template="user",
            model_id="fake-model",
            tools=frozenset(["read_file"]),
            verification="none",
            max_turns=10,
            tool_extensions=frozenset(),
        ),
        overrides=[],
        created_at=datetime(2026, 1, 1, 0, 0, 0),
        is_builtin=False,
    )
    return BundleSnapshot.capture(strategy)
