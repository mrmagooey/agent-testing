"""Live end-to-end test for LiteLLMEndpointProbe.

Exercises the probe against the actual local OpenAI-compatible server at
LIVE_TEST_API_BASE — no mocking of litellm.get_valid_models.

Tests 1 and 2 require a live provider (skipped when LIVE_TEST_API_BASE is unset).
Test 3 exercises the disabled-when-env-unset path and runs unconditionally.
"""

from __future__ import annotations

import os

import pytest

from sec_review_framework.models.probes.litellm_endpoint_probe import LiteLLMEndpointProbe

LIVE_TEST_API_BASE = os.environ.get("LIVE_TEST_API_BASE")
LIVE_TEST_API_KEY = os.environ.get("LIVE_TEST_API_KEY")

_live_required = pytest.mark.skipif(
    not LIVE_TEST_API_BASE,
    reason="LIVE_TEST_API_BASE not set",
)


@_live_required
@pytest.mark.slow
@pytest.mark.asyncio
async def test_probe_returns_fresh_status_against_local_provider(monkeypatch):
    monkeypatch.setenv("LIVE_PROBE_API_BASE", LIVE_TEST_API_BASE)
    if LIVE_TEST_API_KEY:
        monkeypatch.setenv("LIVE_PROBE_API_KEY", LIVE_TEST_API_KEY)
    else:
        monkeypatch.delenv("LIVE_PROBE_API_KEY", raising=False)

    probe = LiteLLMEndpointProbe(
        provider_key="live-local",
        api_base_env="LIVE_PROBE_API_BASE",
        api_key_env="LIVE_PROBE_API_KEY",
    )
    snapshot = await probe.probe()

    assert snapshot.probe_status == "fresh"
    assert snapshot.last_error is None
    assert len(snapshot.model_ids) >= 1
    assert snapshot.fetched_at is not None


@_live_required
@pytest.mark.slow
@pytest.mark.asyncio
async def test_probe_discovers_at_least_one_known_model(monkeypatch):
    monkeypatch.setenv("LIVE_PROBE_API_BASE", LIVE_TEST_API_BASE)
    if LIVE_TEST_API_KEY:
        monkeypatch.setenv("LIVE_PROBE_API_KEY", LIVE_TEST_API_KEY)
    else:
        monkeypatch.delenv("LIVE_PROBE_API_KEY", raising=False)

    probe = LiteLLMEndpointProbe(
        provider_key="live-local",
        api_base_env="LIVE_PROBE_API_BASE",
        api_key_env="LIVE_PROBE_API_KEY",
    )
    snapshot = await probe.probe()

    assert any(mid.startswith("openai/") for mid in snapshot.model_ids), (
        f"Expected at least one model_id with 'openai/' prefix; got {snapshot.model_ids}"
    )


@pytest.mark.asyncio
async def test_probe_returns_disabled_when_api_base_env_unset(monkeypatch):
    monkeypatch.delenv("THIS_VAR_IS_INTENTIONALLY_UNSET_FOR_TEST", raising=False)

    probe = LiteLLMEndpointProbe(
        provider_key="live-local",
        api_base_env="THIS_VAR_IS_INTENTIONALLY_UNSET_FOR_TEST",
        api_key_env="THIS_VAR_IS_INTENTIONALLY_UNSET_FOR_TEST_KEY",
    )
    snapshot = await probe.probe()

    assert snapshot.probe_status == "disabled"
    assert snapshot.last_error is not None
    assert "THIS_VAR_IS_INTENTIONALLY_UNSET_FOR_TEST" in snapshot.last_error
