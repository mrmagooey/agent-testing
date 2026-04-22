"""LiteLLM-backed probes for OpenAI, Anthropic, Gemini, Mistral, Cohere.

Each provider is instantiated as a separate probe so the catalog has
per-provider snapshots.  A probe is ``disabled`` when its required API-key
env var is absent; otherwise it calls ``litellm.get_valid_models`` to
enumerate reachable models and filters to the provider prefix.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

import litellm

from sec_review_framework.models.catalog import ModelMetadata, ProviderSnapshot

logger = logging.getLogger(__name__)


# Maps catalog provider_key → (env-var name, litellm model-name prefix).
# The prefix is used to filter the full list returned by get_valid_models.
_PROVIDER_SPEC: dict[str, tuple[str, str]] = {
    "openai": ("OPENAI_API_KEY", "gpt"),
    "anthropic": ("ANTHROPIC_API_KEY", "claude"),
    "gemini": ("GEMINI_API_KEY", "gemini"),
    "mistral": ("MISTRAL_API_KEY", "mistral"),
    "cohere": ("COHERE_API_KEY", "command"),
}


class LiteLLMProbe:
    """Probe one LiteLLM-supported provider."""

    def __init__(self, provider_key: str, api_key_env: str, model_prefix: str) -> None:
        self.provider_key = provider_key
        self._api_key_env = api_key_env
        self._model_prefix = model_prefix

    async def probe(self) -> ProviderSnapshot:
        if not os.environ.get(self._api_key_env):
            return ProviderSnapshot(
                probe_status="disabled",
                last_error=f"{self._api_key_env} not set",
            )

        try:
            all_models: list[str] = await asyncio.to_thread(
                litellm.get_valid_models, check_provider_endpoint=True
            )
        except Exception as exc:
            raise RuntimeError(
                f"litellm.get_valid_models failed for {self.provider_key}: {exc}"
            ) from exc

        matched = frozenset(
            m for m in all_models if m.startswith(self._model_prefix)
        )
        metadata = {
            m: ModelMetadata(id=m)
            for m in matched
        }
        return ProviderSnapshot(
            probe_status="fresh",
            model_ids=matched,
            metadata=metadata,
            fetched_at=datetime.now(timezone.utc),
        )


def build_litellm_probes() -> list[LiteLLMProbe]:
    """Return one probe per configured LiteLLM provider."""
    return [
        LiteLLMProbe(key, env_var, prefix)
        for key, (env_var, prefix) in _PROVIDER_SPEC.items()
    ]
