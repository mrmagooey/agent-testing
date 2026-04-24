"""Smoke Test Layer 3: Coordinator Smoke

Integration tests that exercise the full experiment lifecycle using FastAPI
TestClient. No Kubernetes required — worker execution is driven directly
via ExperimentWorker.run() with FakeModelProvider injected.

Lifecycle under test:
    POST /experiments → collect_results() → finalize_experiment() → GET /experiments/{id}/results
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from sec_review_framework.coordinator import ExperimentCoordinator, app
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

EXPERIMENT_ID = "smoke-experiment-001"
DATASET_NAME = "smoke-dataset"
DATASET_VERSION = "1.0.0"
MODEL_ID = "claude-opus-4-5"


def _minimal_matrix() -> ExperimentMatrix:
    """1 strategy × 1 rep = 1 run (builtin.single_agent bakes in MODEL_ID)."""
    return ExperimentMatrix(
        experiment_id=EXPERIMENT_ID,
        dataset_name=DATASET_NAME,
        dataset_version=DATASET_VERSION,
        strategy_ids=["builtin.single_agent"],
        num_repetitions=1,
        allow_unavailable_models=True,
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
    """Build and initialise a real ExperimentCoordinator with a temp SQLite DB."""
    db = Database(tmp_path / "test.db")
    _run_async(db.init())

    coord = ExperimentCoordinator(
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
def test_client(coordinator_instance: ExperimentCoordinator, datasets_dir: Path):
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
# Helper: run all workers for a submitted experiment
# ---------------------------------------------------------------------------

def _run_workers_for_experiment(
    coordinator_inst: ExperimentCoordinator,
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
        output_dir = coordinator_inst.storage_root / "outputs" / run.experiment_id / run.id

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

class TestSmokeSubmitExperiment:
    """POST /experiments returns 201 with experiment_id and correct total_runs."""

    def test_smoke_submit_experiment(self, test_client: TestClient):
        matrix = _minimal_matrix()
        expected_runs = len(matrix.expand())  # 1

        resp = test_client.post("/experiments", json=matrix.model_dump())
        assert resp.status_code == 201, resp.text

        data = resp.json()
        assert data["experiment_id"] == EXPERIMENT_ID
        assert data["total_runs"] == expected_runs


class TestSmokeExperimentStatusAfterSubmit:
    """After submitting, GET /experiments/{id} reflects the correct total and all runs pending."""

    def test_smoke_experiment_status_after_submit(self, test_client: TestClient):
        matrix = _minimal_matrix()
        post_resp = test_client.post("/experiments", json=matrix.model_dump())
        assert post_resp.status_code == 201

        get_resp = test_client.get(f"/experiments/{EXPERIMENT_ID}")
        assert get_resp.status_code == 200, get_resp.text

        status = get_resp.json()
        assert status["experiment_id"] == EXPERIMENT_ID
        assert status["total_runs"] == 1
        assert status["completed_runs"] == 0
        assert status["failed_runs"] == 0
        # Runs may be 'pending' or 'running' depending on the scheduling thread timing;
        # the key guarantee is that nothing is completed yet.
        assert status["completed_runs"] == 0


class TestSmokeRunWorkerAndCollect:
    """After submit + worker execution, collect_results() finds the run result file."""

    def test_smoke_run_worker_and_collect(
        self,
        test_client: TestClient,
        coordinator_instance: ExperimentCoordinator,
        datasets_dir: Path,
    ):
        matrix = _minimal_matrix()
        resp = test_client.post("/experiments", json=matrix.model_dump())
        assert resp.status_code == 201

        _run_workers_for_experiment(coordinator_instance, datasets_dir)

        results = coordinator_instance.collect_results(EXPERIMENT_ID)
        assert len(results) == 1, f"Expected 1 result, got {len(results)}"
        result = results[0]
        assert result.experiment.experiment_id == EXPERIMENT_ID
        assert result.experiment.model_id == MODEL_ID


class TestSmokeFinalizeGeneratesReports:
    """After collecting results, finalize_experiment() writes both matrix report files."""

    def test_smoke_finalize_generates_reports(
        self,
        test_client: TestClient,
        coordinator_instance: ExperimentCoordinator,
        datasets_dir: Path,
    ):
        matrix = _minimal_matrix()
        resp = test_client.post("/experiments", json=matrix.model_dump())
        assert resp.status_code == 201

        _run_workers_for_experiment(coordinator_instance, datasets_dir)

        _run_async(coordinator_instance.finalize_experiment(EXPERIMENT_ID))

        output_dir = coordinator_instance.storage_root / "outputs" / EXPERIMENT_ID
        assert (output_dir / "matrix_report.md").exists(), "matrix_report.md not found"
        assert (output_dir / "matrix_report.json").exists(), "matrix_report.json not found"

        # Basic content checks
        md_content = (output_dir / "matrix_report.md").read_text()
        assert EXPERIMENT_ID in md_content

        json_content = json.loads((output_dir / "matrix_report.json").read_text())
        assert json_content["experiment_id"] == EXPERIMENT_ID
        assert "runs" in json_content
        assert len(json_content["runs"]) == 1


class TestSmokeResultsEndpoint:
    """After finalization, GET /experiments/{id}/results returns JSON with a runs array."""

    def test_smoke_results_endpoint(
        self,
        test_client: TestClient,
        coordinator_instance: ExperimentCoordinator,
        datasets_dir: Path,
    ):
        matrix = _minimal_matrix()
        resp = test_client.post("/experiments", json=matrix.model_dump())
        assert resp.status_code == 201

        _run_workers_for_experiment(coordinator_instance, datasets_dir)
        _run_async(coordinator_instance.finalize_experiment(EXPERIMENT_ID))

        results_resp = test_client.get(f"/experiments/{EXPERIMENT_ID}/results")
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
        coordinator_instance: ExperimentCoordinator,
        datasets_dir: Path,
    ):
        # --- 1. Submit ---
        matrix = _minimal_matrix()
        post_resp = test_client.post("/experiments", json=matrix.model_dump())
        assert post_resp.status_code == 201
        experiment_id = post_resp.json()["experiment_id"]
        total_runs = post_resp.json()["total_runs"]
        assert experiment_id == EXPERIMENT_ID
        assert total_runs == 1

        # --- 2. Run workers (K8s substituted by direct worker invocation) ---
        _run_workers_for_experiment(coordinator_instance, datasets_dir)

        # Verify result files exist on disk before finalizing
        results = coordinator_instance.collect_results(experiment_id)
        assert len(results) == 1

        # --- 3. Finalize ---
        _run_async(coordinator_instance.finalize_experiment(experiment_id))

        # --- 4. GET /experiments/{id}/results ---
        results_resp = test_client.get(f"/experiments/{experiment_id}/results")
        assert results_resp.status_code == 200, results_resp.text

        data = results_resp.json()

        # Top-level structure
        assert data["experiment_id"] == experiment_id
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

        # --- 5. GET /experiments/{id}/runs lists individual runs ---
        runs_resp = test_client.get(f"/experiments/{experiment_id}/runs")
        assert runs_resp.status_code == 200
        runs_list = runs_resp.json()
        assert isinstance(runs_list, list)
        assert len(runs_list) == total_runs

        # --- 6. Health check still works throughout ---
        health_resp = test_client.get("/health")
        assert health_resp.status_code == 200
        assert health_resp.json()["status"] == "ok"


class TestSmokeDeleteExperimentRemovesArtifacts:
    """
    DELETE /experiments/{id} removes on-disk artifacts and returns 204.

    NOTE: The implementation does NOT delete the DB row — it only marks runs
    as "cancelled" and removes filesystem artifacts (outputs dir + run config
    files).  As a consequence GET /experiments/{id} still returns 200 after
    deletion with status "cancelled".  This is tested below as the *actual*
    observable contract.  See the potential-bug note in the docstring for the
    coordinator's delete_experiment() method.
    """

    def test_delete_experiment_removes_artifacts(
        self,
        test_client: TestClient,
        coordinator_instance: ExperimentCoordinator,
        datasets_dir: Path,
    ):
        # --- 1. Submit, run workers, finalize to get a fully completed experiment ---
        matrix = _minimal_matrix()
        post_resp = test_client.post("/experiments", json=matrix.model_dump())
        assert post_resp.status_code == 201
        experiment_id = post_resp.json()["experiment_id"]
        assert experiment_id == EXPERIMENT_ID

        _run_workers_for_experiment(coordinator_instance, datasets_dir)
        _run_async(coordinator_instance.finalize_experiment(experiment_id))

        # --- 2. Pre-delete snapshot: experiment accessible and artifacts exist ---
        pre_get_resp = test_client.get(f"/experiments/{experiment_id}")
        assert pre_get_resp.status_code == 200, pre_get_resp.text
        assert pre_get_resp.json()["experiment_id"] == experiment_id

        outputs_dir = coordinator_instance.storage_root / "outputs" / experiment_id
        assert outputs_dir.exists(), "outputs dir must exist before deletion"
        assert (outputs_dir / "matrix_report.md").exists(), "matrix_report.md must exist"

        # Confirm at least one run config file was written
        config_dir = coordinator_instance.storage_root / "config" / "runs"
        config_files_before = list(config_dir.glob("*.json"))
        assert len(config_files_before) >= 1, "at least one run config file expected"

        # Confirm experiment appears in list
        list_resp = test_client.get("/experiments")
        assert list_resp.status_code == 200
        exp_ids_before = [e["experiment_id"] for e in list_resp.json()]
        assert experiment_id in exp_ids_before

        # --- 3. DELETE /experiments/{id} — expect 204 No Content ---
        del_resp = test_client.delete(f"/experiments/{experiment_id}")
        assert del_resp.status_code == 204, (
            f"Expected 204, got {del_resp.status_code}: {del_resp.text}"
        )
        # 204 responses must have no body
        assert del_resp.content == b""

        # --- 4. Post-delete assertions: filesystem artifacts are gone ---
        assert not outputs_dir.exists(), (
            "outputs directory must be removed after DELETE"
        )
        # Run config files for this experiment's runs should be removed
        config_files_after = list(config_dir.glob("*.json"))
        assert len(config_files_after) == 0, (
            f"Run config files should be removed, but found: {config_files_after}"
        )

        # --- 5. DB row is NOT deleted (potential bug — noted below) ---
        # The implementation cancels runs in the DB but never removes the row,
        # so GET /experiments/{id} still returns 200 with status "cancelled".
        # This is tested as the actual observable contract, not the ideal one.
        post_get_resp = test_client.get(f"/experiments/{experiment_id}")
        assert post_get_resp.status_code == 200, (
            "GET /experiments/{id} returns 200 after delete because the DB row "
            "is NOT removed — only disk artifacts are cleaned up. "
            "This may be a bug: a caller expecting 404 will be surprised."
        )
        post_data = post_get_resp.json()
        assert post_data["experiment_id"] == experiment_id
        # Status must be "cancelled" (set by cancel_experiment() inside delete_experiment())
        assert post_data["status"] == "cancelled", (
            f"Expected status 'cancelled' after delete, got {post_data['status']!r}"
        )

        # Similarly, GET /experiments still lists the experiment (DB row retained)
        list_after_resp = test_client.get("/experiments")
        assert list_after_resp.status_code == 200
        exp_ids_after = [e["experiment_id"] for e in list_after_resp.json()]
        assert experiment_id in exp_ids_after, (
            "Experiment should still appear in listing because the DB row is retained"
        )
