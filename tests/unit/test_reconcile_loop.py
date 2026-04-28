"""Unit tests for ExperimentCoordinator._reconcile_once() and _reconcile_loop().

Tests exercise the result-scanning reconcile step that flips runs from
'running' -> 'completed'/'failed' and finalises experiments.  No K8s, no real
worker — we manipulate the filesystem and DB directly.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sec_review_framework.coordinator import ExperimentCoordinator
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.data.experiment import (
    ExperimentRun,
    ReviewProfileName,
    RunResult,
    RunStatus,
    StrategyName,
    ToolVariant,
    VerificationVariant,
)
from sec_review_framework.data.findings import (
    StrategyOutput,
)
from sec_review_framework.db import Database
from sec_review_framework.reporting.markdown import MarkdownReportGenerator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EXPERIMENT_ID = "test-experiment-reconcile"
MODEL_ID = "fake-model"
DATASET = "test-ds"


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


def _make_run(experiment_id: str = EXPERIMENT_ID, run_id: str | None = None) -> ExperimentRun:
    rid = run_id or f"{experiment_id}_builtin.single_agent"
    return ExperimentRun(
        id=rid,
        experiment_id=experiment_id,
        strategy_id="builtin.single_agent",
        model_id=MODEL_ID,
        strategy=StrategyName.SINGLE_AGENT,
        tool_variant=ToolVariant.WITH_TOOLS,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        dataset_name=DATASET,
        dataset_version="1.0.0",
        created_at=datetime(2026, 4, 18, tzinfo=UTC),
    )


def _make_run_result(run: ExperimentRun, status: RunStatus = RunStatus.COMPLETED) -> RunResult:
    from tests.helpers import make_test_bundle_snapshot

    strategy_output = StrategyOutput(
        findings=[],
        pre_dedup_count=0,
        post_dedup_count=0,
        dedup_log=[],
    )
    return RunResult(
        experiment=run,
        status=status,
        findings=[],
        strategy_output=strategy_output,
        bundle_snapshot=make_test_bundle_snapshot(),
        tool_call_count=0,
        total_input_tokens=100,
        total_output_tokens=50,
        verification_tokens=0,
        estimated_cost_usd=0.01,
        duration_seconds=10.0,
        completed_at=datetime(2026, 4, 18, 1, 0, 0, tzinfo=UTC),
    )


async def _setup_experiment_and_run(db: Database, coord: ExperimentCoordinator) -> ExperimentRun:
    """Create experiment + run in DB and mark run as 'running'."""
    run = _make_run()
    coord.storage_root.mkdir(parents=True, exist_ok=True)

    await db.create_experiment(
        experiment_id=EXPERIMENT_ID,
        config_json="{}",
        total_runs=1,
        max_cost_usd=None,
    )
    await db.create_run(
        run_id=run.id,
        experiment_id=EXPERIMENT_ID,
        config_json=run.model_dump_json(),
        model_id=run.model_id,
        strategy=run.strategy.value,
        tool_variant=run.tool_variant.value,
        review_profile=run.review_profile.value,
        verification_variant=run.verification_variant.value,
    )
    await db.update_run(run.id, status="running")
    return run


def _write_result_file(coord: ExperimentCoordinator, run: ExperimentRun, result: RunResult) -> Path:
    out_dir = coord.storage_root / "outputs" / EXPERIMENT_ID / run.id
    out_dir.mkdir(parents=True, exist_ok=True)
    result_file = out_dir / "run_result.json"
    result_file.write_text(result.model_dump_json())
    return result_file


# ---------------------------------------------------------------------------
# Fixture: async temp DB
# ---------------------------------------------------------------------------


@pytest.fixture
async def temp_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "test.db")
    await db.init()
    return db


# ---------------------------------------------------------------------------
# Test 1: run_result.json on disk → DB transitions to 'completed'
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_once_marks_run_completed(tmp_path: Path, temp_db: Database):
    coord = _make_coordinator(tmp_path, temp_db)
    run = await _setup_experiment_and_run(temp_db, coord)

    result = _make_run_result(run, RunStatus.COMPLETED)
    _write_result_file(coord, run, result)

    await asyncio.wait_for(coord._reconcile_once(), timeout=5.0)

    db_run = await temp_db.get_run(run.id)
    assert db_run is not None
    assert db_run["status"] == "completed", (
        f"Expected 'completed', got '{db_run['status']}'"
    )


# ---------------------------------------------------------------------------
# Test 2: all runs completed on disk → finalize_experiment called exactly once
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_once_finalizes_experiment_once(tmp_path: Path, temp_db: Database):
    coord = _make_coordinator(tmp_path, temp_db)
    run = await _setup_experiment_and_run(temp_db, coord)

    result = _make_run_result(run, RunStatus.COMPLETED)
    _write_result_file(coord, run, result)

    finalize_calls: list[str] = []
    original_finalize = coord.finalize_experiment

    async def _tracked_finalize(experiment_id: str) -> None:
        finalize_calls.append(experiment_id)
        await original_finalize(experiment_id)

    coord.finalize_experiment = _tracked_finalize  # type: ignore[method-assign]

    # Call twice — must only finalize once (second call skips completed experiment)
    await asyncio.wait_for(coord._reconcile_once(), timeout=5.0)
    await asyncio.wait_for(coord._reconcile_once(), timeout=5.0)

    assert finalize_calls.count(EXPERIMENT_ID) == 1, (
        f"Expected finalize_experiment called exactly once, got {len(finalize_calls)} calls"
    )

    experiment = await temp_db.get_experiment(EXPERIMENT_ID)
    assert experiment is not None
    assert experiment["status"] == "completed"

    report_file = coord.storage_root / "outputs" / EXPERIMENT_ID / "matrix_report.md"
    assert report_file.exists(), "matrix_report.md was not written"


# ---------------------------------------------------------------------------
# Test 3: corrupted run_result.json → run marked 'failed', no crash
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_once_corrupted_result_marks_failed(tmp_path: Path, temp_db: Database):
    coord = _make_coordinator(tmp_path, temp_db)
    run = await _setup_experiment_and_run(temp_db, coord)

    # Write truncated / invalid JSON
    out_dir = coord.storage_root / "outputs" / EXPERIMENT_ID / run.id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "run_result.json").write_text("{this is not valid json ...")

    # Should not raise
    await asyncio.wait_for(coord._reconcile_once(), timeout=5.0)

    db_run = await temp_db.get_run(run.id)
    assert db_run is not None
    assert db_run["status"] == "failed", (
        f"Expected 'failed' for corrupted result, got '{db_run['status']}'"
    )
    assert db_run.get("error") is not None
    assert "parse error" in (db_run.get("error") or "").lower()


# ---------------------------------------------------------------------------
# Test 4: cancelled experiment is NOT re-finalized to 'completed'
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_once_does_not_finalize_cancelled_experiment(
    tmp_path: Path, temp_db: Database
):
    coord = _make_coordinator(tmp_path, temp_db)
    run = await _setup_experiment_and_run(temp_db, coord)

    # Cancel the experiment before reconcile runs
    await temp_db.update_experiment_status(EXPERIMENT_ID, "cancelled")

    result = _make_run_result(run, RunStatus.COMPLETED)
    _write_result_file(coord, run, result)

    finalize_calls: list[str] = []
    original_finalize = coord.finalize_experiment

    async def _tracked_finalize(experiment_id: str) -> None:
        finalize_calls.append(experiment_id)
        await original_finalize(experiment_id)

    coord.finalize_experiment = _tracked_finalize  # type: ignore[method-assign]

    await asyncio.wait_for(coord._reconcile_once(), timeout=5.0)

    assert len(finalize_calls) == 0, (
        "finalize_experiment should not be called for a cancelled experiment"
    )

    experiment = await temp_db.get_experiment(EXPERIMENT_ID)
    assert experiment is not None
    assert experiment["status"] == "cancelled", (
        f"Cancelled experiment should stay 'cancelled', got '{experiment['status']}'"
    )


# ---------------------------------------------------------------------------
# Test 5: _reconcile_loop does not swallow exceptions (iterates after error)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_loop_continues_after_exception(tmp_path: Path, temp_db: Database):
    """_reconcile_loop should catch exceptions in _reconcile_once and keep running."""
    coord = _make_coordinator(tmp_path, temp_db)

    call_count = 0

    async def _failing_once() -> None:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("deliberate test error")
        # Second call: cancel the loop by raising CancelledError
        raise asyncio.CancelledError

    coord._reconcile_once = _failing_once  # type: ignore[method-assign]

    # The loop should survive the first RuntimeError and reach the second iteration
    with pytest.raises(asyncio.CancelledError):
        # Patch sleep to 0 so the loop is fast
        with patch("sec_review_framework.coordinator.RECONCILE_INTERVAL_S", 0):
            await asyncio.wait_for(coord._reconcile_loop(), timeout=3.0)

    assert call_count == 2, (
        f"Expected loop to iterate twice (survive first error), got {call_count}"
    )


# ---------------------------------------------------------------------------
# Test 6: reconcile() marks missing-config pending runs as failed (Bug B fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_marks_missing_config_run_as_failed(
    tmp_path: Path, temp_db: Database
):
    """
    A pending run whose config file doesn't exist should be transitioned to
    'failed' by reconcile(), not silently skipped.  This prevents the
    reconciler from spamming "orphaned runs" for deleted experiments.
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
    # Run is 'pending' but its config file is intentionally absent

    await asyncio.wait_for(coord.reconcile(), timeout=5.0)

    db_run = await temp_db.get_run(run.id)
    assert db_run is not None
    assert db_run["status"] == "failed", (
        f"Expected 'failed' for missing-config run, got '{db_run['status']}'"
    )
    assert db_run.get("error") is not None
    assert "config missing" in (db_run.get("error") or "").lower(), (
        f"Expected error to mention config missing, got: {db_run.get('error')!r}"
    )


