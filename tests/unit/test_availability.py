"""Unit tests for compute_availability() — every (auth, env, snapshot) permutation.

Table-driven: parametrize over all combinations of:
  - auth type: api_key | aws
  - env var set or not
  - snapshot status: fresh | stale | failed | disabled | None (no snapshot)
  - model in snapshot or not
"""

from __future__ import annotations

import pytest

from sec_review_framework.config import ModelProviderConfig
from sec_review_framework.models.availability import (
    ModelEntry,
    ProviderGroup,
    build_id_to_status,
    compute_availability,
    flat_model_list,
    groups_to_dicts,
)
from sec_review_framework.models.catalog import ModelMetadata, ProviderSnapshot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_api_key_cfg(
    model_id: str = "gpt-4o",
    model_name: str = "gpt-4o",
    api_key_env: str = "OPENAI_API_KEY",
    display_name: str | None = "GPT-4o",
) -> ModelProviderConfig:
    return ModelProviderConfig(
        id=model_id,
        model_name=model_name,
        api_key_env=api_key_env,
        auth="api_key",
        display_name=display_name,
    )


def _make_aws_cfg(
    model_id: str = "bedrock-claude",
    model_name: str = "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
    region: str = "us-east-1",
    display_name: str | None = "Claude (Bedrock)",
) -> ModelProviderConfig:
    return ModelProviderConfig(
        id=model_id,
        model_name=model_name,
        auth="aws",
        region=region,
        display_name=display_name,
    )


def _fresh_snapshot(*model_ids: str) -> ProviderSnapshot:
    return ProviderSnapshot(
        probe_status="fresh",
        model_ids=frozenset(model_ids),
        metadata={mid: ModelMetadata(id=mid) for mid in model_ids},
    )


def _disabled_snapshot() -> ProviderSnapshot:
    return ProviderSnapshot(probe_status="disabled")


def _failed_snapshot() -> ProviderSnapshot:
    return ProviderSnapshot(probe_status="failed", last_error="timeout")


def _stale_snapshot(*model_ids: str) -> ProviderSnapshot:
    return ProviderSnapshot(
        probe_status="stale",
        model_ids=frozenset(model_ids),
        metadata={mid: ModelMetadata(id=mid) for mid in model_ids},
    )


# ---------------------------------------------------------------------------
# api_key auth — no API key in env
# ---------------------------------------------------------------------------

class TestApiKeyMissing:
    """All api_key entries → key_missing when env var absent."""

    def test_key_missing_no_snapshot(self):
        cfg = _make_api_key_cfg()
        groups = compute_availability([cfg], {}, {})
        assert groups[0].models[0].status == "key_missing"

    def test_key_missing_fresh_snapshot_model_listed(self):
        cfg = _make_api_key_cfg()
        snap = _fresh_snapshot("gpt-4o")
        groups = compute_availability([cfg], {"openai": snap}, {})
        assert groups[0].models[0].status == "key_missing"

    def test_key_missing_fresh_snapshot_model_not_listed(self):
        cfg = _make_api_key_cfg()
        snap = _fresh_snapshot("gpt-5")
        groups = compute_availability([cfg], {"openai": snap}, {})
        assert groups[0].models[0].status == "key_missing"

    def test_key_missing_failed_snapshot(self):
        cfg = _make_api_key_cfg()
        snap = _failed_snapshot()
        groups = compute_availability([cfg], {"openai": snap}, {})
        assert groups[0].models[0].status == "key_missing"

    def test_key_missing_disabled_snapshot(self):
        cfg = _make_api_key_cfg()
        snap = _disabled_snapshot()
        groups = compute_availability([cfg], {"openai": snap}, {})
        assert groups[0].models[0].status == "key_missing"


# ---------------------------------------------------------------------------
# api_key auth — API key IS set
# ---------------------------------------------------------------------------

