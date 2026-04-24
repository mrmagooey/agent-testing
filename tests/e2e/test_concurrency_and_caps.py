"""E2E tests for concurrency caps and spend caps in ExperimentCoordinator.

Gap #8 of 13 in backend e2e coverage.

Concurrency cap behaviour
-------------------------
``_schedule_jobs`` respects a per-model (or default) concurrency cap.  With a
cap of N and N+1 runs submitted at once, the scheduler schedules N runs
(marking them "running") and leaves the remainder in "pending" state.  The
background loop then polls (every 10 s in production) until a slot opens.

Spend cap behaviour
-------------------
``max_experiment_cost_usd`` on ``ExperimentMatrix`` sets the spend cap for the
experiment.  ``ExperimentCostTracker.record_job_cost`` increments spent_usd and
returns ``True`` when the cap is crossed.  ``_reconcile_experiment_once`` calls
``cancel_experiment`` when the tracker reports the cap exceeded.  The spend cap
is NOT enforced at submission time — it acts as a mid-run circuit breaker.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from sec_review_framework.coordinator import ExperimentCoordinator, ExperimentCostTracker
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.data.evaluation import (
    GroundTruthLabel,
    GroundTruthSource,
)
from sec_review_framework.data.experiment import (
    ExperimentMatrix,
    ExperimentRun,
    PromptSnapshot,
    ReviewProfileName,
    RunResult,
    RunStatus,
    StrategyName,
    ToolVariant,
    VerificationVariant,
)
from sec_review_framework.data.findings import Severity, StrategyOutput, VulnClass
from sec_review_framework.data.strategy_bundle import (
    OrchestrationShape,
    StrategyBundleDefault,
    UserStrategy,
)
from sec_review_framework.db import Database
from sec_review_framework.reporting.generator import ReportGenerator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MODEL_ID = "claude-opus-4-5"
DATASET_NAME = "cap-test-dataset"
DATASET_VERSION = "1.0.0"


def _make_test_strategy(strategy_id: str, model_id: str) -> UserStrategy:
    """Build a minimal UserStrategy suitable for insertion into the DB for tests
    that need strategies with specific model_ids (e.g. per-model cap tests)."""
    return UserStrategy(
        id=strategy_id,
        name=strategy_id,
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.SINGLE_AGENT,
        default=StrategyBundleDefault(
            system_prompt="test",
            user_prompt_template="test",
            profile_modifier="",
            model_id=model_id,
            tools=frozenset(["read_file"]),
            verification="none",
            max_turns=5,
            tool_extensions=frozenset(),
        ),
        overrides=[],
        created_at=datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None),
        is_builtin=False,
    )


class _NullReporter(ReportGenerator):
    """Report generator that does nothing — we only care about DB state."""

    def render_run(self, result, output_dir: Path) -> None:
        pass

    def render_matrix(self, results, output_dir: Path) -> None:
        pass


def _run_async(coro):
    """Run an async coroutine synchronously."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def _make_dataset(storage_root: Path) -> Path:
    """Create a minimal dataset so ExperimentMatrix.expand() works end-to-end."""
    datasets_dir = storage_root / "datasets"
    target_dir = datasets_dir / "targets" / DATASET_NAME
    repo_dir = target_dir / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)

    (repo_dir / "app.py").write_text(
        'def login(user, pw):\n'
        '    q = "SELECT * FROM users WHERE name = \'%s\'" % user\n'
        '    return db.execute(q)\n',
        encoding="utf-8",
    )
    label = GroundTruthLabel(
        id="label-sqli-cap-001",
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
    labels_path = target_dir / "labels.jsonl"
    labels_path.write_text(label.model_dump_json() + "\n", encoding="utf-8")
    return datasets_dir


def _make_prompt_snapshot() -> PromptSnapshot:
    return PromptSnapshot(
        snapshot_id="test-snapshot",
        strategy_id="builtin.single_agent",
        captured_at=datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None),
        bundle_json="{}",
    )


def _make_strategy_output() -> StrategyOutput:
    return StrategyOutput(
        findings=[],
        pre_dedup_count=0,
        post_dedup_count=0,
        dedup_log=[],
    )


