"""Tests for sec_review_framework.models.providers."""

from __future__ import annotations

import pytest

from sec_review_framework.models.providers import (
    ENV_VAR_FOR_PROVIDER,
    provider_key_for_model,
)


class TestProviderKeyForModel:
    """provider_key_for_model returns the logical group key for any routing string."""

    @pytest.mark.parametrize("raw_id,expected", [
        # LiteLLM-prefixed routing strings
        ("bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0", "bedrock"),
        ("openrouter/meta-llama/llama-3.1-8b-instruct", "openrouter"),
        ("anthropic/claude-3-5-sonnet-latest", "anthropic"),
        ("gpt-4o", "openai"),
        ("gpt-4.1", "openai"),
        ("o1", "openai"),
        ("o3-mini", "openai"),
        ("o4-mini", "openai"),
        # Bare-name (no prefix) — must still classify correctly
        ("claude-3-5-sonnet-latest", "anthropic"),
        ("claude-opus-4-7", "anthropic"),
        ("gemini-2.0-flash", "gemini"),      # LiteLLM says vertex_ai; we remap
        ("gemini-1.5-pro", "gemini"),
        ("mistral-large-latest", "mistral"),
        ("codestral-latest", "mistral"),
        ("command-r-plus", "cohere"),        # LiteLLM says cohere_chat; we remap
        ("command-r", "cohere"),
    ])
    def test_known_ids(self, raw_id: str, expected: str) -> None:
        assert provider_key_for_model(raw_id) == expected

    def test_empty_string(self) -> None:
        assert provider_key_for_model("") == "unknown"

    def test_totally_unknown_id(self) -> None:
        # No crash — returns some non-empty string.
        result = provider_key_for_model("xyzzy-mystery-model")
        assert isinstance(result, str) and result


class TestEnvVarForProvider:
    @pytest.mark.parametrize("provider,expected", [
        ("openai", "OPENAI_API_KEY"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("gemini", "GEMINI_API_KEY"),
        ("mistral", "MISTRAL_API_KEY"),
        ("cohere", "COHERE_API_KEY"),
        ("openrouter", "OPENROUTER_API_KEY"),
        ("local_llm", "LOCAL_LLM_BASE_URL"),
    ])
    def test_env_vars(self, provider: str, expected: str) -> None:
        assert ENV_VAR_FOR_PROVIDER[provider] == expected

    def test_bedrock_has_no_env_var(self) -> None:
        """Bedrock uses the boto3 credential chain — no single env var."""
        assert ENV_VAR_FOR_PROVIDER["bedrock"] is None
