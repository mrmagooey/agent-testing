"""Unit tests for the Kubernetes Job spec produced by _create_k8s_job.

Regression tests for the pod-label propagation bug: the NetworkPolicy selector
``app: sec-review-worker`` matches the *pod* label, but Kubernetes does NOT
auto-propagate Job labels to pods.  The fix adds ``metadata`` to
``V1PodTemplateSpec`` with the same labels as the Job.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import ExperimentCoordinator
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.data.experiment import (
    ExperimentRun,
    ReviewProfileName,
    StrategyName,
    ToolVariant,
    VerificationVariant,
)
from sec_review_framework.db import Database
from sec_review_framework.reporting.markdown import MarkdownReportGenerator

EXPERIMENT_ID = "test-experiment-job-spec"
MODEL_ID = "fake-model"


def _make_coordinator(tmp_path: Path, db: Database) -> ExperimentCoordinator:
    fake_k8s = MagicMock()
    return ExperimentCoordinator(
        k8s_client=fake_k8s,
        storage_root=tmp_path / "storage",
        concurrency_caps={},
        worker_image="worker:latest",
        namespace="default",
        db=db,
        reporter=MarkdownReportGenerator(),
        cost_calculator=CostCalculator(
            pricing={MODEL_ID: ModelPricing(input_per_million=0.0, output_per_million=0.0)}
        ),
        default_cap=4,
        result_transport="http",
        coordinator_internal_url="http://coordinator:8080",
    )


def _make_run(result_transport: str = "http") -> ExperimentRun:
    return ExperimentRun(
        id=f"{EXPERIMENT_ID}_builtin.single_agent",
        experiment_id=EXPERIMENT_ID,
        strategy_id="builtin.single_agent",
        model_id=MODEL_ID,
        strategy=StrategyName.SINGLE_AGENT,
        tool_variant=ToolVariant.WITH_TOOLS,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        dataset_name="test-ds",
        dataset_version="1.0.0",
        created_at=datetime(2026, 4, 18, tzinfo=UTC),
        result_transport=result_transport,  # type: ignore[arg-type]
    )


@pytest.fixture
async def temp_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    await db.init()
    return db


# ---------------------------------------------------------------------------
# Bug regression: pod template must carry the app label
# ---------------------------------------------------------------------------


def test_create_k8s_job_pod_template_has_app_label(tmp_path: Path, temp_db: Database):
    """V1PodTemplateSpec.metadata.labels must include ``app: sec-review-worker``.

    Regression test for the NetworkPolicy bug: Kubernetes does NOT propagate
    Job-level labels to pods.  The ingress NetworkPolicy selector
    ``app: sec-review-worker`` (network-policy.yaml:69) must match the *pod*
    labels, so they must be set explicitly on the pod template metadata.
    """
    # We need the real kubernetes module to build actual V1* objects so we can
    # inspect the constructed spec.
    assert coord_module.K8S_AVAILABLE, (
        "kubernetes package must be installed for this test to run"
    )

    coord = _make_coordinator(tmp_path, temp_db)
    run = _make_run(result_transport="http")

    # Capture the job object passed to create_namespaced_job
    captured: list = []

    def _capture_job(namespace, job):
        captured.append(job)

    coord.k8s_client.create_namespaced_job.side_effect = _capture_job

    coord._create_k8s_job(EXPERIMENT_ID, run)

    assert captured, "create_namespaced_job was not called"
    job = captured[0]

    template = job.spec.template
    assert template is not None, "job.spec.template is None"
    assert template.metadata is not None, (
        "job.spec.template.metadata is None — pod labels will not be set"
    )
    pod_labels = template.metadata.labels
    assert pod_labels is not None, "pod template labels dict is None"
    assert pod_labels.get("app") == "sec-review-worker", (
        f"Expected pod label 'app=sec-review-worker', got labels={pod_labels!r}. "
        "Without this label the NetworkPolicy ingress rule (app: sec-review-worker) "
        "matches zero pods and HTTP upload traffic is silently blocked."
    )


def test_create_k8s_job_pod_labels_match_job_labels(tmp_path: Path, temp_db: Database):
    """Pod template labels must match Job metadata labels (they share the same dict)."""
    assert coord_module.K8S_AVAILABLE

    coord = _make_coordinator(tmp_path, temp_db)
    run = _make_run(result_transport="http")

    captured: list = []

    def _capture_job(namespace, job):
        captured.append(job)

    coord.k8s_client.create_namespaced_job.side_effect = _capture_job
    coord._create_k8s_job(EXPERIMENT_ID, run)

    job = captured[0]
    job_labels = job.metadata.labels
    pod_labels = job.spec.template.metadata.labels

    # Both dicts must contain the same keys and values.
    assert job_labels == pod_labels, (
        f"Job labels {job_labels!r} != pod template labels {pod_labels!r}"
    )


def test_create_k8s_job_pod_labels_present_without_http_transport(
    tmp_path: Path, temp_db: Database
):
    """Pod template labels must be set even when using PVC transport (not just HTTP)."""
    assert coord_module.K8S_AVAILABLE

    coord = _make_coordinator(tmp_path, temp_db)
    coord.result_transport = "pvc"
    run = _make_run(result_transport="pvc")

    captured: list = []

    def _capture_job(namespace, job):
        captured.append(job)

    coord.k8s_client.create_namespaced_job.side_effect = _capture_job
    coord._create_k8s_job(EXPERIMENT_ID, run)

    job = captured[0]
    assert job.spec.template.metadata is not None
    assert job.spec.template.metadata.labels.get("app") == "sec-review-worker"
