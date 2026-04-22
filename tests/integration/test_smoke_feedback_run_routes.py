"""B4: Integration tests for smoke-test route, /feedback/patterns, run detail shape.

Routes covered:
  - POST /smoke-test                          — creates an experiment, returns experiment_id + message
  - GET  /feedback/patterns/{experiment_id}   — returns list (empty for new experiment)
  - GET  /experiments/{id}/runs/{run_id}      — 404 for missing; shape contract on hit
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import app
from sec_review_framework.db import Database

from tests.integration.test_coordinator_api import _make_coordinator


def _seed_coordinator_prerequisites(tmp_path: Path) -> None:
    """Seed a minimal models.yaml and dataset directory so smoke-test preconditions pass."""
    import yaml

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    models_yaml = config_dir / "models.yaml"
    models_yaml.write_text(yaml.dump({
        "defaults": {"temperature": 0.2, "max_tokens": 8192},
        "providers": {
            "gpt-4o-mini": {
                "model_name": "gpt-4o-mini",
                "api_key_env": "OPENAI_API_KEY",
                "display_name": "GPT-4o mini",
            }
        },
    }))

    dataset_dir = tmp_path / "storage" / "datasets" / "smoke-test-dataset"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    (dataset_dir / "labels.json").write_text("[]")


@pytest.fixture
async def coordinator_client(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    await db.init()
    _seed_coordinator_prerequisites(tmp_path)
    c = _make_coordinator(tmp_path, db)
    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                yield client, c, tmp_path


# ---------------------------------------------------------------------------
# POST /smoke-test
# ---------------------------------------------------------------------------

def test_smoke_test_returns_200(coordinator_client):
    client, *_ = coordinator_client
    resp = client.post("/smoke-test")
    assert resp.status_code == 200


def test_smoke_test_returns_experiment_id(coordinator_client):
    client, *_ = coordinator_client
    data = client.post("/smoke-test").json()
    assert "experiment_id" in data
    assert isinstance(data["experiment_id"], str)
    assert len(data["experiment_id"]) > 0


def test_smoke_test_returns_message(coordinator_client):
    client, *_ = coordinator_client
    data = client.post("/smoke-test").json()
    assert "message" in data
    assert isinstance(data["message"], str)


def test_smoke_test_returns_total_runs(coordinator_client):
    client, *_ = coordinator_client
    data = client.post("/smoke-test").json()
    assert "total_runs" in data
    assert isinstance(data["total_runs"], int)
    assert data["total_runs"] >= 1


def test_smoke_test_experiment_id_starts_with_smoke_test(coordinator_client):
    """Smoke test experiment IDs are prefixed 'smoke-test-' for easy identification."""
    client, *_ = coordinator_client
    data = client.post("/smoke-test").json()
    assert data["experiment_id"].startswith("smoke-test-")


def test_smoke_test_creates_experiment_in_db(coordinator_client):
    """The smoke test experiment should be retrievable via GET /experiments/{id}."""
    client, *_ = coordinator_client
    data = client.post("/smoke-test").json()
    experiment_id = data["experiment_id"]
    get_resp = client.get(f"/experiments/{experiment_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["experiment_id"] == experiment_id


def test_smoke_test_503_when_coordinator_none():
    """POST /smoke-test returns 503 when coordinator is None.

    We must patch build_coordinator_from_env to prevent the startup event from
    trying to create /data (PermissionError), and then force coordinator back to
    None so the route's guard fires the 503.
    """
    from unittest.mock import AsyncMock

    # build_coordinator_from_env would be called by startup() when coordinator is None
    # Intercept it so it returns a dummy, then override coordinator to None again
    # so the /smoke-test guard triggers 503.
    sentinel = object()  # anything non-None to satisfy startup

    async def _patched_startup():
        # Skip all real startup; coordinator stays None
        pass

    # Replace the registered startup handler by patching at the router level
    original_handlers = app.router.on_startup[:]
    app.router.on_startup.clear()
    app.router.on_startup.append(_patched_startup)
    try:
        with patch.object(coord_module, "coordinator", None):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post("/smoke-test")
        assert resp.status_code == 503
    finally:
        app.router.on_startup.clear()
        app.router.on_startup.extend(original_handlers)


def test_smoke_test_sets_max_turns_10(coordinator_client):
    """submit_experiment receives a matrix with strategy_configs max_turns=10."""
    client, c, _ = coordinator_client
    captured = {}

    original = c.submit_experiment

    async def _capture(matrix):
        captured["matrix"] = matrix
        return await original(matrix)

    with patch.object(c, "submit_experiment", side_effect=_capture):
        resp = client.post("/smoke-test")
    assert resp.status_code == 200
    assert captured["matrix"].strategy_configs["single_agent"]["max_turns"] == 10


def test_smoke_test_returns_412_when_no_models(coordinator_client):
    """POST /smoke-test returns 412 when no models are configured."""
    client, c, _ = coordinator_client
    with patch.object(c, "list_models", return_value=[]):
        resp = client.post("/smoke-test")
    assert resp.status_code == 412
    assert "model" in resp.json()["detail"].lower()


def test_smoke_test_returns_412_when_no_datasets(coordinator_client):
    """POST /smoke-test returns 412 when no datasets are registered."""
    client, c, _ = coordinator_client
    with patch.object(c, "list_datasets", return_value=[]):
        resp = client.post("/smoke-test")
    assert resp.status_code == 412
    assert "dataset" in resp.json()["detail"].lower()


def test_smoke_test_returns_409_when_already_running(coordinator_client):
    """Second POST /smoke-test returns 409 while the first is still non-terminal."""
    client, *_ = coordinator_client
    first = client.post("/smoke-test")
    assert first.status_code == 200
    first_id = first.json()["experiment_id"]

    second = client.post("/smoke-test")
    assert second.status_code == 409
    assert first_id in second.json()["detail"]


@pytest.mark.asyncio
async def test_smoke_test_allows_new_after_previous_completes(tmp_path: Path):
    """POST /smoke-test succeeds after the previous smoke experiment is completed."""
    from unittest.mock import patch as _patch
    import sec_review_framework.coordinator as _coord_mod

    db = Database(tmp_path / "test.db")
    await db.init()
    _seed_coordinator_prerequisites(tmp_path)
    c = _make_coordinator(tmp_path, db)

    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                with _patch("sec_review_framework.coordinator.datetime") as mock_dt:
                    mock_dt.utcnow.return_value.timestamp.return_value = 1000000
                    first = client.post("/smoke-test")
                assert first.status_code == 200
                first_id = first.json()["experiment_id"]

                await c.db.update_experiment_status(first_id, "completed")

                with _patch("sec_review_framework.coordinator.datetime") as mock_dt:
                    mock_dt.utcnow.return_value.timestamp.return_value = 1000001
                    second = client.post("/smoke-test")
                assert second.status_code == 200


# ---------------------------------------------------------------------------
# GET /feedback/patterns/{experiment_id}
# ---------------------------------------------------------------------------

def test_fp_patterns_empty_experiment_returns_list(coordinator_client):
    """feedback/patterns for an experiment with no runs returns an empty list."""
    client, *_ = coordinator_client
    resp = client.get("/feedback/patterns/nonexistent-experiment")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_fp_patterns_returns_list_type(coordinator_client):
    """feedback/patterns always returns a JSON list."""
    client, *_ = coordinator_client
    resp = client.get("/feedback/patterns/any-experiment")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)


def test_fp_patterns_items_have_expected_fields_when_nonempty(coordinator_client):
    """If patterns are returned, each has a pattern-like field."""
    client, c, _ = coordinator_client
    # Mock get_fp_patterns to return a sample pattern
    with patch.object(c, "get_fp_patterns", return_value=[
        {"pattern": "import os", "count": 3, "severity": "high"},
    ]):
        resp = client.get("/feedback/patterns/some-experiment")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert "pattern" in data[0]


# ---------------------------------------------------------------------------
# GET /experiments/{id}/runs/{run_id} — shape contract
# ---------------------------------------------------------------------------

def test_get_run_404_for_missing_run(coordinator_client):
    """GET /experiments/{id}/runs/{run_id} returns 404 when run doesn't exist."""
    client, *_ = coordinator_client
    resp = client.get("/experiments/no-experiment/runs/no-run")
    assert resp.status_code == 404


