"""Unit tests for synthesize_configs_from_snapshot and related helpers.

Phase 2 rewrite: tests cover the new AuthSpec-based API, deterministic_display_name,
and all AuthSpec variants (ApiKeyAuth, AwsAuth, NoAuth).
"""

from __future__ import annotations

import pytest

from sec_review_framework.models.catalog import ModelMetadata, ProviderSnapshot
from sec_review_framework.models.synthesized import (
    ApiKeyAuth,
    AuthSpec,
    AwsAuth,
    NoAuth,
    deterministic_display_name,
    synthesize_configs_from_snapshot,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_snapshot(model_ids: set[str], metadata: dict | None = None) -> ProviderSnapshot:
    meta = metadata or {mid: ModelMetadata(id=mid, raw_id=mid) for mid in model_ids}
    return ProviderSnapshot(
        probe_status="fresh",
        model_ids=frozenset(model_ids),
        metadata=meta,
    )


def _disabled_snapshot() -> ProviderSnapshot:
    return ProviderSnapshot(probe_status="disabled")


def _failed_snapshot() -> ProviderSnapshot:
    return ProviderSnapshot(probe_status="failed")


def _bedrock_meta(raw_id: str, region: str = "us-east-1") -> ModelMetadata:
    return ModelMetadata(
        id=raw_id,
        raw_id=raw_id,
        region=region,
        provider_key="bedrock",
    )


# ---------------------------------------------------------------------------
# deterministic_display_name
# ---------------------------------------------------------------------------

class TestDeterministicDisplayName:
    def test_returns_display_name_when_set(self):
        meta = ModelMetadata(id="x", display_name="My Model", raw_id="x")
        assert deterministic_display_name(meta) == "My Model"

    def test_strips_bedrock_prefix(self):
        meta = ModelMetadata(
            id="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
            raw_id="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
        )
        name = deterministic_display_name(meta)
        # Should strip "bedrock/anthropic." and version suffix.
        assert "bedrock/" not in name
        assert "anthropic." not in name

    def test_strips_openrouter_prefix_and_org(self):
        meta = ModelMetadata(
            id="openrouter/meta-llama/llama-3.1-8b-instruct",
            raw_id="openrouter/meta-llama/llama-3.1-8b-instruct",
        )
        name = deterministic_display_name(meta)
        assert "openrouter/" not in name
        assert "meta-llama/" not in name
        assert "llama" in name.lower()

    def test_strips_openai_prefix(self):
        meta = ModelMetadata(
            id="openai/gpt-oss",
            raw_id="openai/gpt-oss",
        )
        name = deterministic_display_name(meta)
        assert "openai/" not in name

    def test_strips_bedrock_version_suffix(self):
        raw_id = "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"
        meta = ModelMetadata(id=raw_id, raw_id=raw_id)
        name = deterministic_display_name(meta)
        assert "20240620" not in name
        assert "v1:0" not in name

    def test_falls_back_to_id_when_raw_id_missing(self):
        meta = ModelMetadata(id="gpt-4o")
        name = deterministic_display_name(meta)
        assert name  # non-empty

    def test_bare_model_id_gives_reasonable_name(self):
        meta = ModelMetadata(id="gpt-4o", raw_id="gpt-4o")
        name = deterministic_display_name(meta)
        assert "gpt" in name.lower() or "4o" in name.lower()


# ---------------------------------------------------------------------------
# ApiKeyAuth
# ---------------------------------------------------------------------------

class TestApiKeyAuth:
    def test_basic_api_key(self):
        snap = _fresh_snapshot({"gpt-4o"})
        result = synthesize_configs_from_snapshot(
            "openai", snap, ApiKeyAuth(api_key_env="OPENAI_API_KEY")
        )
        assert len(result) == 1
        cfg = result[0]
        assert cfg.id == "gpt-4o"
        assert cfg.model_name == "gpt-4o"
        assert cfg.auth == "api_key"
        assert cfg.api_key_env == "OPENAI_API_KEY"
        assert cfg.api_base is None

    def test_api_key_with_api_base(self, monkeypatch):
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://x")
        snap = _fresh_snapshot({"openai/gpt-oss"})
        result = synthesize_configs_from_snapshot(
            "local_llm",
            snap,
            ApiKeyAuth(api_key_env="LOCAL_LLM_API_KEY", api_base_env="LOCAL_LLM_BASE_URL"),
        )
        assert len(result) == 1
        cfg = result[0]
        assert cfg.id == "openai/gpt-oss"
        assert cfg.api_base == "http://x"
        assert cfg.api_key_env == "LOCAL_LLM_API_KEY"

    def test_api_key_with_api_base_direct(self):
        snap = _fresh_snapshot({"openai/gpt-oss"})
        result = synthesize_configs_from_snapshot(
            "local_llm",
            snap,
            ApiKeyAuth(api_key_env="LOCAL_LLM_API_KEY", api_base="http://direct"),
        )
        assert len(result) == 1
        assert result[0].api_base == "http://direct"

    def test_returns_empty_when_api_base_env_not_set(self, monkeypatch):
        monkeypatch.delenv("LOCAL_LLM_BASE_URL", raising=False)
        snap = _fresh_snapshot({"openai/some-model"})
        result = synthesize_configs_from_snapshot(
            "local_llm",
            snap,
            ApiKeyAuth(api_key_env="LOCAL_LLM_API_KEY", api_base_env="LOCAL_LLM_BASE_URL"),
        )
        assert result == []

    def test_sorted_by_raw_id(self):
        snap = _fresh_snapshot({"gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo"})
        result = synthesize_configs_from_snapshot(
            "openai", snap, ApiKeyAuth(api_key_env="OPENAI_API_KEY")
        )
        ids = [c.id for c in result]
        assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# AwsAuth
# ---------------------------------------------------------------------------

class TestAwsAuth:
    def test_bedrock_produces_aws_auth_configs(self):
        raw_id = "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"
        snap = _fresh_snapshot(
            {raw_id},
            metadata={raw_id: _bedrock_meta(raw_id, "us-east-1")},
        )
        result = synthesize_configs_from_snapshot("bedrock", snap, AwsAuth())
        assert len(result) == 1
        cfg = result[0]
        assert cfg.auth == "aws"
        assert cfg.region == "us-east-1"
        assert cfg.id == raw_id
        assert cfg.model_name == raw_id

    def test_bedrock_region_from_metadata(self):
        raw_id = "bedrock/anthropic.claude-3-5-haiku-20241022-v1:0"
        snap = _fresh_snapshot(
            {raw_id},
            metadata={raw_id: _bedrock_meta(raw_id, "eu-west-1")},
        )
        result = synthesize_configs_from_snapshot("bedrock", snap, AwsAuth())
        assert result[0].region == "eu-west-1"

    def test_bedrock_entry_without_region_skipped(self):
        raw_id = "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"
        snap = ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset([raw_id]),
            metadata={
                raw_id: ModelMetadata(id=raw_id, raw_id=raw_id, region=None)
            },
        )
        result = synthesize_configs_from_snapshot("bedrock", snap, AwsAuth())
        assert result == []

    def test_bedrock_sorted_deterministically(self):
        models = {
            "bedrock/amazon.nova-pro-v1:0": "us-east-1",
            "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0": "us-east-1",
            "bedrock/amazon.nova-lite-v1:0": "us-east-1",
        }
        snap = _fresh_snapshot(
            set(models.keys()),
            metadata={k: _bedrock_meta(k, v) for k, v in models.items()},
        )
        result = synthesize_configs_from_snapshot("bedrock", snap, AwsAuth())
        ids = [c.id for c in result]
        assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# NoAuth
