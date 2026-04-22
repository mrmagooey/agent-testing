"""Unit tests for _effective_registry helper."""

from __future__ import annotations

from sec_review_framework.config import ModelProviderConfig
from sec_review_framework.models.availability import _effective_registry
from sec_review_framework.models.catalog import ProviderSnapshot


def _make_cfg(id: str, model_name: str = "openai/test") -> ModelProviderConfig:
    return ModelProviderConfig.model_construct(
        id=id,
        model_name=model_name,
        api_base="http://local",
        api_key_env="LOCAL_LLM_API_KEY",
        auth="api_key",
        display_name=id,
    )


def _fresh_local_snap(*model_ids: str) -> ProviderSnapshot:
    return ProviderSnapshot(
        probe_status="fresh",
        model_ids=frozenset(model_ids),
    )


class TestEffectiveRegistry:
    def test_effective_registry_dedups_by_id(self, monkeypatch):
        """Registry entry with same id as a synthesized config wins — no duplicate."""
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://x")

        registry_cfg = ModelProviderConfig.model_construct(
            id="local_llm-foo",
            model_name="openai/foo",
            api_base="http://registry",
            api_key_env="LOCAL_LLM_API_KEY",
            auth="api_key",
            display_name="registry-display",
        )
        snap = _fresh_local_snap("openai/foo")
        result = _effective_registry([registry_cfg], {"local_llm": snap})

        matching = [c for c in result if c.id == "local_llm-foo"]
        assert len(matching) == 1
        # Registry entry (not synthesized) wins.
        assert matching[0].display_name == "registry-display"

    def test_effective_registry_passes_through_when_no_local_snapshot(self):
        """Empty snapshots dict → helper returns registry unchanged."""
        cfg = _make_cfg("some-model")
        result = _effective_registry([cfg], {})
        assert result == [cfg]

    def test_effective_registry_adds_synthesized_when_not_in_registry(self, monkeypatch):
        """New model from snapshot gets appended when registry has no clash."""
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://x")

        snap = _fresh_local_snap("openai/newmodel")
        result = _effective_registry([], {"local_llm": snap})

        assert any(c.id == "local_llm-newmodel" for c in result)

    def test_effective_registry_skips_failed_snapshot(self, monkeypatch):
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://x")

        snap = ProviderSnapshot(probe_status="failed")
        result = _effective_registry([], {"local_llm": snap})

        assert result == []
