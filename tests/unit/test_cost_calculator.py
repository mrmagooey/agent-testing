"""Unit tests for CostCalculator."""

import textwrap
from pathlib import Path

import litellm
import pytest

from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.cost.pricing_view import PricingView


@pytest.fixture
def calculator() -> CostCalculator:
    """CostCalculator pre-loaded with known pricing for two models."""
    pricing = {
        "gpt-4o": ModelPricing(input_per_million=5.00, output_per_million=15.00),
        "claude-opus-4": ModelPricing(input_per_million=15.00, output_per_million=75.00),
    }
    return CostCalculator(pricing=pricing)


# ---------------------------------------------------------------------------
# compute()
# ---------------------------------------------------------------------------


def test_compute_known_model_gpt4o(calculator):
    """1M input + 1M output for gpt-4o = $5 + $15 = $20."""
    cost = calculator.compute("gpt-4o", input_tokens=1_000_000, output_tokens=1_000_000)
    assert cost == pytest.approx(20.00)


def test_compute_known_model_claude(calculator):
    """500k input + 500k output for claude-opus-4 = $7.50 + $37.50 = $45."""
    cost = calculator.compute("claude-opus-4", input_tokens=500_000, output_tokens=500_000)
    assert cost == pytest.approx(45.00)


def test_compute_zero_tokens(calculator):
    """Zero tokens should give zero cost."""
    cost = calculator.compute("gpt-4o", input_tokens=0, output_tokens=0)
    assert cost == pytest.approx(0.0)


def test_compute_unknown_model_returns_zero(calculator):
    """Unknown model should return 0.0 without raising."""
    cost = calculator.compute("unknown-model-xyz", input_tokens=100_000, output_tokens=50_000)
    assert cost == pytest.approx(0.0)


def test_compute_partial_million(calculator):
    """100k input tokens + 0 output for gpt-4o = $0.50."""
    cost = calculator.compute("gpt-4o", input_tokens=100_000, output_tokens=0)
    assert cost == pytest.approx(0.50)


# ---------------------------------------------------------------------------
# cost_per_true_positive()
# ---------------------------------------------------------------------------


def test_cost_per_true_positive_normal(calculator):
    """$10 cost with 5 TPs = $2.00 per TP."""
    cpp = calculator.cost_per_true_positive(cost=10.0, true_positives=5)
    assert cpp == pytest.approx(2.0)


def test_cost_per_true_positive_zero_tps_returns_none(calculator):
    """Zero TPs should return None (infinite cost) rather than raising ZeroDivisionError."""
    cpp = calculator.cost_per_true_positive(cost=10.0, true_positives=0)
    assert cpp is None


def test_cost_per_true_positive_zero_cost(calculator):
    """Zero cost with TPs = $0.00 per TP."""
    cpp = calculator.cost_per_true_positive(cost=0.0, true_positives=3)
    assert cpp == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# price_per_token() — resolution order: YAML > litellm.model_cost > 0.0
# ---------------------------------------------------------------------------