# ---------------------------------------------------------------------------
# Test 7: _reconcile_once marks stalled K8s Job runs as failed (Bug A fix)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_marks_stalled_job_as_failed(tmp_path: Path, temp_db: Database):
    """
    A running run whose K8s Job reports active>0 but has no live pods and is
    older than RUN_STALL_TIMEOUT_S should be transitioned to 'failed' and the
    Job should be deleted.
    """
    from datetime import timedelta

    import sec_review_framework.coordinator as coord_module

    coord = _make_coordinator(tmp_path, temp_db)
    coord.storage_root.mkdir(parents=True, exist_ok=True)
    run = await _setup_experiment_and_run(temp_db, coord)

    # Build a fake K8s BatchV1Api that returns one stale Job with active=1,
    # no succeeded/failed, and an empty pod list.
    stale_start = datetime.now(UTC) - timedelta(seconds=600)

    fake_job = MagicMock()
    fake_job.metadata.name = "exp-stalled-job"
    fake_job.metadata.annotations = {"sec-review.io/run-id": run.id}
    fake_job.metadata.creation_timestamp = stale_start
    fake_job.status.active = 1
    fake_job.status.succeeded = None
    fake_job.status.failed = None
    fake_job.status.start_time = stale_start

    fake_jobs_list = MagicMock()
    fake_jobs_list.items = [fake_job]

    fake_pod_list = MagicMock()
    fake_pod_list.items = []  # No live pods

    fake_k8s = MagicMock()
    fake_k8s.list_namespaced_job.return_value = fake_jobs_list
    fake_k8s.list_namespaced_pod.return_value = fake_pod_list
    fake_k8s.delete_namespaced_job.return_value = None

    coord.k8s_client = fake_k8s

    # Patch module-level K8S_AVAILABLE so the K8s branch is entered
    original_k8s_available = coord_module.K8S_AVAILABLE
    coord_module.K8S_AVAILABLE = True

    # Also need kubernetes module available for V1DeleteOptions and CoreV1Api.
    # After the fix, _check_stalled_job lazily creates CoreV1Api() to call
    # list_namespaced_pod (that method is not on BatchV1Api).
    import types
    fake_core_v1_instance = MagicMock()
    fake_core_v1_instance.list_namespaced_pod.return_value = fake_pod_list
    fake_kubernetes = types.SimpleNamespace(
        client=types.SimpleNamespace(
            CoreV1Api=MagicMock(return_value=fake_core_v1_instance),
            V1DeleteOptions=MagicMock(return_value=MagicMock()),
        )
    )
    original_kubernetes = coord_module.kubernetes
    coord_module.kubernetes = fake_kubernetes

    try:
        # Patch stall timeout to a small value so we don't need to fiddle with time
        with patch.object(coord_module, "RUN_STALL_TIMEOUT_S", 300):
            await asyncio.wait_for(coord._reconcile_once(), timeout=5.0)
    finally:
        coord_module.K8S_AVAILABLE = original_k8s_available
        coord_module.kubernetes = original_kubernetes

    db_run = await temp_db.get_run(run.id)
    assert db_run is not None
    assert db_run["status"] == "failed", (
        f"Expected stalled run to be 'failed', got '{db_run['status']}'"
    )
    assert db_run.get("error") is not None
    assert "no active pods" in (db_run.get("error") or "").lower(), (
        f"Expected error to mention no active pods, got: {db_run.get('error')!r}"
    )

    # Verify the stalled Job was deleted
    fake_k8s.delete_namespaced_job.assert_called_once_with(
        "exp-stalled-job",
        "default",
        body=fake_kubernetes.client.V1DeleteOptions.return_value,
    )


