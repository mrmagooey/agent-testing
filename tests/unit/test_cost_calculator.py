"""Unit tests for CostCalculator."""

import litellm
import pytest

from sec_review_framework.cost.calculator import CostCalculator, ModelPricing


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