class TestApiKeyPresent:
    """Env var is set; status depends on snapshot."""

    def _env(self) -> dict:
        return {"OPENAI_API_KEY": "sk-test"}

    def test_available_when_in_fresh_snapshot(self):
        cfg = _make_api_key_cfg()
        snap = _fresh_snapshot("gpt-4o")
        groups = compute_availability([cfg], {"openai": snap}, self._env())
        assert groups[0].models[0].status == "available"

    def test_available_when_in_stale_snapshot(self):
        cfg = _make_api_key_cfg()
        snap = _stale_snapshot("gpt-4o")
        groups = compute_availability([cfg], {"openai": snap}, self._env())
        assert groups[0].models[0].status == "available"

    def test_not_listed_when_model_absent_from_fresh_snapshot(self):
        cfg = _make_api_key_cfg()
        snap = _fresh_snapshot("gpt-4o-other")
        groups = compute_availability([cfg], {"openai": snap}, self._env())
        assert groups[0].models[0].status == "not_listed"

    def test_probe_failed_when_snapshot_failed(self):
        cfg = _make_api_key_cfg()
        snap = _failed_snapshot()
        groups = compute_availability([cfg], {"openai": snap}, self._env())
        assert groups[0].models[0].status == "probe_failed"

    def test_available_when_snapshot_disabled_key_present(self):
        """Key is set but probing is disabled → trust the key, mark available."""
        cfg = _make_api_key_cfg()
        snap = _disabled_snapshot()
        groups = compute_availability([cfg], {"openai": snap}, self._env())
        assert groups[0].models[0].status == "available"

    def test_available_when_no_snapshot_key_present(self):
        """No snapshot for provider (no probe registered) + key set → available."""
        cfg = _make_api_key_cfg()
        groups = compute_availability([cfg], {}, self._env())
        assert groups[0].models[0].status == "available"


# ---------------------------------------------------------------------------
# aws auth
# ---------------------------------------------------------------------------

class TestAwsAuth:
    """Bedrock models: status driven by bedrock snapshot, not env vars."""

    def test_key_missing_when_snapshot_missing(self):
        cfg = _make_aws_cfg()
        groups = compute_availability([cfg], {}, {})
        assert groups[0].models[0].status == "key_missing"

    def test_key_missing_when_snapshot_disabled(self):
        cfg = _make_aws_cfg()
        snap = _disabled_snapshot()
        groups = compute_availability([cfg], {"bedrock": snap}, {})
        assert groups[0].models[0].status == "key_missing"

    def test_available_when_model_in_fresh_snapshot(self):
        model_name = "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"
        cfg = _make_aws_cfg(model_name=model_name)
        snap = _fresh_snapshot(model_name)
        groups = compute_availability([cfg], {"bedrock": snap}, {})
        assert groups[0].models[0].status == "available"

    def test_not_listed_when_model_absent_from_fresh_snapshot(self):
        cfg = _make_aws_cfg()
        snap = _fresh_snapshot("bedrock/other-model")
        groups = compute_availability([cfg], {"bedrock": snap}, {})
        assert groups[0].models[0].status == "not_listed"

    def test_probe_failed_when_snapshot_failed(self):
        cfg = _make_aws_cfg()
        snap = _failed_snapshot()
        groups = compute_availability([cfg], {"bedrock": snap}, {})
        assert groups[0].models[0].status == "probe_failed"

    def test_available_in_stale_snapshot(self):
        model_name = "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"
        cfg = _make_aws_cfg(model_name=model_name)
        snap = _stale_snapshot(model_name)
        groups = compute_availability([cfg], {"bedrock": snap}, {})
        assert groups[0].models[0].status == "available"


# ---------------------------------------------------------------------------
# Provider key derivation
# ---------------------------------------------------------------------------

class TestProviderKeyDerivation:
    """Provider key is derived correctly from api_key_env."""

    @pytest.mark.parametrize("api_key_env,expected_provider", [
        ("OPENAI_API_KEY", "openai"),
        ("ANTHROPIC_API_KEY", "anthropic"),
        ("GEMINI_API_KEY", "gemini"),
        ("MISTRAL_API_KEY", "mistral"),
        ("COHERE_API_KEY", "cohere"),
        ("OPENROUTER_API_KEY", "openrouter"),
    ])
    def test_api_key_env_to_provider(self, api_key_env: str, expected_provider: str):
        cfg = _make_api_key_cfg(api_key_env=api_key_env)
        groups = compute_availability([cfg], {}, {})
        assert groups[0].provider == expected_provider

    def test_aws_maps_to_bedrock(self):
        cfg = _make_aws_cfg()
        groups = compute_availability([cfg], {}, {})
        assert groups[0].provider == "bedrock"


# ---------------------------------------------------------------------------
# Grouping — multiple models same provider
# ---------------------------------------------------------------------------

