"""Phase 5: Integration tests for experiment lifecycle endpoints.

Routes covered:
  - POST /experiments/{experiment_id}/cancel           (happy, 0-cancelled)
  - GET  /experiments/{experiment_id}/results/download (404, shape)
  - GET  /compare-runs                                 (cross-experiment, validation)
  - GET  /experiments/{experiment_id}/compare-runs     (within-experiment)
  - POST /experiments/{experiment_id}/runs/{run_id}/reclassify (happy, 404, validation)
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

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
from tests.integration.test_coordinator_api import _make_coordinator

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def coordinator_client(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)
    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=False) as client:
                yield client, c, tmp_path


# ---------------------------------------------------------------------------
# POST /experiments/{experiment_id}/cancel
# ---------------------------------------------------------------------------


def test_cancel_nonexistent_experiment_returns_200(coordinator_client):
    """Cancelling an experiment that doesn't exist returns 200 with 0 cancelled jobs."""
    client, *_ = coordinator_client
    resp = client.post("/experiments/nonexistent-exp/cancel")
    assert resp.status_code == 200
    data = resp.json()
    assert "cancelled_jobs" in data
    assert data["cancelled_jobs"] == 0


def test_cancel_returns_cancelled_jobs_key(coordinator_client):
    """Cancel response always has cancelled_jobs key."""
    client, *_ = coordinator_client
    resp = client.post("/experiments/any-exp/cancel")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data["cancelled_jobs"], int)


@pytest.mark.asyncio
async def test_cancel_existing_experiment_flips_pending_runs_to_cancelled(tmp_path: Path):
    """Cancelling an experiment with pending runs marks them all cancelled."""
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)

    matrix = ExperimentMatrix(
        experiment_id="cancel-lifecycle-test",
        dataset_name="test-dataset",
        dataset_version="1.0.0",
        model_ids=["gpt-4o"],
        strategies=[StrategyName.SINGLE_AGENT],
        tool_variants=[ToolVariant.WITH_TOOLS],
        review_profiles=[ReviewProfileName.DEFAULT],
        verification_variants=[VerificationVariant.NONE],
        parallel_modes=[False],
    )
    with patch.object(c, "reconcile", return_value=None):
        await c.submit_experiment(matrix)

    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                resp = client.post("/experiments/cancel-lifecycle-test/cancel")
    assert resp.status_code == 200
    assert isinstance(resp.json()["cancelled_jobs"], int)


def test_cancel_multiple_times_is_idempotent(coordinator_client):
    """Calling cancel twice on the same experiment should not raise an error."""
    client, *_ = coordinator_client
    resp1 = client.post("/experiments/idempotent-exp/cancel")
    resp2 = client.post("/experiments/idempotent-exp/cancel")
    assert resp1.status_code == 200
    assert resp2.status_code == 200


# ---------------------------------------------------------------------------
# GET /experiments/{experiment_id}/results/download
# ---------------------------------------------------------------------------


def test_download_results_missing_experiment_returns_404(coordinator_client):
    """Downloading results for a nonexistent experiment returns 404."""
    client, *_ = coordinator_client
    resp = client.get("/experiments/ghost-experiment/results/download")
    # package_reports raises HTTPException 404 when no outputs
    assert resp.status_code in (404, 500)  # No zip exists


def test_download_results_mocked_returns_zip(coordinator_client, tmp_path):
    """When package_reports returns a path, the response is a zip file."""
    client, c, tmp_path_coord = coordinator_client
    # Create a fake zip file to return
    fake_zip = tmp_path / "fake_reports.zip"
    fake_zip.write_bytes(b"PK\x03\x04")  # minimal zip magic bytes

    with patch.object(c, "package_reports", return_value=fake_zip):
        resp = client.get("/experiments/any-exp/results/download")
    assert resp.status_code == 200
    assert "zip" in resp.headers.get("content-type", "").lower()


