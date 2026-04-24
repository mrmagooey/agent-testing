"""Integration tests for ExperimentCoordinator-level operations.

Replaces the original placeholder. Tests use a real ExperimentCoordinator backed
by a temp SQLite database and temp storage root. K8s is set to None so no
cluster is required.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sec_review_framework.coordinator import ExperimentCoordinator, ExperimentCostTracker
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.data.experiment import (
    BundleSnapshot,
    ExperimentMatrix,
    ExperimentRun,
    ReviewProfileName,
    RunResult,
    RunStatus,
    StrategyName,
    ToolVariant,
    VerificationVariant,
)
from sec_review_framework.data.findings import StrategyOutput
from sec_review_framework.db import Database
from sec_review_framework.reporting.markdown import MarkdownReportGenerator


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    await database.init()
    return database


@pytest.fixture
def cost_calc() -> CostCalculator:
    return CostCalculator(
        pricing={
            "gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0),
            "claude-opus-4": ModelPricing(input_per_million=15.0, output_per_million=75.0),
        }
    )


@pytest.fixture
async def coordinator(tmp_path: Path, db: Database, cost_calc: CostCalculator) -> ExperimentCoordinator:
    storage = tmp_path / "storage"
    storage.mkdir()
    return ExperimentCoordinator(
        k8s_client=None,       # no K8s needed
        storage_root=storage,
        concurrency_caps={},
        worker_image="worker:latest",
        namespace="default",
        db=db,
        reporter=MarkdownReportGenerator(),
        cost_calculator=cost_calc,
        default_cap=4,
    )


def _minimal_matrix(experiment_id: str = "test-experiment") -> ExperimentMatrix:
    return ExperimentMatrix(
        experiment_id=experiment_id,
        dataset_name="test-dataset",
        dataset_version="1.0.0",
        strategy_ids=["builtin.single_agent"],
    )


def _run_result_json(run: ExperimentRun) -> str:
    """Build a minimal RunResult JSON that passes model_validate_json."""
    from tests.helpers import make_test_bundle_snapshot

    result = RunResult(
        experiment=run,
        status=RunStatus.COMPLETED,
        findings=[],
        strategy_output=StrategyOutput(
            findings=[], pre_dedup_count=0, post_dedup_count=0, dedup_log=[]
        ),
        bundle_snapshot=make_test_bundle_snapshot(),
        tool_call_count=0,
        total_input_tokens=100,
        total_output_tokens=50,
        verification_tokens=0,
        estimated_cost_usd=0.01,
        duration_seconds=10.0,
        completed_at=datetime(2026, 4, 16, tzinfo=timezone.utc),
    )
    return result.model_dump_json(indent=2)


# ---------------------------------------------------------------------------
# Test 1: submit_experiment creates experiment + runs in DB
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_submit_experiment_creates_db_records(coordinator, db):
    matrix = _minimal_matrix("experiment-001")
    experiment_id = await coordinator.submit_experiment(matrix)

    assert experiment_id == "experiment-001"

    experiment_row = await db.get_experiment("experiment-001")
    assert experiment_row is not None
    assert experiment_row["total_runs"] == 1
    assert experiment_row["status"] == "pending"

    runs = await db.list_runs("experiment-001")
    assert len(runs) == 1
    # model_id and strategy are now derived from the resolved strategy bundle
    assert runs[0]["model_id"] is not None
    assert runs[0]["strategy"] is not None


@pytest.mark.asyncio
async def test_submit_experiment_multi_dim_expands_correctly(coordinator, db):
    matrix = ExperimentMatrix(
        experiment_id="experiment-multi",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=[
            "builtin.single_agent",
            "builtin.per_file",
            "builtin.per_vuln_class",
            "builtin.sast_first",
        ],
    )
    await coordinator.submit_experiment(matrix)

    runs = await db.list_runs("experiment-multi")
    assert len(runs) == 4  # 4 strategies


# ---------------------------------------------------------------------------
# Test 2: get_experiment_status returns correct counts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_experiment_status_counts(coordinator, db):
    matrix = _minimal_matrix("experiment-status")
    await coordinator.submit_experiment(matrix)

    status = await coordinator.get_experiment_status("experiment-status")
    assert status.experiment_id == "experiment-status"
    assert status.total == 1
    # K8s is None so the run stays pending
    assert status.pending + status.running + status.completed + status.failed == 1


@pytest.mark.asyncio
async def test_get_experiment_status_404_for_unknown(coordinator):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        await coordinator.get_experiment_status("no-such-experiment")
    assert exc_info.value.status_code == 404


# ---------------------------------------------------------------------------
# Test 3: collect_results reads run_result.json files
# ---------------------------------------------------------------------------

def test_collect_results_reads_result_files(coordinator, tmp_path):
    """Write a synthetic run_result.json and verify collect_results picks it up."""
    matrix = _minimal_matrix("experiment-cr")
    runs = matrix.expand()
    run = runs[0]

    output_dir = coordinator.storage_root / "outputs" / "experiment-cr" / run.id
    output_dir.mkdir(parents=True)
    (output_dir / "run_result.json").write_text(_run_result_json(run))

    results = coordinator.collect_results("experiment-cr")
    assert len(results) == 1
    assert results[0].experiment.id == run.id
    assert results[0].status == RunStatus.COMPLETED


def test_collect_results_empty_when_no_outputs(coordinator):
    results = coordinator.collect_results("experiment-nonexistent")
    assert results == []


def test_collect_results_skips_malformed_json(coordinator):
    output_dir = coordinator.storage_root / "outputs" / "experiment-bad" / "run-xyz"
    output_dir.mkdir(parents=True)
    (output_dir / "run_result.json").write_text("NOT VALID JSON {{{{")

    results = coordinator.collect_results("experiment-bad")
    assert results == []


# ---------------------------------------------------------------------------
# Test 4: finalize_experiment generates matrix reports
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_finalize_experiment_writes_matrix_report(coordinator, db):
    matrix = _minimal_matrix("experiment-fin")
    await coordinator.submit_experiment(matrix)

    # Seed a result file so finalize has something to render
    runs = matrix.expand()
    run = runs[0]
    output_dir = coordinator.storage_root / "outputs" / "experiment-fin" / run.id
    output_dir.mkdir(parents=True)
    (output_dir / "run_result.json").write_text(_run_result_json(run))

    await coordinator.finalize_experiment("experiment-fin")

    # matrix_report.md should exist
    report = coordinator.storage_root / "outputs" / "experiment-fin" / "matrix_report.md"
    assert report.exists()
    assert len(report.read_text()) > 0

    # DB status updated to completed
    experiment_row = await db.get_experiment("experiment-fin")
    assert experiment_row["status"] == "completed"


@pytest.mark.asyncio
async def test_finalize_experiment_no_results_still_completes(coordinator, db):
    """finalize_experiment with no result files should not crash and marks DB completed."""
    matrix = _minimal_matrix("experiment-empty-fin")
    await coordinator.submit_experiment(matrix)
    await coordinator.finalize_experiment("experiment-empty-fin")

    experiment_row = await db.get_experiment("experiment-empty-fin")
    assert experiment_row["status"] == "completed"


# ---------------------------------------------------------------------------
# Test 5: ExperimentCostTracker.record_job_cost returns True when cap exceeded
# ---------------------------------------------------------------------------

def test_experiment_cost_tracker_cap_exceeded():
    tracker = ExperimentCostTracker(experiment_id="experiment-cap", cap_usd=1.00)
    assert not tracker.record_job_cost(0.50)
    assert tracker.spent_usd == pytest.approx(0.50)
    assert not tracker._cancelled

    # Next call pushes us over the cap
    exceeded = tracker.record_job_cost(0.60)
    assert exceeded is True
    assert tracker._cancelled is True
    assert tracker.spent_usd == pytest.approx(1.10)


def test_experiment_cost_tracker_no_cap_never_triggers():
    tracker = ExperimentCostTracker(experiment_id="experiment-nocap", cap_usd=None)
    for _ in range(10):
        result = tracker.record_job_cost(100.0)
        assert result is False
    assert tracker.spent_usd == pytest.approx(1000.0)
    assert tracker._cancelled is False


def test_experiment_cost_tracker_exact_cap_triggers():
    tracker = ExperimentCostTracker(experiment_id="experiment-exact", cap_usd=1.00)
    result = tracker.record_job_cost(1.00)
    assert result is True
    assert tracker._cancelled is True


def test_experiment_cost_tracker_already_cancelled_returns_false():
    """Once cancelled, subsequent calls should not re-trigger (idempotent guard)."""
    tracker = ExperimentCostTracker(experiment_id="experiment-idem", cap_usd=0.50)
    tracker.record_job_cost(1.00)  # triggers cancellation
    assert tracker._cancelled is True
    # Second call — cap already marked, should not re-cancel
    result = tracker.record_job_cost(0.01)
    assert result is False


# ---------------------------------------------------------------------------
# Test 6: cancel_experiment marks experiment cancelled in DB (no K8s required)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_cancel_experiment_updates_db_status(coordinator, db):
    matrix = _minimal_matrix("experiment-cancel")
    await coordinator.submit_experiment(matrix)

    cancelled_count = await coordinator.cancel_experiment("experiment-cancel")
    # No K8s client, so no jobs to cancel
    assert cancelled_count == 0

    experiment_row = await db.get_experiment("experiment-cancel")
    assert experiment_row["status"] == "cancelled"


# ---------------------------------------------------------------------------
# Test 7: reconcile is idempotent (calling twice doesn't crash or duplicate)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_reconcile_is_idempotent(coordinator, db):
    # Submit an experiment so reconcile has something to look at
    matrix = _minimal_matrix("experiment-reconcile")
    await coordinator.submit_experiment(matrix)

    # Mark the run as completed so reconcile finalizes rather than re-dispatches
    runs = await db.list_runs("experiment-reconcile")
    for run in runs:
        await db.update_run(run["id"], status="completed")

    # Seed result file so finalize doesn't fail
    run_id = runs[0]["id"]
    run_obj = matrix.expand()[0]
    output_dir = coordinator.storage_root / "outputs" / "experiment-reconcile" / run_id
    output_dir.mkdir(parents=True)
    (output_dir / "run_result.json").write_text(_run_result_json(run_obj))

    # First reconcile
    await coordinator.reconcile()
    experiment_row_1 = await db.get_experiment("experiment-reconcile")

    # Second reconcile — should be a no-op on completed experiment
    await coordinator.reconcile()
    experiment_row_2 = await db.get_experiment("experiment-reconcile")

    assert experiment_row_1["status"] == experiment_row_2["status"]


# ---------------------------------------------------------------------------
# Test 8: delete_experiment removes output directory
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_experiment_removes_output_dir(coordinator, db):
    matrix = _minimal_matrix("experiment-del")
    await coordinator.submit_experiment(matrix)

    # Create an output dir to be cleaned up
    output_dir = coordinator.storage_root / "outputs" / "experiment-del"
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "dummy.json").write_text("{}")

    assert output_dir.exists()
    await coordinator.delete_experiment("experiment-del")
    assert not output_dir.exists()
