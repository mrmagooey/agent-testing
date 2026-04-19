"""Layer 1: Deploy smoke tests — no LLM calls.

Verifies the coordinator is running and basic reference endpoints respond
correctly. These should be fast and always pass when the cluster is healthy.
"""

from __future__ import annotations

import os

import pytest

from tests.e2e.live.conftest import K8S_LIVE_MARK

pytestmark = [
    K8S_LIVE_MARK,
    pytest.mark.skipif(
        not os.getenv("OPENROUTER_TEST_KEY"),
        reason="OPENROUTER_TEST_KEY not set",
    ),
]


def test_health(live_client):
    resp = live_client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body.get("status") == "ok"


def test_models_non_empty(live_client):
    resp = live_client.get("/models")
    assert resp.status_code == 200
    models = resp.json()
    assert isinstance(models, list)
    assert len(models) > 0, "Expected at least one configured model"


def test_strategies_includes_single_agent(live_client):
    resp = live_client.get("/strategies")
    assert resp.status_code == 200
    strategies = resp.json()
    assert isinstance(strategies, list)
    assert len(strategies) > 0, "Expected at least one strategy"
    strategy_names = [s.get("name") or s.get("id") or str(s) for s in strategies]
    assert any("single_agent" in name for name in strategy_names), (
        f"single_agent not found in strategies: {strategy_names}"
    )


def test_profiles_includes_default(live_client):
    resp = live_client.get("/profiles")
    assert resp.status_code == 200
    profiles = resp.json()
    assert isinstance(profiles, list)
    assert len(profiles) > 0, "Expected at least one review profile"
    profile_names = [p.get("name") or p.get("id") or str(p) for p in profiles]
    assert any("default" in name for name in profile_names), (
        f"default not found in profiles: {profile_names}"
    )
