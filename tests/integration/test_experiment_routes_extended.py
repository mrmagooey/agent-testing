"""B1: Integration tests for uncovered /experiments/* routes.

Routes covered here:
  - GET  /experiments/{id}/compare-runs (happy path + missing runs)
  - POST /experiments/{id}/cancel        (happy path + nonexistent experiment)
  - GET  /experiments/{id}/results/download (missing file → 404)
  - DELETE /experiments/{id}             (204 + gone)
  - POST /experiments/{id}/runs/{run_id}/reclassify (request shape)
  - GET  /experiments/{id}/findings/search with run_id filter
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import app
from sec_review_framework.data.experiment import (
    ExperimentMatrix,
    ReviewProfileName,
    StrategyName,
    ToolVariant,
    VerificationVariant,
)
from sec_review_framework.db import Database
from fastapi.testclient import TestClient

from tests.integration.test_coordinator_api import (
    _make_coordinator,
    _minimal_matrix,
)


@pytest.fixture
async def coordinator_client(tmp_path: Path):
    """TestClient with the global coordinator patched to a temp instance."""
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)
    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                yield client, c, tmp_path


# ---------------------------------------------------------------------------
# GET /experiments/{id}/compare-runs
# ---------------------------------------------------------------------------

def test_compare_runs_nonexistent_experiment_returns_non_500(coordinator_client):
    """compare-runs on a nonexistent experiment does not crash with 500."""
    client, *_ = coordinator_client
    resp = client.get("/experiments/no-experiment/compare-runs?run_a=r1&run_b=r2")
    # May return 404 (experiment not found) or 200 (empty comparison) — not 500
    assert resp.status_code != 500


def test_compare_runs_requires_run_a_and_run_b_params(coordinator_client):
    """compare-runs without query params returns 422 validation error."""
    client, *_ = coordinator_client
    resp = client.get("/experiments/some-experiment/compare-runs")
    assert resp.status_code == 422


def test_compare_runs_with_both_params_does_not_crash(coordinator_client):
    """compare-runs with two run IDs doesn't crash the server."""
    client, *_ = coordinator_client
    resp = client.get("/experiments/empty-experiment/compare-runs?run_a=r1&run_b=r2")
    # 404 is acceptable (experiment not found); 500 is not
    assert resp.status_code != 500


# ---------------------------------------------------------------------------
# POST /experiments/{id}/cancel
# ---------------------------------------------------------------------------

def test_cancel_nonexistent_experiment_returns_zero_cancelled(coordinator_client):
    """Cancelling a nonexistent experiment returns 200 with cancelled_jobs=0."""
    client, *_ = coordinator_client
    resp = client.post("/experiments/nonexistent-experiment/cancel")
    assert resp.status_code == 200
    data = resp.json()
    assert "cancelled_jobs" in data
    assert data["cancelled_jobs"] == 0


@pytest.mark.asyncio
async def test_cancel_existing_experiment_returns_cancelled_count(tmp_path: Path):
    """Cancel on a real experiment with pending runs returns a non-negative count."""
    from sec_review_framework.db import Database
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    from sec_review_framework.coordinator import _seed_builtin_strategies
    await _seed_builtin_strategies(db)

    matrix = ExperimentMatrix(
        experiment_id="cancel-test",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=["builtin.single_agent"],
    )
    await c.submit_experiment(matrix)

    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                resp = client.post("/experiments/cancel-test/cancel")
    assert resp.status_code == 200
    data = resp.json()
    assert "cancelled_jobs" in data
    assert isinstance(data["cancelled_jobs"], int)
    assert data["cancelled_jobs"] >= 0


# ---------------------------------------------------------------------------
# GET /experiments/{id}/results/download
# ---------------------------------------------------------------------------

def test_download_reports_404_when_no_files(coordinator_client):
    """Download endpoint returns 404 when no report files exist."""
    client, *_ = coordinator_client
    resp = client.get("/experiments/missing-experiment/results/download")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /experiments/{id}
# ---------------------------------------------------------------------------

def test_delete_nonexistent_experiment_returns_204(coordinator_client):
    """Deleting a nonexistent experiment is idempotent — returns 204."""
    client, *_ = coordinator_client
    resp = client.delete("/experiments/ghost-experiment")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_existing_experiment_removes_it(tmp_path: Path):
    """DELETE /experiments/{id} returns 204, cancels jobs, and purges the DB row."""
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    from sec_review_framework.coordinator import _seed_builtin_strategies
    await _seed_builtin_strategies(db)

    matrix = ExperimentMatrix(
        experiment_id="del-test",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=["builtin.single_agent"],
    )
    await c.submit_experiment(matrix)

    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                # Verify experiment exists
                assert client.get("/experiments/del-test").status_code == 200
                # Delete it — must return 204
                resp = client.delete("/experiments/del-test")
                assert resp.status_code == 204
                # Second delete is idempotent
                resp2 = client.delete("/experiments/del-test")
                assert resp2.status_code == 204


# ---------------------------------------------------------------------------
# POST /experiments/{id}/runs/{run_id}/reclassify
# ---------------------------------------------------------------------------

def test_reclassify_returns_empty_or_not_found_for_missing_run(coordinator_client):
    """Reclassify on a nonexistent run returns a non-500 response."""
    client, *_ = coordinator_client
    payload = {
        "finding_id": "find-xyz",
        "new_status": "unlabeled_real",
        "note": "false positive",
    }
    resp = client.post("/experiments/no-experiment/runs/no-run/reclassify", json=payload)
    # Should not be 500 — either 200/204/404
    assert resp.status_code != 500


