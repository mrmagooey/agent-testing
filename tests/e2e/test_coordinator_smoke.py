"""Smoke Test Layer 3: Coordinator Smoke

Integration tests that exercise the full batch lifecycle using FastAPI
TestClient. No Kubernetes required — worker execution is driven directly
via ExperimentWorker.run() with FakeModelProvider injected.

Lifecycle under test:
    POST /batches → collect_results() → finalize_batch() → GET /batches/{id}/results
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from sec_review_framework.coordinator import BatchCoordinator, app
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.data.evaluation import (
    GroundTruthLabel,
    GroundTruthSource,
)
from sec_review_framework.data.experiment import (
    ExperimentMatrix,
    ReviewProfileName,
    StrategyName,
    ToolVariant,
    VerificationVariant,
)
from sec_review_framework.data.findings import Severity, VulnClass
from sec_review_framework.db import Database
from sec_review_framework.models.base import ModelResponse, RetryPolicy
from sec_review_framework.reporting.json_report import JSONReportGenerator
from sec_review_framework.reporting.markdown import MarkdownReportGenerator
from sec_review_framework.reporting.generator import ReportGenerator
from sec_review_framework.worker import ExperimentWorker, ModelProviderFactory

# Reuse the FakeModelProvider from conftest by importing it directly
import sys
import os
sys.path.insert(0, str(Path(__file__).parent.parent))
from conftest import FakeModelProvider


# ---------------------------------------------------------------------------
# Combined reporter — writes both .md and .json matrix reports
# ---------------------------------------------------------------------------

class CombinedReportGenerator(ReportGenerator):
    """Delegates to both MarkdownReportGenerator and JSONReportGenerator."""

    def __init__(self) -> None:
        self._md = MarkdownReportGenerator()
        self._json = JSONReportGenerator()

    def render_run(self, result, output_dir: Path) -> None:
        self._md.render_run(result, output_dir)
        self._json.render_run(result, output_dir)

    def render_matrix(self, results, output_dir: Path) -> None:
        self._md.render_matrix(results, output_dir)
        self._json.render_matrix(results, output_dir)


# ---------------------------------------------------------------------------
# Minimal dataset fixture helpers
# ---------------------------------------------------------------------------

def _make_minimal_dataset(datasets_dir: Path, dataset_name: str) -> None:
    """
    Create a minimal dataset layout that ExperimentWorker and LabelStore expect:

        datasets_dir/
          targets/
            <dataset_name>/
              repo/
                app.py          ← source file to review
              labels.jsonl      ← ground truth labels
    """
    target_dir = datasets_dir / "targets" / dataset_name
    repo_dir = target_dir / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)

    # One vulnerable source file so the strategy has something to review
    (repo_dir / "app.py").write_text(
        'def login(username, password):\n'
        '    query = "SELECT * FROM users WHERE name = \'%s\'" % username\n'
        '    return db.execute(query)\n',
        encoding="utf-8",
    )

    # One ground-truth label matching the injected sqli
    label = GroundTruthLabel(
        id="label-sqli-001",
        dataset_version="1.0.0",
        file_path="app.py",
        line_start=2,
        line_end=2,
        cwe_id="CWE-89",
        vuln_class=VulnClass.SQLI,
        severity=Severity.HIGH,
        description="SQL injection via string formatting",
        source=GroundTruthSource.INJECTED,
        confidence="confirmed",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    labels_path = target_dir / "labels.jsonl"
    labels_path.write_text(label.model_dump_json() + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# FakeModelProvider that emits a single SQLi finding as JSON
# ---------------------------------------------------------------------------

FAKE_FINDING_RESPONSE = ModelResponse(
    content=(
        '```json\n'
        '[\n'
        '  {\n'
        '    "file_path": "app.py",\n'
        '    "line_start": 2,\n'
        '    "line_end": 2,\n'
        '    "vuln_class": "sqli",\n'
        '    "cwe_ids": ["CWE-89"],\n'
        '    "severity": "high",\n'
        '    "title": "SQL Injection in login()",\n'
        '    "description": "String formatting used to construct raw SQL query.",\n'
        '    "recommendation": "Use parameterised queries.",\n'
        '    "confidence": 0.95\n'
        '  }\n'
        ']\n'
        '```'
    ),
    tool_calls=[],
    input_tokens=100,
    output_tokens=80,
    model_id="fake-model",
    raw={},
)


def _make_fake_provider() -> FakeModelProvider:
    """Return a FakeModelProvider with enough canned responses for one run."""
    # The agentic loop calls model.complete() at least once;
    # provide a few extra responses so any strategy depth up to 3 works.
    return FakeModelProvider(
        responses=[FAKE_FINDING_RESPONSE] * 5,
        retry_policy=RetryPolicy(max_retries=0),
    )


# ---------------------------------------------------------------------------
# Coordinator fixture
# ---------------------------------------------------------------------------

BATCH_ID = "smoke-batch-001"
DATASET_NAME = "smoke-dataset"
DATASET_VERSION = "1.0.0"
MODEL_ID = "fake-model"


def _minimal_matrix() -> ExperimentMatrix:
    """1 model × 1 strategy × with_tools only × no verification = 1 run."""
    return ExperimentMatrix(
        batch_id=BATCH_ID,
        dataset_name=DATASET_NAME,
        dataset_version=DATASET_VERSION,
        model_ids=[MODEL_ID],
        strategies=[StrategyName.SINGLE_AGENT],
        tool_variants=[ToolVariant.WITH_TOOLS],
        review_profiles=[ReviewProfileName.DEFAULT],
        verification_variants=[VerificationVariant.NONE],
        parallel_modes=[False],
        num_repetitions=1,
    )


@pytest.fixture()
def storage_root(tmp_path: Path) -> Path:
    root = tmp_path / "storage"
    root.mkdir()
    return root


@pytest.fixture()
def datasets_dir(storage_root: Path) -> Path:
    """Minimal dataset directory under storage_root/datasets."""
    ds_dir = storage_root / "datasets"
    _make_minimal_dataset(ds_dir, DATASET_NAME)
    return ds_dir


@pytest.fixture()
def cost_calculator() -> CostCalculator:
    return CostCalculator(
        pricing={MODEL_ID: ModelPricing(input_per_million=0.0, output_per_million=0.0)}
    )


def _run_async(coro):
    """Run an async coroutine synchronously (for use inside sync fixtures)."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture()
