"""Unit tests for LiteLLMMultiProviderProbe."""

from __future__ import annotations

import pytest

_ALL_ENVS = {
    "OPENAI_API_KEY": "sk-openai-test",
    "ANTHROPIC_API_KEY": "sk-ant-test",
    "GEMINI_API_KEY": "gemini-test",
    "MISTRAL_API_KEY": "mistral-test",
    "COHERE_API_KEY": "cohere-test",
}

_SAMPLE_MODELS = [
    "gpt-4o",
    "claude-3-5-sonnet-latest",
    "gemini-2.0-flash",
]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_multi_probe_partitions_by_prefix(monkeypatch):
    """probe_many partitions one get_valid_models result into per-provider snapshots."""
    for k, v in _ALL_ENVS.items():
        monkeypatch.setenv(k, v)

    import litellm

    monkeypatch.setattr(
        litellm,
        "get_valid_models",
        lambda check_provider_endpoint=False: _SAMPLE_MODELS,
    )

    from sec_review_framework.models.probes.litellm_probe import LiteLLMMultiProviderProbe

    probe = LiteLLMMultiProviderProbe()
    result = await probe.probe_many()

    assert result["openai"].probe_status == "fresh"
    assert "gpt-4o" in result["openai"].model_ids

    assert result["anthropic"].probe_status == "fresh"
    assert "claude-3-5-sonnet-latest" in result["anthropic"].model_ids

    assert result["gemini"].probe_status == "fresh"
    assert "gemini-2.0-flash" in result["gemini"].model_ids

    # Keys are set → fresh even if no models matched the prefix.
    assert result["mistral"].probe_status == "fresh"
    assert result["mistral"].model_ids == frozenset()

    assert result["cohere"].probe_status == "fresh"
    assert result["cohere"].model_ids == frozenset()


async def test_multi_probe_disables_missing_env_vars(monkeypatch):
    """Providers whose env var is absent are disabled; others are fresh."""
    for k, v in _ALL_ENVS.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("ANTHROPIC_API_KEY")

    import litellm

    monkeypatch.setattr(
        litellm,
        "get_valid_models",
        lambda check_provider_endpoint=False: _SAMPLE_MODELS,
    )

    from sec_review_framework.models.probes.litellm_probe import LiteLLMMultiProviderProbe

    probe = LiteLLMMultiProviderProbe()
    result = await probe.probe_many()

    assert result["anthropic"].probe_status == "disabled"
    assert result["anthropic"].last_error is not None

    # All other keys were set → fresh.
    for pk in ("openai", "gemini", "mistral", "cohere"):
        assert result[pk].probe_status == "fresh", f"{pk} should be fresh"


async def test_multi_probe_single_litellm_call(monkeypatch):
    """probe_many makes exactly one get_valid_models call regardless of provider count."""
    for k, v in _ALL_ENVS.items():
        monkeypatch.setenv(k, v)

    import litellm

    call_count = 0

    def _counting_get_valid_models(**kwargs):
        nonlocal call_count
        call_count += 1
        return _SAMPLE_MODELS

    monkeypatch.setattr(litellm, "get_valid_models", _counting_get_valid_models)

    from sec_review_framework.models.probes.litellm_probe import LiteLLMMultiProviderProbe

    probe = LiteLLMMultiProviderProbe()
    await probe.probe_many()

    assert call_count == 1


async def test_multi_probe_no_call_when_all_disabled(monkeypatch):
    """When no env vars are set, get_valid_models is never called."""
    for k in _ALL_ENVS:
        monkeypatch.delenv(k, raising=False)

    import litellm

    call_count = 0

    def _should_not_be_called(**kwargs):
        nonlocal call_count
        call_count += 1
        return []

    monkeypatch.setattr(litellm, "get_valid_models", _should_not_be_called)

    from sec_review_framework.models.probes.litellm_probe import LiteLLMMultiProviderProbe

    probe = LiteLLMMultiProviderProbe()
    result = await probe.probe_many()

    assert call_count == 0
    for snap in result.values():
        assert snap.probe_status == "disabled"


async def test_multi_probe_raises_on_litellm_error(monkeypatch):
    """probe_many lets exceptions from get_valid_models propagate."""
    for k, v in _ALL_ENVS.items():
        monkeypatch.setenv(k, v)

    import litellm

    def _fail(**kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(litellm, "get_valid_models", _fail)

    from sec_review_framework.models.probes.litellm_probe import LiteLLMMultiProviderProbe

    probe = LiteLLMMultiProviderProbe()
    with pytest.raises(RuntimeError, match="network down"):
        await probe.probe_many()


async def test_build_litellm_probes_returns_single_multi_probe():
    """build_litellm_probes returns exactly one LiteLLMMultiProviderProbe."""
    from sec_review_framework.models.probes.litellm_probe import (
        LiteLLMMultiProviderProbe,
        build_litellm_probes,
    )

    probes = build_litellm_probes()
    assert len(probes) == 1
    assert isinstance(probes[0], LiteLLMMultiProviderProbe)


async def test_multi_probe_dispatches_get_valid_models_to_thread(monkeypatch):
    import asyncio as _asyncio

    import litellm as _litellm

    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setattr(_litellm, "get_valid_models", lambda **kw: [])

    seen = {}
    real_to_thread = _asyncio.to_thread

    async def _spy(func, *args, **kwargs):
        seen["func"] = func
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(_asyncio, "to_thread", _spy)
    from sec_review_framework.models.probes.litellm_probe import LiteLLMMultiProviderProbe

    await LiteLLMMultiProviderProbe().probe_many()
    assert seen.get("func") is _litellm.get_valid_models