class TestGrouping:
    def test_same_provider_merged_into_one_group(self):
        cfg1 = _make_api_key_cfg(model_id="gpt-4o", model_name="gpt-4o")
        cfg2 = _make_api_key_cfg(model_id="gpt-4o-mini", model_name="gpt-4o-mini")
        groups = compute_availability([cfg1, cfg2], {}, {})
        assert len(groups) == 1
        assert len(groups[0].models) == 2

    def test_different_providers_separate_groups(self):
        cfg1 = _make_api_key_cfg(api_key_env="OPENAI_API_KEY")
        cfg2 = _make_api_key_cfg(
            model_id="claude", model_name="claude-3", api_key_env="ANTHROPIC_API_KEY"
        )
        groups = compute_availability([cfg1, cfg2], {}, {})
        assert len(groups) == 2
        providers = {g.provider for g in groups}
        assert providers == {"openai", "anthropic"}

    def test_probe_status_propagated_from_snapshot(self):
        cfg = _make_api_key_cfg()
        snap = _fresh_snapshot("gpt-4o")
        groups = compute_availability([cfg], {"openai": snap}, {"OPENAI_API_KEY": "sk"})
        assert groups[0].probe_status == "fresh"

    def test_probe_status_disabled_when_no_snapshot(self):
        cfg = _make_api_key_cfg()
        groups = compute_availability([cfg], {}, {})
        assert groups[0].probe_status == "disabled"


# ---------------------------------------------------------------------------
# Metadata enrichment
# ---------------------------------------------------------------------------

class TestMetadataEnrichment:
    def test_display_name_from_registry(self):
        cfg = _make_api_key_cfg(display_name="My Model")
        groups = compute_availability([cfg], {}, {})
        assert groups[0].models[0].display_name == "My Model"

    def test_snapshot_display_name_wins(self):
        cfg = _make_api_key_cfg(model_name="gpt-4o", display_name="Registry Name")
        snap = ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["gpt-4o"]),
            metadata={"gpt-4o": ModelMetadata(id="gpt-4o", display_name="Snapshot Name")},
        )
        groups = compute_availability(
            [cfg], {"openai": snap}, {"OPENAI_API_KEY": "sk"}
        )
        assert groups[0].models[0].display_name == "Snapshot Name"

    def test_context_length_from_snapshot(self):
        cfg = _make_api_key_cfg(model_name="gpt-4o")
        snap = ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["gpt-4o"]),
            metadata={"gpt-4o": ModelMetadata(id="gpt-4o", context_length=128000)},
        )
        groups = compute_availability(
            [cfg], {"openai": snap}, {"OPENAI_API_KEY": "sk"}
        )
        assert groups[0].models[0].context_length == 128000

    def test_region_from_aws_config(self):
        cfg = _make_aws_cfg(region="us-west-2")
        snap = _fresh_snapshot("bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0")
        groups = compute_availability([cfg], {"bedrock": snap}, {})
        assert groups[0].models[0].region == "us-west-2"


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

class TestSerialisationHelpers:
    def _groups(self) -> list[ProviderGroup]:
        cfg1 = _make_api_key_cfg(model_name="gpt-4o")
        snap = _fresh_snapshot("gpt-4o")
        return compute_availability(
            [cfg1], {"openai": snap}, {"OPENAI_API_KEY": "sk"}
        )

    def test_groups_to_dicts_shape(self):
        groups = self._groups()
        result = groups_to_dicts(groups)
        assert isinstance(result, list)
        assert result[0]["provider"] == "openai"
        assert result[0]["probe_status"] == "fresh"
        model = result[0]["models"][0]
        assert model["id"] == "gpt-4o"
        assert model["status"] == "available"

    def test_flat_list_shape(self):
        groups = self._groups()
        result = flat_model_list(groups)
        assert isinstance(result, list)
        assert result[0]["id"] == "gpt-4o"
        # flat list should NOT include status
        assert "status" not in result[0]

    def test_build_id_to_status(self):
        groups = self._groups()
        mapping = build_id_to_status(groups)
        assert mapping["gpt-4o"] == "available"

    def test_context_length_omitted_when_none(self):
        cfg = _make_api_key_cfg(model_name="gpt-4o")
        groups = compute_availability([cfg], {}, {})
        result = groups_to_dicts(groups)
        model = result[0]["models"][0]
        assert "context_length" not in model

    def test_region_omitted_for_api_key_models(self):
        cfg = _make_api_key_cfg()
        groups = compute_availability([cfg], {}, {})
        result = groups_to_dicts(groups)
        model = result[0]["models"][0]
        assert "region" not in model


# ---------------------------------------------------------------------------
# OpenRouter end-to-end: prefixed model_name matches prefixed snapshot entry
# ---------------------------------------------------------------------------

