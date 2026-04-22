"""Integration tests for GET /models — new grouped-by-provider shape.

Covers:
- No keys set → key_missing for api_key models
- OPENAI_API_KEY set + catalog stub returning gpt-4o → available;
  registry entry not in snapshot → not_listed
- Catalog snapshot failed → probe_failed
- ?format=flat returns legacy list shape
- Accept: application/vnd.sec-review.v0+json returns legacy shape
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
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
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    return ExperimentCoordinator(
        k8s_client=None,
        storage_root=tmp_path / "storage",
        concurrency_caps={},
        worker_image="worker:latest",
        namespace="default",
        db=db,
        reporter=MarkdownReportGenerator(),
        cost_calculator=cost_calc,
        config_dir=config_dir,
        default_cap=4,
    )


def _write_models_yaml(config_dir: Path, providers: dict) -> None:
    data = {
        "defaults": {"temperature": 0.2, "max_tokens": 8192},
        "providers": providers,
    }
    (config_dir / "models.yaml").write_text(yaml.dump(data))


def _fake_catalog(snapshots: dict[str, ProviderSnapshot]) -> ProviderCatalog:
    catalog = MagicMock(spec=ProviderCatalog)
    catalog.snapshot.return_value = snapshots
    return catalog


_OPENAI_PROVIDERS = {
    "gpt-4o": {
        "model_name": "gpt-4o",
        "api_key_env": "OPENAI_API_KEY",
        "display_name": "GPT-4o",
    },
    "gpt-4o-ultra-preview": {
        "model_name": "gpt-4o-ultra-preview",
        "api_key_env": "OPENAI_API_KEY",
        "display_name": "GPT-4o Ultra Preview",
    },
}

_BEDROCK_PROVIDERS = {
    "bedrock-claude": {
        "model_name": "bedrock/anthropic.claude-3-5-sonnet-20240620-v1:0",
        "auth": "aws",
        "region": "us-east-1",
        "display_name": "Claude 3.5 Sonnet (Bedrock)",
    }
}


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
                yield client, c, tmp_path / "config"


# ---------------------------------------------------------------------------
# No keys set → key_missing
# ---------------------------------------------------------------------------

def test_no_keys_all_key_missing(_ctx, monkeypatch):
    """Without any API keys set, all api_key-auth models should be key_missing."""
    client, c, config_dir = _ctx
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    _write_models_yaml(config_dir, _OPENAI_PROVIDERS)
    c.catalog = _fake_catalog({})

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
    client, c, config_dir = _ctx
    _write_models_yaml(config_dir, _BEDROCK_PROVIDERS)
    c.catalog = _fake_catalog({
        "bedrock": ProviderSnapshot(probe_status="disabled")
    })

    resp = client.get("/models")
    assert resp.status_code == 200
    groups = resp.json()
    bedrock_group = next((g for g in groups if g["provider"] == "bedrock"), None)
    assert bedrock_group is not None
    assert bedrock_group["probe_status"] == "disabled"
    for model in bedrock_group["models"]:
        assert model["status"] == "key_missing"


# ---------------------------------------------------------------------------
# OPENAI_API_KEY set + catalog stub returning gpt-4o in snapshot
# ---------------------------------------------------------------------------

def test_available_and_not_listed(_ctx, monkeypatch):
    """gpt-4o listed in snapshot → available; gpt-4o-ultra-preview not → not_listed."""
    client, c, config_dir = _ctx
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    _write_models_yaml(config_dir, _OPENAI_PROVIDERS)
    snap = ProviderSnapshot(
        probe_status="fresh",
        model_ids=frozenset(["gpt-4o"]),
        metadata={"gpt-4o": ModelMetadata(id="gpt-4o", display_name="GPT-4o", context_length=128000)},
    )
    c.catalog = _fake_catalog({"openai": snap})

    resp = client.get("/models")
    assert resp.status_code == 200
    groups = resp.json()
    openai_group = next(g for g in groups if g["provider"] == "openai")

    model_by_id = {m["id"]: m for m in openai_group["models"]}
    assert model_by_id["gpt-4o"]["status"] == "available"
    assert model_by_id["gpt-4o"]["context_length"] == 128000
    assert model_by_id["gpt-4o-ultra-preview"]["status"] == "not_listed"


# ---------------------------------------------------------------------------
# Catalog snapshot failed → probe_failed
# ---------------------------------------------------------------------------

def test_probe_failed_status(_ctx, monkeypatch):
    client, c, config_dir = _ctx
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    _write_models_yaml(config_dir, _OPENAI_PROVIDERS)
    snap = ProviderSnapshot(probe_status="failed", last_error="connection refused")
    c.catalog = _fake_catalog({"openai": snap})

    resp = client.get("/models")
    assert resp.status_code == 200
    groups = resp.json()
    openai_group = next(g for g in groups if g["provider"] == "openai")
    assert openai_group["probe_status"] == "failed"
    for model in openai_group["models"]:
        assert model["status"] == "probe_failed"


# ---------------------------------------------------------------------------
# Legacy flat format via ?format=flat
# ---------------------------------------------------------------------------

def test_format_flat_returns_list_of_dicts(_ctx, monkeypatch):
    """?format=flat returns [{id, display_name}] without grouping or status."""
    client, c, config_dir = _ctx
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    _write_models_yaml(config_dir, _OPENAI_PROVIDERS)
    snap = ProviderSnapshot(
        probe_status="fresh",
        model_ids=frozenset(["gpt-4o"]),
        metadata={"gpt-4o": ModelMetadata(id="gpt-4o")},
    )
    c.catalog = _fake_catalog({"openai": snap})

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
    assert "gpt-4o-ultra-preview" in ids


# ---------------------------------------------------------------------------
# Legacy flat format via Accept header
# ---------------------------------------------------------------------------

def test_accept_header_v0_returns_flat(_ctx, monkeypatch):
    """Accept: application/vnd.sec-review.v0+json triggers flat legacy shape."""
    client, c, config_dir = _ctx
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    _write_models_yaml(config_dir, {
        "gpt-4o": {
            "model_name": "gpt-4o",
            "api_key_env": "OPENAI_API_KEY",
            "display_name": "GPT-4o",
        }
    })
    c.catalog = _fake_catalog({})

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
    """Verify the grouped response has provider, probe_status, and models fields."""
    client, c, config_dir = _ctx
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    _write_models_yaml(config_dir, {
        "gpt-4o": {
            "model_name": "gpt-4o",
            "api_key_env": "OPENAI_API_KEY",
            "display_name": "GPT-4o",
        }
    })
    snap = ProviderSnapshot(
        probe_status="fresh",
        model_ids=frozenset(["gpt-4o"]),
        metadata={"gpt-4o": ModelMetadata(id="gpt-4o")},
    )
    c.catalog = _fake_catalog({"openai": snap})

    resp = client.get("/models")
    assert resp.status_code == 200
    groups = resp.json()
    assert len(groups) > 0

    for group in groups:
        assert "provider" in group
        assert "probe_status" in group
        assert "models" in group
        for model in group["models"]:
            assert "id" in model
            assert "status" in model
            assert "display_name" in model


# ---------------------------------------------------------------------------
# No models.yaml → empty list
# ---------------------------------------------------------------------------

def test_no_models_yaml_returns_empty_list(_ctx):
    """When models.yaml is missing, list_models returns []."""
    client, c, config_dir = _ctx
    # Don't write models.yaml
    c.catalog = _fake_catalog({})

    resp = client.get("/models")
    assert resp.status_code == 200
    assert resp.json() == []
