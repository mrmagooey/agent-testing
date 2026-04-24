"""Unit tests for BatchCoordinator.get_trends() and _compute_trend_summary()."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sec_review_framework.coordinator import ExperimentCoordinator, _compute_trend_summary
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.data.experiment import (
    BundleSnapshot,
    ExperimentRun,
    ReviewProfileName,
    RunResult,
    RunStatus,
    StrategyName,
    ToolExtension,
    ToolVariant,
    VerificationVariant,
)
from sec_review_framework.data.findings import (
    Finding,
    Severity,
    StrategyOutput,
    VulnClass,
)
from sec_review_framework.data.evaluation import EvaluationResult, MatchedFinding, GroundTruthLabel
from sec_review_framework.db import Database
from sec_review_framework.reporting.markdown import MarkdownReportGenerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MODEL_ID = "gpt-4o"
DATASET = "test-ds"
OTHER_DATASET = "other-ds"


def _make_evaluation(f1: float = 0.8, precision: float | None = None, recall: float | None = None) -> EvaluationResult:
    """Create a minimal but valid EvaluationResult for test use."""
    p = precision if precision is not None else f1
    r = recall if recall is not None else f1
    return EvaluationResult(
        experiment_id="test-run",
        dataset_version="1.0.0",
        total_labels=10,
        total_findings=10,
        true_positives=int(f1 * 10),
        false_positives=2,
        false_negatives=2,
        unlabeled_real_count=0,
        precision=p,
        recall=r,
        f1=f1,
        false_positive_rate=0.1,
        matched_findings=[],
        unmatched_labels=[],
        evidence_quality_counts={"strong": 5, "adequate": 3, "weak": 2},
    )


def _make_coordinator(tmp_path: Path, db: Database) -> ExperimentCoordinator:
    return ExperimentCoordinator(
        k8s_client=None,
        storage_root=tmp_path / "storage",
        concurrency_caps={},
        worker_image="unused",
        namespace="default",
        db=db,
        reporter=MarkdownReportGenerator(),
        cost_calculator=CostCalculator(
            pricing={MODEL_ID: ModelPricing(input_per_million=0.0, output_per_million=0.0)}
        ),
        default_cap=4,
    )


def _write_result(storage_root: Path, batch_id: str, result: RunResult) -> None:
    out = storage_root / "outputs" / batch_id / result.experiment.id
    out.mkdir(parents=True, exist_ok=True)
    (out / "run_result.json").write_text(result.model_dump_json())


async def _init_db_with_batches(
    db: Database,
    batches: list[dict],
) -> None:
    await db.init()
    for b in batches:
        await db.create_experiment(
            experiment_id=b["id"],
            config_json=json.dumps({"dataset_name": b.get("dataset_name", DATASET)}),
            total_runs=1,
            max_cost_usd=None,
        )
        await db.update_experiment_status(
            b["id"],
            status=b.get("status", "completed"),
            completed_at=b.get("completed_at"),
        )


# ---------------------------------------------------------------------------
# _compute_trend_summary
# ---------------------------------------------------------------------------


class TestComputeTrendSummary:
    def _pt(self, f1: float) -> dict:
        return {
            "experiment_id": "x",
            "completed_at": "2026-01-01",
            "f1": f1,
            "precision": f1,
            "recall": f1,
            "cost_usd": 0.0,
            "run_count": 1,
        }

    def test_empty_returns_none_fields(self):
        s = _compute_trend_summary([])
        assert s["latest_f1"] is None
        assert s["is_regression"] is False

    def test_single_point(self):
        s = _compute_trend_summary([self._pt(0.8)])
        assert s["latest_f1"] == pytest.approx(0.8, abs=1e-4)
        assert s["prev_f1"] is None
        assert s["delta_f1"] is None
        assert s["trailing_median_f1"] is None
        assert s["is_regression"] is False

    def test_two_points_delta_computed(self):
        s = _compute_trend_summary([self._pt(0.7), self._pt(0.8)])
        assert s["latest_f1"] == pytest.approx(0.8, abs=1e-4)
        assert s["prev_f1"] == pytest.approx(0.7, abs=1e-4)
        assert s["delta_f1"] == pytest.approx(0.1, abs=1e-4)
        # Only 2 points → not enough for regression detection
        assert s["is_regression"] is False

    def test_regression_threshold_triggered(self):
        # latest = 0.5, trailing = [0.8, 0.9] → median = 0.85
        # 0.5 - 0.85 = -0.35 < -0.05 ✓ and len >= 3 ✓ → regression
        s = _compute_trend_summary([self._pt(0.8), self._pt(0.9), self._pt(0.5)])
        assert s["is_regression"] is True

    def test_regression_threshold_not_triggered_small_drop(self):
        # latest = 0.78, trailing = [0.8, 0.82] → median = 0.81
        # 0.78 - 0.81 = -0.03 > -0.05 → NOT regression
        s = _compute_trend_summary([self._pt(0.8), self._pt(0.82), self._pt(0.78)])
        assert s["is_regression"] is False

    def test_fewer_than_3_points_never_regression(self):
        # Even if the drop would exceed threshold with >= 3 points
        s = _compute_trend_summary([self._pt(0.9), self._pt(0.5)])
        assert s["is_regression"] is False

    def test_minus_0_06_triggers_regression(self):
        # median = 0.8, latest = 0.74 → delta = -0.06 < -0.05 → regression
        s = _compute_trend_summary([self._pt(0.8), self._pt(0.8), self._pt(0.74)])
        assert s["is_regression"] is True

    def test_minus_0_04_does_not_trigger(self):
        # median = 0.8, latest = 0.76 → delta = -0.04 > -0.05 → not regression
        s = _compute_trend_summary([self._pt(0.8), self._pt(0.8), self._pt(0.76)])
        assert s["is_regression"] is False


# ---------------------------------------------------------------------------
# get_trends — integration tests against real SQLite + filesystem
# ---------------------------------------------------------------------------


class TestGetTrends:
    @pytest.fixture
    def tmp_storage(self, tmp_path):
        return tmp_path / "storage"

    @pytest.mark.asyncio
    async def test_groups_by_experiment_key(self, tmp_path, tmp_storage):
        """Two different models → two series."""
        db = Database(tmp_path / "coordinator.db")
        await _init_db_with_batches(db, [
            {"id": "b1", "completed_at": "2026-01-01T10:00:00", "dataset_name": DATASET},
        ])
        coord = _make_coordinator(tmp_path, db)

        r1 = _make_result_with_key(
            tmp_storage, "b1", model_id="gpt-4o", f1=0.8
        )
        r2 = _make_result_with_key(
            tmp_storage, "b1", model_id="claude-3", f1=0.7
        )

        result = await coord.get_trends(dataset=DATASET)
        assert result["dataset"] == DATASET
        assert len(result["series"]) == 2
        models = {s["key"]["model"] for s in result["series"]}
        assert models == {"gpt-4o", "claude-3"}

    @pytest.mark.asyncio
    async def test_filters_by_dataset(self, tmp_path, tmp_storage):
        """Batches from other datasets are excluded."""
        db = Database(tmp_path / "coordinator.db")
        await _init_db_with_batches(db, [
            {"id": "b1", "completed_at": "2026-01-01T10:00:00", "dataset_name": DATASET},
            {"id": "b2", "completed_at": "2026-01-02T10:00:00", "dataset_name": OTHER_DATASET},
        ])
        coord = _make_coordinator(tmp_path, db)
        _write_result_for(tmp_storage, "b1", dataset=DATASET, model_id="gpt-4o", f1=0.8)
        _write_result_for(tmp_storage, "b2", dataset=OTHER_DATASET, model_id="gpt-4o", f1=0.9)

        result = await coord.get_trends(dataset=DATASET)
        # Only the batch from DATASET should be in the experiment list
        exp_ids = {e["experiment_id"] for e in result["experiments"]}
        assert "b1" in exp_ids
        assert "b2" not in exp_ids

    @pytest.mark.asyncio
    async def test_orders_by_completed_at(self, tmp_path, tmp_storage):
        """Experiments are returned in ascending completed_at order."""
        db = Database(tmp_path / "coordinator.db")
        await _init_db_with_batches(db, [
            {"id": "b3", "completed_at": "2026-01-03T10:00:00", "dataset_name": DATASET},
            {"id": "b1", "completed_at": "2026-01-01T10:00:00", "dataset_name": DATASET},
            {"id": "b2", "completed_at": "2026-01-02T10:00:00", "dataset_name": DATASET},
        ])
        coord = _make_coordinator(tmp_path, db)

        result = await coord.get_trends(dataset=DATASET)
        completed_ats = [e["completed_at"] for e in result["experiments"]]
        assert completed_ats == sorted(completed_ats)

    @pytest.mark.asyncio
    async def test_excludes_failed_experiments(self, tmp_path, tmp_storage):
        """Failed batches are not included in trends."""
        db = Database(tmp_path / "coordinator.db")
        await _init_db_with_batches(db, [
            {"id": "b1", "completed_at": "2026-01-01T10:00:00", "dataset_name": DATASET},
            {"id": "b2", "completed_at": "2026-01-02T10:00:00", "dataset_name": DATASET, "status": "failed"},
        ])
        coord = _make_coordinator(tmp_path, db)
        _write_result_for(tmp_storage, "b1", dataset=DATASET, model_id="gpt-4o", f1=0.8)
        # b2 has result files too but status=failed
        _write_result_for(tmp_storage, "b2", dataset=DATASET, model_id="gpt-4o", f1=0.5)

        result = await coord.get_trends(dataset=DATASET)
        exp_ids = {e["experiment_id"] for e in result["experiments"]}
        assert "b1" in exp_ids
        assert "b2" not in exp_ids

    @pytest.mark.asyncio
    async def test_respects_limit(self, tmp_path, tmp_storage):
        """Only the last `limit` completed experiments are used."""
        db = Database(tmp_path / "coordinator.db")
        batches = [
            {"id": f"b{i}", "completed_at": f"2026-01-0{i}T10:00:00", "dataset_name": DATASET}
            for i in range(1, 6)
        ]
        await _init_db_with_batches(db, batches)
        coord = _make_coordinator(tmp_path, db)

        result = await coord.get_trends(dataset=DATASET, limit=3)
        # Should only include 3 most recent experiments
        assert len(result["experiments"]) == 3
        # They should be the last 3 (b3, b4, b5)
        exp_ids = {e["experiment_id"] for e in result["experiments"]}
        assert "b5" in exp_ids
        assert "b1" not in exp_ids

    @pytest.mark.asyncio
    async def test_handles_sparse_series(self, tmp_path, tmp_storage):
        """A cell present in experiments 1 and 3 but not 2 produces 2 points, not 3."""
        db = Database(tmp_path / "coordinator.db")
        await _init_db_with_batches(db, [
            {"id": "b1", "completed_at": "2026-01-01T10:00:00", "dataset_name": DATASET},
            {"id": "b2", "completed_at": "2026-01-02T10:00:00", "dataset_name": DATASET},
            {"id": "b3", "completed_at": "2026-01-03T10:00:00", "dataset_name": DATASET},
        ])
        coord = _make_coordinator(tmp_path, db)
        # b1 and b3 have results; b2 does NOT (result file missing)
        _write_result_for(tmp_storage, "b1", dataset=DATASET, model_id="gpt-4o", f1=0.8)
        _write_result_for(tmp_storage, "b3", dataset=DATASET, model_id="gpt-4o", f1=0.7)

        result = await coord.get_trends(dataset=DATASET)
        gpt_series = [s for s in result["series"] if s["key"]["model"] == "gpt-4o"]
        assert len(gpt_series) == 1
        # Must be exactly 2 points (not 3, no interpolated zero for b2)
        assert len(gpt_series[0]["points"]) == 2
        # Verify no zeros exist
        for p in gpt_series[0]["points"]:
            assert p["f1"] > 0

    @pytest.mark.asyncio
    async def test_summary_regression_threshold_triggered(self, tmp_path, tmp_storage):
        """latest_f1 - trailing_median > -0.05 triggers is_regression=True."""
        db = Database(tmp_path / "coordinator.db")
        await _init_db_with_batches(db, [
            {"id": "b1", "completed_at": "2026-01-01T10:00:00", "dataset_name": DATASET},
            {"id": "b2", "completed_at": "2026-01-02T10:00:00", "dataset_name": DATASET},
            {"id": "b3", "completed_at": "2026-01-03T10:00:00", "dataset_name": DATASET},
        ])
        coord = _make_coordinator(tmp_path, db)
        # b1=0.85, b2=0.85 → median=0.85, b3=0.79 → delta=-0.06 → regression
        _write_result_for(tmp_storage, "b1", dataset=DATASET, model_id="gpt-4o", f1=0.85)
        _write_result_for(tmp_storage, "b2", dataset=DATASET, model_id="gpt-4o", f1=0.85)
        _write_result_for(tmp_storage, "b3", dataset=DATASET, model_id="gpt-4o", f1=0.79)

        result = await coord.get_trends(dataset=DATASET)
        gpt_series = [s for s in result["series"] if s["key"]["model"] == "gpt-4o"][0]
        assert gpt_series["summary"]["is_regression"] is True

    @pytest.mark.asyncio
    async def test_summary_regression_not_triggered_small_drop(self, tmp_path, tmp_storage):
        """delta = -0.04 does NOT trigger regression."""
        db = Database(tmp_path / "coordinator.db")
        await _init_db_with_batches(db, [
            {"id": "b1", "completed_at": "2026-01-01T10:00:00", "dataset_name": DATASET},
            {"id": "b2", "completed_at": "2026-01-02T10:00:00", "dataset_name": DATASET},
            {"id": "b3", "completed_at": "2026-01-03T10:00:00", "dataset_name": DATASET},
        ])
        coord = _make_coordinator(tmp_path, db)
        # median = 0.8, latest = 0.76 → delta = -0.04 → NOT regression
        _write_result_for(tmp_storage, "b1", dataset=DATASET, model_id="gpt-4o", f1=0.8)
        _write_result_for(tmp_storage, "b2", dataset=DATASET, model_id="gpt-4o", f1=0.8)
        _write_result_for(tmp_storage, "b3", dataset=DATASET, model_id="gpt-4o", f1=0.76)

        result = await coord.get_trends(dataset=DATASET)
        gpt_series = [s for s in result["series"] if s["key"]["model"] == "gpt-4o"][0]
        assert gpt_series["summary"]["is_regression"] is False

    @pytest.mark.asyncio
    async def test_cache_invalidation_on_finalize(self, tmp_path, tmp_storage):
        """Trend cache is cleared when finalize_experiment is called."""
        db = Database(tmp_path / "coordinator.db")
        await _init_db_with_batches(db, [
            {"id": "b1", "completed_at": "2026-01-01T10:00:00", "dataset_name": DATASET},
        ])
        coord = _make_coordinator(tmp_path, db)
        _write_result_for(tmp_storage, "b1", dataset=DATASET, model_id="gpt-4o", f1=0.8)

        # Populate cache
        result1 = await coord.get_trends(dataset=DATASET)
        assert len(coord._trends_cache) > 0

        # Finalize (which should clear cache)
        # Mock reporter to avoid writing files
        coord.reporter = MagicMock()
        await coord.finalize_experiment("b1")

        # Cache should be empty
        assert len(coord._trends_cache) == 0


# ---------------------------------------------------------------------------
# Endpoint integration test (FastAPI TestClient)
# ---------------------------------------------------------------------------


class TestTrendsEndpoint:
    def test_dataset_required_400(self):
        """GET /trends without dataset param returns 400."""
        from fastapi.testclient import TestClient
        from sec_review_framework.coordinator import app, coordinator as _coord
        import sec_review_framework.coordinator as coord_mod

        # Stub coordinator
        mock_coord = MagicMock()
        original = coord_mod.coordinator
        coord_mod.coordinator = mock_coord
        try:
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/trends")
            assert response.status_code == 400
        finally:
            coord_mod.coordinator = original

    def test_valid_dataset_proxies_to_coordinator(self):
        """GET /trends?dataset=foo calls coordinator.get_trends."""
        from fastapi.testclient import TestClient
        from sec_review_framework.coordinator import app
        import sec_review_framework.coordinator as coord_mod

        mock_coord = MagicMock()
        mock_coord.get_trends = AsyncMock(return_value={
            "dataset": "foo",
            "experiments": [],
            "series": [],
        })
        original = coord_mod.coordinator
        coord_mod.coordinator = mock_coord
        try:
            client = TestClient(app)
            response = client.get("/api/trends?dataset=foo&limit=5")
            assert response.status_code == 200
            data = response.json()
            assert data["dataset"] == "foo"
            mock_coord.get_trends.assert_called_once()
            call_kwargs = mock_coord.get_trends.call_args.kwargs
            assert call_kwargs["dataset"] == "foo"
            assert call_kwargs["limit"] == 5
        finally:
            coord_mod.coordinator = original

    def test_limit_above_max_is_rejected(self):
        """GET /trends?limit=999999 returns 422 (Query bound violation)."""
        from fastapi.testclient import TestClient
        from sec_review_framework.coordinator import app
        import sec_review_framework.coordinator as coord_mod

        original = coord_mod.coordinator
        coord_mod.coordinator = MagicMock()
        try:
            client = TestClient(app)
            response = client.get("/api/trends?dataset=foo&limit=999999")
            assert response.status_code == 422
        finally:
            coord_mod.coordinator = original

    def test_invalid_since_date_is_rejected(self):
        """GET /trends with non-ISO since returns 400."""
        from fastapi.testclient import TestClient
        from sec_review_framework.coordinator import app
        import sec_review_framework.coordinator as coord_mod

        original = coord_mod.coordinator
        coord_mod.coordinator = MagicMock()
        try:
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/trends?dataset=foo&since=not-a-date")
            assert response.status_code == 400
            assert "ISO date" in response.json()["detail"]
        finally:
            coord_mod.coordinator = original

    def test_invalid_until_date_is_rejected(self):
        """GET /trends with non-ISO until returns 400."""
        from fastapi.testclient import TestClient
        from sec_review_framework.coordinator import app
        import sec_review_framework.coordinator as coord_mod

        original = coord_mod.coordinator
        coord_mod.coordinator = MagicMock()
        try:
            client = TestClient(app, raise_server_exceptions=False)
            response = client.get("/api/trends?dataset=foo&until=zzz")
            assert response.status_code == 400
            assert "ISO date" in response.json()["detail"]
        finally:
            coord_mod.coordinator = original


# ---------------------------------------------------------------------------
# Helpers for writing result files
# ---------------------------------------------------------------------------

def _write_result_for(
    storage_root: Path,
    batch_id: str,
    dataset: str,
    model_id: str,
    f1: float,
    strategy: StrategyName = StrategyName.SINGLE_AGENT,
    tool_variant: ToolVariant = ToolVariant.WITH_TOOLS,
    tool_extensions: frozenset | None = None,
) -> None:
    """Write a run_result.json to storage_root/outputs/batch_id/run_id/."""
    from tests.helpers import make_test_bundle_snapshot

    ext = tool_extensions or frozenset()
    run = ExperimentRun(
        id=f"{batch_id}_{model_id}_{strategy.value}",
        experiment_id=batch_id,
        strategy_id="builtin.single_agent",
        model_id=model_id,
        strategy=strategy,
        tool_variant=tool_variant,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        dataset_name=dataset,
        dataset_version="1.0.0",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        tool_extensions=ext,
    )
    evaluation = _make_evaluation(f1=f1)
    strategy_output = StrategyOutput(
        findings=[],
        pre_dedup_count=0,
        post_dedup_count=0,
        dedup_log=[],
    )
    result = RunResult(
        experiment=run,
        status=RunStatus.COMPLETED,
        findings=[],
        strategy_output=strategy_output,
        bundle_snapshot=make_test_bundle_snapshot(),
        tool_call_count=0,
        total_input_tokens=100,
        total_output_tokens=50,
        verification_tokens=0,
        estimated_cost_usd=0.01,
        duration_seconds=5.0,
        evaluation=evaluation,
    )
    out = storage_root / "outputs" / batch_id / run.id
    out.mkdir(parents=True, exist_ok=True)
    (out / "run_result.json").write_text(result.model_dump_json())


def _make_result_with_key(
    storage_root: Path,
    batch_id: str,
    model_id: str,
    f1: float,
) -> RunResult:
    """Write AND return a RunResult for the given key."""
    _write_result_for(storage_root, batch_id, DATASET, model_id, f1)
    # Return something (not used directly in assertions)
    return MagicMock()
