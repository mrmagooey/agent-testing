"""Unit tests for build_effective_registry — probe-driven registry builder.

Replaces the old _effective_registry helper tests.  The new function takes
a snapshots dict and returns a synthesized list[ModelProviderConfig] directly
from probe data, with no YAML involved.
"""

from __future__ import annotations

from sec_review_framework.config import ModelProviderConfig
from sec_review_framework.models.availability import build_effective_registry
from sec_review_framework.models.catalog import ModelMetadata, ProviderSnapshot


# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------

def _fresh_snap(
    *model_ids: str,
    metadata: dict | None = None,
) -> ProviderSnapshot:
    meta = metadata or {mid: ModelMetadata(id=mid, raw_id=mid) for mid in model_ids}
    return ProviderSnapshot(
        probe_status="fresh",
        model_ids=frozenset(model_ids),
        metadata=meta,
    )


def _disabled_snap() -> ProviderSnapshot:
    return ProviderSnapshot(probe_status="disabled")


def _failed_snap() -> ProviderSnapshot:
    return ProviderSnapshot(probe_status="failed")


# ---------------------------------------------------------------------------
# Basic empty/disabled/failed states
# ---------------------------------------------------------------------------

class TestBuildEffectiveRegistryBasics:
    def test_empty_snapshots_returns_empty(self):
        result = build_effective_registry({})
        assert result == []

    def test_disabled_snapshot_returns_empty(self):
        result = build_effective_registry({"openai": _disabled_snap()})
        assert result == []

    def test_failed_snapshot_returns_empty(self):
        result = build_effective_registry({"openai": _failed_snap()})
        assert result == []


# ---------------------------------------------------------------------------
# OpenAI snapshot
# ---------------------------------------------------------------------------

class TestOpenAISnapshot:
    def test_openai_models_get_api_key_auth(self):
        snap = _fresh_snap("gpt-4o", "gpt-4o-mini")
        result = build_effective_registry({"openai": snap})
        assert len(result) == 2
        for cfg in result:
            assert cfg.auth == "api_key"
            assert cfg.api_key_env == "OPENAI_API_KEY"

    def test_openai_id_equals_raw_id(self):
        snap = _fresh_snap("gpt-4o")
        result = build_effective_registry({"openai": snap})
        assert result[0].id == "gpt-4o"
        assert result[0].model_name == "gpt-4o"

    def test_openai_sorted_deterministically(self):
        snap = _fresh_snap("gpt-4o-mini", "gpt-4o", "gpt-3.5-turbo")
        result = build_effective_registry({"openai": snap})
        ids = [c.id for c in result]
        assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# Anthropic snapshot
# ---------------------------------------------------------------------------

class TestAnthropicSnapshot:
    def test_anthropic_models_get_anthropic_api_key(self):
        snap = _fresh_snap("claude-3-5-sonnet-latest", "claude-3-5-haiku-latest")
        result = build_effective_registry({"anthropic": snap})
        assert len(result) == 2
        for cfg in result:
            assert cfg.api_key_env == "ANTHROPIC_API_KEY"
            assert cfg.auth == "api_key"

    def test_anthropic_id_is_full_routing_string(self):
        snap = _fresh_snap("claude-opus-4")
        result = build_effective_registry({"anthropic": snap})
        assert result[0].id == "claude-opus-4"


# ---------------------------------------------------------------------------
# Gemini snapshot
# ---------------------------------------------------------------------------

class TestGeminiSnapshot:
    def test_gemini_api_key_env(self):
        snap = _fresh_snap("gemini-2.5-pro", "gemini-2.0-flash")
        result = build_effective_registry({"gemini": snap})
        for cfg in result:
            assert cfg.api_key_env == "GEMINI_API_KEY"

    def test_gemini_sorted(self):
        snap = _fresh_snap("gemini-2.5-pro", "gemini-2.0-flash", "gemini-1.5-pro")
        result = build_effective_registry({"gemini": snap})
        ids = [c.id for c in result]
        assert ids == sorted(ids)


# ---------------------------------------------------------------------------
# Cohere snapshot
# ---------------------------------------------------------------------------

class TestCohereSnapshot:
    def test_cohere_api_key_env(self):
        snap = _fresh_snap("command-r-plus", "command-r")
        result = build_effective_registry({"cohere": snap})
        for cfg in result:
            assert cfg.api_key_env == "COHERE_API_KEY"
            assert cfg.auth == "api_key"


# ---------------------------------------------------------------------------
# OpenRouter snapshot
# ---------------------------------------------------------------------------

