"""Unit tests for LiteLLMProbe."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_litellm_probe_disabled_when_key_missing(monkeypatch):
    """Probe returns disabled when API key env var is absent."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from sec_review_framework.models.probes.litellm_probe import LiteLLMProbe

    probe = LiteLLMProbe("openai", "OPENAI_API_KEY", "gpt")
    snap = await probe.probe()
    assert snap.probe_status == "disabled"
    assert snap.last_error is not None
    assert len(snap.model_ids) == 0


async def test_litellm_probe_partitions_by_prefix(monkeypatch):
    """Probe filters get_valid_models results to its own prefix."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    import litellm

    monkeypatch.setattr(
        litellm,
        "get_valid_models",
        lambda check_provider_endpoint=False: [
            "gpt-4o",
            "gpt-4-turbo",
            "claude-3-5-sonnet-20241022",
            "gemini/gemini-1.5-pro",
        ],
    )

    from sec_review_framework.models.probes.litellm_probe import LiteLLMProbe

    openai_probe = LiteLLMProbe("openai", "OPENAI_API_KEY", "gpt")
    snap = await openai_probe.probe()
    assert snap.probe_status == "fresh"
    assert "gpt-4o" in snap.model_ids
    assert "gpt-4-turbo" in snap.model_ids
    assert "claude-3-5-sonnet-20241022" not in snap.model_ids
    assert "gemini/gemini-1.5-pro" not in snap.model_ids


async def test_litellm_probe_anthropic_prefix(monkeypatch):
    """Anthropic probe picks up claude-prefixed models."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    import litellm

    monkeypatch.setattr(
        litellm,
        "get_valid_models",
        lambda check_provider_endpoint=False: [
            "claude-3-opus-20240229",
            "claude-3-5-sonnet-20241022",
            "gpt-4o",
        ],
    )

    from sec_review_framework.models.probes.litellm_probe import LiteLLMProbe

    probe = LiteLLMProbe("anthropic", "ANTHROPIC_API_KEY", "claude")
    snap = await probe.probe()
    assert snap.probe_status == "fresh"
    assert "claude-3-opus-20240229" in snap.model_ids
    assert "gpt-4o" not in snap.model_ids


async def test_litellm_probe_raises_on_api_error(monkeypatch):
    """Probe propagates when get_valid_models raises."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    import litellm

    def _fail(**kwargs):
        raise ConnectionError("network down")

    monkeypatch.setattr(litellm, "get_valid_models", _fail)

    from sec_review_framework.models.probes.litellm_probe import LiteLLMProbe

    probe = LiteLLMProbe("openai", "OPENAI_API_KEY", "gpt")
    with pytest.raises(RuntimeError, match="litellm.get_valid_models failed"):
        await probe.probe()


async def test_build_litellm_probes_returns_all_providers():
    """build_litellm_probes returns one probe per known provider."""
    from sec_review_framework.models.probes.litellm_probe import (
        _PROVIDER_SPEC,
        build_litellm_probes,
    )

    probes = build_litellm_probes()
    probe_keys = {p.provider_key for p in probes}
    assert probe_keys == set(_PROVIDER_SPEC.keys())


async def test_litellm_probe_metadata_populated(monkeypatch):
    """Each matched model gets a ModelMetadata entry."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    import litellm

    monkeypatch.setattr(
        litellm,
        "get_valid_models",
        lambda check_provider_endpoint=False: ["gemini/gemini-1.5-pro", "gemini/gemini-pro"],
    )

    from sec_review_framework.models.probes.litellm_probe import LiteLLMProbe

    probe = LiteLLMProbe("gemini", "GEMINI_API_KEY", "gemini")
    snap = await probe.probe()
    assert "gemini/gemini-1.5-pro" in snap.metadata
    assert snap.metadata["gemini/gemini-1.5-pro"].id == "gemini/gemini-1.5-pro"


async def test_litellm_probe_uses_asyncio_to_thread(monkeypatch):
    """probe() must offload get_valid_models to a worker thread via asyncio.to_thread
    so that the blocking HTTP call does not stall the event loop."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    import litellm
    import asyncio

    monkeypatch.setattr(
        litellm,
        "get_valid_models",
        lambda check_provider_endpoint=False: ["gpt-4o"],
    )

    from sec_review_framework.models.probes.litellm_probe import LiteLLMProbe

    probe = LiteLLMProbe("openai", "OPENAI_API_KEY", "gpt")

    to_thread_calls: list = []
    original_to_thread = asyncio.to_thread

    async def _spy_to_thread(func, *args, **kwargs):
        to_thread_calls.append(func)
        return await original_to_thread(func, *args, **kwargs)

    with patch("asyncio.to_thread", side_effect=_spy_to_thread):
        await probe.probe()

    assert len(to_thread_calls) == 1
    assert to_thread_calls[0] is litellm.get_valid_models
