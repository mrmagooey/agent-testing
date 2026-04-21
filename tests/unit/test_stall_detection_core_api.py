"""Regression test for stall detection using CoreV1Api for pod listing.

Before the fix, _check_stalled_job called self.k8s_client.list_namespaced_pod()
where self.k8s_client is a BatchV1Api — which does not have that method.
The AttributeError was swallowed by the except block, so stalled jobs were
never marked failed.

After the fix, _check_stalled_job creates a CoreV1Api lazily and calls
list_namespaced_pod on it.  This test verifies:
  1. The CoreV1Api constructor is called (not BatchV1Api).
  2. The run is marked failed in the DB.
  3. The stalled Job is deleted via self.k8s_client (BatchV1Api).
"""

from __future__ import annotations

import asyncio
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import ExperimentCoordinator
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.data.experiment import (
    ExperimentRun,
    ReviewProfileName,
    RunStatus,
    StrategyName,
    ToolVariant,
    VerificationVariant,
)
from sec_review_framework.db import Database
from sec_review_framework.reporting.markdown import MarkdownReportGenerator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EXPERIMENT_ID = "stall-det-experiment"
MODEL_ID = "fake-model"
DATASET = "test-ds"


def _make_coordinator(tmp_path: Path, db: Database) -> ExperimentCoordinator:
    # k8s_client is intentionally a pure BatchV1Api-shaped mock with NO
    # list_namespaced_pod attribute — mimicking what main() creates today.
    batch_v1_mock = MagicMock(spec=[
        "list_namespaced_job",
        "create_namespaced_job",
        "delete_namespaced_job",
        # Deliberately omitting list_namespaced_pod — that is a CoreV1Api method.
    ])
    return ExperimentCoordinator(
        k8s_client=batch_v1_mock,
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


def _make_run(run_id: str | None = None) -> ExperimentRun:
    rid = run_id or f"{EXPERIMENT_ID}_{MODEL_ID}_single_agent_with_tools_default_none"
    return ExperimentRun(
        id=rid,
        experiment_id=EXPERIMENT_ID,
        model_id=MODEL_ID,
        strategy=StrategyName.SINGLE_AGENT,
        tool_variant=ToolVariant.WITH_TOOLS,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        dataset_name=DATASET,
        dataset_version="1.0.0",
        created_at=datetime(2026, 4, 18, tzinfo=timezone.utc),
    )


@pytest.fixture
async def temp_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    await db.init()
    return db


# ---------------------------------------------------------------------------
# Regression test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_stalled_job_uses_core_v1_api(tmp_path: Path, temp_db: Database):
    """
    _check_stalled_job must use CoreV1Api (not BatchV1Api) to list pods.

    This test fails on main (before the fix) because the BatchV1Api mock has
    no list_namespaced_pod attribute — the AttributeError is swallowed and the
    run is never marked failed.  After the fix, a CoreV1Api is lazily created
    and the run is correctly marked failed.
    """
    coord = _make_coordinator(tmp_path, temp_db)
    coord.storage_root.mkdir(parents=True, exist_ok=True)

    run = _make_run()

    await temp_db.create_experiment(
        experiment_id=EXPERIMENT_ID,
        config_json="{}",
        total_runs=1,
        max_cost_usd=None,
    )
    await temp_db.create_run(
        run_id=run.id,
        experiment_id=EXPERIMENT_ID,
        config_json=run.model_dump_json(),
        model_id=run.model_id,
        strategy=run.strategy.value,
        tool_variant=run.tool_variant.value,
        review_profile=run.review_profile.value,
        verification_variant=run.verification_variant.value,
    )
    await temp_db.update_run(run.id, status="running")

    # Build a stale job (active=1, no live pods, age > timeout)
    stale_start = datetime.now(timezone.utc) - timedelta(seconds=600)

    fake_job = MagicMock()
    fake_job.metadata.name = "exp-stalled-job"
    fake_job.metadata.annotations = {"sec-review.io/run-id": run.id}
    fake_job.metadata.creation_timestamp = stale_start
    fake_job.status.active = 1
    fake_job.status.succeeded = None
    fake_job.status.failed = None
    fake_job.status.start_time = stale_start

    # CoreV1Api mock: list_namespaced_pod returns an empty pod list (no live pods).
    fake_pod_list = MagicMock()
    fake_pod_list.items = []

    fake_core_v1 = MagicMock()
    fake_core_v1.list_namespaced_pod.return_value = fake_pod_list

    # Patch kubernetes.client.CoreV1Api() to return our fake_core_v1
    fake_v1_delete_opts = MagicMock(return_value=MagicMock())
    fake_kubernetes = types.SimpleNamespace(
        client=types.SimpleNamespace(
            CoreV1Api=MagicMock(return_value=fake_core_v1),
            V1DeleteOptions=fake_v1_delete_opts,
        )
    )

    original_kubernetes = coord_module.kubernetes
    original_k8s_available = coord_module.K8S_AVAILABLE
    coord_module.kubernetes = fake_kubernetes
    coord_module.K8S_AVAILABLE = True

    try:
        with patch.object(coord_module, "RUN_STALL_TIMEOUT_S", 300):
            await asyncio.wait_for(
                coord._check_stalled_job(run.id, fake_job),
                timeout=5.0,
            )
    finally:
        coord_module.kubernetes = original_kubernetes
        coord_module.K8S_AVAILABLE = original_k8s_available

    # CoreV1Api() must have been instantiated and list_namespaced_pod called on it.
    fake_kubernetes.client.CoreV1Api.assert_called_once()
    fake_core_v1.list_namespaced_pod.assert_called_once_with(
        "default",
        label_selector="job-name=exp-stalled-job",
    )

    # Run must be marked failed.
    db_run = await temp_db.get_run(run.id)
    assert db_run is not None
    assert db_run["status"] == "failed", (
        f"Expected stalled run to be 'failed', got '{db_run['status']}'"
    )
    assert db_run.get("error") is not None
    assert "no active pods" in (db_run.get("error") or "").lower(), (
        f"Expected error to mention 'no active pods', got: {db_run.get('error')!r}"
    )

    # BatchV1Api mock (self.k8s_client) must have been used to delete the Job.
    coord.k8s_client.delete_namespaced_job.assert_called_once_with(
        "exp-stalled-job",
        "default",
        body=fake_v1_delete_opts.return_value,
    )
