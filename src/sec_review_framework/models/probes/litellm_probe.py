"""LiteLLM multi-provider probe: one call, multiple ProviderSnapshots.

A single ``litellm.get_valid_models`` call is partitioned by prefix to produce
one ``ProviderSnapshot`` per configured provider.  Providers whose API-key env
var is absent receive a ``disabled`` snapshot without triggering the call at all
if none are set.
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

# Maps catalog provider_key → (env-var name, litellm model-name prefix).
_PROVIDER_PREFIXES: dict[str, tuple[str, str]] = {
    "openai":    ("OPENAI_API_KEY",    "gpt"),
    "anthropic": ("ANTHROPIC_API_KEY", "claude"),
    "gemini":    ("GEMINI_API_KEY",    "gemini"),
    "mistral":   ("MISTRAL_API_KEY",   "mistral"),
    "cohere":    ("COHERE_API_KEY",    "command"),
}


class LiteLLMMultiProviderProbe:
    provider_keys: tuple[str, ...] = tuple(_PROVIDER_PREFIXES.keys())

    async def probe_many(self) -> dict[str, ProviderSnapshot]:
        enabled_keys = {
            pk
            for pk, (env_var, _) in _PROVIDER_PREFIXES.items()
            if os.environ.get(env_var)
        }

        if not enabled_keys:
            return {
                pk: ProviderSnapshot(
                    probe_status="disabled",
                    last_error=f"{_PROVIDER_PREFIXES[pk][0]} not set",
                )
                for pk in self.provider_keys
            }

        raw: list[str] = await asyncio.to_thread(
            litellm.get_valid_models, check_provider_endpoint=True
        )

        now = datetime.now(timezone.utc)  # noqa: UP017
        result: dict[str, ProviderSnapshot] = {}
        for pk in self.provider_keys:
            env_var, prefix = _PROVIDER_PREFIXES[pk]
            if pk in enabled_keys:
                matched = frozenset(m for m in raw if m.startswith(prefix))
                result[pk] = ProviderSnapshot(
                    probe_status="fresh",
                    model_ids=matched,
                    metadata={m: ModelMetadata(id=m) for m in matched},
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
