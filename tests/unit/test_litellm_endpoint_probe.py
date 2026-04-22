"""Unit tests for LiteLLMEndpointProbe."""

from __future__ import annotations

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_probe(**kwargs):
    from sec_review_framework.models.probes.litellm_endpoint_probe import LiteLLMEndpointProbe

    defaults = dict(
        provider_key="local_llm",
        api_base_env="LOCAL_LLM_BASE_URL",
        api_key_env="LOCAL_LLM_API_KEY",
        litellm_provider="openai",
    )
    defaults.update(kwargs)
    return LiteLLMEndpointProbe(**defaults)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_probe_disabled_when_base_url_missing(monkeypatch):
    """Probe returns disabled when the base-URL env var is absent."""
    monkeypatch.delenv("LOCAL_LLM_BASE_URL", raising=False)

    probe = _make_probe()
    snap = await probe.probe()

    assert snap.probe_status == "disabled"
    assert snap.last_error is not None
    assert "LOCAL_LLM_BASE_URL" in snap.last_error
    assert len(snap.model_ids) == 0


async def test_probe_calls_litellm_with_custom_provider_and_api_base(monkeypatch):
    """probe() passes custom_llm_provider, api_base, api_key, and check_provider_endpoint."""
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://192.168.7.100:8080")
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "somekey")

    import litellm

    captured: dict = {}

    def _fake_get_valid_models(**kwargs):
        captured.update(kwargs)
        return ["mymodel"]

    monkeypatch.setattr(litellm, "get_valid_models", _fake_get_valid_models)

    probe = _make_probe()
    await probe.probe()

    assert captured.get("custom_llm_provider") == "openai"
    assert captured.get("api_base") == "http://192.168.7.100:8080"
    assert captured.get("api_key") == "somekey"
    assert captured.get("check_provider_endpoint") is True


async def test_probe_prefixes_ids_with_openai_for_routing(monkeypatch):
    """Returned raw ids are prefixed with 'openai/' so LiteLLM routes via OpenAI-compat."""
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:8080")
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "k")

    import litellm

    monkeypatch.setattr(litellm, "get_valid_models", lambda **kw: ["foo", "bar"])

    probe = _make_probe()
    snap = await probe.probe()

    assert snap.probe_status == "fresh"
    assert snap.model_ids == {"openai/foo", "openai/bar"}


async def test_probe_raises_on_endpoint_error(monkeypatch):
    """Exceptions from litellm.get_valid_models propagate so the catalog marks the snapshot failed."""
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:8080")
    monkeypatch.setenv("LOCAL_LLM_API_KEY", "k")

    import litellm

    def _raise(**kwargs):
        raise ConnectionError("endpoint unreachable")

    monkeypatch.setattr(litellm, "get_valid_models", _raise)

    probe = _make_probe()
    with pytest.raises(ConnectionError, match="endpoint unreachable"):
        await probe.probe()


async def test_probe_empty_api_key_passes_none_to_litellm(monkeypatch):
    """When API-key env var is absent, probe passes api_key=None (not empty string) to LiteLLM."""
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:8080")
    monkeypatch.delenv("LOCAL_LLM_API_KEY", raising=False)

    import litellm

    captured: dict = {}

    def _fake_get_valid_models(**kwargs):
        captured.update(kwargs)
        return []

    monkeypatch.setattr(litellm, "get_valid_models", _fake_get_valid_models)

    probe = _make_probe()
    await probe.probe()

    assert captured.get("api_key") is None