def test_pricing_yaml_wins_over_litellm_model_cost(monkeypatch):
    """YAML entry takes priority; litellm.model_cost is not consulted when YAML has the model."""
    monkeypatch.setitem(
        litellm.model_cost,
        "gpt-4o",
        {"input_cost_per_token": 999e-06, "output_cost_per_token": 999e-06},
    )
    calc = CostCalculator(
        pricing={"gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0)}
    )
    result = calc.price_per_token("gpt-4o")
    assert result == pytest.approx((5e-06, 15e-06))


def test_falls_back_to_litellm_model_cost_when_yaml_missing(monkeypatch):
    """When a model is absent from YAML, price_per_token falls back to litellm.model_cost."""
    monkeypatch.setitem(
        litellm.model_cost,
        "some-novel-id",
        {"input_cost_per_token": 1.5e-06, "output_cost_per_token": 3e-06},
    )
    calc = CostCalculator(pricing={})
    result = calc.price_per_token("some-novel-id")
    assert result == pytest.approx((1.5e-06, 3e-06))


def test_falls_back_zero_when_both_missing(monkeypatch, caplog):
    """Neither YAML nor litellm.model_cost has the model — returns (0.0, 0.0) with a warning."""
    monkeypatch.delitem(litellm.model_cost, "no-such-model-xyz", raising=False)
    calc = CostCalculator(pricing={})
    import logging

    with caplog.at_level(logging.WARNING, logger="sec_review_framework.cost.calculator"):
        result = calc.price_per_token("no-such-model-xyz")
    assert result == (0.0, 0.0)
    assert any("no-such-model-xyz" in r.message for r in caplog.records)


def test_fallback_requires_both_input_and_output_cost_fields(monkeypatch):
    """litellm.model_cost entry missing output_cost_per_token should not return partial values."""
    monkeypatch.setitem(
        litellm.model_cost,
        "partial-model",
        {"input_cost_per_token": 1e-06},
    )
    calc = CostCalculator(pricing={})
    result = calc.price_per_token("partial-model")
    assert result == (0.0, 0.0)


def test_fallback_with_non_numeric_values_returns_zero(monkeypatch):
    """Non-numeric values in litellm.model_cost are treated as absent — returns (0.0, 0.0)."""
    monkeypatch.setitem(
        litellm.model_cost,
        "bad-model",
        {"input_cost_per_token": "string", "output_cost_per_token": None},
    )
    calc = CostCalculator(pricing={})
    result = calc.price_per_token("bad-model")
    assert result == (0.0, 0.0)


def test_per_million_to_per_token_conversion_yaml():
    """pricing.yaml stores per-million; price_per_token returns per-token (divide by 1e6)."""
    calc = CostCalculator(
        pricing={"m": ModelPricing(input_per_million=5.0, output_per_million=15.0)}
    )
    assert calc.price_per_token("m") == pytest.approx((5e-06, 15e-06))


def test_fallback_rejects_bool_pricing_values(monkeypatch):
    monkeypatch.setitem(
        litellm.model_cost,
        "bool-model",
        {"input_cost_per_token": True, "output_cost_per_token": False},
    )
    calc = CostCalculator(pricing={})
    assert calc.price_per_token("bool-model") == (0.0, 0.0)


# ---------------------------------------------------------------------------
# TestDynamicPricingTier — pricing_view (tier 2) integration
# ---------------------------------------------------------------------------


class _FixedPricingView:
    """Stub PricingView returning a fixed value for one model, None for others."""

    def __init__(self, model_id: str, pricing: tuple[float, float] | None) -> None:
        self._model_id = model_id
        self._pricing = pricing

    def get(self, model_id: str) -> tuple[float, float] | None:
        if model_id == self._model_id:
            return self._pricing
        return None


class TestDynamicPricingTier:
    """Tests for the three-tier pricing resolution introduced in Phase 3."""

    def test_pricing_view_wins_over_litellm(self, monkeypatch):
        """pricing_view (tier 2) beats litellm.model_cost (tier 3)."""
        monkeypatch.setitem(
            litellm.model_cost,
            "dynamic-model",
            {"input_cost_per_token": 999e-6, "output_cost_per_token": 999e-6},
        )
        view = _FixedPricingView("dynamic-model", (1.5e-6, 2.0e-6))
        calc = CostCalculator(pricing={}, pricing_view=view)

        result = calc.price_per_token("dynamic-model")
        assert result == pytest.approx((1.5e-6, 2.0e-6))

    def test_yaml_override_beats_pricing_view(self, monkeypatch):
        """pricing.yaml (tier 1) is still authoritative even when pricing_view has data."""
        view = _FixedPricingView("gpt-4o", (999e-6, 999e-6))
        calc = CostCalculator(
            pricing={"gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0)},
            pricing_view=view,
        )

        result = calc.price_per_token("gpt-4o")
        # Should come from YAML (5.0/1e6, 15.0/1e6), not the pricing view.
        assert result == pytest.approx((5e-6, 15e-6))

    def test_pricing_view_none_falls_through_to_litellm(self, monkeypatch):
        """When pricing_view returns None, tier 3 (litellm.model_cost) is used."""
        monkeypatch.setitem(
            litellm.model_cost,
            "litellm-only-model",
            {"input_cost_per_token": 1.0e-6, "output_cost_per_token": 2.0e-6},
        )
        view = _FixedPricingView("other-model", (999e-6, 999e-6))  # won't match
        calc = CostCalculator(pricing={}, pricing_view=view)

        result = calc.price_per_token("litellm-only-model")
        assert result == pytest.approx((1.0e-6, 2.0e-6))

    def test_no_pricing_view_preserves_existing_behaviour(self, monkeypatch):
        """Without pricing_view, behaviour is byte-identical to pre-Phase-3."""
        monkeypatch.setitem(
            litellm.model_cost,
            "classic-model",
            {"input_cost_per_token": 3.0e-6, "output_cost_per_token": 6.0e-6},
        )
        calc = CostCalculator(pricing={})  # no pricing_view

        result = calc.price_per_token("classic-model")
        assert result == pytest.approx((3.0e-6, 6.0e-6))

    def test_all_tiers_miss_returns_zero_with_warning(self, monkeypatch, caplog):
        """All three tiers miss → (0.0, 0.0) with a warning (existing behaviour)."""
        import logging

        monkeypatch.delitem(litellm.model_cost, "ghost-model", raising=False)
        view = _FixedPricingView("ghost-model", None)
        calc = CostCalculator(pricing={}, pricing_view=view)

        with caplog.at_level(logging.WARNING, logger="sec_review_framework.cost.calculator"):
            result = calc.price_per_token("ghost-model")

        assert result == (0.0, 0.0)
        assert any("ghost-model" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# CostCalculator.from_config()
# ---------------------------------------------------------------------------

_MINIMAL_PRICING_YAML = textwrap.dedent(
    """\
    models:
      test-model-a:
        input_per_million: 2.00
        output_per_million: 8.00
      test-model-b:
        input_per_million: 10.00
        output_per_million: 30.00
    """
)


class TestFromConfig:
    def test_valid_config_returns_cost_calculator(self, tmp_path):
        (tmp_path / "pricing.yaml").write_text(_MINIMAL_PRICING_YAML)
        calc = CostCalculator.from_config(config_dir=tmp_path)
        assert isinstance(calc, CostCalculator)

    def test_valid_config_produces_expected_pricing(self, tmp_path):
        (tmp_path / "pricing.yaml").write_text(_MINIMAL_PRICING_YAML)
        calc = CostCalculator.from_config(config_dir=tmp_path)
        assert set(calc.pricing.keys()) == {"test-model-a", "test-model-b"}
        assert calc.pricing["test-model-a"].input_per_million == pytest.approx(2.00)
        assert calc.pricing["test-model-a"].output_per_million == pytest.approx(8.00)
        assert calc.pricing["test-model-b"].input_per_million == pytest.approx(10.00)
        assert calc.pricing["test-model-b"].output_per_million == pytest.approx(30.00)

    def test_valid_config_computes_cost_correctly(self, tmp_path):
        (tmp_path / "pricing.yaml").write_text(_MINIMAL_PRICING_YAML)
        calc = CostCalculator.from_config(config_dir=tmp_path)
        cost = calc.compute("test-model-a", input_tokens=1_000_000, output_tokens=1_000_000)
        assert cost == pytest.approx(10.00)

    def test_missing_pricing_yaml_raises_file_not_found(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            CostCalculator.from_config(config_dir=tmp_path)

    def test_missing_config_dir_raises_file_not_found(self, tmp_path):
        absent = tmp_path / "no-such-dir"
        with pytest.raises(FileNotFoundError):
            CostCalculator.from_config(config_dir=absent)

    def test_malformed_yaml_raises_without_silent_fallback(self, tmp_path):
        (tmp_path / "pricing.yaml").write_text(": invalid: {{{ yaml")
        import yaml

        with pytest.raises(yaml.YAMLError):
            CostCalculator.from_config(config_dir=tmp_path)

    def test_valid_yaml_wrong_structure_raises_validation_error(self, tmp_path):
        (tmp_path / "pricing.yaml").write_text("totally_wrong_key: 42\n")
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CostCalculator.from_config(config_dir=tmp_path)

    def test_explicit_config_dir_overrides_default(self, tmp_path):
        (tmp_path / "pricing.yaml").write_text(_MINIMAL_PRICING_YAML)
        calc = CostCalculator.from_config(config_dir=tmp_path)
        assert "test-model-a" in calc.pricing

    def test_config_dir_none_uses_project_config(self):
        calc = CostCalculator.from_config(config_dir=None)
        assert isinstance(calc, CostCalculator)
        assert len(calc.pricing) > 0

    def test_config_dir_from_env_var_used_when_passed(self, tmp_path, monkeypatch):
        (tmp_path / "pricing.yaml").write_text(_MINIMAL_PRICING_YAML)
        monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
        import os

        config_dir = Path(os.environ["CONFIG_DIR"])
        calc = CostCalculator.from_config(config_dir=config_dir)
        assert "test-model-a" in calc.pricing

    def test_config_dir_env_var_unset_falls_back_to_default(self, monkeypatch):
        monkeypatch.delenv("CONFIG_DIR", raising=False)
        calc = CostCalculator.from_config(config_dir=None)
        assert isinstance(calc, CostCalculator)
        assert len(calc.pricing) > 0

    def test_empty_models_dict_is_accepted(self, tmp_path):
        (tmp_path / "pricing.yaml").write_text("models: {}\n")
        calc = CostCalculator.from_config(config_dir=tmp_path)
        assert calc.pricing == {}

    def test_pricing_view_not_set_by_from_config(self, tmp_path):
        (tmp_path / "pricing.yaml").write_text(_MINIMAL_PRICING_YAML)
        calc = CostCalculator.from_config(config_dir=tmp_path)
        assert calc._pricing_view is None
