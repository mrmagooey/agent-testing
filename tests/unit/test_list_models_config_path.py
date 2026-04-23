"""Unit tests for ExperimentCoordinator.list_models() — probe-driven path.

Phase 2 rewrite: list_models() now reads from ProviderCatalog snapshots rather
than models.yaml.  Tests verify the probe-driven flow and empty-state handling.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from sec_review_framework.coordinator import ExperimentCoordinator
from sec_review_framework.cost.calculator import CostCalculator
from sec_review_framework.db import Database
from sec_review_framework.models.catalog import ModelMetadata, ProviderCatalog, ProviderSnapshot


def _fake_catalog(snapshots: dict[str, ProviderSnapshot]) -> ProviderCatalog:
    catalog = MagicMock(spec=ProviderCatalog)
    catalog.snapshot.return_value = snapshots
    catalog.snapshot_version = 0
    return catalog


@pytest.fixture
def temp_coordinator(tmp_path):
    """Create an ExperimentCoordinator with no catalog attached yet."""
    storage_root = tmp_path / "data"
    storage_root.mkdir(parents=True, exist_ok=True)

    db = Database(storage_root / "test.db")

    return ExperimentCoordinator(
        k8s_client=None,
        storage_root=storage_root,
        concurrency_caps={},
        worker_image="test:latest",
        namespace="test",
        db=db,
        reporter=None,
        cost_calculator=CostCalculator(pricing={}),
        config_dir=None,
        default_cap=1,
    )


def test_list_models_returns_probe_discovered_models(temp_coordinator, monkeypatch):
    """list_models() returns models from the catalog snapshot."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "ant-test")

    coordinator = temp_coordinator
    coordinator.catalog = _fake_catalog({
        "openai": ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["gpt-4o"]),
            metadata={"gpt-4o": ModelMetadata(id="gpt-4o", raw_id="gpt-4o")},
        ),
        "anthropic": ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["claude-opus-4"]),
            metadata={"claude-opus-4": ModelMetadata(id="claude-opus-4", raw_id="claude-opus-4")},
        ),
    })

    groups = coordinator.list_models()
    all_model_ids = {m["id"] for g in groups for m in g["models"]}
    assert len(all_model_ids) == 2
    assert "gpt-4o" in all_model_ids
    assert "claude-opus-4" in all_model_ids


def test_list_models_returns_empty_when_no_catalog(temp_coordinator):
    """When no catalog is set, list_models() returns []."""
    coordinator = temp_coordinator
    coordinator.catalog = None

    result = coordinator.list_models()
    assert result == []


def test_list_models_returns_empty_when_all_snapshots_disabled(temp_coordinator):
    """All snapshots disabled → empty registry → empty list."""
    coordinator = temp_coordinator
    coordinator.catalog = _fake_catalog({
        "openai": ProviderSnapshot(probe_status="disabled"),
        "anthropic": ProviderSnapshot(probe_status="disabled"),
    })

    result = coordinator.list_models()
    assert result == []


def test_list_models_returns_empty_when_snapshots_empty(temp_coordinator):
    """No snapshots in catalog → no snapshots returns empty."""
    coordinator = temp_coordinator
    coordinator.catalog = _fake_catalog({})

    result = coordinator.list_models()
    assert result == []


def test_list_models_flat_format(temp_coordinator, monkeypatch):
    """list_models(flat=True) returns the legacy flat [{id, display_name}] shape."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    coordinator = temp_coordinator
    coordinator.catalog = _fake_catalog({
        "openai": ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["gpt-4o"]),
            metadata={"gpt-4o": ModelMetadata(id="gpt-4o", raw_id="gpt-4o")},
        ),
    })

    result = coordinator.list_models(flat=True)
    assert isinstance(result, list)
    assert any(item["id"] == "gpt-4o" for item in result)
    for item in result:
        assert "provider" not in item
        assert "probe_status" not in item


def test_list_models_grouped_shape(temp_coordinator, monkeypatch):
    """Grouped shape has provider, probe_status, and models keys."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    coordinator = temp_coordinator
    coordinator.catalog = _fake_catalog({
        "openai": ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["gpt-4o"]),
            metadata={"gpt-4o": ModelMetadata(id="gpt-4o", raw_id="gpt-4o")},
        ),
    })

    groups = coordinator.list_models()
    assert len(groups) > 0
    for group in groups:
        assert "provider" in group
        assert "probe_status" in group
        assert "models" in group
