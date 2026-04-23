"""Model availability computation.

Pure helper that combines the probe-driven registry (list[ModelProviderConfig]) with
ProviderCatalog snapshots to produce a grouped-by-provider availability list.

The result is used by both GET /api/models and the submit-time validator.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Mapping

from sec_review_framework.config import ModelProviderConfig
from sec_review_framework.models.catalog import ProviderSnapshot
from sec_review_framework.models.providers import ENV_VAR_FOR_PROVIDER
from sec_review_framework.models.synthesized import (
    ApiKeyAuth,
    AuthSpec,
    AwsAuth,
    NoAuth,
    synthesize_configs_from_snapshot,
)

ModelStatus = str  # "available" | "key_missing" | "not_listed" | "probe_failed"
ProbeStatus = str  # "fresh" | "stale" | "failed" | "disabled"


@dataclass
class ModelEntry:
    id: str
    display_name: str | None
    status: ModelStatus
    context_length: int | None = None
    region: str | None = None


@dataclass
class ProviderGroup:
    provider: str
    probe_status: ProbeStatus
    models: list[ModelEntry]


def _auth_spec_for_provider(provider_key: str) -> AuthSpec:
    """Return the appropriate AuthSpec for a given provider key.

    Uses ENV_VAR_FOR_PROVIDER as the canonical source of truth for env-var
    names.  Special-cases bedrock (AwsAuth) and local_llm (ApiKeyAuth with
    api_base_env).
    """
    if provider_key == "bedrock":
        return AwsAuth()
    if provider_key == "local_llm":
        return ApiKeyAuth(
            api_key_env="LOCAL_LLM_API_KEY",
            api_base_env="LOCAL_LLM_BASE_URL",
        )
    env_var = ENV_VAR_FOR_PROVIDER.get(provider_key)
    if env_var is not None:
        return ApiKeyAuth(api_key_env=env_var)
    # Unknown provider — no auth, best-effort.
    return NoAuth()


def build_effective_registry(
    snapshots: dict[str, ProviderSnapshot],
) -> list[ModelProviderConfig]:
    """Build the full model registry from probe snapshots.

    Loops every snapshot, selects the appropriate AuthSpec, synthesizes
    ModelProviderConfig objects, and concatenates them preserving insertion
    order.

    For local_llm, the base URL must be set in the environment
    (LOCAL_LLM_BASE_URL) for any configs to be emitted.

    Parameters
    ----------
    snapshots:
        Provider snapshots from ProviderCatalog.snapshot().

    Returns
    -------
    Flat ordered list of ModelProviderConfig covering all reachable models.
    """
    registry: list[ModelProviderConfig] = []
    for provider_key, snapshot in snapshots.items():
        auth_spec = _auth_spec_for_provider(provider_key)
        configs = synthesize_configs_from_snapshot(provider_key, snapshot, auth_spec)
        registry.extend(configs)
    return registry


def _derive_provider_key(cfg: ModelProviderConfig) -> str:
    """Derive a logical provider key from a ModelProviderConfig entry.

    - auth=aws  →  "bedrock"
    - auth=api_key with api_key_env="OPENAI_API_KEY"  →  "openai"
      (strip trailing _API_KEY and lowercase)
    - Fallback: first segment of model id
    """
    if cfg.auth == "aws":
        return "bedrock"
    if cfg.api_key_env:
        # Strip trailing _API_KEY suffix (case-insensitive) then lowercase.
        key = re.sub(r"_API_KEY$", "", cfg.api_key_env, flags=re.IGNORECASE)
        return key.lower()
    # No api_key_env (local keyless endpoint) — derive from model id prefix or api_base.
    # For synthesized local_llm entries: id starts with "openai/" or similar.
    model_id = cfg.id or ""
    if "/" in model_id:
        return model_id.split("/")[0].lower()
    return model_id.split("-")[0].lower()


def _compute_model_status(
    cfg: ModelProviderConfig,
    snapshot: ProviderSnapshot | None,
    env: Mapping[str, str],
) -> ModelStatus:
    """Compute availability status for a single registry entry."""
    if cfg.auth == "aws":
        if snapshot is None or snapshot.probe_status == "disabled":
            return "key_missing"
        # Bedrock probe prefixes model ids with "bedrock/"
        # cfg.model_name is already "bedrock/<raw_id>"
        if cfg.model_name in snapshot.model_ids:
            return "available"
        if snapshot.probe_status == "failed":
            return "probe_failed"
        return "not_listed"

    # auth == "api_key"
    # A probed local endpoint that returned the model id is self-evidencing —
    # no key check needed even if api_key_env is set.
    if cfg.api_base and snapshot is not None and snapshot.probe_status in ("fresh", "stale"):
        if cfg.model_name in snapshot.model_ids:
            return "available"

    # Validator permits api_key_env=None when api_base is set (keyless local
    # endpoints). Decide status purely on snapshot state in that case — falling
    # through to env.get(None) would raise TypeError on real os.environ.
    if cfg.api_key_env is None:
        if snapshot is None or snapshot.probe_status == "disabled":
            return "available"
        if snapshot.probe_status == "failed":
            return "probe_failed"
        return "not_listed"

    api_key_env = cfg.api_key_env
    if not env.get(api_key_env):
        return "key_missing"
    if snapshot is None or snapshot.probe_status == "disabled":
        # Probing is globally off for this provider; trust the key being present.
        return "available"
    if cfg.model_name in snapshot.model_ids:
        return "available"
    if snapshot.probe_status == "failed":
        return "probe_failed"
    return "not_listed"


def _enrich_entry(
    cfg: ModelProviderConfig,
    snapshot: ProviderSnapshot | None,
    status: ModelStatus,
) -> ModelEntry:
    """Build a ModelEntry, enriching display_name/context_length/region from snapshot."""
    display_name = cfg.display_name
    context_length: int | None = None
    region: str | None = cfg.region if cfg.auth == "aws" else None

    if snapshot is not None:
        snap_meta = snapshot.metadata.get(cfg.model_name)
        if snap_meta is not None:
            # Snapshot wins when both have a value.
            if snap_meta.display_name:
                display_name = snap_meta.display_name
            if snap_meta.context_length is not None:
                context_length = snap_meta.context_length
            if snap_meta.region is not None:
                region = snap_meta.region

    return ModelEntry(
        id=cfg.id,
        display_name=display_name,
        status=status,
        context_length=context_length,
        region=region,
    )


def compute_availability(
    registry: list[ModelProviderConfig],
    snapshots: dict[str, ProviderSnapshot],
    env: Mapping[str, str],
) -> list[ProviderGroup]:
    """Compute availability for every registry entry, grouped by provider.

    Parameters
    ----------
    registry:
        Ordered list of ModelProviderConfig entries.
    snapshots:
        Provider snapshots from ProviderCatalog.snapshot().
    env:
        Environment variable mapping (usually os.environ).

    Returns
    -------
    list[ProviderGroup] in insertion order of first appearance of each provider.
    """
    # Preserve insertion order of providers.
    provider_order: list[str] = []
    groups: dict[str, list[ModelEntry]] = {}

    for cfg in registry:
        provider_key = _derive_provider_key(cfg)
        if provider_key not in groups:
            provider_order.append(provider_key)
            groups[provider_key] = []

        snapshot = snapshots.get(provider_key)
        status = _compute_model_status(cfg, snapshot, env)
        entry = _enrich_entry(cfg, snapshot, status)
        groups[provider_key].append(entry)

    result: list[ProviderGroup] = []
    for provider_key in provider_order:
        # Determine probe_status from snapshot; default "disabled" if none.
        snapshot = snapshots.get(provider_key)
        probe_status: ProbeStatus = snapshot.probe_status if snapshot else "disabled"
        result.append(ProviderGroup(
            provider=provider_key,
            probe_status=probe_status,
            models=groups[provider_key],
        ))

    return result


def flat_model_list(groups: list[ProviderGroup]) -> list[dict]:
    """Return the legacy flat list shape: [{id, display_name}, ...].

    Used by the ?format=flat / Accept: application/vnd.sec-review.v0+json
    backward-compat path.
    """
    result: list[dict] = []
    for group in groups:
        for model in group.models:
            entry: dict = {"id": model.id}
            if model.display_name is not None:
                entry["display_name"] = model.display_name
            result.append(entry)
    return result


def groups_to_dicts(groups: list[ProviderGroup]) -> list[dict]:
    """Serialise ProviderGroup list to JSON-safe dicts."""
    out: list[dict] = []
    for g in groups:
        models_out: list[dict] = []
        for m in g.models:
            entry: dict = {
                "id": m.id,
                "display_name": m.display_name,
                "status": m.status,
            }
            if m.context_length is not None:
                entry["context_length"] = m.context_length
            if m.region is not None:
                entry["region"] = m.region
            models_out.append(entry)
        out.append({
            "provider": g.provider,
            "probe_status": g.probe_status,
            "models": models_out,
        })
    return out


def build_id_to_status(groups: list[ProviderGroup]) -> dict[str, ModelStatus]:
    """Return a flat mapping of model_id → status for quick lookup."""
    return {m.id: m.status for g in groups for m in g.models}
