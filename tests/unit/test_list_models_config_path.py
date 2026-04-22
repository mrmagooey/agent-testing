"""Unit tests for ExperimentCoordinator.list_models() config_dir handling."""

import json
from pathlib import Path

import pytest
import yaml

from sec_review_framework.coordinator import ExperimentCoordinator
from sec_review_framework.cost.calculator import CostCalculator
from sec_review_framework.db import Database


@pytest.fixture
def temp_coordinator(tmp_path):
    """Create a ExperimentCoordinator with a temporary config_dir."""
    storage_root = tmp_path / "data"
    storage_root.mkdir(parents=True, exist_ok=True)

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

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
        config_dir=config_dir,
        default_cap=1,
    ), config_dir


def test_list_models_reads_from_config_dir(temp_coordinator):
    """list_models() should read models.yaml from config_dir, not storage_root.parent."""
    coordinator, config_dir = temp_coordinator

    # Write a models.yaml file with valid provider entries (Phase 2 schema).
    models_yaml = config_dir / "models.yaml"
    models_data = {
        "defaults": {"temperature": 0.2, "max_tokens": 8192},
        "providers": {
            "gpt-4o": {
                "model_name": "gpt-4o",
                "api_key_env": "OPENAI_API_KEY",
                "display_name": "GPT-4o",
            },
            "claude-opus": {
                "model_name": "claude-opus-4",
                "api_key_env": "ANTHROPIC_API_KEY",
                "display_name": "Claude Opus",
            },
        },
    }
    models_yaml.write_text(yaml.dump(models_data))

    # Call list_models and verify it reads from config_dir.
    # The new response shape is a grouped list; extract ids from models within groups.
    groups = coordinator.list_models()
    all_model_ids = {m["id"] for g in groups for m in g["models"]}
    assert len(all_model_ids) == 2
    assert all_model_ids == {"gpt-4o", "claude-opus"}


def test_list_models_uses_env_var_config_dir(tmp_path, monkeypatch):
    """list_models() should use CONFIG_DIR env var, falling back to /app/config."""
    storage_root = tmp_path / "data"
    storage_root.mkdir(parents=True, exist_ok=True)

    config_dir = tmp_path / "custom_config"
    config_dir.mkdir(parents=True, exist_ok=True)

    # Monkeypatch CONFIG_DIR to point to our temp config dir
    monkeypatch.setenv("CONFIG_DIR", str(config_dir))

    db = Database(storage_root / "test.db")

    # Create coordinator with config_dir from env
    coordinator = ExperimentCoordinator(
        k8s_client=None,
        storage_root=storage_root,
        concurrency_caps={},
        worker_image="test:latest",
        namespace="test",
        db=db,
        reporter=None,
        cost_calculator=CostCalculator(pricing={}),
        config_dir=config_dir,
        default_cap=1,
    )

    # Write models.yaml to the env var location (valid Phase 2 schema).
    models_yaml = config_dir / "models.yaml"
    models_data = {
        "defaults": {"temperature": 0.2, "max_tokens": 8192},
        "providers": {
            "test-model": {
                "model_name": "gpt-4o",
                "api_key_env": "OPENAI_API_KEY",
                "display_name": "Test Model",
            }
        },
    }
    models_yaml.write_text(yaml.dump(models_data))

    # Verify list_models reads from the env var location.
    # New shape is grouped; extract all model ids.
    groups = coordinator.list_models()
    all_model_ids = [m["id"] for g in groups for m in g["models"]]
    assert len(all_model_ids) == 1
    assert all_model_ids[0] == "test-model"


def test_list_models_returns_empty_when_file_missing(temp_coordinator):
    """list_models() should return [] if models.yaml does not exist."""
    coordinator, config_dir = temp_coordinator

    # Don't create models.yaml; it should return empty list
    models = coordinator.list_models()
    assert models == []


def test_list_models_returns_empty_on_parse_error(temp_coordinator):
    """list_models() should return [] if models.yaml is invalid YAML."""
    coordinator, config_dir = temp_coordinator

    # Write invalid YAML
    models_yaml = config_dir / "models.yaml"
    models_yaml.write_text("{ invalid: yaml: syntax:")

    # Should return [] without raising
    models = coordinator.list_models()
    assert models == []


def test_list_models_does_not_read_from_storage_root_parent(tmp_path):
    """list_models() should NOT fall back to storage_root.parent/config."""
    storage_root = tmp_path / "storage" / "data"
    storage_root.mkdir(parents=True, exist_ok=True)

    config_dir = tmp_path / "app" / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    # Write a file to storage_root.parent/config that should NOT be read
    wrong_path = storage_root.parent / "config"
    wrong_path.mkdir(parents=True, exist_ok=True)
    wrong_models = wrong_path / "models.yaml"
    wrong_models.write_text(yaml.dump({
        "providers": {"should-not-read": {"display_name": "Wrong", "cost": 1.0}}
    }))

    db = Database(storage_root / "test.db")
    coordinator = ExperimentCoordinator(
        k8s_client=None,
        storage_root=storage_root,
        concurrency_caps={},
        worker_image="test:latest",
        namespace="test",
        db=db,
        reporter=None,
        cost_calculator=CostCalculator(pricing={}),
        config_dir=config_dir,
        default_cap=1,
    )

    # list_models should return empty (not read from wrong_path)
    models = coordinator.list_models()
    assert models == []
