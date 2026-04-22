"""OpenRouter probe.

Calls ``https://openrouter.ai/api/v1/models`` with the bearer token from
``OPENROUTER_API_KEY``.  Populates ``ModelMetadata.context_length`` and
``ModelMetadata.pricing``.  Returns ``disabled`` when the env var is absent.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

import httpx

from sec_review_framework.models.catalog import ModelMetadata, ProviderSnapshot

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


class OpenRouterProbe:
    provider_key = "openrouter"

    async def probe(self) -> ProviderSnapshot:
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            return ProviderSnapshot(
                probe_status="disabled",
                last_error="OPENROUTER_API_KEY not set",
            )

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.get(
                OPENROUTER_MODELS_URL,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()

        models_raw: list[dict] = data.get("data", [])
        model_ids: frozenset[str] = frozenset(m["id"] for m in models_raw if "id" in m)
        metadata: dict[str, ModelMetadata] = {}
        for m in models_raw:
            mid = m.get("id")
            if not mid:
                continue
            pricing_raw = m.get("pricing")
            metadata[mid] = ModelMetadata(
                id=mid,
                display_name=m.get("name"),
                context_length=m.get("context_length"),
                pricing=pricing_raw,
            )

        return ProviderSnapshot(
            probe_status="fresh",
            model_ids=model_ids,
            metadata=metadata,
            fetched_at=datetime.now(timezone.utc),
        )
