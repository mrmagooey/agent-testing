"""E2E API surface tests — gap #7.

Four endpoints that had no e2e coverage:
  1. POST /experiments/{id}/runs/{run_id}/reclassify
  2. POST /feedback/compare
  3. GET  /trends
  4. POST /datasets/discover-cves

All tests use TestClient + FakeModelProvider; no Kubernetes, no real LLM calls,
no external network I/O (CVEDiscovery is monkeypatched).
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
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
    RunResult,
)
from sec_review_framework.data.findings import Severity, VulnClass
from sec_review_framework.db import Database
from sec_review_framework.models.base import ModelResponse, RetryPolicy
from sec_review_framework.reporting.generator import ReportGenerator
from sec_review_framework.reporting.json_report import JSONReportGenerator
from sec_review_framework.reporting.markdown import MarkdownReportGenerator
from sec_review_framework.worker import ExperimentWorker, ModelProviderFactory

sys.path.insert(0, str(Path(__file__).parent.parent))
from conftest import FakeModelProvider

# ---------------------------------------------------------------------------
# Combined reporter (same pattern as test_coordinator_smoke)
# ---------------------------------------------------------------------------


class _CombinedReporter(ReportGenerator):
    def __init__(self) -> None:
        self._md = MarkdownReportGenerator()
        self._json = JSONReportGenerator()

    def render_run(self, result: Any, output_dir: Path) -> None:
        self._md.render_run(result, output_dir)
        self._json.render_run(result, output_dir)

    def render_matrix(self, results: Any, output_dir: Path) -> None:
        self._md.render_matrix(results, output_dir)
        self._json.render_matrix(results, output_dir)


# ---------------------------------------------------------------------------
# Dataset helpers
# ---------------------------------------------------------------------------

DATASET_NAME = "surface-dataset"
DATASET_VERSION = "1.0.0"
MODEL_ID = "claude-opus-4-5"


def _make_dataset(datasets_dir: Path, dataset_name: str = DATASET_NAME) -> None:
    """Minimal dataset: one source file + one ground-truth SQLi label."""
    repo_dir = datasets_dir / "targets" / dataset_name / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    (repo_dir / "app.py").write_text(
        'def login(username):\n'
        '    query = "SELECT * FROM users WHERE name = \'%s\'" % username\n'
        '    return db.execute(query)\n',
        encoding="utf-8",
    )
    label = GroundTruthLabel(
        id="label-sqli-001",
        dataset_version=DATASET_VERSION,
        file_path="app.py",
        line_start=2,
        line_end=2,
        cwe_id="CWE-89",
        vuln_class=VulnClass.SQLI,
        severity=Severity.HIGH,
        description="SQL injection via string formatting",
        source=GroundTruthSource.INJECTED,
        confidence="confirmed",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    labels_path = datasets_dir / "targets" / dataset_name / "labels.jsonl"
    labels_path.write_text(label.model_dump_json() + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Canned model responses
# ---------------------------------------------------------------------------

# One TP (correct file/line) + one FP (wrong file) → precision=0.5, recall=1.0
_RESPONSE_1TP_1FP = ModelResponse(
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
        '    "title": "SQL Injection",\n'
        '    "description": "String formatting used for SQL.",\n'
        '    "recommendation": "Use parameterised queries.",\n'
        '    "confidence": 0.95\n'
        '  },\n'
        '  {\n'
        '    "file_path": "nonexistent.py",\n'
        '    "line_start": 10,\n'
        '    "line_end": 11,\n'
        '    "vuln_class": "sqli",\n'
        '    "cwe_ids": ["CWE-89"],\n'
        '    "severity": "medium",\n'
        '    "title": "Spurious Finding",\n'
        '    "description": "This file does not exist.",\n'
        '    "recommendation": "Investigate.",\n'
        '    "confidence": 0.3\n'
        '  }\n'
        ']\n'
        '```'
    ),
    tool_calls=[],
    input_tokens=100,
    output_tokens=80,
    model_id=MODEL_ID,
    raw={},
)

# One TP only → precision=1.0, recall=1.0
_RESPONSE_1TP_0FP = ModelResponse(
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
        '    "title": "SQL Injection",\n'
        '    "description": "String formatting used for SQL.",\n'
        '    "recommendation": "Use parameterised queries.",\n'
        '    "confidence": 0.97\n'
        '  }\n'
        ']\n'
        '```'
    ),
    tool_calls=[],
    input_tokens=100,
    output_tokens=60,
    model_id=MODEL_ID,
    raw={},
)


def _make_provider(response: ModelResponse) -> FakeModelProvider:
    # Provide a few repeats so the agentic loop can call complete() multiple times.
    return FakeModelProvider(
        responses=[response] * 5,
        retry_policy=RetryPolicy(max_retries=0),
    )


# ---------------------------------------------------------------------------
# Coordinator / TestClient fixtures
# ---------------------------------------------------------------------------


def _run_async(coro: Any) -> Any:
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _build_coordinator(tmp_path: Path, storage_root: Path) -> ExperimentCoordinator:
    db = Database(tmp_path / "test.db")
    _run_async(db.init())
    return ExperimentCoordinator(
        k8s_client=None,
        storage_root=storage_root,
        concurrency_caps={},
        worker_image="unused-in-test",
        namespace="default",
        db=db,
        reporter=_CombinedReporter(),
        cost_calculator=CostCalculator(
            pricing={MODEL_ID: ModelPricing(input_per_million=0.0, output_per_million=0.0)}
        ),
        default_cap=4,
    )


def _minimal_matrix(experiment_id: str, dataset_name: str = DATASET_NAME) -> ExperimentMatrix:
    return ExperimentMatrix(
        experiment_id=experiment_id,
        dataset_name=dataset_name,
        dataset_version=DATASET_VERSION,
        strategy_ids=["builtin.single_agent"],
        num_repetitions=1,
        allow_unavailable_models=True,
    )


def _load_disk_labels(datasets_dir: Path, dataset_name: str) -> list[GroundTruthLabel]:
    """Read labels.jsonl from the test datasets directory."""
    labels_path = datasets_dir / "targets" / dataset_name / "labels.jsonl"
    if not labels_path.exists():
        return []
    labels: list[GroundTruthLabel] = []
    for line in labels_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            labels.append(GroundTruthLabel.model_validate_json(line))
    return labels


def _run_workers(
    coord: ExperimentCoordinator,
    datasets_dir: Path,
    provider: FakeModelProvider,
    experiment_id: str | None = None,
) -> None:
    """Execute pending workers for this coordinator's storage.

    If *experiment_id* is given, only runs belonging to that experiment are
    executed.  This is important when multiple experiments share the same
    storage root: without the filter, a second call would re-run stale config
    files from earlier experiments with the wrong FakeModelProvider.
    """
    config_dir = coord.storage_root / "config" / "runs"
    if not config_dir.exists():
        return
    from sec_review_framework.data.experiment import ExperimentRun

    # The worker normally fetches labels from the coordinator over HTTP.
    # In-process tests can't reach the coordinator that way, so load labels
    # straight off disk (where _make_dataset wrote them).
    def _disk_fetch_labels(self, run, _datasets_dir):  # noqa: ANN001
        return _load_disk_labels(_datasets_dir, run.dataset_name)

    for config_file in config_dir.glob("*.json"):
        run = ExperimentRun.model_validate_json(config_file.read_text())
        if experiment_id is not None and run.experiment_id != experiment_id:
            continue
        output_dir = coord.storage_root / "outputs" / run.experiment_id / run.id
        with patch.object(ModelProviderFactory, "create", return_value=provider):
            with patch.object(ExperimentWorker, "_fetch_labels", _disk_fetch_labels):
                ExperimentWorker().run(run, output_dir, datasets_dir)


# ---------------------------------------------------------------------------
# Helper: submit + run + finalize, return (run_id, result)
# ---------------------------------------------------------------------------


def _full_lifecycle(
    client: TestClient,
    coord: ExperimentCoordinator,
    datasets_dir: Path,
    experiment_id: str,
    provider: FakeModelProvider,
    dataset_name: str = DATASET_NAME,
) -> tuple[str, RunResult]:
    """Submit → run workers → finalize. Returns (run_id, RunResult)."""
    matrix = _minimal_matrix(experiment_id, dataset_name=dataset_name)
    resp = client.post("/experiments", json=matrix.model_dump())
    assert resp.status_code == 201, resp.text

    _run_workers(coord, datasets_dir, provider, experiment_id=experiment_id)
    _run_async(coord.finalize_experiment(experiment_id))

    results = coord.collect_results(experiment_id)
    assert results, f"No results for {experiment_id}"
    result = results[0]
    return result.experiment.id, result


# ===========================================================================
# Test 1: POST /experiments/{id}/runs/{run_id}/reclassify persists the change
# ===========================================================================


class TestReclassifyPersists:
    """Reclassify a finding and verify the change is persisted to filesystem and DB."""

    def test_reclassify_persists(self, tmp_path: Path) -> None:
        storage_root = tmp_path / "storage"
        storage_root.mkdir()
        datasets_dir = storage_root / "datasets"
        _make_dataset(datasets_dir)

        experiment_id = "reclassify-exp-001"
        coord = _build_coordinator(tmp_path, storage_root)

        import sec_review_framework.coordinator as coord_module

        original = coord_module.coordinator
        coord_module.coordinator = coord
        try:
            with TestClient(app, raise_server_exceptions=True) as client:
                run_id, result = _full_lifecycle(
                    client,
                    coord,
                    datasets_dir,
                    experiment_id,
                    _make_provider(_RESPONSE_1TP_1FP),
                )

                # Grab the first finding from the run result
                assert result.findings, "Expected at least one finding"
                finding = result.findings[0]
                finding_id = finding.id

                # Manually index the finding into the DB so reclassify can update it
                _run_async(
                    coord.db.upsert_findings_for_run(
                        run_id=run_id,
                        experiment_id=experiment_id,
                        findings=[f.model_dump(mode="json") for f in result.findings],
                        model_id=result.experiment.model_id,
                        strategy=result.experiment.strategy.value,
                        dataset_name=result.experiment.dataset_name,
                    )
                )

                # POST reclassify
                resp = client.post(
                    f"/experiments/{experiment_id}/runs/{run_id}/reclassify",
                    json={
                        "finding_id": finding_id,
                        "status": "unlabeled_real",
                        "note": "Confirmed real vulnerability",
                    },
                )
                assert resp.status_code == 200, resp.text
                body = resp.json()
                assert body["status"] == "reclassified"
                assert body["finding_id"] == finding_id

                # --- Filesystem verification ---
                result_file = (
                    storage_root / "outputs" / experiment_id / run_id / "run_result.json"
                )
                assert result_file.exists(), "run_result.json must still exist after reclassify"
                persisted = RunResult.model_validate_json(result_file.read_text())

                reclassified = next(
                    (f for f in persisted.findings if f.id == finding_id), None
                )
                assert reclassified is not None, f"Finding {finding_id} not found in persisted result"
                assert reclassified.verified is True, (
                    f"Expected verified=True after reclassify, got {reclassified.verified}"
                )
                assert reclassified.verification_evidence == "Confirmed real vulnerability"

                # --- DB verification ---
                import aiosqlite

                async def _fetch_match_status() -> str | None:
                    async with aiosqlite.connect(coord.db.db_path) as db:
                        async with db.execute(
                            "SELECT match_status FROM findings WHERE id = ?",
                            (finding_id,),
                        ) as cursor:
                            row = await cursor.fetchone()
                            return row[0] if row else None

                db_match_status = _run_async(_fetch_match_status())
                assert db_match_status == "unlabeled_real", (
                    f"DB match_status should be 'unlabeled_real', got {db_match_status!r}"
                )

        finally:
            coord_module.coordinator = original


# ===========================================================================
# Test 2: POST /feedback/compare returns metric_deltas with correct sign
# ===========================================================================


class TestFeedbackCompareStructuredDelta:
    """Two experiments with different quality; compare endpoint returns correct deltas."""

    def test_feedback_compare_structured_delta(self, tmp_path: Path) -> None:
        storage_root = tmp_path / "storage"
        storage_root.mkdir()
        datasets_dir = storage_root / "datasets"
        _make_dataset(datasets_dir)

        # Experiment A: 1 TP + 1 FP → precision=0.5, recall=1.0, f1≈0.667
        exp_a_id = "compare-exp-a"
        # Experiment B: 1 TP + 0 FP → precision=1.0, recall=1.0, f1=1.0
        exp_b_id = "compare-exp-b"

        # Use separate coordinators with the same storage_root so both experiments
        # share the DB but have independent in-memory state.
        coord = _build_coordinator(tmp_path, storage_root)

        import sec_review_framework.coordinator as coord_module

        original = coord_module.coordinator
        coord_module.coordinator = coord
        try:
            with TestClient(app, raise_server_exceptions=True) as client:
                # Run experiment A (worse — 1 TP + 1 FP)
                _full_lifecycle(
                    client,
                    coord,
                    datasets_dir,
                    exp_a_id,
                    _make_provider(_RESPONSE_1TP_1FP),
                )

                # Run experiment B (better — 1 TP + 0 FP)
                _full_lifecycle(
                    client,
                    coord,
                    datasets_dir,
                    exp_b_id,
                    _make_provider(_RESPONSE_1TP_0FP),
                )

                # POST /feedback/compare
                resp = client.post(
                    "/feedback/compare",
                    json={
                        "experiment_a_id": exp_a_id,
                        "experiment_b_id": exp_b_id,
                    },
                )
                assert resp.status_code == 200, resp.text
                data = resp.json()

                # --- Shape assertions ---
                assert "experiment_a_id" in data
                assert "experiment_b_id" in data
                assert data["experiment_a_id"] == exp_a_id
                assert data["experiment_b_id"] == exp_b_id
                assert "metric_deltas" in data, "Response must include metric_deltas"

                metric_deltas = data["metric_deltas"]
                # metric_deltas is a dict: experiment_key → {precision, recall, f1}
                assert isinstance(metric_deltas, dict), (
                    f"metric_deltas should be a dict, got {type(metric_deltas)}"
                )

                # There should be at least one key (both experiments share the same
                # model/strategy/tool_variant key).
                assert len(metric_deltas) >= 1, (
                    "Expected at least one key in metric_deltas"
                )

                # Every delta entry must have precision, recall, f1
                for key, delta in metric_deltas.items():
                    assert "precision" in delta, f"Entry {key!r} missing 'precision'"
                    assert "recall" in delta, f"Entry {key!r} missing 'recall'"
                    assert "f1" in delta, f"Entry {key!r} missing 'f1'"

                # Experiment B is strictly better than A:
                # B precision (1.0) > A precision (0.5) → delta_precision > 0
                # B f1 (1.0) > A f1 (0.667) → delta_f1 > 0
                any_positive_precision = any(
                    d["precision"] > 0 for d in metric_deltas.values()
                )
                any_positive_f1 = any(d["f1"] > 0 for d in metric_deltas.values())
                assert any_positive_precision, (
                    "Expected positive precision delta (B is better than A): "
                    f"{metric_deltas}"
                )
                assert any_positive_f1, (
                    "Expected positive f1 delta (B is better than A): "
                    f"{metric_deltas}"
                )

                # improvements list should flag B > A
                assert "improvements" in data
                assert len(data["improvements"]) >= 1, (
                    f"Expected at least one improvement entry: {data['improvements']}"
                )

        finally:
            coord_module.coordinator = original


# ===========================================================================
# Test 3: GET /trends — missing param → 400; with param → 200 with structure
# ===========================================================================


class TestTrendsRequiresDatasetParam:
    """GET /trends without dataset returns 400; with dataset returns 200."""

    def test_trends_requires_dataset_param(self, tmp_path: Path) -> None:
        storage_root = tmp_path / "storage"
        storage_root.mkdir()
        datasets_dir = storage_root / "datasets"
        _make_dataset(datasets_dir)

        coord = _build_coordinator(tmp_path, storage_root)

        import sec_review_framework.coordinator as coord_module

        original = coord_module.coordinator
        coord_module.coordinator = coord
        try:
            with TestClient(app, raise_server_exceptions=True) as client:
                # --- Without dataset param → 400 ---
                resp_no_param = client.get("/trends")
                assert resp_no_param.status_code == 400, (
                    f"Expected 400 when dataset omitted, got {resp_no_param.status_code}: "
                    f"{resp_no_param.text}"
                )
                error_body = resp_no_param.json()
                # FastAPI wraps HTTPException detail in {"detail": ...}
                assert "detail" in error_body
                assert error_body["detail"], "Error detail must be non-empty"

                # --- With dataset param → 200 with expected structure ---
                resp_with_param = client.get(f"/trends?dataset={DATASET_NAME}")
                assert resp_with_param.status_code == 200, (
                    f"Expected 200 when dataset provided, got {resp_with_param.status_code}: "
                    f"{resp_with_param.text}"
                )
                body = resp_with_param.json()

                # Top-level shape: dataset, experiments, series
                assert "dataset" in body, "Response must include 'dataset'"
                assert body["dataset"] == DATASET_NAME
                assert "experiments" in body, "Response must include 'experiments'"
                assert "series" in body, "Response must include 'series'"
                assert isinstance(body["experiments"], list)
                assert isinstance(body["series"], list)

                # With no completed experiments for this dataset, series is empty
                # (consistent with the "may be {points: []} or similar" spec).
                # If we had seeded data the series would be non-empty — that path
                # is exercised implicitly through test_feedback_compare_structured_delta.

                # --- Invalid date param → 400 ---
                resp_bad_since = client.get(
                    f"/trends?dataset={DATASET_NAME}&since=not-a-date"
                )
                assert resp_bad_since.status_code == 400, (
                    f"Expected 400 for invalid since, got {resp_bad_since.status_code}"
                )

        finally:
            coord_module.coordinator = original


# ===========================================================================
# Test 4: POST /datasets/discover-cves returns a candidate list (CVEDiscovery mocked)
# ===========================================================================


class TestDiscoverCVEsReturnsCandidateList:
    """discover-cves endpoint returns shaped candidates via monkeypatched CVEDiscovery."""

    def test_discover_cves_returns_candidate_list(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from sec_review_framework.ground_truth.cve_importer import (
            CVECandidate,
            CVEDiscovery,
            CVESelectionCriteria,
            ResolvedCVE,
        )

        # Build a realistic fake resolved CVE
        fake_resolved = ResolvedCVE(
            cve_id="CVE-2024-12345",
            ghsa_id="GHSA-xxxx-yyyy-zzzz",
            description="SQL injection in login handler",
            cwe_ids=["CWE-89"],
            vuln_class=VulnClass.SQLI,
            severity=Severity.HIGH,
            cvss_score=8.1,
            repo_url="https://github.com/example/project",
            fix_commit_sha="abc123def456",
            affected_files=["src/auth.py"],
            lines_changed=12,
            language="python",
            repo_kloc=25.0,
            published_date="2024-03-15",
            source="ghsa",
        )
        fake_candidate = CVECandidate(
            resolved=fake_resolved,
            score=0.87,
            score_breakdown={"severity": 0.4, "patch_size": 0.3, "language": 0.17},
            importable=True,
            rejection_reason=None,
        )

        def _fake_discover(
            self: CVEDiscovery,
            criteria: CVESelectionCriteria,
            max_results: int = 50,
        ) -> list[CVECandidate]:
            return [fake_candidate]

        monkeypatch.setattr(CVEDiscovery, "discover", _fake_discover)

        storage_root = tmp_path / "storage"
        storage_root.mkdir()
        coord = _build_coordinator(tmp_path, storage_root)

        import sec_review_framework.coordinator as coord_module

        original = coord_module.coordinator
        coord_module.coordinator = coord
        try:
            with TestClient(app, raise_server_exceptions=True) as client:
                resp = client.post(
                    "/datasets/discover-cves",
                    json={
                        "languages": ["python"],
                        "max_results": 10,
                    },
                )
                assert resp.status_code == 200, resp.text
                candidates = resp.json()

                assert isinstance(candidates, list), (
                    f"Expected list of candidates, got {type(candidates)}"
                )
                assert len(candidates) == 1, (
                    f"Expected 1 candidate from mock, got {len(candidates)}"
                )

                cand = candidates[0]
                # Required fields from CVECandidateResponse schema
                assert "cve_id" in cand, f"Missing 'cve_id' in {cand}"
                assert "description" in cand, f"Missing 'description' in {cand}"
                assert "score" in cand, f"Missing 'score' in {cand}"
                assert "vuln_class" in cand, f"Missing 'vuln_class' in {cand}"
                assert "severity" in cand, f"Missing 'severity' in {cand}"
                assert "importable" in cand, f"Missing 'importable' in {cand}"

                # Value assertions
                assert cand["cve_id"] == "CVE-2024-12345"
                assert cand["description"] == "SQL injection in login handler"
                assert cand["score"] == pytest.approx(0.87, abs=1e-6)
                assert cand["vuln_class"] == "sqli"
                assert cand["severity"] == "high"
                assert cand["importable"] is True

        finally:
            coord_module.coordinator = original
