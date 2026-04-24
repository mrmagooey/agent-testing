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


# ---------------------------------------------------------------------------
# Upload token CRUD (HTTP result transport)
# ---------------------------------------------------------------------------


async def _setup_run(db: Database, run_id: str = "run-1", experiment_id: str = "experiment-1") -> None:
    """Helper: ensure experiment + run exist for token tests."""
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


@pytest.mark.asyncio
async def test_issue_upload_token_returns_plaintext(db: Database) -> None:
    """issue_upload_token returns a non-empty string (the plaintext token)."""
    await _setup_run(db, "run-tok-1", "exp-tok-1")
    token = await db.issue_upload_token("run-tok-1")
    assert isinstance(token, str)
    assert len(token) > 10  # 32 URL-safe bytes base64 = ~43 chars


@pytest.mark.asyncio
async def test_issue_upload_token_not_stored_as_plaintext(db: Database) -> None:
    """The token stored in the DB is the SHA-256 hash, not the plaintext."""
    import aiosqlite
    import hashlib

    await _setup_run(db, "run-tok-2", "exp-tok-2")
    token = await db.issue_upload_token("run-tok-2")

    async with aiosqlite.connect(db.db_path) as conn:
        async with conn.execute(
            "SELECT token_hash FROM run_upload_tokens WHERE run_id = ?",
            ("run-tok-2",),
        ) as cursor:
            row = await cursor.fetchone()

    assert row is not None
    stored_hash = row[0]
    # Must NOT be the plaintext token
    assert stored_hash != token
    # Must be the SHA-256 hex digest
    expected = hashlib.sha256(token.encode()).hexdigest()
    assert stored_hash == expected


@pytest.mark.asyncio
async def test_consume_upload_token_valid(db: Database) -> None:
    """consume_upload_token returns True for a valid, unconsumed token."""
    await _setup_run(db, "run-tok-3", "exp-tok-3")
    token = await db.issue_upload_token("run-tok-3")
    result = await db.consume_upload_token("run-tok-3", token)
    assert result is True


@pytest.mark.asyncio
async def test_consume_upload_token_is_single_use(db: Database) -> None:
    """A token can only be consumed once — second call returns False."""
    await _setup_run(db, "run-tok-4", "exp-tok-4")
    token = await db.issue_upload_token("run-tok-4")
    assert await db.consume_upload_token("run-tok-4", token) is True
    assert await db.consume_upload_token("run-tok-4", token) is False


@pytest.mark.asyncio
async def test_consume_upload_token_wrong_token(db: Database) -> None:
    """consume_upload_token returns False when the token doesn't match."""
    await _setup_run(db, "run-tok-5", "exp-tok-5")
    await db.issue_upload_token("run-tok-5")
    result = await db.consume_upload_token("run-tok-5", "wrong-token-value")
    assert result is False


@pytest.mark.asyncio
async def test_consume_upload_token_no_token_issued(db: Database) -> None:
    """consume_upload_token returns False when no token was issued."""
    await _setup_run(db, "run-tok-6", "exp-tok-6")
    result = await db.consume_upload_token("run-tok-6", "any-token")
    assert result is False


@pytest.mark.asyncio
async def test_get_upload_token_issued_before_issue(db: Database) -> None:
    """get_upload_token_issued returns False before a token is issued."""
    await _setup_run(db, "run-tok-7", "exp-tok-7")
    assert await db.get_upload_token_issued("run-tok-7") is False


@pytest.mark.asyncio
async def test_get_upload_token_issued_after_issue(db: Database) -> None:
    """get_upload_token_issued returns True after a token is issued."""
    await _setup_run(db, "run-tok-8", "exp-tok-8")
    await db.issue_upload_token("run-tok-8")
    assert await db.get_upload_token_issued("run-tok-8") is True


@pytest.mark.asyncio
async def test_is_upload_token_consumed_before_consume(db: Database) -> None:
    """is_upload_token_consumed returns False before consumption."""
    await _setup_run(db, "run-tok-9", "exp-tok-9")
    await db.issue_upload_token("run-tok-9")
    assert await db.is_upload_token_consumed("run-tok-9") is False


@pytest.mark.asyncio
async def test_is_upload_token_consumed_after_consume(db: Database) -> None:
    """is_upload_token_consumed returns True after successful consumption."""
    await _setup_run(db, "run-tok-10", "exp-tok-10")
    token = await db.issue_upload_token("run-tok-10")
    await db.consume_upload_token("run-tok-10", token)
    assert await db.is_upload_token_consumed("run-tok-10") is True


@pytest.mark.asyncio
async def test_revoke_upload_tokens_for_experiment(db: Database) -> None:
    """revoke_upload_tokens_for_experiment deletes tokens for all runs in experiment."""
    exp_id = "exp-tok-revoke"
    await db.create_experiment(
        experiment_id=exp_id,
        config_json="{}",
        total_runs=2,
        max_cost_usd=None,
    )
    for run_id in ("run-rv-1", "run-rv-2"):
        await db.create_run(
            run_id=run_id,
            experiment_id=exp_id,
            config_json="{}",
            model_id="gpt-4o",
            strategy="single_agent",
            tool_variant="with_tools",
            review_profile="default",
            verification_variant="none",
        )
        await db.issue_upload_token(run_id)

    deleted = await db.revoke_upload_tokens_for_experiment(exp_id)
    assert deleted == 2

    # Tokens are gone — consume_upload_token should fail for both
    for run_id in ("run-rv-1", "run-rv-2"):
        assert await db.get_upload_token_issued(run_id) is False


@pytest.mark.asyncio
async def test_issue_upload_token_raises_on_duplicate(db: Database) -> None:
    """Calling issue_upload_token twice for the same run_id raises UploadTokenAlreadyExists.

    Regression test for the silent-broken-token bug: the old implementation
    returned a new plaintext token even when the INSERT was a no-op, producing
    a token whose hash did not match the stored one.  The fix raises instead.
    """
    from sec_review_framework.db import UploadTokenAlreadyExists

    await _setup_run(db, "run-tok-idem", "exp-tok-idem")
    await db.issue_upload_token("run-tok-idem")
    # Second call must raise — the original hash is preserved in the DB and
    # the caller must NOT receive a mismatched plaintext token.
    with pytest.raises(UploadTokenAlreadyExists) as exc_info:
        await db.issue_upload_token("run-tok-idem")
    assert exc_info.value.run_id == "run-tok-idem"