# ---------------------------------------------------------------------------

class TestNoAuth:
    def test_no_auth_produces_api_key_configs_without_env(self):
        snap = _fresh_snapshot({"openai/model-a"})
        result = synthesize_configs_from_snapshot("local_llm", snap, NoAuth())
        assert len(result) == 1
        cfg = result[0]
        assert cfg.auth == "api_key"
        assert cfg.api_key_env is None


# ---------------------------------------------------------------------------
# Snapshot status handling
# ---------------------------------------------------------------------------

class TestSnapshotStatus:
    def test_disabled_returns_empty(self):
        result = synthesize_configs_from_snapshot(
            "openai", _disabled_snapshot(), ApiKeyAuth(api_key_env="OPENAI_API_KEY")
        )
        assert result == []

    def test_failed_returns_empty(self):
        result = synthesize_configs_from_snapshot(
            "openai", _failed_snapshot(), ApiKeyAuth(api_key_env="OPENAI_API_KEY")
        )
        assert result == []

    def test_stale_snapshot_returns_configs(self):
        snap = ProviderSnapshot(
            probe_status="stale",
            model_ids=frozenset(["gpt-4o"]),
            metadata={"gpt-4o": ModelMetadata(id="gpt-4o", raw_id="gpt-4o")},
        )
        result = synthesize_configs_from_snapshot(
            "openai", snap, ApiKeyAuth(api_key_env="OPENAI_API_KEY")
        )
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Display name in synthesized configs
# ---------------------------------------------------------------------------

class TestSynthesizedDisplayName:
    def test_display_name_from_metadata(self):
        snap = ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["gpt-4o"]),
            metadata={
                "gpt-4o": ModelMetadata(
                    id="gpt-4o",
                    raw_id="gpt-4o",
                    display_name="GPT-4o (Custom)",
                )
            },
        )
        result = synthesize_configs_from_snapshot(
            "openai", snap, ApiKeyAuth(api_key_env="OPENAI_API_KEY")
        )
        assert result[0].display_name == "GPT-4o (Custom)"

    def test_display_name_deterministic_when_no_metadata_name(self):
        snap = ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["gpt-4o"]),
            metadata={"gpt-4o": ModelMetadata(id="gpt-4o", raw_id="gpt-4o")},
        )
        result = synthesize_configs_from_snapshot(
            "openai", snap, ApiKeyAuth(api_key_env="OPENAI_API_KEY")
        )
        # Should have a non-empty display name derived deterministically.
        assert result[0].display_name
