"""Unit tests for synthesize_configs_from_snapshot."""

from __future__ import annotations

from sec_review_framework.models.catalog import ProviderSnapshot

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fresh_snapshot(model_ids: set[str]) -> ProviderSnapshot:
    return ProviderSnapshot(
        probe_status="fresh",
        model_ids=frozenset(model_ids),
    )


def _disabled_snapshot() -> ProviderSnapshot:
    return ProviderSnapshot(probe_status="disabled")


def _failed_snapshot() -> ProviderSnapshot:
    return ProviderSnapshot(probe_status="failed")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_synthesize_strips_openai_prefix_for_raw_id(monkeypatch):
    """openai/ prefix is stripped to derive raw_id; full prefixed string becomes model_name."""
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://x")

    from sec_review_framework.models.synthesized import synthesize_configs_from_snapshot

    snap = _fresh_snapshot({"openai/gpt-oss"})
    result = synthesize_configs_from_snapshot(
        "local_llm",
        snap,
        api_key_env="LOCAL_LLM_API_KEY",
        api_base_env="LOCAL_LLM_BASE_URL",
    )

    assert len(result) == 1
    cfg = result[0]
    assert cfg.id == "local_llm-gpt-oss"
    assert cfg.model_name == "openai/gpt-oss"
    assert cfg.api_base == "http://x"
    assert cfg.display_name == "gpt-oss"


def test_synthesize_empty_on_disabled_snapshot(monkeypatch):
    """disabled snapshot → empty list."""
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://x")

    from sec_review_framework.models.synthesized import synthesize_configs_from_snapshot

    result = synthesize_configs_from_snapshot(
        "local_llm",
        _disabled_snapshot(),
        api_key_env="LOCAL_LLM_API_KEY",
        api_base_env="LOCAL_LLM_BASE_URL",
    )
    assert result == []


def test_synthesize_empty_on_failed_snapshot(monkeypatch):
    """failed snapshot → empty list."""
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://x")

    from sec_review_framework.models.synthesized import synthesize_configs_from_snapshot

    result = synthesize_configs_from_snapshot(
        "local_llm",
        _failed_snapshot(),
        api_key_env="LOCAL_LLM_API_KEY",
        api_base_env="LOCAL_LLM_BASE_URL",
    )
    assert result == []


def test_synthesize_empty_when_api_base_env_not_set(monkeypatch):
    """Fresh snapshot but missing base-URL env var → empty list (defensive)."""
    monkeypatch.delenv("LOCAL_LLM_BASE_URL", raising=False)

    from sec_review_framework.models.synthesized import synthesize_configs_from_snapshot

    snap = _fresh_snapshot({"openai/some-model"})
    result = synthesize_configs_from_snapshot(
        "local_llm",
        snap,
        api_key_env="LOCAL_LLM_API_KEY",
        api_base_env="LOCAL_LLM_BASE_URL",
    )
    assert result == []


def test_synthesize_skips_validation_for_api_key_env(monkeypatch):
    """model_construct bypasses Pydantic validators so a missing/unusual api_key_env is fine."""
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://x")

    from sec_review_framework.models.synthesized import synthesize_configs_from_snapshot

    snap = _fresh_snapshot({"openai/local-model"})
    # Pass a value that would fail normal validation (e.g. empty string for api_key_env
    # combined with auth="api_key") — model_construct must not raise.
    result = synthesize_configs_from_snapshot(
        "local_llm",
        snap,
        api_key_env="",
        api_base_env="LOCAL_LLM_BASE_URL",
    )
    assert len(result) == 1
    assert result[0].api_key_env == ""
