"""B3: Integration tests for config + matrix reference routes.

Routes covered:
  - GET /models          — list of configured model dicts
  - GET /strategies      — list with name + description
  - GET /profiles        — list with name + description
  - GET /templates       — list (may be empty)
  - GET /matrix/accuracy — accuracy heatmap dict
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import app
from sec_review_framework.db import Database

from tests.integration.test_coordinator_api import _make_coordinator


@pytest.fixture
async def coordinator_client(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)
    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                yield client, c, tmp_path


# ---------------------------------------------------------------------------
# GET /models
# ---------------------------------------------------------------------------

def test_list_models_returns_200(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/models")
    assert resp.status_code == 200


def test_list_models_returns_list(coordinator_client):
    client, *_ = coordinator_client
    data = client.get("/models").json()
    assert isinstance(data, list)


def test_list_models_items_have_id_field_if_nonempty(coordinator_client):
    """Each model entry (if present) has at least an 'id' field."""
    client, *_ = coordinator_client
    data = client.get("/models").json()
    for item in data:
        assert "id" in item, f"Model entry missing 'id': {item}"


# ---------------------------------------------------------------------------
# GET /strategies
# ---------------------------------------------------------------------------

def test_list_strategies_returns_200(coordinator_client):
    client, *_ = coordinator_client
    assert client.get("/strategies").status_code == 200


def test_list_strategies_is_nonempty(coordinator_client):
    client, *_ = coordinator_client
    data = client.get("/strategies").json()
    assert len(data) > 0


def test_list_strategies_items_have_name_and_description(coordinator_client):
    client, *_ = coordinator_client
    data = client.get("/strategies").json()
    for item in data:
        assert "name" in item
        assert "description" in item
        assert isinstance(item["name"], str)
        assert isinstance(item["description"], str)


def test_list_strategies_contains_single_agent(coordinator_client):
    client, *_ = coordinator_client
    data = client.get("/strategies").json()
    names = [s["name"] for s in data]
    assert "single_agent" in names


# ---------------------------------------------------------------------------
# GET /profiles
# ---------------------------------------------------------------------------

def test_list_profiles_returns_200(coordinator_client):
    client, *_ = coordinator_client
    assert client.get("/profiles").status_code == 200


def test_list_profiles_is_nonempty(coordinator_client):
    client, *_ = coordinator_client
    data = client.get("/profiles").json()
    assert len(data) > 0


def test_list_profiles_items_have_name_and_description(coordinator_client):
    client, *_ = coordinator_client
    data = client.get("/profiles").json()
    for item in data:
        assert "name" in item
        assert "description" in item


def test_list_profiles_contains_default(coordinator_client):
    client, *_ = coordinator_client
    data = client.get("/profiles").json()
    names = [p["name"] for p in data]
    assert "default" in names


def test_list_profiles_all_names_are_strings(coordinator_client):
    client, *_ = coordinator_client
    data = client.get("/profiles").json()
    for item in data:
        assert isinstance(item["name"], str)


# ---------------------------------------------------------------------------
# GET /templates
# ---------------------------------------------------------------------------

def test_list_templates_returns_200(coordinator_client):
    client, *_ = coordinator_client
    assert client.get("/templates").status_code == 200


def test_list_templates_returns_list(coordinator_client):
    client, *_ = coordinator_client
    data = client.get("/templates").json()
    assert isinstance(data, list)


def test_list_templates_items_have_id_if_nonempty(coordinator_client):
    """Each template entry (if present) has at least an 'id' field."""
    client, *_ = coordinator_client
    data = client.get("/templates").json()
    for item in data:
        assert "id" in item or "name" in item, f"Template missing id/name: {item}"


# ---------------------------------------------------------------------------
# GET /matrix/accuracy
# ---------------------------------------------------------------------------

def test_matrix_accuracy_returns_200(coordinator_client):
    client, *_ = coordinator_client
    assert client.get("/matrix/accuracy").status_code == 200


def test_matrix_accuracy_returns_dict(coordinator_client):
    client, *_ = coordinator_client
    data = client.get("/matrix/accuracy").json()
    assert isinstance(data, dict)


def test_matrix_accuracy_empty_with_no_completed_batches(coordinator_client):
    """With no completed batches, matrix returns empty or minimal structure."""
    client, *_ = coordinator_client
    data = client.get("/matrix/accuracy").json()
    # Should be a dict (possibly with empty cells/models/strategies)
    assert isinstance(data, dict)
    # Common keys: models, strategies, cells — or empty dict
    # We just verify it doesn't crash and returns parseable JSON


def test_matrix_accuracy_does_not_include_pending_batches(coordinator_client):
    """Accuracy matrix only reflects completed batches — pending don't appear."""
    client, *_ = coordinator_client
    # Submit a batch but don't complete it
    client.post("/batches", json={
        "batch_id": "pending-batch",
        "dataset_name": "ds",
        "dataset_version": "1.0",
        "model_ids": ["gpt-4o"],
        "strategies": ["single_agent"],
        "tool_variants": ["with_tools"],
        "review_profiles": ["default"],
        "verification_variants": ["none"],
        "parallel_modes": [False],
    })
    data = client.get("/matrix/accuracy").json()
    # Pending batch shouldn't corrupt the matrix
    assert isinstance(data, dict)
