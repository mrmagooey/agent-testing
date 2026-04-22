"""synthesize_configs_from_snapshot — build ModelProviderConfig objects from a probe snapshot.

Synthesized configs let probe-discovered models flow into availability
computation and submit-time enrichment without manual YAML entries.
"""

from __future__ import annotations

import os

from sec_review_framework.config import ModelProviderConfig
from sec_review_framework.models.catalog import ProviderSnapshot

_OPENAI_PREFIX = "openai/"


def synthesize_configs_from_snapshot(
    provider_key: str,
    snapshot: ProviderSnapshot,
    *,
    api_key_env: str,
    api_base_env: str,
    id_template: str = "{provider_key}-{raw_id}",
) -> list[ModelProviderConfig]:
    if snapshot.probe_status not in ("fresh", "stale"):
        return []

    base_url = os.environ.get(api_base_env)
    if not base_url:
        return []

    configs: list[ModelProviderConfig] = []
    for prefixed_id in snapshot.model_ids:
        raw_id = prefixed_id.removeprefix(_OPENAI_PREFIX)
        cfg = ModelProviderConfig.model_construct(
            id=id_template.format(provider_key=provider_key, raw_id=raw_id),
            provider_class="LiteLLMProvider",
            model_name=prefixed_id,
            temperature=0.2,
            max_tokens=8192,
            api_key_env=api_key_env,
            api_base=base_url,
            auth="api_key",
            display_name=raw_id,
        )
        configs.append(cfg)

    return configs
