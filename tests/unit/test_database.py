"""Async tests for the Database class — CRUD, status updates, spend tracking."""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

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


async def _create_batch(db: Database, batch_id: str = "batch-1") -> None:
    await db.create_batch(
        batch_id=batch_id,
        config_json='{"key": "value"}',
        total_runs=5,
        max_cost_usd=10.0,
    )


async def _create_run(
    db: Database,
    run_id: str = "run-1",
    batch_id: str = "batch-1",
    status_override: str | None = None,
) -> None:
    await db.create_run(
        run_id=run_id,
        batch_id=batch_id,
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
    """After init(), inserting into batches and runs succeeds."""
    await _create_batch(db)
    row = await db.get_batch("batch-1")
    assert row is not None


# ---------------------------------------------------------------------------
# Batch CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_batch_and_get_batch_round_trip(db: Database):
    """create_batch + get_batch returns a dict with the expected fields."""
    await _create_batch(db, "batch-rt")
    row = await db.get_batch("batch-rt")
    assert row is not None
    assert row["id"] == "batch-rt"
    assert row["total_runs"] == 5
    assert row["max_cost_usd"] == pytest.approx(10.0)
    assert row["status"] == "pending"


@pytest.mark.asyncio
async def test_list_batches_returns_results(db: Database):
    """list_batches returns all created batches."""
    await _create_batch(db, "b1")
    await _create_batch(db, "b2")
    batches = await db.list_batches()
    ids = {b["id"] for b in batches}
    assert "b1" in ids
    assert "b2" in ids


@pytest.mark.asyncio
async def test_get_batch_not_found_returns_none(db: Database):
    """get_batch on a non-existent id returns None."""
    result = await db.get_batch("does-not-exist")
    assert result is None


@pytest.mark.asyncio
async def test_update_batch_status_changes_status(db: Database):
    """update_batch_status changes the status field."""
    await _create_batch(db)
    await db.update_batch_status("batch-1", "running")
    row = await db.get_batch("batch-1")
    assert row["status"] == "running"


@pytest.mark.asyncio
async def test_update_batch_status_with_completed_at(db: Database):
    """update_batch_status can also set completed_at."""
    await _create_batch(db)
    await db.update_batch_status("batch-1", "completed", completed_at="2026-04-16T12:00:00")
    row = await db.get_batch("batch-1")
    assert row["status"] == "completed"
    assert row["completed_at"] == "2026-04-16T12:00:00"


# ---------------------------------------------------------------------------
# Run CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_run_and_get_run_round_trip(db: Database):
    """create_run + get_run returns a dict with the expected fields."""
    await _create_batch(db)
    await _create_run(db, run_id="run-rt", batch_id="batch-1")
    row = await db.get_run("run-rt")
    assert row is not None
    assert row["id"] == "run-rt"
    assert row["batch_id"] == "batch-1"
    assert row["model_id"] == "gpt-4o"
    assert row["status"] == "pending"


@pytest.mark.asyncio
async def test_list_runs_filtered_by_batch_id(db: Database):
    """list_runs only returns runs belonging to the specified batch."""
    await _create_batch(db, "batch-A")
    await _create_batch(db, "batch-B")
    await _create_run(db, run_id="run-A1", batch_id="batch-A")
    await _create_run(db, run_id="run-A2", batch_id="batch-A")
    await _create_run(db, run_id="run-B1", batch_id="batch-B")

    runs_a = await db.list_runs("batch-A")
    assert len(runs_a) == 2
    assert all(r["batch_id"] == "batch-A" for r in runs_a)


@pytest.mark.asyncio
async def test_get_run_not_found_returns_none(db: Database):
    """get_run on a non-existent id returns None."""
    result = await db.get_run("ghost-run")
    assert result is None


@pytest.mark.asyncio
async def test_update_run_partial_fields(db: Database):
    """update_run can set status, duration_seconds, and error independently."""
    await _create_batch(db)
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
    await _create_batch(db)
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
    await _create_batch(db)
    await _create_run(db, run_id="r1", status_override="completed")
    await _create_run(db, run_id="r2", status_override="completed")
    await _create_run(db, run_id="r3", status_override="failed")
    await _create_run(db, run_id="r4")  # stays pending

    counts = await db.count_runs_by_status("batch-1")
    assert counts["completed"] == 2
    assert counts["failed"] == 1
    assert counts["pending"] == 1


# ---------------------------------------------------------------------------
# Batch spend
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_add_batch_spend_accumulates(db: Database):
    """add_batch_spend increments cumulatively."""
    await _create_batch(db)
    await db.add_batch_spend("batch-1", 1.50)
    await db.add_batch_spend("batch-1", 0.75)
    total = await db.get_batch_spend("batch-1")
    assert total == pytest.approx(2.25)


@pytest.mark.asyncio
async def test_get_batch_spend_returns_zero_for_new_batch(db: Database):
    """A freshly created batch has 0 spend."""
    await _create_batch(db)
    total = await db.get_batch_spend("batch-1")
    assert total == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_get_batch_spend_missing_batch_returns_zero(db: Database):
    """get_batch_spend on a non-existent batch returns 0.0."""
    total = await db.get_batch_spend("non-existent")
    assert total == pytest.approx(0.0)