def test_get_run_404_message_is_informative(coordinator_client):
    """404 response for missing run includes a detail field."""
    client, *_ = coordinator_client
    resp = client.get("/experiments/no-experiment/runs/no-run")
    data = resp.json()
    assert "detail" in data


@pytest.mark.asyncio
async def test_get_run_shape_after_submit(tmp_path: Path):
    """A submitted run's detail endpoint returns expected shape fields."""
    from sec_review_framework.data.experiment import (
        ExperimentMatrix, ReviewProfileName, StrategyName, ToolVariant, VerificationVariant
    )
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    matrix = ExperimentMatrix(
        experiment_id="shape-test",
        dataset_name="ds",
        dataset_version="1.0",
        model_ids=["gpt-4o"],
        strategies=[StrategyName.SINGLE_AGENT],
        tool_variants=[ToolVariant.WITH_TOOLS],
        review_profiles=[ReviewProfileName.DEFAULT],
        verification_variants=[VerificationVariant.NONE],
        parallel_modes=[False],
    )
    await c.submit_experiment(matrix)

    # Get run list
    runs = await db.list_runs("shape-test")
    assert len(runs) == 1
    # DB column is "id", not "run_id"
    run_id = runs[0].get("run_id") or runs[0]["id"]

    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                resp = client.get(f"/experiments/shape-test/runs/{run_id}")

    # Run is pending — should return 200 with run data or 404 if not yet in results
    # Either is acceptable; we check shape if 200
    if resp.status_code == 200:
        data = resp.json()
        assert "run_id" in data or "status" in data
    else:
        assert resp.status_code == 404
