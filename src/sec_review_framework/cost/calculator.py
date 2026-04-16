"""Cost calculation — ModelPricing and CostCalculator."""

import logging
from pathlib import Path

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ModelPricing(BaseModel):
    """Per-model token pricing in USD per million tokens."""

    input_per_million: float
    output_per_million: float


class CostCalculator:
    """
    Computes token-based costs for experiment runs.

    Pricing is loaded from config/pricing.yaml via PricingConfig, or supplied
    directly as a dict[str, ModelPricing] for testing.
    """

    def __init__(self, pricing: dict[str, ModelPricing]) -> None:
        self.pricing = pricing

    def compute(self, model_id: str, input_tokens: int, output_tokens: int) -> float:
        """
        Compute cost in USD for a single model call.

        Returns 0.0 for unknown models (logs a warning rather than crashing).
        """
        p = self.pricing.get(model_id)
        if p is None:
            logger.warning("Unknown model %r — cost recorded as $0.00", model_id)
            return 0.0
        return (
            input_tokens / 1_000_000 * p.input_per_million
            + output_tokens / 1_000_000 * p.output_per_million
        )

    def cost_per_true_positive(self, cost: float, true_positives: int) -> float | None:
        """
        Return cost-per-TP as the key efficiency metric.

        Returns None when there are zero true positives to avoid division by
        zero — callers should treat None as "infinite cost".
        """
        if true_positives == 0:
            return None
        return cost / true_positives

    @classmethod
    def from_config(cls, config_dir: Path | None = None) -> "CostCalculator":
        """
        Load pricing from config/pricing.yaml via PricingConfig and return a
        CostCalculator instance.

        Parameters
        ----------
        config_dir:
            Directory containing pricing.yaml. Defaults to the project-level
            config/ directory (two levels above the package root).
        """
        from sec_review_framework.config import PricingConfig

        if config_dir is None:
            # Resolve relative to this file: src/sec_review_framework/cost/calculator.py
            # -> project root is four levels up -> config/pricing.yaml
            config_dir = Path(__file__).parent.parent.parent.parent / "config"

        pricing_path = config_dir / "pricing.yaml"
        pricing_config = PricingConfig.from_yaml(pricing_path)

        pricing: dict[str, ModelPricing] = {
            model_id: ModelPricing(
                input_per_million=model_cfg.input_per_million,
                output_per_million=model_cfg.output_per_million,
            )
            for model_id, model_cfg in pricing_config.models.items()
        }
        return cls(pricing=pricing)
