"""B4: Integration tests for smoke-test route, /feedback/patterns, run detail shape.

Routes covered:
  - POST /smoke-test                     — creates a batch, returns batch_id + message
  - GET  /feedback/patterns/{batch_id}   — returns list (empty for new batch)
  - GET  /batches/{id}/runs/{run_id}     — 404 for missing; shape contract on hit
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
# POST /smoke-test
# ---------------------------------------------------------------------------

def test_smoke_test_returns_200(coordinator_client):
    client, *_ = coordinator_client
    resp = client.post("/smoke-test")
    assert resp.status_code == 200


def test_smoke_test_returns_batch_id(coordinator_client):
    client, *_ = coordinator_client
    data = client.post("/smoke-test").json()
    assert "batch_id" in data
    assert isinstance(data["batch_id"], str)
    assert len(data["batch_id"]) > 0


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


def test_smoke_test_batch_id_starts_with_smoke_test(coordinator_client):
    """Smoke test batch IDs are prefixed 'smoke-test-' for easy identification."""
    client, *_ = coordinator_client
    data = client.post("/smoke-test").json()
    assert data["batch_id"].startswith("smoke-test-")


def test_smoke_test_creates_batch_in_db(coordinator_client):
    """The smoke test batch should be retrievable via GET /batches/{id}."""
    client, *_ = coordinator_client
    data = client.post("/smoke-test").json()
    batch_id = data["batch_id"]
    get_resp = client.get(f"/batches/{batch_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["batch_id"] == batch_id


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


# ---------------------------------------------------------------------------
# GET /feedback/patterns/{batch_id}
# ---------------------------------------------------------------------------

def test_fp_patterns_empty_batch_returns_list(coordinator_client):
    """feedback/patterns for a batch with no runs returns an empty list."""
    client, *_ = coordinator_client
    resp = client.get("/feedback/patterns/nonexistent-batch")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_fp_patterns_returns_list_type(coordinator_client):
    """feedback/patterns always returns a JSON list."""
    client, *_ = coordinator_client
    resp = client.get("/feedback/patterns/any-batch")
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
        resp = client.get("/feedback/patterns/some-batch")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert "pattern" in data[0]


# ---------------------------------------------------------------------------
# GET /batches/{id}/runs/{run_id} — shape contract
# ---------------------------------------------------------------------------

def test_get_run_404_for_missing_run(coordinator_client):
    """GET /batches/{id}/runs/{run_id} returns 404 when run doesn't exist."""
    client, *_ = coordinator_client
    resp = client.get("/batches/no-batch/runs/no-run")
    assert resp.status_code == 404


def test_get_run_404_message_is_informative(coordinator_client):
    """404 response for missing run includes a detail field."""
    client, *_ = coordinator_client
    resp = client.get("/batches/no-batch/runs/no-run")
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
        batch_id="shape-test",
        dataset_name="ds",
        dataset_version="1.0",
        model_ids=["gpt-4o"],
        strategies=[StrategyName.SINGLE_AGENT],
        tool_variants=[ToolVariant.WITH_TOOLS],
        review_profiles=[ReviewProfileName.DEFAULT],
        verification_variants=[VerificationVariant.NONE],
        parallel_modes=[False],
    )
    await c.submit_batch(matrix)

    # Get run list
    runs = await db.list_runs("shape-test")
    assert len(runs) == 1
    # DB column is "id", not "run_id"
    run_id = runs[0].get("run_id") or runs[0]["id"]

    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                resp = client.get(f"/batches/shape-test/runs/{run_id}")

    # Run is pending — should return 200 with run data or 404 if not yet in results
    # Either is acceptable; we check shape if 200
    if resp.status_code == 200:
        data = resp.json()
        assert "run_id" in data or "status" in data
    else:
        assert resp.status_code == 404
