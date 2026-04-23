"""LiteLLMEndpointProbe — thin wrapper around litellm.get_valid_models.

Discovers models served by any OpenAI-compatible endpoint (vLLM, llama.cpp,
LM Studio, LocalAI, Ollama) without implementing its own HTTP client or schema
parser.  LiteLLM already handles the /v1/models or /api/tags call depending on
``custom_llm_provider``.

The probe is ``disabled`` when the base-URL env var is absent — not when the
API-key var is absent — because many local endpoints don't require auth.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

import litellm

from sec_review_framework.models.catalog import ModelMetadata, ProviderSnapshot

logger = logging.getLogger(__name__)


class LiteLLMEndpointProbe:
    def __init__(
        self,
        *,
        provider_key: str,
        api_base_env: str,
        api_key_env: str,
        litellm_provider: str = "openai",
        model_name_prefix: str = "openai/",
    ) -> None:
        self.provider_key = provider_key
        self.api_base_env = api_base_env
        self.api_key_env = api_key_env
        self.litellm_provider = litellm_provider
        self.model_name_prefix = model_name_prefix

    async def probe(self) -> ProviderSnapshot:
        base_url = os.environ.get(self.api_base_env)
        if not base_url:
            return ProviderSnapshot(
                probe_status="disabled",
                last_error=f"{self.api_base_env} not set",
            )

        api_key = os.environ.get(self.api_key_env, "")

        raw_ids: list[str] = await asyncio.to_thread(
            litellm.get_valid_models,
            check_provider_endpoint=True,
            custom_llm_provider=self.litellm_provider,
            api_base=base_url,
            api_key=(api_key or None),
        )

        model_ids: frozenset[str] = frozenset(
            f"{self.model_name_prefix}{mid}" for mid in raw_ids
        )

        metadata: dict[str, ModelMetadata] = {}
        for mid in model_ids:
            raw_id = mid.removeprefix(self.model_name_prefix)
            cost_entry = litellm.model_cost.get(mid)
            context_length: int | None = None
            if isinstance(cost_entry, dict):
                context_length = cost_entry.get("max_input_tokens")
            metadata[mid] = ModelMetadata(
                id=mid,
                display_name=raw_id,
                context_length=context_length,
                provider_key=self.provider_key,
                raw_id=mid,
            )

        return ProviderSnapshot(
            probe_status="fresh",
            model_ids=model_ids,
            metadata=metadata,
            fetched_at=datetime.now(timezone.utc),
        )
