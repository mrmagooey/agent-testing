"""Model availability computation.

Pure helper that combines the probe-driven registry (list[ModelProviderConfig]) with
ProviderCatalog snapshots to produce a grouped-by-provider availability list.

The result is used by both GET /api/models and the submit-time validator.
"""

from __future__ import annotations

import hashlib
import re
import threading
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

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
    fetched_at: datetime | None = None
    last_error: str | None = None


# ---------------------------------------------------------------------------
# Memoisation helpers
# ---------------------------------------------------------------------------

_CACHE_MAXSIZE = 4
_cache_lock = threading.Lock()
# OrderedDict used as an LRU: key → result.
_lru_cache: OrderedDict[str, list[ProviderGroup]] = OrderedDict()


def _env_subset_hash(env: Mapping[str, str]) -> str:
    """Hash only the env vars relevant to provider authentication.

    Uses ENV_VAR_FOR_PROVIDER values so the hash changes when any provider
    key is added, removed, or rotated between calls.
    """
    relevant_vars = sorted(
        v for v in ENV_VAR_FOR_PROVIDER.values() if v is not None
    )
    parts = "|".join(f"{k}={env.get(k, '')}" for k in relevant_vars)
    return hashlib.sha256(parts.encode()).hexdigest()[:16]


def _snapshots_hash(snapshots: dict[str, ProviderSnapshot]) -> str:
    """Stable hash of the snapshots dict contents.

    ProviderSnapshot is a mutable dataclass and not hashable, so we
    derive a digest from its probe-relevant fields including metadata.
    """
    parts: list[str] = []
    for key in sorted(snapshots):
        s = snapshots[key]
        model_ids_str = ",".join(sorted(s.model_ids))
        fetched = s.fetched_at.isoformat() if s.fetched_at else ""
        # Include a hash of metadata keys/display_name/context_length so metadata
        # changes also invalidate the cache.
        meta_parts = sorted(
            f"{mid}:{m.display_name or ''}:{m.context_length or ''}:{m.region or ''}"
            for mid, m in s.metadata.items()
        )
        meta_str = ";".join(meta_parts)
        parts.append(
            f"{key}:{s.probe_status}:{model_ids_str}:{fetched}:{s.last_error or ''}:{meta_str}"
        )
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


def _registry_hash(registry: list[ModelProviderConfig]) -> str:
    """Stable hash of the registry list contents."""
    parts = [
        f"{c.id}:{c.model_name}:{c.auth}:{c.api_key_env or ''}:"
        f"{c.api_base or ''}:{c.display_name or ''}:{c.region or ''}"
        for c in registry
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]


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


def _compute_availability_impl(
    registry: list[ModelProviderConfig],
    snapshots: dict[str, ProviderSnapshot],
    env: Mapping[str, str],
) -> list[ProviderGroup]:
    """Core implementation — not memoized directly; called by compute_availability."""
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

    # Also include providers that have a disabled snapshot but no registry
    # entries, when the snapshot has a user-actionable last_error (env var not
    # set, or AWS credentials missing).  This surfaces the empty-state card in
    # the frontend so users know what to configure.  Infrastructure-level
    # disabled providers (e.g. local_llm with no base URL) are intentionally
    # omitted to avoid cluttering the UI.
    _actionable_re = re.compile(r"^[A-Z_]+ not set$|^AWS credentials not configured$")
    for provider_key, snapshot in snapshots.items():
        if (
            snapshot.probe_status == "disabled"
            and provider_key not in groups
            and snapshot.last_error is not None
            and _actionable_re.match(snapshot.last_error)
        ):
            provider_order.append(provider_key)
            groups[provider_key] = []

    result: list[ProviderGroup] = []
    for provider_key in provider_order:
        # Determine probe_status from snapshot; default "disabled" if none.
        snapshot = snapshots.get(provider_key)
        probe_status: ProbeStatus = snapshot.probe_status if snapshot else "disabled"
        result.append(ProviderGroup(
            provider=provider_key,
            probe_status=probe_status,
            models=groups[provider_key],
            fetched_at=snapshot.fetched_at if snapshot else None,
            last_error=snapshot.last_error if snapshot else None,
        ))

    return result


def compute_availability(
    registry: list[ModelProviderConfig],
    snapshots: dict[str, ProviderSnapshot],
    env: Mapping[str, str],
    *,
    snapshot_version: int = 0,
) -> list[ProviderGroup]:
    """Compute availability for every registry entry, grouped by provider.

    Results are memoized per ``(snapshot_version, env_subset_hash)`` so the
    hot ``/models`` path avoids redundant work between requests.  The cache
    is process-local, thread-safe, and bounded to ``_CACHE_MAXSIZE=4`` entries
    (LRU eviction).

    Parameters
    ----------
    registry:
        Ordered list of ModelProviderConfig entries.
    snapshots:
        Provider snapshots from ProviderCatalog.snapshot().
    env:
        Environment variable mapping (usually os.environ).
    snapshot_version:
        Monotonically-increasing version from ProviderCatalog.  Pass
        ``catalog.snapshot_version`` at call site to invalidate the cache on
        each probe refresh.

    Returns
    -------
    list[ProviderGroup] in insertion order of first appearance of each provider.
    """
    env_hash = _env_subset_hash(env)
    snaps_hash = _snapshots_hash(snapshots)
    reg_hash = _registry_hash(registry)
    cache_key = f"{snapshot_version}:{snaps_hash}:{env_hash}:{reg_hash}"

    with _cache_lock:
        if cache_key in _lru_cache:
            # Move to end (most-recently-used).
            _lru_cache.move_to_end(cache_key)
            return _lru_cache[cache_key]

        result = _compute_availability_impl(registry, snapshots, env)

        _lru_cache[cache_key] = result
        _lru_cache.move_to_end(cache_key)
        while len(_lru_cache) > _CACHE_MAXSIZE:
            _lru_cache.popitem(last=False)
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
        # Serialise fetched_at as ISO-8601 UTC string; null when absent.
        fetched_at_str: str | None = None
        if g.fetched_at is not None:
            dt = g.fetched_at
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            fetched_at_str = dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        out.append({
            "provider": g.provider,
            "probe_status": g.probe_status,
            "fetched_at": fetched_at_str,
            "last_error": g.last_error,
            "models": models_out,
        })
    return out


def build_id_to_status(groups: list[ProviderGroup]) -> dict[str, ModelStatus]:
    """Return a flat mapping of model_id → status for quick lookup."""
    return {m.id: m.status for g in groups for m in g.models}
