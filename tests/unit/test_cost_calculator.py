"""Unit tests for CostCalculator."""

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
