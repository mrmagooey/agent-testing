"""Regression test for the DevDocs emptyDir fallback in coordinator worker pod specs.

Bug: In e2e / kind clusters where the devdocs-sync Job is not run
(workerTools.devdocs.enabled=false), the devdocs docset directory is absent
from the shared PVC.  Worker pods that include ToolExtension.DEVDOCS then
crash with:

    RuntimeError: DevDocs root not mounted at '/data/devdocs'. ...

Fix: ExperimentCoordinator._create_k8s_job checks whether the devdocs root exists
on its own filesystem (the coordinator also mounts the shared PVC). When it
does NOT exist, it injects an emptyDir volume+mount at the devdocs path so
the worker process finds the directory and the DevDocs MCP server starts
cleanly (serving no docsets, which is acceptable for the plumbing test).

This test verifies:
1. When the devdocs root is ABSENT, an emptyDir volume named "devdocs-empty"
   and a corresponding volumeMount at the devdocs path are added to the
   worker pod spec.
2. When the devdocs root IS PRESENT, no emptyDir is injected (production
   behaviour is unchanged — the worker reaches the docsets via the shared PVC).
3. When DEVDOCS is not in tool_extensions, no emptyDir is added regardless
   of whether the path exists.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# kubernetes client is optional; skip the whole module when absent.
kubernetes = pytest.importorskip("kubernetes", reason="kubernetes package not installed")

from sec_review_framework.coordinator import ExperimentCoordinator
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.data.experiment import (
    ExperimentRun,
    ReviewProfileName,
    StrategyName,
    ToolExtension,
    ToolVariant,
    VerificationVariant,
)
from sec_review_framework.reporting.markdown import MarkdownReportGenerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MODEL_ID = "test-model"
EXPERIMENT_ID = "test-experiment-devdocs"
DATASET = "test-ds"


def _make_coordinator(tmp_path: Path) -> ExperimentCoordinator:
    """Create a ExperimentCoordinator with mock k8s and db clients."""
    mock_k8s = MagicMock()
    mock_db = MagicMock()
    coord = ExperimentCoordinator(
        k8s_client=mock_k8s,
        storage_root=tmp_path / "storage",
        concurrency_caps={},
        worker_image="sec-review-worker:e2e",
        namespace="sec-review-e2e",
        db=mock_db,
        reporter=MarkdownReportGenerator(),
        cost_calculator=CostCalculator(
            pricing={MODEL_ID: ModelPricing(input_per_million=0.0, output_per_million=0.0)}
        ),
        default_cap=4,
        pvc_name="sec-review-e2e-data",
        llm_secret_name="llm-api-keys",
        config_map_name="experiment-config",
        worker_image_pull_policy="IfNotPresent",
    )
    return coord


def _make_run(tool_extensions: frozenset[ToolExtension] = frozenset()) -> ExperimentRun:
    ext_suffix = (
        "_ext-" + "-".join(sorted(e.value for e in tool_extensions))
        if tool_extensions else ""
    )
    return ExperimentRun(
        id=f"{EXPERIMENT_ID}_{MODEL_ID}_single_agent_with_tools_default_none{ext_suffix}",
        experiment_id=EXPERIMENT_ID,
        strategy_id="builtin.single_agent",
        model_id=MODEL_ID,
        strategy=StrategyName.SINGLE_AGENT,
        tool_variant=ToolVariant.WITH_TOOLS,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        dataset_name=DATASET,
        dataset_version="1.0.0",
        created_at=datetime(2026, 4, 20, tzinfo=timezone.utc),
        tool_extensions=tool_extensions,
    )


def _call_create_k8s_job(coordinator: ExperimentCoordinator, run: ExperimentRun) -> object:
    """Call _create_k8s_job and return the V1Job passed to create_namespaced_job."""
    coordinator._create_k8s_job(EXPERIMENT_ID, run)
    create_calls = coordinator.k8s_client.create_namespaced_job.call_args_list
    assert create_calls, "_create_k8s_job did not call create_namespaced_job"
    # create_namespaced_job(namespace, job) — job is the second positional arg
    return create_calls[-1][0][1]  # latest call, positional args, second arg


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_devdocs_emptydir_injected_when_root_absent(tmp_path: Path) -> None:
    """emptyDir must be added when DEVDOCS is requested and root path is absent."""
    coord = _make_coordinator(tmp_path)
    run = _make_run(tool_extensions=frozenset({ToolExtension.DEVDOCS}))

    # The devdocs root path does not exist — simulate e2e/kind environment.
    nonexistent_devdocs = tmp_path / "nonexistent-devdocs"
    assert not nonexistent_devdocs.exists()

    with patch.dict(os.environ, {"DEVDOCS_ROOT": str(nonexistent_devdocs)}):
        job = _call_create_k8s_job(coord, run)

    pod_spec = job.spec.template.spec

    # Verify emptyDir volume is present.
    volume_names = [v.name for v in pod_spec.volumes]
    assert "devdocs-empty" in volume_names, (
        f"Expected 'devdocs-empty' volume when root is absent; got: {volume_names}"
    )

    # Verify volumeMount is present at the expected path.
    mounts = pod_spec.containers[0].volume_mounts
    devdocs_mounts = [m for m in mounts if m.name == "devdocs-empty"]
    assert devdocs_mounts, "Expected volumeMount for 'devdocs-empty'"
    assert devdocs_mounts[0].mount_path == str(nonexistent_devdocs), (
        f"Expected mount_path={nonexistent_devdocs!r}, got {devdocs_mounts[0].mount_path!r}"
    )

    # Verify it is an emptyDir, not a PVC or configMap.
    devdocs_vol = next(v for v in pod_spec.volumes if v.name == "devdocs-empty")
    assert devdocs_vol.empty_dir is not None, (
        "Expected emptyDir volume source; got something else"
    )
    assert devdocs_vol.persistent_volume_claim is None, (
        "emptyDir volume must not have a PVC source"
    )


def test_devdocs_emptydir_not_injected_when_root_exists(tmp_path: Path) -> None:
    """When devdocs root IS present (production), no emptyDir should be added."""
    coord = _make_coordinator(tmp_path)
    run = _make_run(tool_extensions=frozenset({ToolExtension.DEVDOCS}))

    # Create the devdocs directory so it exists (simulates production with sync job).
    existing_devdocs = tmp_path / "existing-devdocs"
    existing_devdocs.mkdir(parents=True)
    assert existing_devdocs.exists()

    with patch.dict(os.environ, {"DEVDOCS_ROOT": str(existing_devdocs)}):
        job = _call_create_k8s_job(coord, run)

    pod_spec = job.spec.template.spec

    volume_names = [v.name for v in pod_spec.volumes]
    assert "devdocs-empty" not in volume_names, (
        "emptyDir must NOT be added when devdocs root already exists (production path)"
    )

    mounts = pod_spec.containers[0].volume_mounts
    assert not any(m.name == "devdocs-empty" for m in mounts), (
        "emptyDir volumeMount must NOT be added when devdocs root already exists"
    )


def test_no_devdocs_emptydir_when_extension_not_requested(tmp_path: Path) -> None:
    """When DEVDOCS is not in tool_extensions, no emptyDir is added regardless."""
    coord = _make_coordinator(tmp_path)
    run = _make_run(tool_extensions=frozenset())  # no extensions

    nonexistent_devdocs = tmp_path / "nonexistent-devdocs"
    assert not nonexistent_devdocs.exists()

    with patch.dict(os.environ, {"DEVDOCS_ROOT": str(nonexistent_devdocs)}):
        job = _call_create_k8s_job(coord, run)

    pod_spec = job.spec.template.spec

    volume_names = [v.name for v in pod_spec.volumes]
    assert "devdocs-empty" not in volume_names, (
        "emptyDir must NOT be added when DEVDOCS extension is not requested"
    )


def test_all_extensions_emptydir_injected_when_root_absent(tmp_path: Path) -> None:
    """Regression for the exact Cell B scenario: all extensions, absent devdocs root.

    This directly mirrors the test_matrix_all_dims Cell B config that originally
    failed with RuntimeError: DevDocs root not mounted at '/data/devdocs'.
    """
    coord = _make_coordinator(tmp_path)
    run = _make_run(
        tool_extensions=frozenset({
            ToolExtension.TREE_SITTER,
            ToolExtension.LSP,
            ToolExtension.DEVDOCS,
        })
    )

    nonexistent_devdocs = tmp_path / "no-devdocs"
    assert not nonexistent_devdocs.exists()

    with patch.dict(os.environ, {"DEVDOCS_ROOT": str(nonexistent_devdocs)}):
        job = _call_create_k8s_job(coord, run)

    pod_spec = job.spec.template.spec
    volume_names = [v.name for v in pod_spec.volumes]
    assert "devdocs-empty" in volume_names, (
        f"Cell B (all-extensions, absent root): expected 'devdocs-empty' in volumes; "
        f"got {volume_names}"
    )

    mounts = pod_spec.containers[0].volume_mounts
    assert any(m.name == "devdocs-empty" for m in mounts), (
        "Cell B: expected 'devdocs-empty' volumeMount on worker container"
    )
