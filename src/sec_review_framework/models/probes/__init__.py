"""Provider probe package.

``build_probes()`` reads environment variables and returns the full set of
configured probes ready to be handed to ``ProviderCatalog``.
"""

from __future__ import annotations

from sec_review_framework.models.catalog import MultiProviderProbe, ProviderProbe
from sec_review_framework.models.probes.bedrock_probe import BedrockProbe
from sec_review_framework.models.probes.litellm_endpoint_probe import LiteLLMEndpointProbe
from sec_review_framework.models.probes.litellm_probe import build_litellm_probes
from sec_review_framework.models.probes.openrouter_probe import OpenRouterProbe


def build_probes() -> list[ProviderProbe | MultiProviderProbe]:
    """Return the full configured probe list.

    Reads environment variables but does NOT make any network calls.
    Disabled probes are still included — the catalog uses them to populate
    ``disabled`` snapshots for every known provider.
    """
    probes: list[ProviderProbe | MultiProviderProbe] = []
    probes.extend(build_litellm_probes())
    probes.append(OpenRouterProbe())
    probes.append(BedrockProbe())
    probes.append(
        LiteLLMEndpointProbe(
            provider_key="local_llm",
            api_base_env="LOCAL_LLM_BASE_URL",
            api_key_env="LOCAL_LLM_API_KEY",
            litellm_provider="openai",
        )
    )
    return probes


__all__ = [
    "build_probes",
    "BedrockProbe",
    "LiteLLMEndpointProbe",
    "MultiProviderProbe",
    "OpenRouterProbe",
]