class TestOpenRouterAvailability:
    """Registry model_name='openrouter/foo' + snapshot containing 'openrouter/foo'
    must resolve to 'available'.  This guards against the probe returning
    un-prefixed IDs that would never match the registry entries.
    """

    def test_openrouter_available_when_prefixed_id_in_snapshot(self):
        cfg = ModelProviderConfig(
            id="llama-8b",
            model_name="openrouter/meta-llama/llama-3.1-8b-instruct",
            api_key_env="OPENROUTER_API_KEY",
            auth="api_key",
            display_name="Llama 3.1 8B",
        )
        snap = ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["openrouter/meta-llama/llama-3.1-8b-instruct"]),
            metadata={
                "openrouter/meta-llama/llama-3.1-8b-instruct": ModelMetadata(
                    id="openrouter/meta-llama/llama-3.1-8b-instruct"
                )
            },
        )
        groups = compute_availability(
            [cfg],
            {"openrouter": snap},
            {"OPENROUTER_API_KEY": "sk-or-test"},
        )
        assert groups[0].models[0].status == "available"

    def test_openrouter_not_listed_when_unprefixed_id_in_snapshot(self):
        """If the probe (incorrectly) returned un-prefixed IDs the model would
        appear not_listed.  This documents the expected failure mode."""
        cfg = ModelProviderConfig(
            id="llama-8b",
            model_name="openrouter/meta-llama/llama-3.1-8b-instruct",
            api_key_env="OPENROUTER_API_KEY",
            auth="api_key",
            display_name="Llama 3.1 8B",
        )
        snap = ProviderSnapshot(
            probe_status="fresh",
            # Simulate the buggy probe that omits the prefix.
            model_ids=frozenset(["meta-llama/llama-3.1-8b-instruct"]),
            metadata={},
        )
        groups = compute_availability(
            [cfg],
            {"openrouter": snap},
            {"OPENROUTER_API_KEY": "sk-or-test"},
        )
        assert groups[0].models[0].status == "not_listed"


# ---------------------------------------------------------------------------
# Synthesized local-LLM integration
# ---------------------------------------------------------------------------

class TestSynthesizedLocalLLM:
    """compute_availability layers probe-discovered local models onto the registry."""

    def _local_snap(self, *model_ids: str, status: str = "fresh") -> ProviderSnapshot:
        return ProviderSnapshot(
            probe_status=status,  # type: ignore[arg-type]
            model_ids=frozenset(model_ids),
        )

    def test_compute_availability_emits_synthesized_local_models(self, monkeypatch):
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://x")
        monkeypatch.delenv("LOCAL_LLM_API_KEY", raising=False)

        snap = self._local_snap("openai/foo", "openai/bar")
        groups = compute_availability([], {"local_llm": snap}, {})

        local_group = next((g for g in groups if g.provider == "local_llm"), None)
        assert local_group is not None, "expected a local_llm provider group"
        ids = {m.id for m in local_group.models}
        assert "local_llm-foo" in ids
        assert "local_llm-bar" in ids
        assert all(m.status == "available" for m in local_group.models)

    def test_registry_entry_wins_over_synthesized_same_id(self, monkeypatch):
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://x")

        # Registry has an entry whose id would collide with the synthesized one.
        registry_cfg = ModelProviderConfig.model_construct(
            id="local_llm-foo",
            model_name="openai/foo",
            api_base="http://registry-override",
            api_key_env="LOCAL_LLM_API_KEY",
            auth="api_key",
            display_name="registry-foo",
        )
        snap = self._local_snap("openai/foo")
        groups = compute_availability([registry_cfg], {"local_llm": snap}, {})

        local_group = next(g for g in groups if g.provider == "local_llm")
        foo_entries = [m for m in local_group.models if m.id == "local_llm-foo"]
        assert len(foo_entries) == 1
        # Registry display_name must win, not the synthesized raw id.
        assert foo_entries[0].display_name == "registry-foo"

    def test_synthesized_available_without_api_key_env_set_in_os_environ(self, monkeypatch):
        """Synthesized config has api_key_env set but the key is absent from env —
        api_base presence proves the endpoint is reachable, so status is still available."""
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://x")
        monkeypatch.delenv("LOCAL_LLM_API_KEY", raising=False)

        snap = self._local_snap("openai/mymodel")
        groups = compute_availability([], {"local_llm": snap}, {})

        local_group = next(g for g in groups if g.provider == "local_llm")
        assert local_group.models[0].status == "available"

    def test_synthesized_skipped_when_snapshot_disabled(self, monkeypatch):
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://x")

        snap = ProviderSnapshot(probe_status="disabled")
        groups = compute_availability([], {"local_llm": snap}, {})

        provider_keys = {g.provider for g in groups}
        assert "local_llm" not in provider_keys

    def test_hand_written_api_base_entry_without_api_key_env_does_not_crash(self):
        cfg = ModelProviderConfig(
            id="local-hand",
            model_name="openai/hand-model",
            auth="api_key",
            api_base="http://x",
        )
        snap = ProviderSnapshot(probe_status="failed", last_error="boom")
        import os as _os

        groups = compute_availability([cfg], {"local": snap}, _os.environ)

        assert len(groups) == 1
        assert groups[0].models[0].status == "probe_failed"