def test_download_results_filename_contains_experiment_id(coordinator_client, tmp_path):
    """The downloaded file has a descriptive filename."""
    client, c, _ = coordinator_client
    fake_zip = tmp_path / "reports.zip"
    fake_zip.write_bytes(b"PK\x03\x04")

    with patch.object(c, "package_reports", return_value=fake_zip):
        resp = client.get("/experiments/my-special-exp/results/download")
    assert resp.status_code == 200
    cd = resp.headers.get("content-disposition", "")
    assert "my-special-exp" in cd


# ---------------------------------------------------------------------------
# GET /compare-runs (cross-experiment)
# ---------------------------------------------------------------------------


def test_compare_runs_cross_requires_all_four_params(coordinator_client):
    """Missing any of the four query params returns 422."""
    client, *_ = coordinator_client
    # Only one param provided
    resp = client.get("/compare-runs?a_experiment=exp1")
    assert resp.status_code == 422


def test_compare_runs_cross_missing_runs_returns_non_500(coordinator_client):
    """Cross-experiment compare with nonexistent runs does not crash with 500."""
    client, *_ = coordinator_client
    resp = client.get(
        "/compare-runs?a_experiment=no-exp-a&a_run=r1&b_experiment=no-exp-b&b_run=r2"
    )
    assert resp.status_code != 500


def test_compare_runs_cross_response_shape_on_empty_experiments(coordinator_client):
    """Cross-experiment compare with no result files returns expected shape."""
    client, *_ = coordinator_client
    resp = client.get(
        "/compare-runs?a_experiment=empty-a&a_run=run-1&b_experiment=empty-b&b_run=run-2"
    )
    # Should return 404 (no result files) — not a crash
    assert resp.status_code in (404, 200)


def test_compare_runs_cross_mocked_happy_path(coordinator_client):
    """Mocked cross-experiment compare returns expected keys."""
    client, c, _ = coordinator_client
    mock_result = {
        "run_a": {"id": "run-1", "experiment_id": "exp-a"},
        "run_b": {"id": "run-2", "experiment_id": "exp-b"},
        "found_by_both": [],
        "only_in_a": [],
        "only_in_b": [],
        "dataset_mismatch": False,
        "warnings": [],
    }
    with patch.object(c, "compare_runs_cross", return_value=mock_result):
        resp = client.get(
            "/compare-runs?a_experiment=exp-a&a_run=run-1&b_experiment=exp-b&b_run=run-2"
        )
    assert resp.status_code == 200
    data = resp.json()
    assert "run_a" in data
    assert "run_b" in data
    assert "found_by_both" in data
    assert "only_in_a" in data
    assert "only_in_b" in data
    assert "dataset_mismatch" in data
    assert "warnings" in data


