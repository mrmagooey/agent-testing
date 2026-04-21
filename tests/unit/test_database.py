"""Async tests for the Database class — CRUD, status updates, spend tracking."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from sec_review_framework.data.experiment import ToolExtension
from sec_review_framework.db import Database


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> Database:
    """Fresh in-process SQLite database for each test."""
    database = Database(tmp_path / "test.db")
    await database.init()
    return database


async def _create_experiment(db: Database, experiment_id: str = "experiment-1") -> None:
    await db.create_experiment(
        experiment_id=experiment_id,
        config_json='{"key": "value"}',
        total_runs=5,
        max_cost_usd=10.0,
    )


async def _create_run(
    db: Database,
    run_id: str = "run-1",
    experiment_id: str = "experiment-1",
    status_override: str | None = None,
) -> None:
    await db.create_run(
        run_id=run_id,
        experiment_id=experiment_id,
        config_json='{"model": "gpt-4o"}',
        model_id="gpt-4o",
        strategy="single_agent",
        tool_variant="with_tools",
        review_profile="default",
        verification_variant="none",
    )
    if status_override:
        await db.update_run(run_id=run_id, status=status_override)


# ---------------------------------------------------------------------------
# Schema / init
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_init_creates_tables(db: Database):
    """After init(), inserting into experiments and runs succeeds."""
    await _create_experiment(db)
    row = await db.get_experiment("experiment-1")
    assert row is not None


# ---------------------------------------------------------------------------
# Experiment CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_experiment_and_get_experiment_round_trip(db: Database):
    """create_experiment + get_experiment returns a dict with the expected fields."""
    await _create_experiment(db, "experiment-rt")
    row = await db.get_experiment("experiment-rt")
    assert row is not None
    assert row["id"] == "experiment-rt"
    assert row["total_runs"] == 5
    assert row["max_cost_usd"] == pytest.approx(10.0)
    assert row["status"] == "pending"


@pytest.mark.asyncio
async def test_list_experiments_returns_results(db: Database):
    """list_experiments returns all created experiments."""
    await _create_experiment(db, "e1")
    await _create_experiment(db, "e2")
    experiments = await db.list_experiments()
    ids = {e["id"] for e in experiments}
    assert "e1" in ids
    assert "e2" in ids


@pytest.mark.asyncio
async def test_get_experiment_not_found_returns_none(db: Database):
    """get_experiment on a non-existent id returns None."""
    result = await db.get_experiment("does-not-exist")
    assert result is None


@pytest.mark.asyncio
async def test_update_experiment_status_changes_status(db: Database):
    """update_experiment_status changes the status field."""
    await _create_experiment(db)
    await db.update_experiment_status("experiment-1", "running")
    row = await db.get_experiment("experiment-1")
    assert row["status"] == "running"


@pytest.mark.asyncio
async def test_update_experiment_status_with_completed_at(db: Database):
    """update_experiment_status can also set completed_at."""
    await _create_experiment(db)
    await db.update_experiment_status("experiment-1", "completed", completed_at="2026-04-16T12:00:00")
    row = await db.get_experiment("experiment-1")
    assert row["status"] == "completed"
    assert row["completed_at"] == "2026-04-16T12:00:00"


# ---------------------------------------------------------------------------
# Run CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_run_and_get_run_round_trip(db: Database):
    """create_run + get_run returns a dict with the expected fields."""
    await _create_experiment(db)
    await _create_run(db, run_id="run-rt", experiment_id="experiment-1")
    row = await db.get_run("run-rt")
    assert row is not None
    assert row["id"] == "run-rt"
    assert row["experiment_id"] == "experiment-1"
    assert row["model_id"] == "gpt-4o"
    assert row["status"] == "pending"


@pytest.mark.asyncio
async def test_list_runs_filtered_by_experiment_id(db: Database):
    """list_runs only returns runs belonging to the specified experiment."""
    await _create_experiment(db, "experiment-A")
    await _create_experiment(db, "experiment-B")
    await _create_run(db, run_id="run-A1", experiment_id="experiment-A")
    await _create_run(db, run_id="run-A2", experiment_id="experiment-A")
    await _create_run(db, run_id="run-B1", experiment_id="experiment-B")

    runs_a = await db.list_runs("experiment-A")
    assert len(runs_a) == 2
    assert all(r["experiment_id"] == "experiment-A" for r in runs_a)


@pytest.mark.asyncio
async def test_get_run_not_found_returns_none(db: Database):
    """get_run on a non-existent id returns None."""
    result = await db.get_run("ghost-run")
    assert result is None


@pytest.mark.asyncio
async def test_update_run_partial_fields(db: Database):
    """update_run can set status, duration_seconds, and error independently."""
    await _create_experiment(db)
    await _create_run(db)
    await db.update_run(
        run_id="run-1",
        status="completed",
        duration_seconds=45.5,
        result_path="/results/run-1.json",
    )
    row = await db.get_run("run-1")
    assert row["status"] == "completed"
    assert row["duration_seconds"] == pytest.approx(45.5)
    assert row["result_path"] == "/results/run-1.json"


@pytest.mark.asyncio
async def test_update_run_error_field(db: Database):
    """update_run can record an error message."""
    await _create_experiment(db)
    await _create_run(db)
    await db.update_run(run_id="run-1", status="failed", error="timeout after 300s")
    row = await db.get_run("run-1")
    assert row["status"] == "failed"
    assert row["error"] == "timeout after 300s"


# ---------------------------------------------------------------------------
# count_runs_by_status
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_count_runs_by_status_correct_counts(db: Database):
    """count_runs_by_status returns accurate counts per status."""
    await _create_experiment(db)
    await _create_run(db, run_id="r1", status_override="completed")
    await _create_run(db, run_id="r2", status_override="completed")
    await _create_run(db, run_id="r3", status_override="failed")
    await _create_run(db, run_id="r4")  # stays pending

    counts = await db.count_runs_by_status("experiment-1")
    assert counts["completed"] == 2
    assert counts["failed"] == 1
    assert counts["pending"] == 1


# ---------------------------------------------------------------------------
# Experiment spend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_experiment_spend_accumulates(db: Database):
    """add_experiment_spend increments cumulatively."""
    await _create_experiment(db)
    await db.add_experiment_spend("experiment-1", 1.50)
    await db.add_experiment_spend("experiment-1", 0.75)
    total = await db.get_experiment_spend("experiment-1")
    assert total == pytest.approx(2.25)


@pytest.mark.asyncio
async def test_get_experiment_spend_returns_zero_for_new_experiment(db: Database):
    """A freshly created experiment has 0 spend."""
    await _create_experiment(db)
    total = await db.get_experiment_spend("experiment-1")
    assert total == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_get_experiment_spend_missing_experiment_returns_zero(db: Database):
    """get_experiment_spend on a non-existent experiment returns 0.0."""
    total = await db.get_experiment_spend("non-existent")
    assert total == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# tool_extensions persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_run_tool_extensions_empty(db: Database):
    """create_run with no tool_extensions stores empty string."""
    await _create_experiment(db)
    await db.create_run(
        run_id="run-ext-empty",
        experiment_id="experiment-1",
        config_json="{}",
        model_id="m",
        strategy="single_agent",
        tool_variant="with_tools",
        review_profile="default",
        verification_variant="none",
    )
    row = await db.get_run("run-ext-empty")
    assert row is not None
    assert row["tool_extensions"] == ""


@pytest.mark.asyncio
async def test_create_run_tool_extensions_persisted(db: Database):
    """create_run with tool_extensions stores them as sorted comma-joined string."""
    await _create_experiment(db)
    await db.create_run(
        run_id="run-ext-lsp-ts",
        experiment_id="experiment-1",
        config_json="{}",
        model_id="m",
        strategy="single_agent",
        tool_variant="with_tools",
        review_profile="default",
        verification_variant="none",
        tool_extensions=frozenset({ToolExtension.TREE_SITTER, ToolExtension.LSP}),
    )
    row = await db.get_run("run-ext-lsp-ts")
    assert row is not None
    assert row["tool_extensions"] == "lsp,tree_sitter"


@pytest.mark.asyncio
async def test_create_run_tool_extensions_single(db: Database):
    """create_run with a single tool_extension persists it correctly."""
    await _create_experiment(db)
    await db.create_run(
        run_id="run-ext-devdocs",
        experiment_id="experiment-1",
        config_json="{}",
        model_id="m",
        strategy="single_agent",
        tool_variant="with_tools",
        review_profile="default",
        verification_variant="none",
        tool_extensions=frozenset({ToolExtension.DEVDOCS}),
    )
    row = await db.get_run("run-ext-devdocs")
    assert row is not None
    assert row["tool_extensions"] == "devdocs"
