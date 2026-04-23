"""LiteLLM multi-provider probe: one call, multiple ProviderSnapshots.

A single ``litellm.get_valid_models`` call is partitioned by provider using
``provider_key_for_model`` (from ``sec_review_framework.models.providers``) to
produce one ``ProviderSnapshot`` per configured provider.  Providers whose
API-key env var is absent receive a ``disabled`` snapshot and the underlying
``get_valid_models`` call is skipped entirely if none are set.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone

import litellm

from sec_review_framework.models.catalog import (
    ModelMetadata,
    MultiProviderProbe,
    ProviderSnapshot,
)
from sec_review_framework.models.providers import (
    ENV_VAR_FOR_PROVIDER,
    provider_key_for_model,
)

# Providers this multi-probe owns (bedrock/openrouter/local_llm have their
# own dedicated probes).
_PROBED_KEYS: tuple[str, ...] = ("openai", "anthropic", "gemini", "mistral", "cohere")


class LiteLLMMultiProviderProbe:
    provider_keys: tuple[str, ...] = _PROBED_KEYS

    async def probe_many(self) -> dict[str, ProviderSnapshot]:
        enabled_keys = {
            pk for pk in self.provider_keys
            if (env := ENV_VAR_FOR_PROVIDER.get(pk)) and os.environ.get(env)
        }

        if not enabled_keys:
            return {
                pk: ProviderSnapshot(
                    probe_status="disabled",
                    last_error=f"{ENV_VAR_FOR_PROVIDER[pk]} not set",
                )
                for pk in self.provider_keys
            }

        raw: list[str] = await asyncio.to_thread(
            litellm.get_valid_models, check_provider_endpoint=True
        )

        # Partition raw ids by resolved provider group.
        partitioned: dict[str, set[str]] = {pk: set() for pk in self.provider_keys}
        for model_id in raw:
            group = provider_key_for_model(model_id)
            if group in partitioned:
                partitioned[group].add(model_id)

        now = datetime.now(timezone.utc)  # noqa: UP017
        result: dict[str, ProviderSnapshot] = {}
        for pk in self.provider_keys:
            env_var = ENV_VAR_FOR_PROVIDER[pk]
            if pk in enabled_keys:
                matched = frozenset(partitioned[pk])
                result[pk] = ProviderSnapshot(
                    probe_status="fresh",
                    model_ids=matched,
                    metadata={
                        m: ModelMetadata(
                            id=m,
                            raw_id=m,
                            provider_key=pk,
                        )
                        for m in matched
                    },
                    fetched_at=now,
                )
            else:
                result[pk] = ProviderSnapshot(
                    probe_status="disabled",
                    last_error=f"{env_var} not set",
                )
        return result


def build_litellm_probes() -> list[MultiProviderProbe]:
    """Return the consolidated multi-provider LiteLLM probe."""
    return [LiteLLMMultiProviderProbe()]
