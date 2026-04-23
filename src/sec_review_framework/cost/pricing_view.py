"""PricingView protocol and CatalogPricingView implementation.

Tier-2 pricing lookup: query the ProviderCatalog snapshots for dynamic
pricing data supplied by probes (e.g. OpenRouter).
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class PricingView(Protocol):
    """Minimal interface for dynamic pricing lookup.

    Returns (input_per_token, output_per_token) in USD, or None when
    no pricing data is available for the given model_id.
    """

    def get(self, model_id: str) -> tuple[float, float] | None: ...


class CatalogPricingView:
    """PricingView backed by a ProviderCatalog snapshot.

    Scans every ProviderSnapshot's metadata values looking for a
    ModelMetadata whose ``raw_id`` or ``id`` matches ``model_id``,
    then attempts to parse its ``pricing`` dict.

    OpenRouter ships pricing as::

        {"prompt": "0.0000015", "completion": "0.000002"}

    Values may be strings or floats (USD per token).  The keys
    ``"prompt"`` / ``"completion"`` are the canonical OpenRouter
    shape; ``"input"`` / ``"output"`` are also accepted in case other
    probes use a different convention.

    Returns None on any parse failure so the caller falls through to
    the next tier.
    """

    # Candidate key pairs: (input_key, output_key) tried in order.
    _KEY_PAIRS: tuple[tuple[str, str], ...] = (
        ("prompt", "completion"),
        ("input", "output"),
        ("input_cost_per_token", "output_cost_per_token"),
    )

    def __init__(self, catalog) -> None:
        # Accept ProviderCatalog; typed as Any to avoid a circular import.
        self._catalog = catalog

    def get(self, model_id: str) -> tuple[float, float] | None:
        """Return (input_per_token, output_per_token) in USD, or None."""
        snapshots = self._catalog.snapshot()
        for snap in snapshots.values():
            meta = snap.metadata.get(model_id)
            if meta is None:
                # Also check by raw_id in case the stored key differs.
                for m in snap.metadata.values():
                    if m.raw_id == model_id:
                        meta = m
                        break
            if meta is None:
                continue
            if meta.pricing is None:
                continue
            result = self._parse_pricing(meta.pricing)
            if result is not None:
                return result
        return None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _parse_pricing(self, pricing: dict) -> tuple[float, float] | None:
        """Parse a pricing dict, returning (input, output) per token or None."""
        for input_key, output_key in self._KEY_PAIRS:
            raw_in = pricing.get(input_key)
            raw_out = pricing.get(output_key)
            if raw_in is None or raw_out is None:
                continue
            parsed = self._to_float_pair(raw_in, raw_out)
            if parsed is not None:
                return parsed
        return None

    @staticmethod
    def _to_float_pair(raw_in, raw_out) -> tuple[float, float] | None:
        """Convert two raw values to floats, returning None if either fails."""
        try:
            fin = float(raw_in)
            fout = float(raw_out)
        except (TypeError, ValueError):
            return None
        return (fin, fout)
