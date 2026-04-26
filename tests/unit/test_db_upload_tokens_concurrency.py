"""Concurrent stress test: consume_upload_token() must be single-use under contention."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import pytest_asyncio

from sec_review_framework.db import Database

CONCURRENCY = 100
ITERATIONS = 10


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "test.db")
    await database.init()
    return database


async def _setup_run(db: Database, run_id: str, experiment_id: str) -> None:
    await db.create_experiment(
        experiment_id=experiment_id,
        config_json="{}",
        total_runs=1,
        max_cost_usd=None,
    )
    await db.create_run(
        run_id=run_id,
        experiment_id=experiment_id,
        config_json="{}",
        model_id="gpt-4o",
        strategy="single_agent",
        tool_variant="with_tools",
        review_profile="default",
        verification_variant="none",
    )


async def test_consume_upload_token_single_use_under_concurrency(db: Database) -> None:
    """Exactly one of N concurrent consume_upload_token() calls succeeds per token."""
    for iteration in range(ITERATIONS):
        run_id = f"run-stress-{iteration}"
        exp_id = f"exp-stress-{iteration}"
        await _setup_run(db, run_id, exp_id)
        token = await db.issue_upload_token(run_id)

        results = await asyncio.gather(
            *[db.consume_upload_token(run_id, token) for _ in range(CONCURRENCY)],
            return_exceptions=False,
        )

        successes = sum(1 for r in results if r is True)
        assert successes == 1, (
            f"iteration {iteration}: expected exactly 1 success, got {successes} "
            f"out of {CONCURRENCY} concurrent attempts"
        )
