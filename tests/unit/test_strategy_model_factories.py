"""Unit tests for StrategyFactory and ModelProviderFactory — negative/edge cases."""

from __future__ import annotations

import pytest

from sec_review_framework.worker import ModelProviderFactory, StrategyFactory
from sec_review_framework.data.experiment import StrategyName
from sec_review_framework.strategies.single_agent import SingleAgentStrategy
from sec_review_framework.strategies.per_file import PerFileStrategy
from sec_review_framework.strategies.per_vuln_class import PerVulnClassStrategy
from sec_review_framework.strategies.sast_first import SASTFirstStrategy
from sec_review_framework.strategies.diff_review import DiffReviewStrategy


# ---------------------------------------------------------------------------
# StrategyFactory
# ---------------------------------------------------------------------------


class TestStrategyFactory:
    def test_create_single_agent_strategy(self):
        factory = StrategyFactory()
        strategy = factory.create(StrategyName.SINGLE_AGENT)
        assert isinstance(strategy, SingleAgentStrategy)

    def test_create_per_file_strategy(self):
        factory = StrategyFactory()
        strategy = factory.create(StrategyName.PER_FILE)
        assert isinstance(strategy, PerFileStrategy)

    def test_create_per_vuln_class_strategy(self):
        factory = StrategyFactory()
        strategy = factory.create(StrategyName.PER_VULN_CLASS)
        assert isinstance(strategy, PerVulnClassStrategy)

    def test_create_sast_first_strategy(self):
        factory = StrategyFactory()
        strategy = factory.create(StrategyName.SAST_FIRST)
        assert isinstance(strategy, SASTFirstStrategy)

    def test_create_diff_review_strategy(self):
        factory = StrategyFactory()
        strategy = factory.create(StrategyName.DIFF_REVIEW)
        assert isinstance(strategy, DiffReviewStrategy)

    def test_all_strategy_names_are_handled(self):
        """Every value in StrategyName enum must produce a strategy without error."""
        factory = StrategyFactory()
        for name in StrategyName:
            strategy = factory.create(name)
            assert strategy is not None

    def test_unknown_strategy_raises_value_error(self):
        """Passing an unknown/invalid strategy name must raise ValueError."""
        factory = StrategyFactory()
        with pytest.raises((ValueError, AttributeError, KeyError)):
            factory.create("completely_unknown_strategy")  # type: ignore[arg-type]

    def test_none_strategy_raises(self):
        factory = StrategyFactory()
        with pytest.raises((ValueError, AttributeError, KeyError, TypeError)):
            factory.create(None)  # type: ignore[arg-type]

    def test_each_call_returns_new_instance(self):
        """Factory should return a fresh instance on each call (no singleton leak)."""
        factory = StrategyFactory()
        s1 = factory.create(StrategyName.SINGLE_AGENT)
        s2 = factory.create(StrategyName.SINGLE_AGENT)
        assert s1 is not s2

    def test_strategy_has_name_method(self):
        """All created strategies should implement the name() method."""
        factory = StrategyFactory()
        for name in StrategyName:
            strategy = factory.create(name)
            assert hasattr(strategy, "name")
            assert callable(strategy.name)
            assert isinstance(strategy.name(), str)
            assert len(strategy.name()) > 0


# ---------------------------------------------------------------------------
# ModelProviderFactory
# ---------------------------------------------------------------------------


class TestModelProviderFactory:
    def test_create_returns_litellm_provider(self):
        from sec_review_framework.models.litellm_provider import LiteLLMProvider

        factory = ModelProviderFactory()
        provider = factory.create("gpt-4o", {})
        assert isinstance(provider, LiteLLMProvider)

    def test_model_id_matches_provided_name(self):
        factory = ModelProviderFactory()
        provider = factory.create("anthropic/claude-3-5-sonnet", {})
        assert provider.model_id() == "anthropic/claude-3-5-sonnet"

    def test_create_with_api_key_in_config(self):
        """api_key in model_config should be accepted without error."""
        factory = ModelProviderFactory()
        provider = factory.create("gpt-4o", {"api_key": "sk-fake-key"})
        assert provider is not None

    def test_create_with_api_base_in_config(self):
        factory = ModelProviderFactory()
        provider = factory.create("gpt-4o", {"api_base": "https://proxy.example.com"})
        assert provider is not None

    def test_create_empty_model_id_accepted(self):
        """Even an empty model ID should not raise at construction time."""
        factory = ModelProviderFactory()
        provider = factory.create("", {})
        assert provider.model_id() == ""

    def test_create_with_malformed_config_raises(self):
        """Unexpected config keys should raise a TypeError from LiteLLMProvider.__init__."""
        factory = ModelProviderFactory()
        with pytest.raises(TypeError):
            factory.create("gpt-4o", {"unknown_kwarg_that_does_not_exist": True})