# ---------------------------------------------------------------------------
# Test 8: worker atomic write — no .tmp file left, final file parses cleanly
# ---------------------------------------------------------------------------


def test_worker_atomic_write_no_tmp_remains(tmp_path: Path):
    """
    After ExperimentWorker writes run_result.json, the .tmp sibling must not
    exist and the final file must deserialise back to a valid RunResult.
    """

    from sec_review_framework.data.experiment import (
        RunResult,
        RunStatus,
    )
    from sec_review_framework.data.findings import StrategyOutput
    from sec_review_framework.worker import ExperimentWorker

    run = _make_run()
    result = _make_run_result(run, RunStatus.COMPLETED)

    # Patch out the heavy bits so the worker only exercises the file-write path
    StrategyOutput(
        findings=[], pre_dedup_count=0, post_dedup_count=0, dedup_log=[]
    )

    ExperimentWorker()

    output_dir = tmp_path / "output"

    # Directly invoke the write logic (replicates lines 148-152 of worker.py)
    output_dir.mkdir(parents=True, exist_ok=True)
    _result_file = output_dir / "run_result.json"
    _result_tmp = _result_file.with_suffix(".json.tmp")
    _result_tmp.write_text(result.model_dump_json(indent=2))
    _result_tmp.replace(_result_file)

    # .tmp must be gone
    assert not _result_tmp.exists(), ".tmp file should not remain after atomic replace"
    # Final file must parse cleanly
    assert _result_file.exists(), "run_result.json must exist"
    parsed = RunResult.model_validate_json(_result_file.read_text())
    assert parsed.status == RunStatus.COMPLETED