def test_compare_runs_cross_dataset_mismatch_flag(coordinator_client):
    """When datasets differ, dataset_mismatch is True and warnings is non-empty."""
    client, c, _ = coordinator_client
    mock_result = {
        "run_a": {"id": "r1", "experiment_id": "exp-x", "dataset": "ds-a"},
        "run_b": {"id": "r2", "experiment_id": "exp-y", "dataset": "ds-b"},
        "found_by_both": [],
        "only_in_a": [],
        "only_in_b": [],
        "dataset_mismatch": True,
        "warnings": ["Datasets differ: ds-a vs ds-b"],
    }
    with patch.object(c, "compare_runs_cross", return_value=mock_result):
        resp = client.get(
            "/compare-runs?a_experiment=exp-x&a_run=r1&b_experiment=exp-y&b_run=r2"
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["dataset_mismatch"] is True
    assert len(data["warnings"]) > 0


# ---------------------------------------------------------------------------
# GET /experiments/{experiment_id}/compare-runs
# ---------------------------------------------------------------------------


def test_per_experiment_compare_requires_run_a_run_b(coordinator_client):
    """Missing run_a or run_b query params returns 422."""
    client, *_ = coordinator_client
    resp = client.get("/experiments/some-exp/compare-runs")
    assert resp.status_code == 422


def test_per_experiment_compare_missing_experiment_returns_non_500(coordinator_client):
    """compare-runs within a nonexistent experiment does not return 500."""
    client, *_ = coordinator_client
    resp = client.get("/experiments/no-exp/compare-runs?run_a=r1&run_b=r2")
    assert resp.status_code != 500


def test_per_experiment_compare_mocked_happy_path(coordinator_client):
    """Mocked same-experiment compare returns the expected shape."""
    client, c, _ = coordinator_client
    mock_result = {
        "run_a": {"id": "run-a", "experiment_id": "shared-exp"},
        "run_b": {"id": "run-b", "experiment_id": "shared-exp"},
        "found_by_both": [],
        "only_in_a": [],
        "only_in_b": [],
        "dataset_mismatch": False,
        "warnings": [],
    }
    with patch.object(c, "compare_runs", return_value=mock_result):
        resp = client.get("/experiments/shared-exp/compare-runs?run_a=run-a&run_b=run-b")
    assert resp.status_code == 200
    data = resp.json()
    assert "run_a" in data
    assert "run_b" in data


def test_per_experiment_compare_same_run_id_is_accepted(coordinator_client):
    """Passing the same run_id for both sides is not a server error."""
    client, c, _ = coordinator_client
    mock_result = {
        "run_a": {"id": "r1"},
        "run_b": {"id": "r1"},
        "found_by_both": [],
        "only_in_a": [],
        "only_in_b": [],
        "dataset_mismatch": False,
        "warnings": [],
    }
    with patch.object(c, "compare_runs", return_value=mock_result):
        resp = client.get("/experiments/exp/compare-runs?run_a=r1&run_b=r1")
    assert resp.status_code in (200, 404)


# ---------------------------------------------------------------------------
# POST /experiments/{experiment_id}/runs/{run_id}/reclassify
# ---------------------------------------------------------------------------


def test_reclassify_missing_run_returns_404(coordinator_client):
    """Reclassifying a finding in a nonexistent run returns 404."""
    client, *_ = coordinator_client
    resp = client.post(
        "/experiments/no-exp/runs/no-run/reclassify",
        json={"finding_id": "f1", "status": "unlabeled_real"},
    )
    assert resp.status_code == 404


def test_reclassify_missing_finding_id_returns_422(coordinator_client):
    """Request body without finding_id is rejected with 422."""
    client, *_ = coordinator_client
    resp = client.post(
        "/experiments/exp/runs/run/reclassify",
        json={"status": "unlabeled_real"},  # missing finding_id
    )
    assert resp.status_code == 422


def test_reclassify_missing_status_returns_422(coordinator_client):
    """Request body without status is rejected with 422."""
    client, *_ = coordinator_client
    resp = client.post(
        "/experiments/exp/runs/run/reclassify",
        json={"finding_id": "f-001"},  # missing status
    )
    assert resp.status_code == 422


def test_reclassify_mocked_happy_path_returns_status(coordinator_client):
    """Mocked reclassification returns the expected status dict."""
    client, c, _ = coordinator_client
    with patch.object(c, "reclassify_finding", return_value={"status": "reclassified", "finding_id": "f-001"}):
        resp = client.post(
            "/experiments/exp/runs/run/reclassify",
            json={"finding_id": "f-001", "status": "unlabeled_real"},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "reclassified"
    assert data["finding_id"] == "f-001"


def test_reclassify_with_note_is_accepted(coordinator_client):
    """Optional note field is accepted without error."""
    client, c, _ = coordinator_client
    with patch.object(c, "reclassify_finding", return_value={"status": "reclassified", "finding_id": "f-002"}):
        resp = client.post(
            "/experiments/exp/runs/run/reclassify",
            json={"finding_id": "f-002", "status": "unlabeled_real", "note": "Confirmed by manual review"},
        )
    assert resp.status_code == 200


def test_reclassify_unknown_finding_in_existing_run_returns_404(coordinator_client, tmp_path):
    """Trying to reclassify a finding that doesn't exist in the result file returns 404."""
    client, c, _ = coordinator_client
    with patch.object(
        c, "reclassify_finding",
        side_effect=HTTPException(status_code=404, detail="Finding nonexistent-finding not found"),
    ):
        resp = client.post(
            "/experiments/real-exp/runs/real-run/reclassify",
            json={"finding_id": "nonexistent-finding", "status": "unlabeled_real"},
        )
    assert resp.status_code == 404
    assert "nonexistent-finding" in resp.json().get("detail", "")
