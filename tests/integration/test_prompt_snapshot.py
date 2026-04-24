"""Integration tests for bundle capture in StrategyOutput and RunResult API.

PromptSnapshot was renamed to BundleSnapshot (schema break accepted per plan).
Tests have been updated accordingly. Old injection-field tests are removed since
those fields no longer exist on BundleSnapshot (the full canonical bundle JSON is
stored instead).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import ExperimentCoordinator, app
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.data.evaluation import EvaluationResult
from sec_review_framework.data.experiment import (
    BundleSnapshot,
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
# Helpers
# ---------------------------------------------------------------------------

def _make_coordinator(tmp_path: Path, db: Database) -> ExperimentCoordinator:
    cost_calc = CostCalculator(
        pricing={"gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0)}
    )
    return ExperimentCoordinator(
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


def _write_run_with_snapshot(
    storage_root: Path,
    experiment_id: str,
    run_id: str,
) -> BundleSnapshot:
    """Write a run_result.json and return the BundleSnapshot used."""
    from tests.helpers import make_test_bundle_snapshot

    run = ExperimentRun(
        id=run_id,
        experiment_id=experiment_id,
        strategy_id="builtin.single_agent",
        model_id="gpt-4o",
        strategy=StrategyName.SINGLE_AGENT,
        tool_variant=ToolVariant.WITH_TOOLS,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        dataset_name="test-dataset",
        dataset_version="1.0.0",
        created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )
    snapshot = make_test_bundle_snapshot()
    result = RunResult(
        experiment=run,
        status=RunStatus.COMPLETED,
        findings=[],
        strategy_output=StrategyOutput(
            findings=[],
            pre_dedup_count=0,
            post_dedup_count=0,
            dedup_log=[],
        ),
        bundle_snapshot=snapshot,
        tool_call_count=0,
        total_input_tokens=100,
        total_output_tokens=50,
        verification_tokens=0,
        estimated_cost_usd=0.01,
        duration_seconds=5.0,
        completed_at=datetime(2026, 4, 1, 1, 0, 0, tzinfo=timezone.utc),
    )
    out_dir = storage_root / "outputs" / experiment_id / run_id
    out_dir.mkdir(parents=True)
    (out_dir / "run_result.json").write_text(result.model_dump_json(indent=2))
    return snapshot


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
# StrategyOutput schema tests
# ---------------------------------------------------------------------------

def test_strategy_output_has_prompt_fields():
    output = StrategyOutput(
        findings=[],
        pre_dedup_count=0,
        post_dedup_count=0,
        dedup_log=[],
        system_prompt="You are a security reviewer.",
        user_message="Review this code.",
    )
    assert output.system_prompt == "You are a security reviewer."
    assert output.user_message == "Review this code."


def test_strategy_output_prompt_fields_optional():
    output = StrategyOutput(
        findings=[],
        pre_dedup_count=0,
        post_dedup_count=0,
        dedup_log=[],
    )
    assert output.system_prompt is None
    assert output.user_message is None


def test_strategy_output_serializes_prompt_fields():
    output = StrategyOutput(
        findings=[],
        pre_dedup_count=0,
        post_dedup_count=0,
        dedup_log=[],
        system_prompt="sys",
        user_message="user",
    )
    d = json.loads(output.model_dump_json())
    assert d["system_prompt"] == "sys"
    assert d["user_message"] == "user"


def test_strategy_output_roundtrip_with_none_prompts():
    output = StrategyOutput(
        findings=[], pre_dedup_count=0, post_dedup_count=0, dedup_log=[]
    )
    restored = StrategyOutput.model_validate_json(output.model_dump_json())
    assert restored.system_prompt is None
    assert restored.user_message is None


# ---------------------------------------------------------------------------
# API: run detail includes bundle_snapshot
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_coordinator_get_run_result_includes_bundle_snapshot(coordinator_client):
    client, coordinator, tmp_path = coordinator_client
    storage = tmp_path / "storage"
    snapshot = _write_run_with_snapshot(storage, "b1", "r1")
    await coordinator.db.create_experiment("b1", config_json="{}", total_runs=1, max_cost_usd=None)
    await coordinator.db.create_run(
        run_id="r1", experiment_id="b1", config_json="{}",
        model_id="gpt-4o", strategy="single_agent",
        tool_variant="with_tools", review_profile="default",
        verification_variant="none",
    )

    result = await coordinator.get_run_result("b1", "r1")
    assert "bundle_snapshot" in result
    bs = result["bundle_snapshot"]
    assert bs["snapshot_id"] == snapshot.snapshot_id
    assert bs["strategy_id"] == snapshot.strategy_id


@pytest.mark.asyncio
async def test_coordinator_get_run_result_bundle_snapshot_is_dict(coordinator_client):
    client, coordinator, tmp_path = coordinator_client
    storage = tmp_path / "storage"
    _write_run_with_snapshot(storage, "b1", "r2")
    await coordinator.db.create_experiment("b1", config_json="{}", total_runs=1, max_cost_usd=None)
    await coordinator.db.create_run(
        run_id="r2", experiment_id="b1", config_json="{}",
        model_id="gpt-4o", strategy="single_agent",
        tool_variant="with_tools", review_profile="default",
        verification_variant="none",
    )

    result = await coordinator.get_run_result("b1", "r2")
    bs = result["bundle_snapshot"]
    assert isinstance(bs, dict)
    assert "snapshot_id" in bs
    assert "strategy_id" in bs
    assert "bundle_json" in bs


# ---------------------------------------------------------------------------
# BundleSnapshot field tests
# ---------------------------------------------------------------------------

def test_bundle_snapshot_has_required_fields():
    from tests.helpers import make_test_bundle_snapshot
    snap = make_test_bundle_snapshot()
    assert snap.snapshot_id is not None
    assert snap.strategy_id is not None
    assert snap.bundle_json is not None
    assert snap.captured_at is not None


def test_bundle_snapshot_id_is_16_chars():
    from tests.helpers import make_test_bundle_snapshot
    snap = make_test_bundle_snapshot()
    assert len(snap.snapshot_id) == 16
