"""Unit tests for BedrockProbe."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_FOUNDATION_MODELS = {
    "modelSummaries": [
        {"modelId": "anthropic.claude-3-5-sonnet-20241022-v2:0", "modelName": "Claude 3.5 Sonnet"},
        {"modelId": "amazon.titan-text-express-v1", "modelName": "Titan Text Express"},
    ]
}

_INFERENCE_PROFILES = {
    "inferenceProfileSummaries": [
        {
            "inferenceProfileId": "us.anthropic.claude-3-5-sonnet-20241022-v2:0",
            "inferenceProfileName": "Claude 3.5 Sonnet (Cross-Region)",
        }
    ]
}


def _make_bedrock_client(fm_resp=None, ip_resp=None, ip_raises=None):
    client = MagicMock()
    client.list_foundation_models.return_value = fm_resp or _FOUNDATION_MODELS
    if ip_raises:
        client.list_inference_profiles.side_effect = ip_raises
    else:
        client.list_inference_profiles.return_value = ip_resp or _INFERENCE_PROFILES
    return client


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_bedrock_probe_disabled_when_boto3_missing(monkeypatch):
    """When boto3 is not importable, probe returns disabled."""
    import sec_review_framework.models.probes.bedrock_probe as mod

    monkeypatch.setattr(mod, "_BOTO3_AVAILABLE", False)

    from sec_review_framework.models.probes.bedrock_probe import BedrockProbe

    probe = BedrockProbe()
    snap = await probe.probe()
    assert snap.probe_status == "disabled"
    assert "boto3" in (snap.last_error or "")


async def test_bedrock_probe_disabled_when_env_false(monkeypatch):
    """BEDROCK_PROBE_ENABLED=false → disabled."""
    import sec_review_framework.models.probes.bedrock_probe as mod

    monkeypatch.setattr(mod, "_BOTO3_AVAILABLE", True)
    monkeypatch.setenv("BEDROCK_PROBE_ENABLED", "false")

    probe = mod.BedrockProbe()
    snap = await probe.probe()
    assert snap.probe_status == "disabled"


async def test_bedrock_probe_disabled_when_no_credentials(monkeypatch):
    """No AWS credentials → disabled."""
    import sec_review_framework.models.probes.bedrock_probe as mod

    monkeypatch.setattr(mod, "_BOTO3_AVAILABLE", True)
    monkeypatch.setenv("BEDROCK_PROBE_ENABLED", "true")

    mock_session = MagicMock()
    mock_session.get_credentials.return_value = None

    with patch("sec_review_framework.models.probes.bedrock_probe.boto3.Session", return_value=mock_session):
        probe = mod.BedrockProbe()
        snap = await probe.probe()

    assert snap.probe_status == "disabled"
    assert "credentials" in (snap.last_error or "").lower()


async def test_bedrock_probe_returns_fresh_with_models(monkeypatch):
    """Happy-path: returns fresh snapshot with prefixed model IDs."""
    import sec_review_framework.models.probes.bedrock_probe as mod

    monkeypatch.setattr(mod, "_BOTO3_AVAILABLE", True)
    monkeypatch.setenv("BEDROCK_PROBE_ENABLED", "true")
    monkeypatch.setenv("BEDROCK_PROBE_REGIONS", "us-east-1")

    mock_session = MagicMock()
    mock_session.get_credentials.return_value = MagicMock()  # non-None

    bedrock_client = _make_bedrock_client()

    with (
        patch("sec_review_framework.models.probes.bedrock_probe.boto3.Session", return_value=mock_session),
        patch("sec_review_framework.models.probes.bedrock_probe.boto3.client", return_value=bedrock_client),
    ):
        probe = mod.BedrockProbe()
        snap = await probe.probe()

    assert snap.probe_status == "fresh"
    assert "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0" in snap.model_ids
    assert "bedrock/amazon.titan-text-express-v1" in snap.model_ids
    assert "bedrock/us.anthropic.claude-3-5-sonnet-20241022-v2:0" in snap.model_ids


async def test_bedrock_probe_annotates_region(monkeypatch):
    """ModelMetadata.region matches the probed region."""
    import sec_review_framework.models.probes.bedrock_probe as mod

    monkeypatch.setattr(mod, "_BOTO3_AVAILABLE", True)
    monkeypatch.setenv("BEDROCK_PROBE_ENABLED", "true")
    monkeypatch.setenv("BEDROCK_PROBE_REGIONS", "eu-west-1")

    mock_session = MagicMock()
    mock_session.get_credentials.return_value = MagicMock()

    bedrock_client = _make_bedrock_client()

    with (
        patch("sec_review_framework.models.probes.bedrock_probe.boto3.Session", return_value=mock_session),
        patch("sec_review_framework.models.probes.bedrock_probe.boto3.client", return_value=bedrock_client),
    ):
        probe = mod.BedrockProbe()
        snap = await probe.probe()

    meta = snap.metadata.get("bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0")
    assert meta is not None
    assert meta.region == "eu-west-1"


async def test_bedrock_probe_multi_region(monkeypatch):
    """IDs from multiple regions are unioned."""
    import sec_review_framework.models.probes.bedrock_probe as mod

    monkeypatch.setattr(mod, "_BOTO3_AVAILABLE", True)
    monkeypatch.setenv("BEDROCK_PROBE_ENABLED", "true")
    monkeypatch.setenv("BEDROCK_PROBE_REGIONS", "us-east-1,us-west-2")

    mock_session = MagicMock()
    mock_session.get_credentials.return_value = MagicMock()

    us_east_client = _make_bedrock_client(
        fm_resp={"modelSummaries": [{"modelId": "amazon.titan-text-express-v1", "modelName": "Titan"}]},
        ip_resp={"inferenceProfileSummaries": []},
    )
    us_west_client = _make_bedrock_client(
        fm_resp={"modelSummaries": [{"modelId": "amazon.nova-pro-v1:0", "modelName": "Nova"}]},
        ip_resp={"inferenceProfileSummaries": []},
    )

    call_order = [us_east_client, us_west_client]

    with (
        patch("sec_review_framework.models.probes.bedrock_probe.boto3.Session", return_value=mock_session),
        patch("sec_review_framework.models.probes.bedrock_probe.boto3.client", side_effect=call_order),
    ):
        probe = mod.BedrockProbe()
        snap = await probe.probe()

    assert "bedrock/amazon.titan-text-express-v1" in snap.model_ids
    assert "bedrock/amazon.nova-pro-v1:0" in snap.model_ids


async def test_bedrock_probe_inference_profiles_failure_non_fatal(monkeypatch):
    """list_inference_profiles failure doesn't kill the whole probe."""
    import sec_review_framework.models.probes.bedrock_probe as mod

    monkeypatch.setattr(mod, "_BOTO3_AVAILABLE", True)
    monkeypatch.setenv("BEDROCK_PROBE_ENABLED", "true")
    monkeypatch.setenv("BEDROCK_PROBE_REGIONS", "us-east-1")

    mock_session = MagicMock()
    mock_session.get_credentials.return_value = MagicMock()

    bedrock_client = _make_bedrock_client(
        ip_raises=Exception("InferenceProfiles not available"),
    )

    with (
        patch("sec_review_framework.models.probes.bedrock_probe.boto3.Session", return_value=mock_session),
        patch("sec_review_framework.models.probes.bedrock_probe.boto3.client", return_value=bedrock_client),
    ):
        probe = mod.BedrockProbe()
        snap = await probe.probe()

    # Foundation models still present; inference profiles missing is non-fatal.
    assert snap.probe_status == "fresh"
    assert "bedrock/anthropic.claude-3-5-sonnet-20241022-v2:0" in snap.model_ids


async def test_bedrock_probe_fm_failure_propagates(monkeypatch):
    """list_foundation_models failure propagates (catalog will handle)."""
    import sec_review_framework.models.probes.bedrock_probe as mod

    monkeypatch.setattr(mod, "_BOTO3_AVAILABLE", True)
    monkeypatch.setenv("BEDROCK_PROBE_ENABLED", "true")
    monkeypatch.setenv("BEDROCK_PROBE_REGIONS", "us-east-1")

    mock_session = MagicMock()
    mock_session.get_credentials.return_value = MagicMock()

    bedrock_client = MagicMock()
    bedrock_client.list_foundation_models.side_effect = Exception("AccessDenied")

    with (
        patch("sec_review_framework.models.probes.bedrock_probe.boto3.Session", return_value=mock_session),
        patch("sec_review_framework.models.probes.bedrock_probe.boto3.client", return_value=bedrock_client),
    ):
        probe = mod.BedrockProbe()
        with pytest.raises(Exception, match="AccessDenied"):
            await probe.probe()
