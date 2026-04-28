"""Unit tests for OpenRouterProbe."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SAMPLE_RESPONSE = {
    "data": [
        {
            "id": "openai/gpt-4o",
            "name": "GPT-4o",
            "context_length": 128000,
            "pricing": {"prompt": "0.000005", "completion": "0.000015"},
        },
        {
            "id": "anthropic/claude-3-5-sonnet",
            "name": "Claude 3.5 Sonnet",
            "context_length": 200000,
            "pricing": {"prompt": "0.000003", "completion": "0.000015"},
        },
        {
            "id": "meta-llama/llama-3.1-8b-instruct",
            "name": "Llama 3.1 8B Instruct",
            "context_length": 131072,
            "pricing": {"prompt": "0.0000001", "completion": "0.0000001"},
        },
    ]
}


def _make_mock_response(data: dict, status_code: int = 200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = data
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_openrouter_probe_disabled_when_key_missing(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    from sec_review_framework.models.probes.openrouter_probe import OpenRouterProbe

    probe = OpenRouterProbe()
    snap = await probe.probe()
    assert snap.probe_status == "disabled"
    assert snap.last_error is not None
    assert len(snap.model_ids) == 0


async def test_openrouter_probe_parses_response(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    mock_resp = _make_mock_response(_SAMPLE_RESPONSE)

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch(
        "sec_review_framework.models.probes.openrouter_probe.httpx.AsyncClient",
        return_value=mock_client,
    ):
        from sec_review_framework.models.probes.openrouter_probe import OpenRouterProbe

        probe = OpenRouterProbe()
        snap = await probe.probe()

    assert snap.probe_status == "fresh"
    assert "openrouter/openai/gpt-4o" in snap.model_ids
    assert "openrouter/anthropic/claude-3-5-sonnet" in snap.model_ids


async def test_openrouter_probe_metadata_context_length(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    mock_resp = _make_mock_response(_SAMPLE_RESPONSE)

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch(
        "sec_review_framework.models.probes.openrouter_probe.httpx.AsyncClient",
        return_value=mock_client,
    ):
        from importlib import reload

        import sec_review_framework.models.probes.openrouter_probe as mod
        reload(mod)

        probe = mod.OpenRouterProbe()
        snap = await probe.probe()

    meta = snap.metadata.get("openrouter/openai/gpt-4o")
    assert meta is not None
    assert meta.context_length == 128000
    assert meta.pricing is not None
    assert meta.display_name == "GPT-4o"


async def test_openrouter_probe_metadata_pricing(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    mock_resp = _make_mock_response(_SAMPLE_RESPONSE)

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch(
        "sec_review_framework.models.probes.openrouter_probe.httpx.AsyncClient",
        return_value=mock_client,
    ):
        from sec_review_framework.models.probes.openrouter_probe import OpenRouterProbe

        probe = OpenRouterProbe()
        snap = await probe.probe()

    meta = snap.metadata.get("openrouter/anthropic/claude-3-5-sonnet")
    assert meta is not None
    assert meta.pricing == {"prompt": "0.000003", "completion": "0.000015"}


async def test_openrouter_probe_http_error_propagates(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    import httpx

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(
        side_effect=httpx.ConnectError("connection refused", request=MagicMock())
    )

    with patch(
        "sec_review_framework.models.probes.openrouter_probe.httpx.AsyncClient",
        return_value=mock_client,
    ):
        from sec_review_framework.models.probes.openrouter_probe import OpenRouterProbe

        probe = OpenRouterProbe()
        with pytest.raises(httpx.ConnectError):
            await probe.probe()


async def test_openrouter_probe_prefixes_upstream_ids(monkeypatch):
    """Upstream IDs (e.g. 'meta-llama/llama-3.1-8b-instruct') must be
    prefixed with 'openrouter/' in both model_ids and metadata keys."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    mock_resp = _make_mock_response(_SAMPLE_RESPONSE)

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch(
        "sec_review_framework.models.probes.openrouter_probe.httpx.AsyncClient",
        return_value=mock_client,
    ):
        from sec_review_framework.models.probes.openrouter_probe import OpenRouterProbe

        probe = OpenRouterProbe()
        snap = await probe.probe()

    # Verify the Llama model appears with prefix in model_ids
    assert "openrouter/meta-llama/llama-3.1-8b-instruct" in snap.model_ids
    # Verify the raw un-prefixed form is NOT in model_ids
    assert "meta-llama/llama-3.1-8b-instruct" not in snap.model_ids
    # Verify metadata key also uses prefixed form
    assert "openrouter/meta-llama/llama-3.1-8b-instruct" in snap.metadata
    assert snap.metadata["openrouter/meta-llama/llama-3.1-8b-instruct"].id == (
        "openrouter/meta-llama/llama-3.1-8b-instruct"
    )


async def test_openrouter_probe_empty_data(monkeypatch):
    """Empty data list → empty model_ids, still fresh."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    mock_resp = _make_mock_response({"data": []})

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_resp)

    with patch(
        "sec_review_framework.models.probes.openrouter_probe.httpx.AsyncClient",
        return_value=mock_client,
    ):
        from sec_review_framework.models.probes.openrouter_probe import OpenRouterProbe

        probe = OpenRouterProbe()
        snap = await probe.probe()

    assert snap.probe_status == "fresh"
    assert len(snap.model_ids) == 0
