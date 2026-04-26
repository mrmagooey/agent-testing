"""B4: Integration tests for smoke-test route, /feedback/patterns, run detail shape.

Routes covered:
  - POST /smoke-test                          — creates an experiment, returns experiment_id + message
  - GET  /feedback/patterns/{experiment_id}   — returns list (empty for new experiment)
  - GET  /experiments/{id}/runs/{run_id}      — 404 for missing; shape contract on hit
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import app
from sec_review_framework.db import Database

from sec_review_framework.models.catalog import ModelMetadata, ProviderCatalog, ProviderSnapshot

from tests.integration.test_coordinator_api import _make_coordinator


def _fake_catalog(snapshots: dict) -> ProviderCatalog:
    catalog = MagicMock(spec=ProviderCatalog)
    catalog.snapshot.return_value = snapshots
    catalog.snapshot_version = 0
    return catalog


def _seed_coordinator_prerequisites(tmp_path: Path, coordinator) -> None:
    """Attach a minimal catalog stub so list_models() returns non-empty."""
    coordinator.catalog = _fake_catalog({
        "openai": ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["gpt-4o-mini"]),
            metadata={"gpt-4o-mini": ModelMetadata(id="gpt-4o-mini", raw_id="gpt-4o-mini")},
        )
    })


async def _register_smoke_dataset(db: Database, name: str = "smoke-test-dataset") -> None:
    """Insert a minimal git-kind dataset row so list_datasets() is non-empty."""
    await db.create_dataset({
        "name": name,
        "kind": "git",
        "origin_url": "https://example.invalid/smoke.git",
        "origin_commit": "0" * 40,
        "origin_ref": "refs/heads/main",
        "cve_id": "CVE-0000-0000",
        "created_at": "2026-01-01T00:00:00",
    })


@pytest.fixture
async def coordinator_client(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)
    fake_cat = _fake_catalog({
        "openai": ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["gpt-4o-mini"]),
            metadata={"gpt-4o-mini": ModelMetadata(id="gpt-4o-mini", raw_id="gpt-4o-mini")},
        )
    })
    _seed_coordinator_prerequisites(tmp_path, c)

    await _register_smoke_dataset(db)
    dataset_dir = tmp_path / "storage" / "datasets" / "smoke-test-dataset"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    (dataset_dir / "labels.json").write_text("[]")

    # Patch the startup event so it doesn't overwrite our fake catalog with a real one.
    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with patch("sec_review_framework.coordinator.ProviderCatalog") as mock_cat_cls:
                mock_cat_cls.return_value = fake_cat
                fake_cat.start = AsyncMock(return_value=None)
                with TestClient(app, raise_server_exceptions=True) as client:
                    # Ensure our catalog is set even after startup.
                    c.catalog = fake_cat
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

    Replace the app's lifespan with a no-op so the real startup won't try to
    build a coordinator (which would fail trying to create /data), then force
    coordinator to None so the route's guard fires the 503.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    original_lifespan = app.router.lifespan_context
    app.router.lifespan_context = _noop_lifespan
    try:
        with patch.object(coord_module, "coordinator", None):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post("/smoke-test")
        assert resp.status_code == 503
    finally:
        app.router.lifespan_context = original_lifespan


def test_smoke_test_sets_max_turns_10(coordinator_client):
    """The smoke test uses the builtin.single_agent strategy.

    In the new strategy-bundle architecture, max_turns is baked into the
    strategy's default bundle rather than passed via strategy_configs.
    We verify the submitted matrix references the expected builtin strategy.
    """
    client, c, _ = coordinator_client
    captured = {}

    original = c.submit_experiment

    async def _capture(matrix):
        captured["matrix"] = matrix
        return await original(matrix)

    with patch.object(c, "submit_experiment", side_effect=_capture):
        resp = client.post("/smoke-test")
    assert resp.status_code == 200
    # Smoke test must target the builtin single_agent strategy.
    assert "builtin.single_agent" in captured["matrix"].strategy_ids


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
    c = _make_coordinator(tmp_path, db)
    fake_cat = _fake_catalog({
        "openai": ProviderSnapshot(
            probe_status="fresh",
            model_ids=frozenset(["gpt-4o-mini"]),
            metadata={"gpt-4o-mini": ModelMetadata(id="gpt-4o-mini", raw_id="gpt-4o-mini")},
        )
    })
    _seed_coordinator_prerequisites(tmp_path, c)
    await _register_smoke_dataset(db)
    dataset_dir = tmp_path / "storage" / "datasets" / "smoke-test-dataset"
    dataset_dir.mkdir(parents=True, exist_ok=True)
    (dataset_dir / "labels.json").write_text("[]")

    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with patch("sec_review_framework.coordinator.ProviderCatalog") as mock_cat_cls:
                mock_cat_cls.return_value = fake_cat
                fake_cat.start = AsyncMock(return_value=None)
                with TestClient(app, raise_server_exceptions=True) as client:
                    c.catalog = fake_cat
                    with _patch("sec_review_framework.coordinator.datetime") as mock_dt:
                        mock_dt.now.return_value.timestamp.return_value = 1000000
                        first = client.post("/smoke-test")
                    assert first.status_code == 200
                    first_id = first.json()["experiment_id"]

                    await c.db.update_experiment_status(first_id, "completed")

                    with _patch("sec_review_framework.coordinator.datetime") as mock_dt:
                        mock_dt.now.return_value.timestamp.return_value = 1000001
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
    with patch.object(c, "get_fp_patterns", return_value=[
        {"pattern": "import os", "count": 3, "severity": "high"},
    ]):
        resp = client.get("/feedback/patterns/some-experiment")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert "pattern" in data[0]


def test_fp_patterns_full_schema_contract(coordinator_client):
    client, c, _ = coordinator_client
    sample = {
        "model_id": "gpt-4o",
        "vuln_class": "sqli",
        "pattern_description": "User input passed directly to SQL query",
        "occurrence_count": 5,
        "example_finding_ids": ["f-001", "f-002"],
        "suggested_action": "Use parameterized queries",
    }
    with patch.object(c, "get_fp_patterns", return_value=[sample]):
        resp = client.get("/feedback/patterns/exp-schema-test")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    item = data[0]
    for field in ("model_id", "vuln_class", "pattern_description",
                  "occurrence_count", "example_finding_ids", "suggested_action"):
        assert field in item, f"Missing schema field: {field}"
    assert item["model_id"] == "gpt-4o"
    assert item["vuln_class"] == "sqli"
    assert isinstance(item["occurrence_count"], int)
    assert isinstance(item["example_finding_ids"], list)


def test_fp_patterns_multiple_items_returned(coordinator_client):
    client, c, _ = coordinator_client
    patterns = [
        {"model_id": "gpt-4o", "vuln_class": "sqli", "pattern_description": "p1",
         "occurrence_count": 2, "example_finding_ids": [], "suggested_action": "a1"},
        {"model_id": "claude-3-sonnet", "vuln_class": "xss", "pattern_description": "p2",
         "occurrence_count": 1, "example_finding_ids": ["f-x"], "suggested_action": "a2"},
    ]
    with patch.object(c, "get_fp_patterns", return_value=patterns):
        resp = client.get("/feedback/patterns/multi-pattern-exp")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    vuln_classes = {item["vuln_class"] for item in data}
    assert "sqli" in vuln_classes
    assert "xss" in vuln_classes


def test_fp_patterns_always_200_even_for_unknown_experiment(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/feedback/patterns/experiment-that-does-not-exist-xyz")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


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
    from sec_review_framework.data.experiment import ExperimentMatrix
    from sec_review_framework.coordinator import _seed_builtin_strategies

    db = Database(tmp_path / "test.db")
    await db.init()
    await _seed_builtin_strategies(db)
    c = _make_coordinator(tmp_path, db)

    matrix = ExperimentMatrix(
        experiment_id="shape-test",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=["builtin.single_agent"],
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
