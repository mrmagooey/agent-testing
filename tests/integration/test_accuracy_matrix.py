"""Integration tests for GET /api/matrix/accuracy."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import BatchCoordinator, app
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.data.evaluation import EvaluationResult
from sec_review_framework.data.experiment import (
    ExperimentRun,
    PromptSnapshot,
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
# Helpers
# ---------------------------------------------------------------------------

def _make_coordinator(tmp_path: Path, db: Database) -> BatchCoordinator:
    cost_calc = CostCalculator(
        pricing={
            "gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0),
        }
    )
    return BatchCoordinator(
        k8s_client=None,
        storage_root=tmp_path / "storage",
        concurrency_caps={},
        worker_image="worker:latest",
        namespace="default",
        db=db,
        reporter=MarkdownReportGenerator(),
        cost_calculator=cost_calc,
        default_cap=4,
    )


def _write_run_result(
    storage_root: Path,
    batch_id: str,
    run_id: str,
    model_id: str,
    strategy: StrategyName,
    recall: float,
) -> None:
    run = ExperimentRun(
        id=run_id,
        batch_id=batch_id,
        model_id=model_id,
        strategy=strategy,
        tool_variant=ToolVariant.WITH_TOOLS,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        dataset_name="test-dataset",
        dataset_version="1.0.0",
        created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )
    evaluation = EvaluationResult(
        experiment_id=run_id,
        dataset_version="1.0.0",
        total_labels=10,
        total_findings=8,
        true_positives=int(recall * 10),
        false_positives=2,
        false_negatives=10 - int(recall * 10),
        unlabeled_real_count=0,
        precision=0.8,
        recall=recall,
        f1=2 * 0.8 * recall / (0.8 + recall) if (0.8 + recall) else 0.0,
        false_positive_rate=0.1,
        matched_findings=[],
        unmatched_labels=[],
        evidence_quality_counts={},
    )
    result = RunResult(
        experiment=run,
        status=RunStatus.COMPLETED,
        findings=[],
        strategy_output=StrategyOutput(
            findings=[], pre_dedup_count=0, post_dedup_count=0, dedup_log=[]
        ),
        prompt_snapshot=PromptSnapshot.capture(
            system_prompt="sys", user_message_template="user", finding_output_format=""
        ),
        tool_call_count=0,
        total_input_tokens=100,
        total_output_tokens=50,
        verification_tokens=0,
        estimated_cost_usd=0.01,
        duration_seconds=10.0,
        evaluation=evaluation,
        completed_at=datetime(2026, 4, 1, 1, 0, 0, tzinfo=timezone.utc),
    )
    out_dir = storage_root / "outputs" / batch_id / run_id
    out_dir.mkdir(parents=True)
    (out_dir / "run_result.json").write_text(result.model_dump_json(indent=2))


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
            with TestClient(app, raise_server_exceptions=True) as client:
                yield client, c, tmp_path


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_accuracy_matrix_empty_returns_empty_cells(coordinator_client):
    client, *_ = coordinator_client
    resp = client.get("/api/matrix/accuracy")
    assert resp.status_code == 200
    data = resp.json()
    assert data["models"] == []
    assert data["strategies"] == []
    assert data["cells"] == []


def test_accuracy_matrix_response_shape(coordinator_client):
    client, _, tmp_path = coordinator_client
    storage = tmp_path / "storage"
    _write_run_result(storage, "batch-1", "run-1", "gpt-4o", StrategyName.SINGLE_AGENT, 0.8)

    resp = client.get("/api/matrix/accuracy")
    assert resp.status_code == 200
    data = resp.json()
    assert "models" in data
    assert "strategies" in data
    assert "cells" in data
    assert isinstance(data["models"], list)
    assert isinstance(data["strategies"], list)
    assert isinstance(data["cells"], list)


def test_accuracy_matrix_single_run(coordinator_client):
    client, _, tmp_path = coordinator_client
    storage = tmp_path / "storage"
    _write_run_result(storage, "batch-1", "run-1", "gpt-4o", StrategyName.SINGLE_AGENT, 0.75)

    resp = client.get("/api/matrix/accuracy")
    data = resp.json()
    assert "gpt-4o" in data["models"]
    assert "single_agent" in data["strategies"]
    assert len(data["cells"]) == 1
    cell = data["cells"][0]
    assert cell["model"] == "gpt-4o"
    assert cell["strategy"] == "single_agent"
    assert cell["accuracy"] == pytest.approx(0.75, abs=1e-4)
    assert cell["run_count"] == 1


def test_accuracy_matrix_multiple_models_and_strategies(coordinator_client):
    client, _, tmp_path = coordinator_client
    storage = tmp_path / "storage"
    _write_run_result(storage, "b1", "r1", "gpt-4o", StrategyName.SINGLE_AGENT, 0.8)
    _write_run_result(storage, "b1", "r2", "gpt-4o", StrategyName.PER_FILE, 0.6)
    _write_run_result(storage, "b1", "r3", "claude-opus-4", StrategyName.SINGLE_AGENT, 0.9)

    resp = client.get("/api/matrix/accuracy")
    data = resp.json()
    assert set(data["models"]) == {"gpt-4o", "claude-opus-4"}
    assert set(data["strategies"]) == {"single_agent", "per_file"}
    assert len(data["cells"]) == 3


def test_accuracy_matrix_averages_multiple_runs_same_cell(coordinator_client):
    client, _, tmp_path = coordinator_client
    storage = tmp_path / "storage"
    _write_run_result(storage, "b1", "r1", "gpt-4o", StrategyName.SINGLE_AGENT, 0.6)
    _write_run_result(storage, "b2", "r2", "gpt-4o", StrategyName.SINGLE_AGENT, 0.8)

    resp = client.get("/api/matrix/accuracy")
    data = resp.json()
    assert len(data["cells"]) == 1
    cell = data["cells"][0]
    assert cell["run_count"] == 2
    assert cell["accuracy"] == pytest.approx(0.7, abs=1e-4)


def test_accuracy_matrix_skips_runs_without_evaluation(coordinator_client):
    client, _, tmp_path = coordinator_client
    storage = tmp_path / "storage"
    _write_run_result(storage, "b1", "r1", "gpt-4o", StrategyName.SINGLE_AGENT, 0.8)

    # Write a run result with no evaluation (failed run)
    run = ExperimentRun(
        id="r-failed",
        batch_id="b1",
        model_id="gpt-4o",
        strategy=StrategyName.PER_FILE,
        tool_variant=ToolVariant.WITH_TOOLS,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        dataset_name="ds",
        dataset_version="1.0",
        created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )
    result = RunResult(
        experiment=run,
        status=RunStatus.FAILED,
        findings=[],
        strategy_output=StrategyOutput(
            findings=[], pre_dedup_count=0, post_dedup_count=0, dedup_log=[]
        ),
        prompt_snapshot=PromptSnapshot.capture(
            system_prompt="", user_message_template="", finding_output_format=""
        ),
        tool_call_count=0,
        total_input_tokens=0,
        total_output_tokens=0,
        verification_tokens=0,
        estimated_cost_usd=0.0,
        duration_seconds=1.0,
        evaluation=None,
        error="something went wrong",
        completed_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )
    out_dir = storage / "outputs" / "b1" / "r-failed"
    out_dir.mkdir(parents=True)
    (out_dir / "run_result.json").write_text(result.model_dump_json(indent=2))

    resp = client.get("/api/matrix/accuracy")
    data = resp.json()
    # Only the completed run with evaluation should appear
    assert len(data["cells"]) == 1
    assert data["cells"][0]["strategy"] == "single_agent"
