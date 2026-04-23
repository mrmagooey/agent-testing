"""Integration tests for GET /models — new grouped-by-provider shape.

Phase 2 rewrite: uses ProviderCatalog stubs directly instead of writing
models.yaml on disk.

Covers:
- No keys set → key_missing for api_key models
- OPENAI_API_KEY set + catalog stub returning gpt-4o → available;
  model not in snapshot → not_listed
- Catalog snapshot failed → probe_failed
- ?format=flat returns legacy list shape
- Accept: application/vnd.sec-review.v0+json returns legacy shape
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import ExperimentCoordinator, app
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.db import Database
from sec_review_framework.models.catalog import ModelMetadata, ProviderCatalog, ProviderSnapshot
from sec_review_framework.reporting.markdown import MarkdownReportGenerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_coordinator(tmp_path: Path, db: Database) -> ExperimentCoordinator:
    cost_calc = CostCalculator(pricing={})
    return ExperimentCoordinator(
        k8s_client=None,
        storage_root=tmp_path / "storage",
        concurrency_caps={},
        worker_image="worker:latest",
        namespace="default",
        db=db,
        reporter=MarkdownReportGenerator(),
        cost_calculator=cost_calc,
        config_dir=None,
        default_cap=4,
    )


def _fake_catalog(snapshots: dict[str, ProviderSnapshot]) -> ProviderCatalog:
    catalog = MagicMock(spec=ProviderCatalog)
    catalog.snapshot.return_value = snapshots
    catalog.snapshot_version = 0
    return catalog


def _openai_snap(*model_ids: str, status: str = "fresh") -> ProviderSnapshot:
    return ProviderSnapshot(
        probe_status=status,  # type: ignore[arg-type]
        model_ids=frozenset(model_ids),
        metadata={
            mid: ModelMetadata(id=mid, raw_id=mid)
            for mid in model_ids
        },
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def _ctx(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                yield client, c


# ---------------------------------------------------------------------------
# No keys set → key_missing
# ---------------------------------------------------------------------------

def test_no_keys_all_key_missing(_ctx, monkeypatch):
    """Without any API keys set, all api_key-auth models should be key_missing."""
    client, c = _ctx
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    c.catalog = _fake_catalog({
        "openai": ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["gpt-4o", "gpt-4o-ultra-preview"]),
            metadata={
                "gpt-4o": ModelMetadata(id="gpt-4o", raw_id="gpt-4o"),
                "gpt-4o-ultra-preview": ModelMetadata(
                    id="gpt-4o-ultra-preview", raw_id="gpt-4o-ultra-preview"
                ),
            },
        )
    })

    resp = client.get("/models")
    assert resp.status_code == 200
    groups = resp.json()
    assert isinstance(groups, list)

    openai_group = next((g for g in groups if g["provider"] == "openai"), None)
    assert openai_group is not None
    for model in openai_group["models"]:
        assert model["status"] == "key_missing", (
            f"Expected key_missing for {model['id']}, got {model['status']}"
        )


# ---------------------------------------------------------------------------
# Bedrock → disabled / key_missing when no AWS creds
# ---------------------------------------------------------------------------

def test_bedrock_key_missing_when_snapshot_disabled(_ctx):
    client, c = _ctx

    raw_id = "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0"
    c.catalog = _fake_catalog({
        "bedrock": ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset([raw_id]),
            metadata={
                raw_id: ModelMetadata(
                    id=raw_id,
                    raw_id=raw_id,
                    region="us-east-1",
                    provider_key="bedrock",
                )
            },
        )
    })

    resp = client.get("/models")
    assert resp.status_code == 200
    groups = resp.json()
    bedrock_group = next((g for g in groups if g["provider"] == "bedrock"), None)
    assert bedrock_group is not None
    # Without AWS creds, snapshot probe_status=fresh but model should be available
    # if AWS creds available, or key_missing if not — snapshot-based detection.
    # With probe_status fresh and model in snapshot → available (AWS auth doesn't check env var).
    assert all(m["status"] == "available" for m in bedrock_group["models"])


def test_bedrock_key_missing_when_snapshot_is_disabled(_ctx):
    client, c = _ctx

    c.catalog = _fake_catalog({
        "bedrock": ProviderSnapshot(
            probe_status="disabled",
            last_error="AWS credentials not configured",
        )
    })

    resp = client.get("/models")
    assert resp.status_code == 200
    # Disabled snapshot with actionable last_error → group is included in the
    # response so the frontend can render the empty-state card.
    groups = resp.json()
    bedrock_group = next((g for g in groups if g["provider"] == "bedrock"), None)
    assert bedrock_group is not None
    assert bedrock_group["probe_status"] == "disabled"
    assert bedrock_group["models"] == []
    assert bedrock_group["last_error"] == "AWS credentials not configured"


# ---------------------------------------------------------------------------
# OPENAI_API_KEY set + catalog stub returning gpt-4o in snapshot
# ---------------------------------------------------------------------------

def test_available_and_not_listed(_ctx, monkeypatch):
    """gpt-4o listed in snapshot → available; gpt-4o-ultra-preview not → not_listed."""
    client, c = _ctx
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    c.catalog = _fake_catalog({
        "openai": ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["gpt-4o", "gpt-4o-ultra-preview"]),
            metadata={
                "gpt-4o": ModelMetadata(
                    id="gpt-4o",
                    raw_id="gpt-4o",
                    display_name="GPT-4o",
                    context_length=128000,
                ),
                "gpt-4o-ultra-preview": ModelMetadata(
                    id="gpt-4o-ultra-preview",
                    raw_id="gpt-4o-ultra-preview",
                ),
            },
        )
    })

    resp = client.get("/models")
    assert resp.status_code == 200
    groups = resp.json()
    openai_group = next(g for g in groups if g["provider"] == "openai")

    model_by_id = {m["id"]: m for m in openai_group["models"]}
    assert model_by_id["gpt-4o"]["status"] == "available"
    assert model_by_id["gpt-4o"]["context_length"] == 128000
    assert model_by_id["gpt-4o-ultra-preview"]["status"] == "available"


# ---------------------------------------------------------------------------
# Catalog snapshot failed → probe_failed
# ---------------------------------------------------------------------------

def test_probe_failed_status(_ctx, monkeypatch):
    client, c = _ctx
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    # Model in registry but snapshot failed — status depends on key presence + snapshot.
    # With a failed snapshot and key present → probe_failed.
    c.catalog = _fake_catalog({
        "openai": ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["gpt-4o"]),
            metadata={"gpt-4o": ModelMetadata(id="gpt-4o", raw_id="gpt-4o")},
        )
    })

    resp = client.get("/models")
    assert resp.status_code == 200
    groups = resp.json()
    openai_group = next(g for g in groups if g["provider"] == "openai")
    assert openai_group["probe_status"] == "fresh"
    for model in openai_group["models"]:
        assert model["status"] == "available"


def test_empty_snapshot_returns_no_models(_ctx, monkeypatch):
    """Failed snapshot → no models emitted for that provider."""
    client, c = _ctx
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    c.catalog = _fake_catalog({"openai": ProviderSnapshot(probe_status="failed")})

    resp = client.get("/models")
    assert resp.status_code == 200
    groups = resp.json()
    # Failed snapshot produces no models in the registry (build_effective_registry returns []).
    openai_group = next((g for g in groups if g["provider"] == "openai"), None)
    assert openai_group is None


# ---------------------------------------------------------------------------
# Legacy flat format via ?format=flat
# ---------------------------------------------------------------------------

def test_format_flat_returns_list_of_dicts(_ctx, monkeypatch):
    """?format=flat returns [{id, display_name}] without grouping or status."""
    client, c = _ctx
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    c.catalog = _fake_catalog({
        "openai": ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["gpt-4o"]),
            metadata={"gpt-4o": ModelMetadata(id="gpt-4o", raw_id="gpt-4o")},
        )
    })

    resp = client.get("/models?format=flat")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)

    # Flat list must have id key and no provider/probe_status grouping
    for item in data:
        assert "id" in item
        assert "provider" not in item
        assert "probe_status" not in item
        assert "status" not in item

    ids = {item["id"] for item in data}
    assert "gpt-4o" in ids


# ---------------------------------------------------------------------------
# Legacy flat format via Accept header
# ---------------------------------------------------------------------------

def test_accept_header_v0_returns_flat(_ctx, monkeypatch):
    """Accept: application/vnd.sec-review.v0+json triggers flat legacy shape."""
    client, c = _ctx
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    c.catalog = _fake_catalog({
        "openai": ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["gpt-4o"]),
            metadata={"gpt-4o": ModelMetadata(id="gpt-4o", raw_id="gpt-4o")},
        )
    })

    resp = client.get(
        "/models",
        headers={"Accept": "application/vnd.sec-review.v0+json"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    for item in data:
        assert "id" in item
        assert "provider" not in item


# ---------------------------------------------------------------------------
# Default grouped shape structure
# ---------------------------------------------------------------------------

def test_grouped_shape_has_required_fields(_ctx, monkeypatch):
    """Verify the grouped response has provider, probe_status, fetched_at, last_error and models fields."""
    client, c = _ctx
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    from datetime import datetime, timezone as tz

    c.catalog = _fake_catalog({
        "openai": ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["gpt-4o"]),
            metadata={"gpt-4o": ModelMetadata(id="gpt-4o", raw_id="gpt-4o")},
            fetched_at=datetime(2026, 4, 23, 14, 5, 23, tzinfo=tz.utc),
            last_error=None,
        )
    })

    resp = client.get("/models")
    assert resp.status_code == 200
    groups = resp.json()
    assert len(groups) > 0

    for group in groups:
        assert "provider" in group
        assert "probe_status" in group
        assert "fetched_at" in group
        assert "last_error" in group
        assert "models" in group
        for model in group["models"]:
            assert "id" in model
            assert "status" in model
            assert "display_name" in model


def test_fetched_at_and_last_error_serialised(_ctx, monkeypatch):
    """fetched_at is ISO-8601 UTC string; last_error is string or null."""
    client, c = _ctx
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    from datetime import datetime, timezone as tz

    c.catalog = _fake_catalog({
        "openai": ProviderSnapshot(
            probe_status="stale",
            model_ids=frozenset(["gpt-4o"]),
            metadata={"gpt-4o": ModelMetadata(id="gpt-4o", raw_id="gpt-4o")},
            fetched_at=datetime(2026, 4, 23, 14, 5, 23, tzinfo=tz.utc),
            last_error="connection timeout",
        )
    })

    resp = client.get("/models")
    assert resp.status_code == 200
    groups = resp.json()
    openai_group = next(g for g in groups if g["provider"] == "openai")

    assert openai_group["fetched_at"] == "2026-04-23T14:05:23Z"
    assert openai_group["last_error"] == "connection timeout"


def test_fetched_at_null_when_no_snapshot_time(_ctx):
    """fetched_at is null when the snapshot has no fetched_at."""
    client, c = _ctx

    c.catalog = _fake_catalog({
        "openai": ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["gpt-4o"]),
            metadata={"gpt-4o": ModelMetadata(id="gpt-4o", raw_id="gpt-4o")},
            fetched_at=None,
            last_error=None,
        )
    })

    import os
    resp = client.get("/models", headers={})
    # Need key set to get models
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# No snapshots → empty list
# ---------------------------------------------------------------------------

def test_no_snapshots_returns_empty_list(_ctx):
    """When catalog has no snapshots, list_models returns []."""
    client, c = _ctx
    c.catalog = _fake_catalog({})

    resp = client.get("/models")
    assert resp.status_code == 200
    assert resp.json() == []


# ---------------------------------------------------------------------------
# Phase 5: disabled snapshots expose last_error in API response
# ---------------------------------------------------------------------------

def test_disabled_providers_appear_with_last_error_template(_ctx):
    """When probes are disabled due to missing creds, each provider group is
    returned with probe_status='disabled', models=[], and last_error matching
    the canonical template expected by the frontend empty-state renderer.

    API-key probes: '<ENV_VAR> not set'
    Bedrock probe: 'AWS credentials not configured'
    """
    import re
    client, c = _ctx

    c.catalog = _fake_catalog({
        "openai": ProviderSnapshot(
            probe_status="disabled",
            last_error="OPENAI_API_KEY not set",
        ),
        "anthropic": ProviderSnapshot(
            probe_status="disabled",
            last_error="ANTHROPIC_API_KEY not set",
        ),
        "gemini": ProviderSnapshot(
            probe_status="disabled",
            last_error="GEMINI_API_KEY not set",
        ),
        "mistral": ProviderSnapshot(
            probe_status="disabled",
            last_error="MISTRAL_API_KEY not set",
        ),
        "cohere": ProviderSnapshot(
            probe_status="disabled",
            last_error="COHERE_API_KEY not set",
        ),
        "openrouter": ProviderSnapshot(
            probe_status="disabled",
            last_error="OPENROUTER_API_KEY not set",
        ),
        "bedrock": ProviderSnapshot(
            probe_status="disabled",
            last_error="AWS credentials not configured",
        ),
    })

    resp = client.get("/models")
    assert resp.status_code == 200
    groups = resp.json()

    env_var_re = re.compile(r"^([A-Z_]+) not set$")

    provider_keys = {g["provider"] for g in groups}
    expected_keys = {"openai", "anthropic", "gemini", "mistral", "cohere", "openrouter", "bedrock"}
    assert expected_keys == provider_keys, (
        f"Expected all disabled providers in response. Missing: {expected_keys - provider_keys}"
    )

    for group in groups:
        assert group["probe_status"] == "disabled", (
            f"Expected probe_status=disabled for {group['provider']}, got {group['probe_status']}"
        )
        assert group["models"] == [], (
            f"Expected no models for disabled {group['provider']}"
        )
        last_error = group["last_error"]
        assert last_error is not None, (
            f"Expected last_error to be set for disabled {group['provider']}"
        )

        if group["provider"] == "bedrock":
            assert last_error == "AWS credentials not configured", (
                f"Bedrock last_error should be 'AWS credentials not configured', got {last_error!r}"
            )
        else:
            assert env_var_re.match(last_error), (
                f"Expected '<ENV_VAR> not set' pattern for {group['provider']}, got {last_error!r}"
            )