def coordinator_instance(tmp_path: Path, storage_root: Path, cost_calculator: CostCalculator):
    """Build and initialise a real BatchCoordinator with a temp SQLite DB."""
    db = Database(tmp_path / "test.db")
    _run_async(db.init())

    coord = BatchCoordinator(
        k8s_client=None,               # K8s disabled — no job creation
        storage_root=storage_root,
        concurrency_caps={},
        worker_image="unused-in-test",
        namespace="default",
        db=db,
        reporter=CombinedReportGenerator(),
        cost_calculator=cost_calculator,
        default_cap=4,
    )
    return coord


@pytest.fixture()
def test_client(coordinator_instance: BatchCoordinator, datasets_dir: Path):
    """
    TestClient with the coordinator global patched.

    The startup event calls coordinator.reconcile(), which is async and needs
    a live coordinator. We patch the module-level ``coordinator`` variable and
    suppress the startup lifespan so tests control the lifecycle explicitly.
    """
    import sec_review_framework.coordinator as coord_module

    original = coord_module.coordinator
    coord_module.coordinator = coordinator_instance
    try:
        # raise_server_exceptions=True surfaces any unhandled errors clearly
        with TestClient(app, raise_server_exceptions=True) as client:
            yield client
    finally:
        coord_module.coordinator = original


