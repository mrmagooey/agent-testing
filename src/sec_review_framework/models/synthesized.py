"""synthesize_configs_from_snapshot — build ModelProviderConfig objects from a probe snapshot.

Synthesized configs let probe-discovered models flow into availability
computation and submit-time enrichment. AuthSpec dataclasses control how
auth fields are populated; the stable model id equals the full LiteLLM
routing string (raw_id).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from sec_review_framework.config import ModelProviderConfig
from sec_review_framework.models.catalog import ModelMetadata, ProviderSnapshot


# ---------------------------------------------------------------------------
# AuthSpec hierarchy
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthSpec:
    """Base class for authentication specifications."""


@dataclass(frozen=True)
class ApiKeyAuth(AuthSpec):
    """API-key authentication.  api_base_env is only needed for local endpoints."""
    api_key_env: str
    api_base_env: str | None = None
    api_base: str | None = None


@dataclass(frozen=True)
class AwsAuth(AuthSpec):
    """AWS credential-chain authentication (Bedrock).
    Region is read off each ModelMetadata entry individually.
    """
    region_from_metadata: bool = True


@dataclass(frozen=True)
class NoAuth(AuthSpec):
    """No authentication required (e.g. entirely open local endpoints)."""


# ---------------------------------------------------------------------------
# Display-name helpers
# ---------------------------------------------------------------------------

# Segments that are purely routing prefixes and carry no display value.
_STRIP_PREFIXES: tuple[str, ...] = (
    "bedrock/",
    "openrouter/",
    "anthropic/",
    "openai/",
    "gemini/",
    "mistral/",
    "cohere/",
    "vertex_ai/",
)


def deterministic_display_name(metadata: ModelMetadata) -> str:
    """Derive a human-readable display name from a ModelMetadata object.

    If ``metadata.display_name`` is set, return it unchanged.
    Otherwise synthesise one from ``raw_id`` (or fall back to ``id``):

    1. Strip known provider-routing prefixes (e.g. ``bedrock/``, ``openrouter/``).
    2. For Bedrock ARN-like strings (``anthropic.claude-3-5-…``), strip the
       vendor prefix (``anthropic.``, ``amazon.``, ``meta.``, ``mistral.``).
    3. Strip trailing ``-v1:0``, ``-20XXXXXX-v1:0`` version suffixes.
    4. Title-case each dash-separated word.
    """
    if metadata.display_name:
        return metadata.display_name

    raw = metadata.raw_id or metadata.id or ""

    # 1. Strip known routing prefixes.
    for prefix in _STRIP_PREFIXES:
        if raw.startswith(prefix):
            raw = raw[len(prefix):]
            break

    # For openrouter paths like "meta-llama/llama-3.1-8b-instruct",
    # keep only the part after the slash (the actual model name).
    if "/" in raw:
        raw = raw.rsplit("/", 1)[-1]

    # 2. Strip Bedrock vendor prefixes (e.g. "anthropic.", "amazon.", "meta.", "mistral.").
    raw = re.sub(r"^(anthropic|amazon|meta|mistral|ai21|cohere)\.", "", raw)

    # 3. Strip version/date suffixes common in Bedrock ARNs.
    #    Patterns: -20XXXXXX-v1:0, -v1:0, :0
    raw = re.sub(r"-\d{8}-v\d+:\d+$", "", raw)
    raw = re.sub(r"-v\d+:\d+$", "", raw)
    raw = re.sub(r":\d+$", "", raw)

    # 4. Title-case dash-separated words (preserves dots, colons, digits).
    parts = raw.split("-")
    titled = " ".join(p.capitalize() if p.isalpha() else p for p in parts if p)
    return titled or raw


# ---------------------------------------------------------------------------
# Main synthesis function
# ---------------------------------------------------------------------------


def synthesize_configs_from_snapshot(
    provider_key: str,
    snapshot: ProviderSnapshot,
    auth_spec: AuthSpec,
) -> list[ModelProviderConfig]:
    """Build a list of ModelProviderConfig from a ProviderSnapshot.

    Parameters
    ----------
    provider_key:
        Logical provider group (e.g. ``"openai"``, ``"bedrock"``).
    snapshot:
        Current snapshot from ProviderCatalog.
    auth_spec:
        Controls how ``api_key_env``, ``auth``, ``api_base``, and ``region``
        are set on each synthesized config.

    Returns
    -------
    Configs are sorted by ``raw_id`` for deterministic ordering.
    Returns an empty list when the snapshot is not fresh/stale.
    """
    if snapshot.probe_status not in ("fresh", "stale"):
        return []

    # For ApiKeyAuth with an api_base_env, resolve the base URL now.
    resolved_api_base: str | None = None
    if isinstance(auth_spec, ApiKeyAuth):
        if auth_spec.api_base is not None:
            resolved_api_base = auth_spec.api_base
        elif auth_spec.api_base_env is not None:
            import os
            resolved_api_base = os.environ.get(auth_spec.api_base_env)
            if not resolved_api_base:
                # No base URL available — cannot synthesize endpoint configs.
                return []

    configs: list[ModelProviderConfig] = []

    # Sort raw_ids for deterministic ordering.
    sorted_ids = sorted(snapshot.model_ids)

    for raw_id in sorted_ids:
        meta = snapshot.metadata.get(raw_id) or ModelMetadata(
            id=raw_id,
            raw_id=raw_id,
            provider_key=provider_key,
        )

        display_name = deterministic_display_name(meta)

        if isinstance(auth_spec, AwsAuth):
            region = meta.region
            if not region:
                # Skip entries that have no region — shouldn't happen for well-formed
                # Bedrock snapshots, but guard anyway.
                continue
            cfg = ModelProviderConfig(
                id=raw_id,
                model_name=raw_id,
                auth="aws",
                region=region,
                display_name=display_name,
            )
        elif isinstance(auth_spec, ApiKeyAuth):
            cfg = ModelProviderConfig(
                id=raw_id,
                model_name=raw_id,
                auth="api_key",
                api_key_env=auth_spec.api_key_env,
                api_base=resolved_api_base,
                display_name=display_name,
            )
        else:
            # NoAuth — local keyless endpoint.  The validator requires either
            # api_key_env or api_base; use model_construct to bypass it since
            # keyless endpoints are legitimate but carry no credential at all.
            cfg = ModelProviderConfig.model_construct(
                id=raw_id,
                model_name=raw_id,
                provider_class="LiteLLMProvider",
                auth="api_key",
                api_base=resolved_api_base,
                api_key_env=None,
                temperature=0.2,
                max_tokens=8192,
                display_name=display_name,
            )

        configs.append(cfg)

    return configs
