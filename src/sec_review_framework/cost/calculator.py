"""Cost calculation — ModelPricing and CostCalculator."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import litellm
from pydantic import BaseModel

if TYPE_CHECKING:
    from sec_review_framework.cost.pricing_view import PricingView

logger = logging.getLogger(__name__)


def _from_model_cost(mid: str) -> tuple[float, float] | None:
    """Return (input_per_token, output_per_token) from litellm.model_cost, or None.

    litellm.model_cost values are already USD-per-token (e.g. 2.5e-06),
    so no conversion is needed — unlike pricing.yaml which uses per-million.
    Returns None when either field is absent or non-numeric (LiteLLM
    occasionally ships placeholder strings).
    """
    entry = litellm.model_cost.get(mid, {})
    input_cost = entry.get("input_cost_per_token")
    output_cost = entry.get("output_cost_per_token")
    if (
        not isinstance(input_cost, (int, float))
        or isinstance(input_cost, bool)
        or not isinstance(output_cost, (int, float))
        or isinstance(output_cost, bool)
    ):
        return None
    return (float(input_cost), float(output_cost))


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

    def __init__(
        self,
        pricing: dict[str, ModelPricing],
        pricing_view: PricingView | None = None,
    ) -> None:
        self.pricing = pricing
        self._pricing_view = pricing_view

    def price_per_token(self, model_id: str) -> tuple[float, float]:
        """Return (input_cost_per_token, output_cost_per_token) in USD.

        Resolution order:
        1. pricing.yaml entry (YAML is the authoritative override).
        2. Catalog snapshot metadata (dynamic pricing from probes, e.g. OpenRouter).
        3. litellm.model_cost entry (broadens coverage without YAML maintenance).
        4. Log warning and return (0.0, 0.0) — same behaviour as before fallback existed.
        """
        p = self.pricing.get(model_id)
        if p is not None:
            return (p.input_per_million / 1_000_000, p.output_per_million / 1_000_000)

        if self._pricing_view is not None:
            catalog_result = self._pricing_view.get(model_id)
            if catalog_result is not None:
                return catalog_result

        litellm_result = _from_model_cost(model_id)
        if litellm_result is not None:
            return litellm_result

        logger.warning("Unknown model %r — cost recorded as $0.00", model_id)
        return (0.0, 0.0)

    def compute(self, model_id: str, input_tokens: int, output_tokens: int) -> float:
        """
        Compute cost in USD for a single model call.

        Returns 0.0 for unknown models (logs a warning rather than crashing).
        """
        input_per_token, output_per_token = self.price_per_token(model_id)
        return input_tokens * input_per_token + output_tokens * output_per_token

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
    def from_config(cls, config_dir: Path | None = None) -> CostCalculator:
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