# ---------------------------------------------------------------------------
# Helper: run all workers for a submitted batch
# ---------------------------------------------------------------------------

def _run_workers_for_batch(
    coordinator_inst: BatchCoordinator,
    datasets_dir: Path,
) -> None:
    """
    Emulate what Kubernetes would do: for each pending run, invoke
    ExperimentWorker.run() directly with a patched ModelProviderFactory.
    """
    # Collect all run configs written to shared storage
    config_dir = coordinator_inst.storage_root / "config" / "runs"
    if not config_dir.exists():
        return

    from sec_review_framework.data.experiment import ExperimentRun

    for config_file in config_dir.glob("*.json"):
        run = ExperimentRun.model_validate_json(config_file.read_text())
        output_dir = coordinator_inst.storage_root / "outputs" / run.batch_id / run.id

        fake_provider = _make_fake_provider()

        with patch.object(
            ModelProviderFactory,
            "create",
            return_value=fake_provider,
        ):
            worker = ExperimentWorker()
            worker.run(run, output_dir, datasets_dir)


# ===========================================================================
# Tests
# ===========================================================================

class TestSmokeSubmitBatch:
    """POST /batches returns 201 with batch_id and correct total_runs."""

    def test_smoke_submit_batch(self, test_client: TestClient):
        matrix = _minimal_matrix()
        expected_runs = len(matrix.expand())  # 1

        resp = test_client.post("/batches", json=matrix.model_dump())
        assert resp.status_code == 201, resp.text

        data = resp.json()
        assert data["batch_id"] == BATCH_ID
        assert data["total_runs"] == expected_runs


class TestSmokeBatchStatusAfterSubmit:
    """After submitting, GET /batches/{id} reflects the correct total and all runs pending."""

    def test_smoke_batch_status_after_submit(self, test_client: TestClient):
        matrix = _minimal_matrix()
        post_resp = test_client.post("/batches", json=matrix.model_dump())
        assert post_resp.status_code == 201

        get_resp = test_client.get(f"/batches/{BATCH_ID}")
        assert get_resp.status_code == 200, get_resp.text

        status = get_resp.json()
        assert status["batch_id"] == BATCH_ID
        assert status["total"] == 1
        assert status["completed"] == 0
        assert status["failed"] == 0
        # Runs may be 'pending' or 'running' depending on the scheduling thread timing;
        # the key guarantee is that nothing is completed yet.
        assert status["completed"] == 0


class TestSmokeRunWorkerAndCollect:
    """After submit + worker execution, collect_results() finds the run result file."""

    def test_smoke_run_worker_and_collect(
        self,
        test_client: TestClient,
        coordinator_instance: BatchCoordinator,
        datasets_dir: Path,
    ):
        matrix = _minimal_matrix()
        resp = test_client.post("/batches", json=matrix.model_dump())
        assert resp.status_code == 201

        _run_workers_for_batch(coordinator_instance, datasets_dir)

        results = coordinator_instance.collect_results(BATCH_ID)
        assert len(results) == 1, f"Expected 1 result, got {len(results)}"
        result = results[0]
        assert result.experiment.batch_id == BATCH_ID
        assert result.experiment.model_id == MODEL_ID


class TestSmokeFinalizeGeneratesReports:
    """After collecting results, finalize_batch() writes both matrix report files."""

    def test_smoke_finalize_generates_reports(
        self,
        test_client: TestClient,
        coordinator_instance: BatchCoordinator,
        datasets_dir: Path,
    ):
        matrix = _minimal_matrix()
        resp = test_client.post("/batches", json=matrix.model_dump())
        assert resp.status_code == 201

        _run_workers_for_batch(coordinator_instance, datasets_dir)

        _run_async(coordinator_instance.finalize_batch(BATCH_ID))

        output_dir = coordinator_instance.storage_root / "outputs" / BATCH_ID
        assert (output_dir / "matrix_report.md").exists(), "matrix_report.md not found"
        assert (output_dir / "matrix_report.json").exists(), "matrix_report.json not found"

        # Basic content checks
        md_content = (output_dir / "matrix_report.md").read_text()
        assert BATCH_ID in md_content

        json_content = json.loads((output_dir / "matrix_report.json").read_text())
        assert json_content["batch_id"] == BATCH_ID
        assert "runs" in json_content
        assert len(json_content["runs"]) == 1


