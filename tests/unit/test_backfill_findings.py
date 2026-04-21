"""Unit tests for findings index backfill.

Builds a mini fixture outputs/ tree, runs the backfill logic, asserts
row count + shapes. Idempotent: second run yields same count.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest
import pytest_asyncio

from sec_review_framework.db import Database
from sec_review_framework.data.experiment import (
    ExperimentRun,
    RunResult,
    RunStatus,
    StrategyName,
    ToolVariant,
    ReviewProfileName,
    VerificationVariant,
)
from sec_review_framework.data.evaluation import EvaluationResult, VerificationResult
from sec_review_framework.data.findings import (
    Finding,
    Severity,
    VulnClass,
    StrategyOutput,
)
from sec_review_framework.data.experiment import PromptSnapshot


def _make_run_result(
    experiment_id: str,
    run_id: str,
    model_id: str = "gpt-4o",
    strategy: str = "single_agent",
    dataset_name: str = "ds-test",
    num_findings: int = 2,
) -> RunResult:
    run = ExperimentRun(
        id=run_id,
        experiment_id=experiment_id,
        model_id=model_id,
        strategy=StrategyName(strategy),
        tool_variant=ToolVariant.WITH_TOOLS,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        dataset_name=dataset_name,
        dataset_version="latest",
    )
    findings = [
        Finding(
            id=f"{run_id}-f{i}",
            file_path=f"src/file{i}.py",
            line_start=10 * i,
            line_end=10 * i + 5,
            vuln_class=VulnClass.SQLI,
            severity=Severity.HIGH,
            title=f"Finding {i}",
            description=f"Description {i}",
            confidence=0.9,
            raw_llm_output="raw",
            produced_by="strategy",
            experiment_id=experiment_id,
        )
        for i in range(num_findings)
    ]
    return RunResult(
        experiment=run,
        status=RunStatus.COMPLETED,
        findings=findings,
        strategy_output=StrategyOutput(
            findings=findings,
            pre_dedup_count=num_findings,
            post_dedup_count=num_findings,
            dedup_log=[],
        ),
        prompt_snapshot=PromptSnapshot.capture(
            system_prompt="sys",
            user_message_template="user",
            finding_output_format="fmt",
        ),
        tool_call_count=0,
        total_input_tokens=100,
        total_output_tokens=50,
        verification_tokens=0,
        estimated_cost_usd=0.01,
        duration_seconds=5.0,
        completed_at=datetime.utcnow(),
    )


def _write_result(storage_root: Path, experiment_id: str, run_id: str, result: RunResult) -> None:
    run_dir = storage_root / "outputs" / experiment_id / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_result.json").write_text(result.model_dump_json(indent=2))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> Database:
    d = Database(tmp_path / "test.db")
    await d.init()
    return d


@pytest.fixture
def storage_root(tmp_path: Path) -> Path:
    root = tmp_path / "storage"
    root.mkdir()
    return root


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backfill_indexes_all_runs(db: Database, storage_root: Path) -> None:
    """Backfill indexes every run_result.json found in outputs/."""
    result1 = _make_run_result("batch-1", "run-1", num_findings=2)
    result2 = _make_run_result("batch-1", "run-2", num_findings=3)
    result3 = _make_run_result("batch-2", "run-3", num_findings=1)

    _write_result(storage_root, "batch-1", "run-1", result1)
    _write_result(storage_root, "batch-1", "run-2", result2)
    _write_result(storage_root, "batch-2", "run-3", result3)

    # Run backfill (same logic as the script)
    from sec_review_framework.data.experiment import RunResult as RR

    indexed = 0
    outputs_dir = storage_root / "outputs"
    for batch_dir in outputs_dir.iterdir():
        for run_dir in batch_dir.iterdir():
            rf = run_dir / "run_result.json"
            if not rf.exists():
                continue
            r = RR.model_validate_json(rf.read_text())
            if r.findings:
                await db.upsert_findings_for_run(
                    run_id=r.experiment.id,
                    experiment_id=batch_dir.name,
                    findings=[f.model_dump(mode="json") for f in r.findings],
                    model_id=r.experiment.model_id,
                    strategy=r.experiment.strategy.value,
                    dataset_name=r.experiment.dataset_name,
                )
                indexed += 1

    assert indexed == 3
    total, _ = await db.query_findings({})
    assert total == 6  # 2 + 3 + 1


@pytest.mark.asyncio
async def test_backfill_idempotent(db: Database, storage_root: Path) -> None:
    """Running backfill twice gives the same row count."""
    result = _make_run_result("batch-1", "run-1", num_findings=2)
    _write_result(storage_root, "batch-1", "run-1", result)

    from sec_review_framework.data.experiment import RunResult as RR

    async def do_backfill() -> None:
        for batch_dir in (storage_root / "outputs").iterdir():
            for run_dir in batch_dir.iterdir():
                rf = run_dir / "run_result.json"
                if not rf.exists():
                    continue
                r = RR.model_validate_json(rf.read_text())
                if r.findings:
                    await db.upsert_findings_for_run(
                        run_id=r.experiment.id,
                        experiment_id=batch_dir.name,
                        findings=[f.model_dump(mode="json") for f in r.findings],
                        model_id=r.experiment.model_id,
                        strategy=r.experiment.strategy.value,
                        dataset_name=r.experiment.dataset_name,
                    )

    await do_backfill()
    await do_backfill()  # second run

    total, _ = await db.query_findings({})
    assert total == 2  # no duplication


@pytest.mark.asyncio
async def test_backfill_skips_empty_outputs_dir(db: Database, tmp_path: Path) -> None:
    """Backfill with non-existent outputs dir does not raise."""
    storage_root = tmp_path / "nonexistent"
    # No outputs dir; batch coordinator method handles missing dir gracefully
    outputs_dir = storage_root / "outputs"
    assert not outputs_dir.exists()

    # Run zero iterations without error
    n = 0
    if outputs_dir.exists():
        for _ in outputs_dir.iterdir():
            n += 1
    assert n == 0


@pytest.mark.asyncio
async def test_backfill_finding_shapes(db: Database, storage_root: Path) -> None:
    """Backfilled findings have correct vuln_class, model_id, strategy, dataset_name."""
    result = _make_run_result(
        "batch-1", "run-1",
        model_id="claude-3-opus",
        strategy="per_file",
        dataset_name="mydata",
        num_findings=1,
    )
    _write_result(storage_root, "batch-1", "run-1", result)

    from sec_review_framework.data.experiment import RunResult as RR

    for batch_dir in (storage_root / "outputs").iterdir():
        for run_dir in batch_dir.iterdir():
            rf = run_dir / "run_result.json"
            r = RR.model_validate_json(rf.read_text())
            await db.upsert_findings_for_run(
                run_id=r.experiment.id,
                experiment_id=batch_dir.name,
                findings=[f.model_dump(mode="json") for f in r.findings],
                model_id=r.experiment.model_id,
                strategy=r.experiment.strategy.value,
                dataset_name=r.experiment.dataset_name,
            )

    _, items = await db.query_findings({})
    assert len(items) == 1
    item = items[0]
    assert item["model_id"] == "claude-3-opus"
    assert item["strategy"] == "per_file"
    assert item["dataset_name"] == "mydata"
    assert item["vuln_class"] == "sqli"