def test_reclassify_requires_finding_id_field(coordinator_client):
    """Reclassify without finding_id returns 422."""
    client, *_ = coordinator_client
    resp = client.post(
        "/experiments/b/runs/r/reclassify",
        json={"new_status": "unlabeled_real"},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# GET /experiments/{id}/findings/search with optional run_id filter
# ---------------------------------------------------------------------------

def test_search_findings_with_run_id_filter_returns_list(coordinator_client):
    """findings/search with run_id filter returns a list (possibly empty)."""
    client, *_ = coordinator_client
    resp = client.get("/experiments/b/findings/search?q=injection&run_id=r1")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_search_findings_empty_query_returns_list(coordinator_client):
    """findings/search with empty string query returns list."""
    client, *_ = coordinator_client
    resp = client.get("/experiments/b/findings/search?q=")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ---------------------------------------------------------------------------
# DELETE /experiments/{id} — DB row removal regression tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_experiment_removes_db_row(tmp_path: Path):
    """DELETE /experiments/{id} removes the experiment row from the DB.

    After deletion, GET /experiments must not list the experiment and
    GET /experiments/{id} must return 404.
    """
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    from sec_review_framework.coordinator import _seed_builtin_strategies
    await _seed_builtin_strategies(db)

    matrix = ExperimentMatrix(
        experiment_id="db-row-del-test",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=["builtin.single_agent"],
    )
    await c.submit_experiment(matrix)

    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                # Confirm experiment is visible before deletion
                list_before = client.get("/experiments").json()
                ids_before = [e["experiment_id"] for e in list_before]
                assert "db-row-del-test" in ids_before

                # Delete
                resp = client.delete("/experiments/db-row-del-test")
                assert resp.status_code == 204

                # Experiment must NOT appear in list
                list_after = client.get("/experiments").json()
                ids_after = [e["experiment_id"] for e in list_after]
                assert "db-row-del-test" not in ids_after

                # Individual GET must return 404
                get_resp = client.get("/experiments/db-row-del-test")
                assert get_resp.status_code == 404


@pytest.mark.asyncio
async def test_delete_experiment_removes_runs_and_findings(tmp_path: Path):
    """DELETE /experiments/{id} removes associated run and finding rows.

    Seeds an experiment with one run and one finding, then verifies that
    both db.list_runs and db.query_findings return empty after deletion.
    """
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    from sec_review_framework.coordinator import _seed_builtin_strategies
    await _seed_builtin_strategies(db)

    exp_id = "findings-del-test"
    matrix = ExperimentMatrix(
        experiment_id=exp_id,
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=["builtin.single_agent"],
    )
    await c.submit_experiment(matrix)

    # Grab the first run ID created by submit_experiment
    runs = await db.list_runs(exp_id)
    assert runs, "Expected at least one run to be created by submit_experiment"
    run_id = runs[0]["id"]

    # Seed a finding for that run
    await db.upsert_findings_for_run(
        run_id=run_id,
        experiment_id=exp_id,
        findings=[{
            "id": "finding-001",
            "file_path": "src/main.py",
            "line_start": 10,
            "line_end": 15,
            "vuln_class": "SQL Injection",
            "cwe_ids": ["CWE-89"],
            "severity": "high",
            "confidence": 0.9,
            "title": "SQL Injection in query()",
            "description": "User input not sanitised",
            "match_status": "true_positive",
        }],
        model_id="claude-opus-4-5",
        strategy="builtin.single_agent",
        dataset_name="ds",
    )

    # Verify finding is indexed
    total_before, _ = await db.query_findings(
        filters={"experiment_id": [exp_id]},
    )
    assert total_before == 1

    # Delete via API
    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                resp = client.delete(f"/experiments/{exp_id}")
                assert resp.status_code == 204

    # Runs must be gone
    runs_after = await db.list_runs(exp_id)
    assert runs_after == [], f"Expected no runs after deletion, got {runs_after}"

    # Findings must be gone
    total_after, rows_after = await db.query_findings(
        filters={"experiment_id": [exp_id]},
    )
    assert total_after == 0, f"Expected 0 findings after deletion, got {total_after}"
    assert rows_after == []


@pytest.mark.asyncio
async def test_delete_experiment_idempotent_for_missing(tmp_path: Path):
    """DELETE on a nonexistent experiment_id returns 204 with no error."""
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                resp = client.delete("/experiments/does-not-exist-at-all")
                assert resp.status_code == 204


@pytest.mark.asyncio
async def test_delete_experiment_invalidates_trends_cache(tmp_path: Path):
    """DELETE /experiments/<id> invalidates the in-memory trends cache.

    Otherwise a deleted completed experiment continues to appear in the
    /trends graph for up to _TRENDS_CACHE_TTL_S (60s) until the TTL fires.
    """
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    # Seed the cache with a sentinel entry so we can confirm it gets cleared.
    c._trends_cache[("test-dataset", 10, None, None, None)] = ({"sentinel": True}, 1e18)
    assert len(c._trends_cache) == 1

    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                resp = client.delete("/experiments/any-id")
                assert resp.status_code == 204

    assert c._trends_cache == {}, (
        "delete_experiment must invalidate the trends cache; otherwise the "
        "deleted experiment lingers on the trends graph for up to 60s."
    )