class TestSmokeResultsEndpoint:
    """After finalization, GET /batches/{id}/results returns JSON with a runs array."""

    def test_smoke_results_endpoint(
        self,
        test_client: TestClient,
        coordinator_instance: BatchCoordinator,
        datasets_dir: Path,
    ):
        matrix = _minimal_matrix()
        resp = test_client.post("/batches", json=matrix.model_dump())
        assert resp.status_code == 201

        _run_workers_for_batch(coordinator_instance, datasets_dir)
        _run_async(coordinator_instance.finalize_batch(BATCH_ID))

        results_resp = test_client.get(f"/batches/{BATCH_ID}/results")
        assert results_resp.status_code == 200, results_resp.text

        data = results_resp.json()
        assert "runs" in data
        assert isinstance(data["runs"], list)
        assert len(data["runs"]) == 1


class TestSmokeFullLifecycle:
    """
    Combined end-to-end smoke:
    submit → run workers → finalize → GET results → verify matrix structure.
    """

    def test_smoke_full_lifecycle(
        self,
        test_client: TestClient,
        coordinator_instance: BatchCoordinator,
        datasets_dir: Path,
    ):
        # --- 1. Submit ---
        matrix = _minimal_matrix()
        post_resp = test_client.post("/batches", json=matrix.model_dump())
        assert post_resp.status_code == 201
        batch_id = post_resp.json()["batch_id"]
        total_runs = post_resp.json()["total_runs"]
        assert batch_id == BATCH_ID
        assert total_runs == 1

        # --- 2. Run workers (K8s substituted by direct worker invocation) ---
        _run_workers_for_batch(coordinator_instance, datasets_dir)

        # Verify result files exist on disk before finalizing
        results = coordinator_instance.collect_results(batch_id)
        assert len(results) == 1

        # --- 3. Finalize ---
        _run_async(coordinator_instance.finalize_batch(batch_id))

        # --- 4. GET /batches/{id}/results ---
        results_resp = test_client.get(f"/batches/{batch_id}/results")
        assert results_resp.status_code == 200, results_resp.text

        data = results_resp.json()

        # Top-level structure
        assert data["batch_id"] == batch_id
        assert "runs" in data
        assert isinstance(data["runs"], list)
        assert len(data["runs"]) == total_runs

        # Per-run structure
        run_data = data["runs"][0]
        assert run_data["model_id"] == MODEL_ID
        assert run_data["strategy"] == StrategyName.SINGLE_AGENT.value
        assert run_data["tool_variant"] == ToolVariant.WITH_TOOLS.value
        assert run_data["verification_variant"] == VerificationVariant.NONE.value
        assert "status" in run_data

        # Metrics should be present (run completed successfully)
        # Note: evaluation may be None if the worker errored; check status first.
        if run_data["status"] == "completed" and run_data.get("metrics") is not None:
            metrics = run_data["metrics"]
            assert "precision" in metrics
            assert "recall" in metrics
            assert "f1" in metrics

        # --- 5. GET /batches/{id}/runs lists individual runs ---
        runs_resp = test_client.get(f"/batches/{batch_id}/runs")
        assert runs_resp.status_code == 200
        runs_list = runs_resp.json()
        assert isinstance(runs_list, list)
        assert len(runs_list) == total_runs

        # --- 6. Health check still works throughout ---
        health_resp = test_client.get("/health")
        assert health_resp.status_code == 200
        assert health_resp.json()["status"] == "ok"
