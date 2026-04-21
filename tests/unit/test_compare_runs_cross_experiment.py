"""Unit tests for cross-experiment run comparison."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import app, ExperimentCoordinator
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.db import Database
from sec_review_framework.reporting.markdown import MarkdownReportGenerator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(tmp_path: Path, db: Database) -> ExperimentCoordinator:
    cost_calc = CostCalculator(
        pricing={
            "gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0),
        }
    )
    return ExperimentCoordinator(
        k8s_client=None,
        storage_root=tmp_path / "storage",
        concurrency_caps={},
        worker_image="worker:latest",
        namespace="default",
        db=db,
        reporter=MarkdownReportGenerator(),
        cost_calculator=cost_calc,
        default_cap=4,
    )


def _make_run_result(
    experiment_id: str,
    run_id: str,
    findings: list[dict] | None = None,
    dataset_name: str = "test-dataset",
) -> dict:
    findings_list = findings or []
    return {
        "experiment": {
            "id": run_id,
            "experiment_id": experiment_id,
            "model_id": "gpt-4o",
            "strategy": "single_agent",
            "tool_variant": "with_tools",
            "review_profile": "default",
            "verification_variant": "none",
            "dataset_name": dataset_name,
            "dataset_version": "v1",
            "tool_extensions": [],
        },
        "status": "completed",
        "findings": findings_list,
        "strategy_output": {
            "findings": findings_list,
            "pre_dedup_count": len(findings_list),
            "post_dedup_count": len(findings_list),
            "dedup_log": [],
        },
        "prompt_snapshot": {
            "snapshot_id": "deadbeef00000000",
            "captured_at": "2026-04-20T00:00:00",
            "system_prompt": "",
            "user_message_template": "",
            "finding_output_format": "",
        },
        "tool_call_count": 0,
        "total_input_tokens": 100,
        "total_output_tokens": 50,
        "verification_tokens": 0,
        "estimated_cost_usd": 0.01,
        "duration_seconds": 10.0,
    }


def _sqli_finding(run_id: str, experiment_id: str) -> dict:
    return {
        "id": f"finding-sqli-{run_id}",
        "file_path": "src/auth/login.py",
        "line_start": 42,
        "vuln_class": "sqli",
        "severity": "high",
        "title": "SQL Injection",
        "description": "Unsanitized user input in SQL query",
        "raw_llm_output": "...",
        "confidence": 0.9,
        "produced_by": "zero_shot",
        "experiment_id": run_id,
    }


def _xss_finding(run_id: str, experiment_id: str) -> dict:
    return {
        "id": f"finding-xss-{run_id}",
        "file_path": "src/templates/index.html",
        "line_start": 10,
        "vuln_class": "xss",
        "severity": "medium",
        "title": "XSS via user input",
        "description": "Reflected XSS",
        "raw_llm_output": "...",
        "confidence": 0.8,
        "produced_by": "zero_shot",
        "experiment_id": run_id,
    }


def _write_run_result(storage: Path, experiment_id: str, run_id: str, result: dict) -> Path:
    run_dir = storage / "outputs" / experiment_id / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    result_file = run_dir / "run_result.json"
    result_file.write_text(json.dumps(result))
    return result_file


async def _create_experiment(db: Database, experiment_id: str, dataset_name: str = "test-dataset") -> None:
    config = json.dumps({"dataset_name": dataset_name})
    await db.create_experiment(
        experiment_id=experiment_id,
        config_json=config,
        total_runs=1,
        max_cost_usd=None,
    )


async def _register_run(db: Database, run_id: str, experiment_id: str, result_path: str) -> None:
    await db.create_run(
        run_id=run_id,
        experiment_id=experiment_id,
        config_json="{}",
        model_id="gpt-4o",
        strategy="zero_shot",
        tool_variant="with_tools",
        review_profile="default",
        verification_variant="none",
    )
    await db.update_run(run_id, "completed", result_path=result_path)


@pytest.fixture
async def coordinator_client(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)
    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=False) as client:
                yield client, c, tmp_path, db


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_compare_runs_cross_experiment_happy_path(coordinator_client):
    """Two runs from different experiments with overlapping findings compare correctly."""
    client, c, tmp_path, db = coordinator_client
    storage = tmp_path / "storage"

    exp_a = "exp-aaa"
    exp_b = "exp-bbb"
    run_a = "run-a-001"
    run_b = "run-b-001"

    await _create_experiment(db, exp_a, "dataset-alpha")
    await _create_experiment(db, exp_b, "dataset-alpha")

    result_a = _make_run_result(exp_a, run_a, findings=[_sqli_finding(run_a, exp_a), _xss_finding(run_a, exp_a)])
    result_b = _make_run_result(exp_b, run_b, findings=[_sqli_finding(run_b, exp_b)])

    path_a = _write_run_result(storage, exp_a, run_a, result_a)
    path_b = _write_run_result(storage, exp_b, run_b, result_b)

    await _register_run(db, run_a, exp_a, str(path_a))
    await _register_run(db, run_b, exp_b, str(path_b))

    resp = client.get(
        f"/compare-runs?a_experiment={exp_a}&a_run={run_a}&b_experiment={exp_b}&b_run={run_b}"
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["run_a"]["id"] == run_a
    assert data["run_a"]["experiment_id"] == exp_a
    assert data["run_b"]["id"] == run_b
    assert data["run_b"]["experiment_id"] == exp_b

    # sqli is in both (same file_path + vuln_class + line_bucket), xss only in A
    assert len(data["found_by_both"]) == 1
    assert len(data["only_in_a"]) == 1
    assert len(data["only_in_b"]) == 0

    assert data["dataset_mismatch"] is False
    assert data["warnings"] == []


@pytest.mark.asyncio
async def test_compare_runs_dataset_mismatch_warning(coordinator_client):
    """When experiments have different datasets, a warning is emitted and mismatch flag set."""
    client, c, tmp_path, db = coordinator_client
    storage = tmp_path / "storage"

    exp_a = "exp-x"
    exp_b = "exp-y"
    run_a = "run-x-001"
    run_b = "run-y-001"

    await _create_experiment(db, exp_a, "dataset-python")
    await _create_experiment(db, exp_b, "dataset-js")

    result_a = _make_run_result(exp_a, run_a, findings=[_sqli_finding(run_a, exp_a)])
    result_b = _make_run_result(exp_b, run_b, findings=[_sqli_finding(run_b, exp_b)])

    path_a = _write_run_result(storage, exp_a, run_a, result_a)
    path_b = _write_run_result(storage, exp_b, run_b, result_b)

    await _register_run(db, run_a, exp_a, str(path_a))
    await _register_run(db, run_b, exp_b, str(path_b))

    resp = client.get(
        f"/compare-runs?a_experiment={exp_a}&a_run={run_a}&b_experiment={exp_b}&b_run={run_b}"
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["dataset_mismatch"] is True
    assert len(data["warnings"]) == 1
    warning = data["warnings"][0]
    assert "dataset-python" in warning
    assert "dataset-js" in warning
    assert "FindingIdentity" in warning


@pytest.mark.asyncio
async def test_compare_runs_dataset_unknown_still_flags_mismatch(coordinator_client):
    """When experiments differ and one has no dataset metadata, mismatch flag is still set
    and a warning surfaces the uncertainty — rather than silently treating empty as equal."""
    client, c, tmp_path, db = coordinator_client
    storage = tmp_path / "storage"

    exp_a = "exp-known"
    exp_b = "exp-unknown"
    run_a = "run-known-001"
    run_b = "run-unknown-001"

    # Only exp_a is registered in the db with dataset metadata.
    # exp_b's run is registered but the experiment row is absent, so dataset will be "".
    await _create_experiment(db, exp_a, "dataset-python")

    result_a = _make_run_result(exp_a, run_a, findings=[_sqli_finding(run_a, exp_a)])
    result_b = _make_run_result(exp_b, run_b, findings=[_sqli_finding(run_b, exp_b)])

    path_a = _write_run_result(storage, exp_a, run_a, result_a)
    path_b = _write_run_result(storage, exp_b, run_b, result_b)

    await _register_run(db, run_a, exp_a, str(path_a))
    await _register_run(db, run_b, exp_b, str(path_b))

    resp = client.get(
        f"/compare-runs?a_experiment={exp_a}&a_run={run_a}&b_experiment={exp_b}&b_run={run_b}"
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["dataset_mismatch"] is True
    assert len(data["warnings"]) == 1
    assert "Could not determine dataset" in data["warnings"][0]


@pytest.mark.asyncio
async def test_compare_runs_missing_run_404(coordinator_client):
    """Returns 404 when the run result file does not exist."""
    client, c, tmp_path, db = coordinator_client
    storage = tmp_path / "storage"

    exp_a = "exp-exists"
    exp_b = "exp-exists"
    run_a = "run-real"
    run_b = "run-ghost"

    await _create_experiment(db, exp_a, "dataset-alpha")

    result_a = _make_run_result(exp_a, run_a, findings=[])
    path_a = _write_run_result(storage, exp_a, run_a, result_a)
    await _register_run(db, run_a, exp_a, str(path_a))

    # run_b not registered or written — expect 404
    resp = client.get(
        f"/compare-runs?a_experiment={exp_a}&a_run={run_a}&b_experiment={exp_b}&b_run={run_b}"
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_legacy_same_experiment_endpoint_still_works(coordinator_client):
    """GET /experiments/{experiment_id}/compare-runs with run_a + run_b still works."""
    client, c, tmp_path, db = coordinator_client
    storage = tmp_path / "storage"

    experiment_id = "legacy-experiment"
    run_a = "run-leg-a"
    run_b = "run-leg-b"

    await _create_experiment(db, experiment_id, "dataset-alpha")

    result_a = _make_run_result(experiment_id, run_a, findings=[_sqli_finding(run_a, experiment_id)])
    result_b = _make_run_result(experiment_id, run_b, findings=[_sqli_finding(run_b, experiment_id)])

    path_a = _write_run_result(storage, experiment_id, run_a, result_a)
    path_b = _write_run_result(storage, experiment_id, run_b, result_b)

    await _register_run(db, run_a, experiment_id, str(path_a))
    await _register_run(db, run_b, experiment_id, str(path_b))

    resp = client.get(f"/experiments/{experiment_id}/compare-runs?run_a={run_a}&run_b={run_b}")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    # Both runs find the same sqli at same location — should be 1 overlap
    assert len(data["found_by_both"]) == 1
    assert len(data["only_in_a"]) == 0
    assert len(data["only_in_b"]) == 0

    # Legacy endpoint still returns the new fields
    assert "dataset_mismatch" in data
    assert data["dataset_mismatch"] is False
    assert "warnings" in data
    assert data["run_a"]["experiment_id"] == experiment_id
    assert data["run_b"]["experiment_id"] == experiment_id


@pytest.mark.asyncio
async def test_compare_runs_cross_missing_params_400(coordinator_client):
    """Returns 422 when required query params are omitted (FastAPI validation)."""
    client, c, tmp_path, db = coordinator_client

    resp = client.get("/compare-runs?a_experiment=x&a_run=y")
    assert resp.status_code == 422  # FastAPI param validation


@pytest.mark.asyncio
async def test_compare_runs_same_experiment_no_mismatch(coordinator_client):
    """Same experiment on both sides — dataset_mismatch must be False even if dataset is set."""
    client, c, tmp_path, db = coordinator_client
    storage = tmp_path / "storage"

    experiment_id = "exp-same"
    run_a = "run-same-a"
    run_b = "run-same-b"

    await _create_experiment(db, experiment_id, "dataset-python")

    result_a = _make_run_result(experiment_id, run_a, findings=[])
    result_b = _make_run_result(experiment_id, run_b, findings=[])

    path_a = _write_run_result(storage, experiment_id, run_a, result_a)
    path_b = _write_run_result(storage, experiment_id, run_b, result_b)

    await _register_run(db, run_a, experiment_id, str(path_a))
    await _register_run(db, run_b, experiment_id, str(path_b))

    resp = client.get(
        f"/compare-runs?a_experiment={experiment_id}&a_run={run_a}&b_experiment={experiment_id}&b_run={run_b}"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["dataset_mismatch"] is False
    assert data["warnings"] == []