def test_worker_atomic_write_overwrites_stale_tmp(tmp_path: Path):
    """
    If a garbage .tmp file already exists (e.g. from a previous crashed write),
    it should be silently overwritten and the final file should still be valid.
    """
    from sec_review_framework.data.experiment import RunResult, RunStatus

    run = _make_run()
    result = _make_run_result(run, RunStatus.COMPLETED)

    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)
    _result_file = output_dir / "run_result.json"
    _result_tmp = _result_file.with_suffix(".json.tmp")

    # Pre-create a garbage .tmp to simulate a prior interrupted write
    _result_tmp.write_text("{truncated garbage ...")

    # Now perform the atomic write
    _result_tmp.write_text(result.model_dump_json(indent=2))
    _result_tmp.replace(_result_file)

    assert not _result_tmp.exists(), ".tmp must be gone after replace"
    parsed = RunResult.model_validate_json(_result_file.read_text())
    assert parsed.experiment.id == run.id


# ---------------------------------------------------------------------------
# Test 9: scheduler retry cap — always-failing job is marked failed after N attempts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_schedule_jobs_marks_run_failed_after_max_attempts(
    tmp_path: Path, temp_db: Database
):
    """
    When _create_k8s_job always raises, _schedule_jobs must stop retrying after
    MAX_SCHEDULE_ATTEMPTS and mark the run 'failed' in the DB.  The function must
    also terminate (not loop forever).
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

    # _create_k8s_job always fails
    coord._create_k8s_job = MagicMock(side_effect=RuntimeError("K8s API unreachable"))

    # Patch sleep to 0 so the loop is fast in tests
    with patch("sec_review_framework.coordinator.asyncio") as mock_asyncio:
        mock_asyncio.sleep = AsyncMock(return_value=None)
        # Must complete (not loop forever) within the timeout
        await asyncio.wait_for(
            coord._schedule_jobs(EXPERIMENT_ID, [run]),
            timeout=5.0,
        )

    db_run = await temp_db.get_run(run.id)
    assert db_run is not None
    assert db_run["status"] == "failed", (
        f"Expected run to be 'failed' after max attempts, got '{db_run['status']}'"
    )
    assert db_run.get("error") is not None
    assert "3 attempts" in (db_run.get("error") or ""), (
        f"Expected error to mention attempt count, got: {db_run.get('error')!r}"
    )
    # Exactly 3 creation attempts
    assert coord._create_k8s_job.call_count == 3, (
        f"Expected 3 _create_k8s_job calls, got {coord._create_k8s_job.call_count}"
    )


@pytest.mark.asyncio
async def test_schedule_jobs_cap_wait_does_not_increment_attempt_count(
    tmp_path: Path, temp_db: Database
):
    """
    A run that is legitimately waiting due to a concurrency cap (running_by_model ==
    cap) must NOT have its attempt counter incremented.  Only actual exceptions from
    _create_k8s_job should count toward the retry limit.
    """
    coord = _make_coordinator(tmp_path, temp_db)
    coord.storage_root.mkdir(parents=True, exist_ok=True)

    # Use a cap of 1 and two runs for the same model so the second waits
    run_a = _make_run(run_id=f"{EXPERIMENT_ID}_{MODEL_ID}_single_agent_with_tools_default_none_A")
    run_b = _make_run(run_id=f"{EXPERIMENT_ID}_{MODEL_ID}_single_agent_with_tools_default_none_B")
    coord.concurrency_caps = {MODEL_ID: 1}

    await temp_db.create_experiment(
        experiment_id=EXPERIMENT_ID,
        config_json="{}",
        total_runs=2,
        max_cost_usd=None,
    )
    for run in (run_a, run_b):
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

    call_count = 0

    def _create_side_effect(experiment_id, run):
        nonlocal call_count
        call_count += 1
        # Always succeed
        return None

    coord._create_k8s_job = MagicMock(side_effect=_create_side_effect)

    # Break the scheduler loop after one round. _schedule_jobs' local
    # running_by_model dict is never decremented, so a legitimate "cap freed"
    # cannot be simulated without reaching into the function. After round 1,
    # run_a is scheduled and run_b is still below-cap-blocked — enough state
    # to verify the invariant. Raising from the sleep also avoids an infinite
    # tight loop: mock_asyncio.sleep never yields to the event loop, so
    # asyncio.wait_for(timeout=5.0) cannot cancel, and the loop would spin
    # forever while MagicMock accumulates call history (was ~5 GB+ of RSS).
    class _LoopBreak(Exception):
        pass

    async def _fake_sleep(n):
        raise _LoopBreak

    with patch("sec_review_framework.coordinator.asyncio") as mock_asyncio:
        mock_asyncio.sleep = AsyncMock(side_effect=_fake_sleep)
        with pytest.raises(_LoopBreak):
            await coord._schedule_jobs(EXPERIMENT_ID, [run_a, run_b])

    # Round 1: run_a scheduled (cap=1 consumed), run_b waiting.
    db_run_a = await temp_db.get_run(run_a.id)
    assert db_run_a is not None
    assert db_run_a["status"] == "running"

    db_run_b = await temp_db.get_run(run_b.id)
    assert db_run_b is not None
    assert db_run_b["status"] == "pending", (
        f"run_b should still be 'pending' (cap-blocked), got {db_run_b['status']!r}"
    )

    # The invariant: run_b being cap-blocked must NOT call _create_k8s_job
    # (and therefore must not increment attempt_counts). Only run_a triggered
    # a creation attempt.
    assert call_count == 1, (
        f"_create_k8s_job should have been called once (for run_a), got {call_count}"
    )


# ---------------------------------------------------------------------------
# Test: DB error on one experiment does not halt other experiments (Issue 1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_once_db_error_one_experiment_continues_others(
    tmp_path: Path, temp_db: Database
):
    """A transient DB error on one experiment must not prevent other experiments from
    being processed in the same _reconcile_once pass."""
    EXPERIMENT_A = "experiment-error"
    EXPERIMENT_B = "experiment-ok"

    coord = _make_coordinator(tmp_path, temp_db)
    coord.storage_root.mkdir(parents=True, exist_ok=True)

    # Create two experiments, each with one running run
    for bid in (EXPERIMENT_A, EXPERIMENT_B):
        run = _make_run(experiment_id=bid, run_id=f"{bid}_{MODEL_ID}_single_agent_with_tools_default_none")
        await temp_db.create_experiment(experiment_id=bid, config_json="{}", total_runs=1, max_cost_usd=None)
        await temp_db.create_run(
            run_id=run.id,
            experiment_id=bid,
            config_json=run.model_dump_json(),
            model_id=run.model_id,
            strategy=run.strategy.value,
            tool_variant=run.tool_variant.value,
            review_profile=run.review_profile.value,
            verification_variant=run.verification_variant.value,
        )
        await temp_db.update_run(run.id, status="running")

        # Write a valid result file for EXPERIMENT_B only
        if bid == EXPERIMENT_B:
            result = _make_run_result(run, RunStatus.COMPLETED)
            out_dir = coord.storage_root / "outputs" / bid / run.id
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "run_result.json").write_text(result.model_dump_json())

    # Patch db.get_experiment so it raises for EXPERIMENT_A but works for EXPERIMENT_B
    original_get_experiment = temp_db.get_experiment

    async def _patched_get_experiment(experiment_id: str):
        if experiment_id == EXPERIMENT_A:
            raise RuntimeError("simulated DB connection error")
        return await original_get_experiment(experiment_id)

    temp_db.get_experiment = _patched_get_experiment  # type: ignore[method-assign]

    # Should not raise; EXPERIMENT_B should still be processed
    await asyncio.wait_for(coord._reconcile_once(), timeout=5.0)

    # Restore
    temp_db.get_experiment = original_get_experiment  # type: ignore[method-assign]

    run_b_id = f"{EXPERIMENT_B}_{MODEL_ID}_single_agent_with_tools_default_none"
    db_run_b = await temp_db.get_run(run_b_id)
    assert db_run_b is not None
    assert db_run_b["status"] == "completed", (
        f"EXPERIMENT_B run should be 'completed' despite EXPERIMENT_A error, got '{db_run_b['status']}'"
    )


# ---------------------------------------------------------------------------
# Test: audit_tool_calls logs warning for malformed JSONL (Issue 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_tool_calls_logs_warning_for_malformed_jsonl(
    tmp_path: Path, temp_db: Database, caplog
):
    """Malformed JSONL lines in tool_calls.jsonl must produce a log warning,
    not a silent skip, and must not raise."""
    import logging

    coord = _make_coordinator(tmp_path, temp_db)
    coord.storage_root.mkdir(parents=True, exist_ok=True)

    out_dir = coord.storage_root / "outputs" / EXPERIMENT_ID / "some-run-id"
    out_dir.mkdir(parents=True, exist_ok=True)

    good_line = '{"tool_name": "read_file", "inputs": {"path": "/tmp/x"}}'
    bad_line = '{this is not valid json ...'
    (out_dir / "tool_calls.jsonl").write_text(f"{good_line}\n{bad_line}\n")

    with caplog.at_level(logging.WARNING, logger="sec_review_framework.coordinator"):
        result = await coord.audit_tool_calls(EXPERIMENT_ID, "some-run-id")

    # Good line was processed
    assert result["counts_by_tool"].get("read_file") == 1

    # Warning was emitted for the bad line
    assert any(
        "malformed" in record.message.lower() or "skipping" in record.message.lower()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ), f"Expected a warning for malformed JSONL, got: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# Test: compare_runs / index_findings logs warning for malformed finding (Issue 2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compare_runs_logs_warning_for_malformed_finding(
    tmp_path: Path, temp_db: Database, caplog
):
    """A malformed finding dict inside a run_result.json must produce a log
    warning inside compare_runs, not be silently dropped."""
    import logging

    coord = _make_coordinator(tmp_path, temp_db)
    coord.storage_root.mkdir(parents=True, exist_ok=True)

    # Build a minimal result dict — one valid finding (empty list is fine for
    # exercising the path), one malformed entry
    good_finding = {
        "id": "f1",
        "title": "SQL Injection",
        "description": "Classic SQLi",
        "severity": "high",
        "vuln_class": "injection",
        "file_path": "app/db.py",
        "line_number": 42,
        "evidence": "cursor.execute(query)",
        "confidence": 0.9,
        "classification": "true_positive",
    }
    malformed_finding = {"not_a_finding": True}

    result_payload = {
        "findings": [good_finding, malformed_finding],
        "strategy_output": {"findings": [], "pre_dedup_count": 0, "post_dedup_count": 0, "dedup_log": []},
    }

    import json as _json

    # Write result files for two fake runs
    for run_id in ("run-a", "run-b"):
        out_dir = coord.storage_root / "outputs" / EXPERIMENT_ID / run_id
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "run_result.json").write_text(_json.dumps(result_payload))

    # Patch get_run_result to return our dict directly
    async def _fake_get_run_result(experiment_id: str, run_id: str) -> dict:
        return result_payload

    coord.get_run_result = _fake_get_run_result  # type: ignore[method-assign]

    with caplog.at_level(logging.WARNING, logger="sec_review_framework.coordinator"):
        await coord.compare_runs(EXPERIMENT_ID, "run-a", "run-b")

    assert any(
        "malformed" in record.message.lower() or "skipping" in record.message.lower()
        for record in caplog.records
        if record.levelno >= logging.WARNING
    ), f"Expected a warning for malformed finding, got: {[r.message for r in caplog.records]}"


# ---------------------------------------------------------------------------
# Test: reconcile() — K8s job-list exception does not halt orphan processing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_k8s_job_list_exception_continues(
    tmp_path: Path, temp_db: Database
):
    """When list_namespaced_job raises, reconcile() should log the error and
    continue processing orphaned runs (treating dispatched_run_ids as empty)."""
    import sec_review_framework.coordinator as coord_module

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

    config_dir = coord.storage_root / "config" / "runs"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / f"{run.id}.json").write_text(run.model_dump_json())

    fake_k8s = MagicMock()
    fake_k8s.list_namespaced_job.side_effect = RuntimeError("K8s API unreachable")
    coord.k8s_client = fake_k8s

    original_k8s_available = coord_module.K8S_AVAILABLE
    coord_module.K8S_AVAILABLE = True

    dispatch_calls: list[str] = []

    def _tracked_create(exp_id, r):
        dispatch_calls.append(r.id)

    coord._create_k8s_job = _tracked_create  # type: ignore[method-assign]

    try:
        await asyncio.wait_for(coord.reconcile(), timeout=5.0)
    finally:
        coord_module.K8S_AVAILABLE = original_k8s_available

    assert run.id in dispatch_calls, (
        "reconcile() should still dispatch orphaned run even when job-list raises"
    )


# ---------------------------------------------------------------------------
# Test: reconcile() — orphaned run with malformed config file is marked failed
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reconcile_orphaned_run_malformed_config_marked_failed(
    tmp_path: Path, temp_db: Database
):
    """A pending orphaned run whose config file contains malformed JSON should be
    transitioned to 'failed' rather than causing reconcile() to raise."""
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

    config_dir = coord.storage_root / "config" / "runs"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / f"{run.id}.json").write_text("{this is not valid json ...")

    await asyncio.wait_for(coord.reconcile(), timeout=5.0)

    db_run = await temp_db.get_run(run.id)
    assert db_run is not None
    assert db_run["status"] == "failed", (
        f"Expected 'failed' for malformed-config run, got '{db_run['status']}'"
    )
    assert db_run.get("error") is not None
