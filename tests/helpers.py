"""Shared test helper utilities.

Import from test files as::

    from tests.helpers import make_test_bundle_snapshot, make_smoke_strategy
"""

from __future__ import annotations

import hashlib
import json
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


def make_smoke_strategy(model_id: str, strategy_suffix: str | None = None) -> UserStrategy:
    """Return a minimal non-persisted UserStrategy for the given *model_id*.

    The strategy ID is deterministic: ``test.<slug>.<6-char-hash>`` so that
    identical calls produce the same strategy.

    Parameters
    ----------
    model_id:
        The model to embed in the strategy's default bundle.
    strategy_suffix:
        Optional suffix appended to the strategy name and included in the
        hash input, allowing multiple distinct strategies for the same model.
    """
    slug = model_id.replace("/", "-").replace(".", "-").lower()
    hash_input = f"{model_id}:{strategy_suffix or ''}"
    short_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:6]
    strategy_id = f"test.{slug}.{short_hash}"

    return UserStrategy(
        id=strategy_id,
        name=f"Smoke test strategy for {model_id}",
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.SINGLE_AGENT,
        default=StrategyBundleDefault(
            system_prompt="You are a security reviewer.",
            user_prompt_template="Review this code for vulnerabilities.",
            model_id=model_id,
            tools=frozenset(["read_file"]),
            verification="none",
            max_turns=10,
            tool_extensions=frozenset(),
        ),
        overrides=[],
        created_at=datetime(2026, 1, 1, 0, 0, 0),
        is_builtin=False,
    )
