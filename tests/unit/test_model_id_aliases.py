"""Tests for LEGACY_ID_ALIASES + rewrite_legacy_id."""

from __future__ import annotations

import logging

import pytest

from sec_review_framework.models.aliases import (
    LEGACY_ID_ALIASES,
    _reset_warnings_for_tests,
    rewrite_legacy_id,
    rewrite_legacy_ids,
)


@pytest.fixture(autouse=True)
def _reset_warnings():
    _reset_warnings_for_tests()
    yield
    _reset_warnings_for_tests()


class TestRewriteLegacyId:
    def test_known_bedrock_alias(self) -> None:
        assert (
            rewrite_legacy_id("bedrock-claude-3-5-sonnet")
            == "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"
        )

    def test_known_openrouter_alias(self) -> None:
        assert (
            rewrite_legacy_id("openrouter-llama-3.1-8b")
            == "openrouter/meta-llama/llama-3.1-8b-instruct"
        )

    def test_unknown_id_passthrough(self) -> None:
        assert rewrite_legacy_id("gpt-4o") == "gpt-4o"

    def test_already_canonical_passthrough(self) -> None:
        canonical = "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"
        assert rewrite_legacy_id(canonical) == canonical

    def test_warning_emitted_once_per_id(self, caplog) -> None:
        caplog.set_level(logging.WARNING, logger="sec_review_framework.models.aliases")

        # First call — warns.
        rewrite_legacy_id("bedrock-claude-3-5-sonnet")
        # Second call (same id) — silent.
        rewrite_legacy_id("bedrock-claude-3-5-sonnet")
        # Third call (different legacy id) — warns again.
        rewrite_legacy_id("openrouter-llama-3.1-8b")

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert len(warnings) == 2
        messages = [r.getMessage() for r in warnings]
        assert any("bedrock-claude-3-5-sonnet" in m for m in messages)
        assert any("openrouter-llama-3.1-8b" in m for m in messages)


class TestRewriteLegacyIdsList:
    def test_preserves_order_and_duplicates(self) -> None:
        out = rewrite_legacy_ids([
            "gpt-4o",
            "bedrock-claude-3-5-sonnet",
            "gpt-4o",
            "openrouter-llama-3.2-3b",
        ])
        assert out == [
            "gpt-4o",
            "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
            "gpt-4o",
            "openrouter/meta-llama/llama-3.2-3b-instruct",
        ]


class TestAliasTableShape:
    def test_all_keys_look_legacy(self) -> None:
        # Every legacy id is either "<provider>-<rest>" or has no "/" —
        # guard against accidentally adding a canonical id as a key.
        for k in LEGACY_ID_ALIASES:
            assert "/" not in k, f"legacy key looks canonical: {k!r}"

    def test_all_values_look_canonical(self) -> None:
        # Every new id is a full LiteLLM routing string (contains "/").
        for k, v in LEGACY_ID_ALIASES.items():
            assert "/" in v, f"alias value for {k!r} is not canonical: {v!r}"