def _make_run_result(run: ExperimentRun, cost_usd: float) -> RunResult:
    """Build a minimal completed RunResult with a fixed cost."""
    return RunResult(
        experiment=run,
        status=RunStatus.COMPLETED,
        findings=[],
        strategy_output=_make_strategy_output(),
        bundle_snapshot=_make_prompt_snapshot(),
        tool_call_count=0,
        total_input_tokens=10,
        total_output_tokens=5,
        verification_tokens=0,
        estimated_cost_usd=cost_usd,
        duration_seconds=1.0,
        completed_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


def _write_result_file(coord: ExperimentCoordinator, run: ExperimentRun, result: RunResult) -> None:
    """Write a run_result.json to the storage layout that reconciliation reads."""
    out_dir = coord.storage_root / "outputs" / run.experiment_id / run.id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "run_result.json").write_text(result.model_dump_json(), encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def storage_root(tmp_path: Path) -> Path:
    root = tmp_path / "storage"
    root.mkdir()
    return root


@pytest.fixture()
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    _run_async(database.init())
    return database


@pytest.fixture()
def cost_calculator() -> CostCalculator:
    return CostCalculator(
        pricing={MODEL_ID: ModelPricing(input_per_million=0.0, output_per_million=0.0)}
    )


def _make_coordinator(
    storage_root: Path,
    db: Database,
    cost_calculator: CostCalculator,
    *,
    concurrency_caps: dict[str, int] | None = None,
    default_cap: int = 4,
) -> ExperimentCoordinator:
    return ExperimentCoordinator(
        k8s_client=None,
        storage_root=storage_root,
        concurrency_caps=concurrency_caps or {},
        worker_image="unused-in-test",
        namespace="default",
        db=db,
        reporter=_NullReporter(),
        cost_calculator=cost_calculator,
        default_cap=default_cap,
    )


def _matrix(experiment_id: str, num_runs: int = 1, *, cost_cap: float | None = None) -> ExperimentMatrix:
    """Return a minimal matrix.  num_runs controls the number of runs via
    num_repetitions (each repetition produces a distinct run for the same strategy).
    """
    return ExperimentMatrix(
        experiment_id=experiment_id,
        dataset_name=DATASET_NAME,
        dataset_version=DATASET_VERSION,
        strategy_ids=["builtin.single_agent"],
        num_repetitions=num_runs,
        max_experiment_cost_usd=cost_cap,
        allow_unavailable_models=True,
    )


def _prepare_runs_in_db(
    coord: ExperimentCoordinator,
    matrix: ExperimentMatrix,
) -> list[ExperimentRun]:
    """Create experiment and run rows in the DB (no scheduling)."""
    from sec_review_framework.strategies.strategy_registry import build_registry_from_db

    registry = _run_async(build_registry_from_db(coord.db))
    runs = matrix.expand(registry=registry)
    experiment_id = matrix.experiment_id

    _run_async(coord.db.create_experiment(
        experiment_id=experiment_id,
        config_json=matrix.model_dump_json(),
        total_runs=len(runs),
        max_cost_usd=matrix.max_experiment_cost_usd,
    ))
    for run in runs:
        _run_async(coord.db.create_run(
            run_id=run.id,
            experiment_id=experiment_id,
            config_json=run.model_dump_json(),
            model_id=run.model_id,
            strategy=run.strategy.value,
            tool_variant=run.tool_variant.value,
            review_profile=run.review_profile.value,
            verification_variant=run.verification_variant.value,
            tool_extensions=run.tool_extensions,
        ))

    # Persist run configs to shared storage
    config_dir = coord.storage_root / "config" / "runs"
    config_dir.mkdir(parents=True, exist_ok=True)
    for run in runs:
        (config_dir / f"{run.id}.json").write_text(run.model_dump_json(indent=2))

    coord._cost_trackers[experiment_id] = ExperimentCostTracker(
        experiment_id, matrix.max_experiment_cost_usd
    )
    return runs


# ===========================================================================
# Concurrency cap tests
# ===========================================================================

class TestConcurrencyCapQueuesExcessExperiments:
    """Verify the scheduler holds excess runs in 'pending' when the cap is hit.

    Strategy: call ``_schedule_jobs`` directly as a coroutine (not via the
    background thread), patching ``asyncio.sleep`` so that the polling loop
    stops after the first round.  This keeps the test deterministic and avoids
    unhandled-exception warnings from the background thread.
    """

    def _run_scheduler_one_round(
        self,
        coord: ExperimentCoordinator,
        experiment_id: str,
        runs: list,
    ) -> None:
        """Run _schedule_jobs for exactly one scheduling round, then stop.

        When ``pending`` is non-empty after the first scheduling pass the loop
        calls ``asyncio.sleep(10)`` before iterating again.  We raise a sentinel
        exception at that point so the coroutine exits having processed exactly
        one round.
        """

        class _SchedulerStop(Exception):
            """Sentinel: stop the scheduler after one round."""

        async def _one_round_sleep(seconds):
            raise _SchedulerStop("test: stop scheduler after first round")

        async def _run():
            try:
                with patch(
                    "sec_review_framework.coordinator.asyncio.sleep",
                    side_effect=_one_round_sleep,
                ):
                    await coord._schedule_jobs(experiment_id, runs)
            except _SchedulerStop:
                pass  # Expected — one round complete

        _run_async(_run())

    def test_concurrency_cap_queues_excess_experiments(
        self,
        storage_root: Path,
        db: Database,
        cost_calculator: CostCalculator,
    ) -> None:
        """Cap=2 with 3 runs: 2 scheduled (running), 1 stays pending."""
        cap = 2
        coord = _make_coordinator(
            storage_root, db, cost_calculator,
            concurrency_caps={MODEL_ID: cap},
            default_cap=cap,
        )
        _make_dataset(storage_root)

        experiment_id = "concurrency-cap-test-001"
        matrix = _matrix(experiment_id, num_runs=3)
        runs = _prepare_runs_in_db(coord, matrix)
        assert len(runs) == 3, f"Expected 3 runs, got {len(runs)}"

        self._run_scheduler_one_round(coord, experiment_id, runs)

        counts = _run_async(db.count_runs_by_status(experiment_id))
        running = counts.get("running", 0)
        pending = counts.get("pending", 0)

        assert running == cap, (
            f"Expected exactly {cap} running runs (one per available slot), "
            f"got running={running}, pending={pending}, counts={counts}"
        )
        assert pending == 1, (
            f"Expected exactly 1 pending run (excess over cap), "
            f"got running={running}, pending={pending}, counts={counts}"
        )

    def test_concurrency_cap_default_cap_respected(
        self,
        storage_root: Path,
        db: Database,
        cost_calculator: CostCalculator,
    ) -> None:
        """The ``default_cap`` applies when no per-model cap is configured."""
        default_cap = 1
        coord = _make_coordinator(
            storage_root, db, cost_calculator,
            concurrency_caps={},        # no per-model override
            default_cap=default_cap,
        )
        _make_dataset(storage_root)

        experiment_id = "concurrency-cap-default-001"
        matrix = _matrix(experiment_id, num_runs=2)
        runs = _prepare_runs_in_db(coord, matrix)
        assert len(runs) == 2, f"Expected 2 runs, got {len(runs)}"

        self._run_scheduler_one_round(coord, experiment_id, runs)

        counts = _run_async(db.count_runs_by_status(experiment_id))
        running = counts.get("running", 0)
        pending = counts.get("pending", 0)

        assert running == default_cap, (
            f"Expected {default_cap} running (default cap), "
            f"got running={running}, pending={pending}"
        )
        assert pending == 1, (
            f"Expected 1 pending (over default cap), "
            f"got running={running}, pending={pending}"
        )

    def test_no_concurrency_cap_exceeded_all_scheduled(
        self,
        storage_root: Path,
        db: Database,
        cost_calculator: CostCalculator,
    ) -> None:
        """When cap > number of runs, ALL runs are scheduled immediately (no pending)."""
        # cap=10, runs=3 — everything fits; sleep is never called
        coord = _make_coordinator(
            storage_root, db, cost_calculator,
            concurrency_caps={MODEL_ID: 10},
            default_cap=10,
        )
        _make_dataset(storage_root)

        experiment_id = "concurrency-cap-no-excess-001"
        matrix = _matrix(experiment_id, num_runs=3)
        runs = _prepare_runs_in_db(coord, matrix)

        # All 3 runs fit within the cap — _schedule_jobs completes without sleeping
        _run_async(coord._schedule_jobs(experiment_id, runs))

        counts = _run_async(db.count_runs_by_status(experiment_id))
        running = counts.get("running", 0)
        pending = counts.get("pending", 0)

        assert pending == 0, (
            f"Expected 0 pending (cap not exceeded), "
            f"got running={running}, pending={pending}"
        )
        assert running == 3, (
            f"Expected 3 running, got running={running}, pending={pending}"
        )

    def test_per_model_cap_does_not_affect_other_models(
        self,
        storage_root: Path,
        db: Database,
        cost_calculator: CostCalculator,
    ) -> None:
        """A cap for model A does not limit model B's concurrency."""
        model_a = "model-alpha"
        model_b = "model-beta"

        # After the matrix collapse, model_id is baked into a strategy rather
        # than being a matrix axis. Register two test strategies — one per
        # model — in the DB so matrix.expand() resolves them to runs with the
        # expected model_ids.
        _run_async(db.insert_user_strategy(
            _make_test_strategy("test.concurrency.model_a", model_a)
        ))
        _run_async(db.insert_user_strategy(
            _make_test_strategy("test.concurrency.model_b", model_b)
        ))

        coord = _make_coordinator(
            storage_root, db, cost_calculator,
            concurrency_caps={model_a: 1},  # cap=1 for model A, unlimited for B
            default_cap=10,
        )
        _make_dataset(storage_root)

        experiment_id = "concurrency-cap-per-model-001"
        matrix = ExperimentMatrix(
            experiment_id=experiment_id,
            dataset_name=DATASET_NAME,
            dataset_version=DATASET_VERSION,
            strategy_ids=["test.concurrency.model_a", "test.concurrency.model_b"],
            num_repetitions=2,  # 2 reps × 2 strategies = 4 runs total
            allow_unavailable_models=True,
        )
        runs = _prepare_runs_in_db(coord, matrix)
        assert len(runs) == 4

        # One round of scheduling
        async def _run():
            try:
                with patch(
                    "sec_review_framework.coordinator.asyncio.sleep",
                    side_effect=lambda s: (_ for _ in ()).throw(
                        type("_Stop", (Exception,), {})()
                    ),
                ):
                    await coord._schedule_jobs(experiment_id, runs)
            except Exception:
                pass

        _run_async(_run())

        counts = _run_async(db.count_runs_by_status(experiment_id))
        running = counts.get("running", 0)
        pending = counts.get("pending", 0)

        # model_a: cap=1 → 1 running, 1 pending; model_b: cap=10 → 2 running, 0 pending
        # Total running = 3, pending = 1
        assert running == 3, (
            f"Expected 3 running (1 model_a + 2 model_b), "
            f"got running={running}, pending={pending}"
        )
        assert pending == 1, (
            f"Expected 1 pending (excess model_a run), "
            f"got running={running}, pending={pending}"
        )


# ===========================================================================
# ExperimentCostTracker unit tests
# ===========================================================================

class TestExperimentCostTracker:
    """Unit-level tests for ExperimentCostTracker in isolation."""

    def test_tracker_does_not_cancel_below_cap(self):
        tracker = ExperimentCostTracker("exp-001", cap_usd=10.0)
        exceeded = tracker.record_job_cost(9.99)
        assert not exceeded
        assert tracker.spent_usd == pytest.approx(9.99)

    def test_tracker_cancels_at_cap(self):
        tracker = ExperimentCostTracker("exp-001", cap_usd=10.0)
        exceeded = tracker.record_job_cost(10.00)
        assert exceeded, "Expected cap to be exceeded at exactly the cap value"

    def test_tracker_cancels_above_cap(self):
        tracker = ExperimentCostTracker("exp-001", cap_usd=5.0)
        tracker.record_job_cost(3.0)
        exceeded = tracker.record_job_cost(3.0)  # total = 6.0 > 5.0
        assert exceeded

    def test_tracker_no_cap_never_cancels(self):
        tracker = ExperimentCostTracker("exp-001", cap_usd=None)
        exceeded = tracker.record_job_cost(1_000_000.0)
        assert not exceeded, "No cap configured — should never cancel"

    def test_tracker_cancelled_flag_only_fires_once(self):
        """After cancellation, subsequent record_job_cost calls return False."""
        tracker = ExperimentCostTracker("exp-001", cap_usd=1.0)
        first = tracker.record_job_cost(2.0)   # exceeds cap → True
        second = tracker.record_job_cost(2.0)  # already cancelled → False
        assert first is True
        assert second is False


# ===========================================================================
# Spend cap integration tests
# ===========================================================================

class TestSpendCapCancelsExperiment:
    """Integration-level tests: spend cap via reconciliation logic.

    The spend cap is NOT enforced at submission time.  It is triggered when
    ``_reconcile_experiment_once`` processes run results and the cumulative
    cost recorded by ``ExperimentCostTracker`` crosses ``max_experiment_cost_usd``.
    """

    def _setup_running_experiment(
        self,
        coord: ExperimentCoordinator,
        storage_root: Path,
        matrix: ExperimentMatrix,
    ) -> list[ExperimentRun]:
        """Prepare DB: create experiment + runs, schedule all to 'running' via scheduler."""
        runs = _prepare_runs_in_db(coord, matrix)
        # Schedule all runs (high cap — no pending)
        coord.concurrency_caps = {MODEL_ID: 100}
        coord.default_cap = 100
        _run_async(coord._schedule_jobs(matrix.experiment_id, runs))
        return runs

    def test_spend_cap_cancels_experiment_when_exceeded(
        self,
        storage_root: Path,
        db: Database,
        cost_calculator: CostCalculator,
    ) -> None:
        """When accumulated run cost crosses the cap the experiment is cancelled."""
        spend_cap = 5.0
        cost_per_run = 6.0   # Single run exceeds cap

        coord = _make_coordinator(storage_root, db, cost_calculator)
        _make_dataset(storage_root)

        experiment_id = "spend-cap-exceed-001"
        matrix = _matrix(experiment_id, num_runs=1, cost_cap=spend_cap)
        runs = self._setup_running_experiment(coord, storage_root, matrix)

        # Verify run is in 'running' state
        db_runs = _run_async(db.list_runs(experiment_id))
        assert db_runs[0]["status"] == "running"

        # Write result file with cost exceeding the cap
        result = _make_run_result(runs[0], cost_usd=cost_per_run)
        _write_result_file(coord, runs[0], result)

        # Trigger reconciliation
        experiment_row = _run_async(db.get_experiment(experiment_id))
        _run_async(coord._reconcile_experiment_once(experiment_row, experiment_id))

        # The experiment should now be cancelled
        updated = _run_async(db.get_experiment(experiment_id))
        assert updated["status"] == "cancelled", (
            f"Expected experiment status 'cancelled' after spend cap exceeded "
            f"(cost={cost_per_run} > cap={spend_cap}), "
            f"got {updated['status']!r}"
        )

    def test_spend_cap_allows_under_budget_experiment(
        self,
        storage_root: Path,
        db: Database,
        cost_calculator: CostCalculator,
    ) -> None:
        """When run cost is under the cap the experiment stays in its normal state."""
        spend_cap = 10.0
        cost_per_run = 4.0   # Under cap

        coord = _make_coordinator(storage_root, db, cost_calculator)
        _make_dataset(storage_root)

        experiment_id = "spend-cap-under-001"
        matrix = _matrix(experiment_id, num_runs=1, cost_cap=spend_cap)
        runs = self._setup_running_experiment(coord, storage_root, matrix)

        result = _make_run_result(runs[0], cost_usd=cost_per_run)
        _write_result_file(coord, runs[0], result)

        experiment_row = _run_async(db.get_experiment(experiment_id))
        _run_async(coord._reconcile_experiment_once(experiment_row, experiment_id))

        updated = _run_async(db.get_experiment(experiment_id))
        assert updated["status"] != "cancelled", (
            f"Experiment should NOT be cancelled when cost ({cost_per_run}) < cap ({spend_cap}), "
            f"got status={updated['status']!r}"
        )

    def test_spend_cap_cumulative_across_multiple_runs(
        self,
        storage_root: Path,
        db: Database,
        cost_calculator: CostCalculator,
    ) -> None:
        """The cap applies cumulatively: 2 runs each at $4 exceeds a $5 cap.

        Reconciliation iterates all running runs with result files in a single
        call.  We write result files one at a time so that the first
        reconciliation only sees run[0] ($4 of $5 cap) and the second sees
        run[1] (cumulative $8 > $5 cap).
        """
        spend_cap = 5.0
        cost_per_run = 4.0   # Each run individually under cap; together they exceed it

        coord = _make_coordinator(storage_root, db, cost_calculator)
        _make_dataset(storage_root)

        experiment_id = "spend-cap-cumulative-001"
        matrix = _matrix(experiment_id, num_runs=2, cost_cap=spend_cap)
        runs = self._setup_running_experiment(coord, storage_root, matrix)
        assert len(runs) == 2

        # --- Round 1: only write result for run[0], reconcile, assert no cancel ---
        result0 = _make_run_result(runs[0], cost_usd=cost_per_run)
        _write_result_file(coord, runs[0], result0)

        experiment_row = _run_async(db.get_experiment(experiment_id))
        _run_async(coord._reconcile_experiment_once(experiment_row, experiment_id))

        after_first = _run_async(db.get_experiment(experiment_id))
        assert after_first["status"] != "cancelled", (
            f"Experiment should not be cancelled after first run "
            f"(${cost_per_run} < ${spend_cap} cap), "
            f"got status={after_first['status']!r}"
        )
        # Spend so far should be $4
        spend_so_far = _run_async(db.get_experiment_spend(experiment_id))
        assert spend_so_far == pytest.approx(cost_per_run), (
            f"Expected ${cost_per_run} spent after first run, got ${spend_so_far}"
        )

        # --- Round 2: write result for run[1], reconcile, assert cancelled ---
        result1 = _make_run_result(runs[1], cost_usd=cost_per_run)
        _write_result_file(coord, runs[1], result1)

        _run_async(coord._reconcile_experiment_once(after_first, experiment_id))

        after_second = _run_async(db.get_experiment(experiment_id))
        assert after_second["status"] == "cancelled", (
            f"Expected 'cancelled' after cumulative cost (${2 * cost_per_run}) "
            f"exceeded cap (${spend_cap}), "
            f"got status={after_second['status']!r}"
        )

    def test_no_spend_cap_allows_any_cost(
        self,
        storage_root: Path,
        db: Database,
        cost_calculator: CostCalculator,
    ) -> None:
        """When no cap is set (max_experiment_cost_usd=None) no cancellation occurs."""
        coord = _make_coordinator(storage_root, db, cost_calculator)
        _make_dataset(storage_root)

        experiment_id = "spend-cap-none-001"
        matrix = _matrix(experiment_id, num_runs=1, cost_cap=None)
        runs = self._setup_running_experiment(coord, storage_root, matrix)

        result = _make_run_result(runs[0], cost_usd=1_000_000.0)  # absurd cost
        _write_result_file(coord, runs[0], result)

        experiment_row = _run_async(db.get_experiment(experiment_id))
        _run_async(coord._reconcile_experiment_once(experiment_row, experiment_id))

        updated = _run_async(db.get_experiment(experiment_id))
        assert updated["status"] != "cancelled", (
            f"No spend cap configured — experiment should not be cancelled, "
            f"got status={updated['status']!r}"
        )

    def test_spend_cap_stored_in_db_after_submission(
        self,
        storage_root: Path,
        db: Database,
        cost_calculator: CostCalculator,
    ) -> None:
        """The spend cap set at submission time is persisted in the DB row."""
        spend_cap = 7.50

        coord = _make_coordinator(storage_root, db, cost_calculator)
        _make_dataset(storage_root)

        experiment_id = "spend-cap-db-001"
        matrix = _matrix(experiment_id, num_runs=1, cost_cap=spend_cap)
        _prepare_runs_in_db(coord, matrix)

        experiment_row = _run_async(db.get_experiment(experiment_id))
        assert experiment_row is not None
        stored_cap = experiment_row.get("max_cost_usd")
        assert stored_cap == pytest.approx(spend_cap), (
            f"Expected max_cost_usd={spend_cap!r} in DB, got {stored_cap!r}"
        )

    def test_spend_tracker_rebuilt_from_db_after_restart(
        self,
        storage_root: Path,
        db: Database,
        cost_calculator: CostCalculator,
    ) -> None:
        """If the coordinator is restarted mid-experiment (no in-memory tracker),
        reconciliation rebuilds the tracker from DB and still enforces the cap.
        """
        spend_cap = 5.0
        cost_per_run = 6.0

        coord = _make_coordinator(storage_root, db, cost_calculator)
        _make_dataset(storage_root)

        experiment_id = "spend-cap-restart-001"
        matrix = _matrix(experiment_id, num_runs=1, cost_cap=spend_cap)
        runs = self._setup_running_experiment(coord, storage_root, matrix)

        # Simulate restart: clear in-memory cost trackers
        coord._cost_trackers.clear()

        result = _make_run_result(runs[0], cost_usd=cost_per_run)
        _write_result_file(coord, runs[0], result)

        # Reconciliation should rebuild tracker from DB and still cancel
        experiment_row = _run_async(db.get_experiment(experiment_id))
        _run_async(coord._reconcile_experiment_once(experiment_row, experiment_id))

        updated = _run_async(db.get_experiment(experiment_id))
        assert updated["status"] == "cancelled", (
            f"Expected 'cancelled' even after simulated restart (tracker rebuilt from DB), "
            f"got {updated['status']!r}"
        )