class TestOpenRouterSnapshot:
    def test_openrouter_api_key_env(self):
        snap = _fresh_snap(
            "openrouter/meta-llama/llama-3.1-8b-instruct",
            "openrouter/meta-llama/llama-3.2-3b-instruct",
        )
        result = build_effective_registry({"openrouter": snap})
        assert len(result) == 2
        for cfg in result:
            assert cfg.api_key_env == "OPENROUTER_API_KEY"

    def test_openrouter_id_is_full_routing_string(self):
        raw_id = "openrouter/meta-llama/llama-3.1-8b-instruct"
        snap = _fresh_snap(raw_id)
        result = build_effective_registry({"openrouter": snap})
        assert result[0].id == raw_id
        assert result[0].model_name == raw_id


# ---------------------------------------------------------------------------
# Bedrock snapshot — region pass-through
# ---------------------------------------------------------------------------

class TestBedrockSnapshot:
    def _bedrock_meta(self, raw_id: str, region: str) -> ModelMetadata:
        return ModelMetadata(
            id=raw_id,
            raw_id=raw_id,
            region=region,
            provider_key="bedrock",
        )

    def _bedrock_snap(self, models: dict[str, str]) -> ProviderSnapshot:
        """models: {raw_id: region}"""
        return ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(models.keys()),
            metadata={
                raw_id: self._bedrock_meta(raw_id, region)
                for raw_id, region in models.items()
            },
        )

    def test_bedrock_auth_is_aws(self):
        snap = self._bedrock_snap({
            "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0": "us-east-1",
        })
        result = build_effective_registry({"bedrock": snap})
        assert len(result) == 1
        assert result[0].auth == "aws"

    def test_bedrock_region_set_from_metadata(self):
        snap = self._bedrock_snap({
            "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0": "us-west-2",
        })
        result = build_effective_registry({"bedrock": snap})
        assert result[0].region == "us-west-2"

    def test_bedrock_id_is_full_routing_string(self):
        raw_id = "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"
        snap = self._bedrock_snap({raw_id: "us-east-1"})
        result = build_effective_registry({"bedrock": snap})
        assert result[0].id == raw_id
        assert result[0].model_name == raw_id

    def test_bedrock_sorted_deterministically(self):
        snap = self._bedrock_snap({
            "bedrock/amazon.nova-pro-v1:0": "us-east-1",
            "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0": "us-east-1",
            "bedrock/amazon.nova-lite-v1:0": "us-east-1",
        })
        result = build_effective_registry({"bedrock": snap})
        ids = [c.id for c in result]
        assert ids == sorted(ids)

    def test_bedrock_entry_without_region_skipped(self):
        """Bedrock entries missing region in metadata are skipped (guard)."""
        snap = ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"]),
            metadata={
                "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0": ModelMetadata(
                    id="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
                    raw_id="bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
                    region=None,  # missing
                )
            },
        )
        result = build_effective_registry({"bedrock": snap})
        # Entry without region is skipped.
        assert result == []


# ---------------------------------------------------------------------------
# Multi-provider snapshot
# ---------------------------------------------------------------------------

class TestMultiProviderRegistry:
    def test_multiple_providers_all_synthesized(self):
        snapshots = {
            "openai": _fresh_snap("gpt-4o"),
            "anthropic": _fresh_snap("claude-opus-4"),
        }
        result = build_effective_registry(snapshots)
        ids = {c.id for c in result}
        assert "gpt-4o" in ids
        assert "claude-opus-4" in ids

    def test_insertion_order_preserved(self):
        """Providers appear in the order they were passed in the snapshots dict."""
        snapshots = {
            "anthropic": _fresh_snap("claude-opus-4"),
            "openai": _fresh_snap("gpt-4o"),
        }
        result = build_effective_registry(snapshots)
        # Anthropic entry should come before OpenAI entry.
        anthropic_idx = next(i for i, c in enumerate(result) if c.id == "claude-opus-4")
        openai_idx = next(i for i, c in enumerate(result) if c.id == "gpt-4o")
        assert anthropic_idx < openai_idx


# ---------------------------------------------------------------------------
# local_llm snapshot — ApiKeyAuth with api_base_env
# ---------------------------------------------------------------------------

class TestLocalLLMSnapshot:
    def test_local_llm_gets_api_base(self, monkeypatch):
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://localhost:11434")
        snap = _fresh_snap("openai/llama3")
        result = build_effective_registry({"local_llm": snap})
        assert len(result) == 1
        assert result[0].api_base == "http://localhost:11434"
        assert result[0].api_key_env == "LOCAL_LLM_API_KEY"

    def test_local_llm_returns_empty_when_base_url_missing(self, monkeypatch):
        monkeypatch.delenv("LOCAL_LLM_BASE_URL", raising=False)
        snap = _fresh_snap("openai/llama3")
        result = build_effective_registry({"local_llm": snap})
        assert result == []
