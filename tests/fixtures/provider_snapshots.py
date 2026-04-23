"""Canonical ProviderSnapshot / ProviderCatalog factories for unit and integration tests.

Import these instead of building ProviderSnapshot objects by hand.

Usage::

    from tests.fixtures.provider_snapshots import (
        canonical_snapshots,
        fresh_snapshot,
        disabled_snapshot,
        failed_snapshot,
        stale_snapshot,
    )

    # All canonical providers, fresh, 2-3 models each.
    snaps = canonical_snapshots()

    # One provider with specific model ids.
    snap = fresh_snapshot("openai", ["gpt-4o", "gpt-4o-mini"])

    # Disabled (env var not set).
    snap = disabled_snapshot("anthropic")

    # Failed probe.
    snap = failed_snapshot("gemini", "connection timeout")

    # Stale (fetched N seconds ago).
    snap = stale_snapshot("mistral", ["mistral-large-latest"], age_seconds=900)
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Sequence
from unittest.mock import MagicMock

from sec_review_framework.models.catalog import ModelMetadata, ProviderCatalog, ProviderSnapshot
from sec_review_framework.models.providers import ENV_VAR_FOR_PROVIDER

# ---------------------------------------------------------------------------
# Canonical model IDs per provider — representative but not exhaustive.
# ---------------------------------------------------------------------------

_CANONICAL_MODELS: dict[str, list[str]] = {
    "openai": ["gpt-4o", "gpt-4o-mini", "o3-mini"],
    "anthropic": ["claude-opus-4", "claude-sonnet-4-5", "claude-haiku-4-5"],
    "gemini": ["gemini-2.5-pro", "gemini-2.0-flash", "gemini-1.5-pro"],
    "cohere": ["command-r-plus", "command-r"],
    "mistral": ["mistral-large-latest", "mistral-small-latest", "codestral-latest"],
    "openrouter": [
        "openrouter/meta-llama/llama-3.1-8b-instruct",
        "openrouter/meta-llama/llama-3.2-3b-instruct",
    ],
    "bedrock": [
        "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
        "bedrock/amazon.nova-pro-v1:0",
        "bedrock/amazon.nova-lite-v1:0",
    ],
}

# Region for each Bedrock model (required by build_effective_registry).
_BEDROCK_REGIONS: dict[str, str] = {
    "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0": "us-east-1",
    "bedrock/amazon.nova-pro-v1:0": "us-east-1",
    "bedrock/amazon.nova-lite-v1:0": "us-east-1",
}


# ---------------------------------------------------------------------------
# Public factories
# ---------------------------------------------------------------------------


def fresh_snapshot(
    provider_key: str,
    model_ids: Sequence[str] | None = None,
) -> ProviderSnapshot:
    """Return a ``probe_status='fresh'`` snapshot for *provider_key*.

    If *model_ids* is omitted, the canonical set for the provider is used.
    Each ``ModelMetadata`` entry is populated with ``provider_key`` and
    ``raw_id`` as added by Phase 1.
    """
    ids: list[str] = list(model_ids) if model_ids is not None else _CANONICAL_MODELS.get(provider_key, [])

    metadata: dict[str, ModelMetadata] = {}
    for mid in ids:
        kwargs: dict = {
            "id": mid,
            "raw_id": mid,
            "provider_key": provider_key,
        }
        if provider_key == "bedrock":
            kwargs["region"] = _BEDROCK_REGIONS.get(mid, "us-east-1")
        metadata[mid] = ModelMetadata(**kwargs)

    return ProviderSnapshot(
        probe_status="fresh",
        model_ids=frozenset(ids),
        metadata=metadata,
        fetched_at=datetime.now(timezone.utc),
    )


def disabled_snapshot(
    provider_key: str,
    last_error: str | None = None,
) -> ProviderSnapshot:
    """Return a ``probe_status='disabled'`` snapshot for *provider_key*.

    *last_error* defaults to ``'{ENV_VAR} not set'`` (or the Bedrock
    equivalent).  Pass an explicit string to override.
    """
    if last_error is None:
        env_var = ENV_VAR_FOR_PROVIDER.get(provider_key)
        if env_var is not None:
            last_error = f"{env_var} not set"
        else:
            # Bedrock (env_var is None) uses the credential-chain message.
            last_error = "AWS credentials not configured"

    return ProviderSnapshot(
        probe_status="disabled",
        last_error=last_error,
    )


def failed_snapshot(
    provider_key: str,
    error: str = "probe failed",
) -> ProviderSnapshot:
    """Return a ``probe_status='failed'`` snapshot for *provider_key*."""
    return ProviderSnapshot(
        probe_status="failed",
        last_error=error,
    )


def stale_snapshot(
    provider_key: str,
    model_ids: Sequence[str],
    age_seconds: float = 700,
) -> ProviderSnapshot:
    """Return a ``probe_status='stale'`` snapshot whose ``fetched_at`` is
    *age_seconds* in the past.

    Models and metadata are populated in the same way as :func:`fresh_snapshot`.
    """
    ids = list(model_ids)
    metadata: dict[str, ModelMetadata] = {}
    for mid in ids:
        kwargs: dict = {
            "id": mid,
            "raw_id": mid,
            "provider_key": provider_key,
        }
        if provider_key == "bedrock":
            kwargs["region"] = _BEDROCK_REGIONS.get(mid, "us-east-1")
        metadata[mid] = ModelMetadata(**kwargs)

    fetched_at = datetime.now(timezone.utc) - timedelta(seconds=age_seconds)

    return ProviderSnapshot(
        probe_status="stale",
        model_ids=frozenset(ids),
        metadata=metadata,
        fetched_at=fetched_at,
        last_error="upstream temporarily unavailable",
    )


def fake_catalog(snapshots: dict[str, ProviderSnapshot]) -> ProviderCatalog:
    """Return a :class:`~ProviderCatalog` stub whose ``snapshot()`` returns *snapshots*.

    ``snapshot_version`` is initialised to ``0``.  The returned object satisfies
    ``MagicMock(spec=ProviderCatalog)`` so it passes ``isinstance`` checks.

    Usage::

        from tests.fixtures.provider_snapshots import fake_catalog, fresh_snapshot

        catalog = fake_catalog({"openai": fresh_snapshot("openai")})
        coordinator.catalog = catalog
    """
    catalog = MagicMock(spec=ProviderCatalog)
    catalog.snapshot.return_value = snapshots
    catalog.snapshot_version = 0
    return catalog


def canonical_snapshots() -> dict[str, ProviderSnapshot]:
    """Return a frozen-like dict of fresh snapshots for every known provider.

    Covers all keys in ``ENV_VAR_FOR_PROVIDER`` with 2-3 representative
    models each.  The dict is a plain ``dict`` (not truly frozen) — callers
    must not mutate the ProviderSnapshot values.
    """
    return {
        provider_key: fresh_snapshot(provider_key)
        for provider_key in ENV_VAR_FOR_PROVIDER
    }
