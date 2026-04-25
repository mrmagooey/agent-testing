"""Round-trip integration tests for Phase 3: materialize_dataset wiring,
rematerialize endpoint, and bundle descriptor round-trips.

Covers 11 spec cases:
  1.  test_round_trip_git_dataset
  2.  test_round_trip_derived_dataset_chain
  3.  test_materialization_failure_warns
  4.  test_templates_version_mismatch_warns
  5.  test_dataset_label_round_trip_full_fidelity
  6.  test_get_labels_with_filters_after_import
  7.  test_idempotent_label_reimport
  8.  test_no_labels_json_files_anywhere
  9.  test_rematerialize_endpoint_happy_path
 10.  test_rematerialize_endpoint_404
 11.  test_rematerialize_endpoint_templates_version_mismatch
"""

from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.bundle import async_apply_bundle, async_write_bundle
from sec_review_framework.coordinator import ExperimentCoordinator, app
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.db import Database
from sec_review_framework.reporting.markdown import MarkdownReportGenerator

# ---------------------------------------------------------------------------
# Fixture / helper paths
# ---------------------------------------------------------------------------

FIXTURE_TEMPLATES_DIR = Path(__file__).parent.parent / "fixtures" / "templates"
TEMPLATE_ID = "test_sqli_format_string"


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_coordinator(tmp_path: Path, db: Database) -> ExperimentCoordinator:
    cost_calc = CostCalculator(
        pricing={"gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0)}
    )
    storage = tmp_path / "storage"
    storage.mkdir(parents=True, exist_ok=True)
    return ExperimentCoordinator(
        k8s_client=None,
        storage_root=storage,
        concurrency_caps={},
        worker_image="worker:latest",
        namespace="default",
        db=db,
        reporter=MarkdownReportGenerator(),
        cost_calculator=cost_calc,
        default_cap=4,
    )


def _init_git_repo(path: Path, content: str = "def foo():\n    pass\n") -> str:
    """Initialize a git repo with a single commit. Returns HEAD SHA."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "app.py").write_text(content)
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path, check=True, capture_output=True,
    )
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=path, check=True, capture_output=True,
    )
    sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=path, text=True
    ).strip()
    return sha


def _install_templates(storage_root: Path) -> None:
    """Copy test fixture templates into storage_root/datasets/templates/sqli/."""
    templates_dir = storage_root / "datasets" / "templates" / "sqli"
    templates_dir.mkdir(parents=True, exist_ok=True)
    src = FIXTURE_TEMPLATES_DIR / "sqli" / "test_sqli_template.yaml"
    (templates_dir / "test_sqli_format_string.yaml").write_text(src.read_text())


def _seed_experiment(
    storage_root: Path,
    exp_id: str,
    dataset_name: str,
) -> tuple[str, dict]:
    """Write a minimal experiment + run to disk (not DB). Returns (run_id, exp_row)."""
    run_id = f"{exp_id}-run-001"
    run_dir = storage_root / "outputs" / exp_id / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    run_result = {
        "experiment": {
            "id": run_id,
            "experiment_id": exp_id,
            "strategy_id": "builtin.single_agent",
            "model_id": "gpt-4o",
            "strategy": "single_agent",
            "tool_variant": "with_tools",
            "review_profile": "default",
            "verification_variant": "none",
            "dataset_name": dataset_name,
            "dataset_version": "v1",
            "strategy_config": {},
            "provider_kwargs": {},
            "parallel": False,
            "repetition_index": 0,
            "tool_extensions": [],
        },
        "status": "completed",
        "findings": [],
        "strategy_output": {
            "findings": [],
            "pre_dedup_count": 0,
            "post_dedup_count": 0,
            "dedup_log": [],
        },
        "bundle_snapshot": {
            "snapshot_id": "abc123def456789a",
            "strategy_id": "builtin.single_agent",
            "captured_at": "2026-01-01T00:00:00",
            "bundle_json": "{}",
        },
        "tool_call_count": 0,
        "total_input_tokens": 100,
        "total_output_tokens": 20,
        "verification_tokens": 0,
        "estimated_cost_usd": 0.01,
        "duration_seconds": 5.0,
        "completed_at": "2026-01-01T01:00:00",
    }
    (run_dir / "run_result.json").write_text(json.dumps(run_result))

    config_dir = storage_root / "config" / "runs"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / f"{run_id}.json").write_text(
        json.dumps({"run_id": run_id, "experiment_id": exp_id,
                    "dataset_name": dataset_name})
    )

    exp_row = {
        "id": exp_id,
        "config_json": json.dumps({"experiment_id": exp_id, "dataset_name": dataset_name}),
        "status": "completed",
        "total_runs": 1,
        "max_cost_usd": None,
        "spent_usd": 0.01,
        "created_at": "2026-01-01T00:00:00",
        "completed_at": "2026-01-01T01:00:00",
    }
    run_row = {
        "id": run_id,
        "experiment_id": exp_id,
        "config_json": json.dumps(
            {"run_id": run_id, "experiment_id": exp_id, "dataset_name": dataset_name}
        ),
        "status": "completed",
        "model_id": "gpt-4o",
        "strategy": "single_agent",
        "tool_variant": "with_tools",
        "review_profile": "default",
        "verification_variant": "none",
        "estimated_cost_usd": 0.01,
        "duration_seconds": 5.0,
        "result_path": None,
        "error": None,
        "created_at": "2026-01-01T00:00:00",
        "completed_at": "2026-01-01T01:00:00",
        "tool_extensions": "",
    }
    return run_id, exp_row, run_row


def _make_label_row(
    label_id: str,
    dataset_name: str,
    file_path: str = "app.py",
    cwe_id: str = "CWE-89",
    vuln_class: str = "sqli",
    severity: str = "high",
    source: str = "cve_patch",
) -> dict:
    return {
        "id": label_id,
        "dataset_name": dataset_name,
        "dataset_version": "v1",
        "file_path": file_path,
        "line_start": 1,
        "line_end": 2,
        "cwe_id": cwe_id,
        "vuln_class": vuln_class,
        "severity": severity,
        "description": "Test label",
        "source": source,
        "confidence": "confirmed",
        "created_at": datetime.now(UTC).isoformat(),
    }


# ---------------------------------------------------------------------------
# 1. test_round_trip_git_dataset
# ---------------------------------------------------------------------------


async def test_round_trip_git_dataset(tmp_path: Path) -> None:
    """Build kind='git' dataset, export with descriptor mode, wipe, import.

    Asserts: dataset row matches, repo re-cloned at expected SHA,
    materialized_at populated, dataset_labels byte-identical.
    """
    # Source setup
    src_repo = tmp_path / "origin_repo"
    sha = _init_git_repo(src_repo)

    db = Database(tmp_path / "src.db")
    await db.init()
    coord = _make_coordinator(tmp_path, db)

    ds_name = "test-git-ds"
    now = datetime.now(UTC).isoformat()

    # Insert dataset row and label into source DB
    await db.create_dataset({
        "name": ds_name,
        "kind": "git",
        "origin_url": f"file://{src_repo}",
        "origin_commit": sha,
        "created_at": now,
    })
    label = _make_label_row(str(uuid.uuid4()), ds_name)
    await db.append_dataset_labels([label])

    # Clone repo so source has a materialized copy
    repo_dir = coord.storage_root / "datasets" / ds_name / "repo"
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", f"file://{src_repo}", str(repo_dir)],
        check=True, capture_output=True,
    )
    await db.update_dataset_materialized_at(ds_name, now)

    # Create experiment referencing the dataset
    exp_id = "rt-git-exp-001"
    run_id, exp_row, run_row = _seed_experiment(coord.storage_root, exp_id, ds_name)
    await db.import_experiment_rows(exp_row, [run_row])

    # Export
    out_path = tmp_path / "bundle.secrev.zip"
    await async_write_bundle(db, coord.storage_root, exp_id, out_path=out_path)

    # Wipe: new DB + new storage root
    db2 = Database(tmp_path / "dst.db")
    await db2.init()
    storage2 = tmp_path / "storage2"
    storage2.mkdir()
    coord2 = ExperimentCoordinator(
        k8s_client=None,
        storage_root=storage2,
        concurrency_caps={},
        worker_image="worker:latest",
        namespace="default",
        db=db2,
        reporter=MarkdownReportGenerator(),
        cost_calculator=CostCalculator(pricing={
            "gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0)
        }),
        default_cap=4,
    )

    # Import with materialize wired
    summary = await async_apply_bundle(
        db2, storage2, out_path,
        conflict_policy="reject",
        materialize=coord2.materialize_dataset,
    )

    # Assertions: DB row matches
    row2 = await db2.get_dataset(ds_name)
    assert row2 is not None, "Dataset row must exist after import"
    assert row2["kind"] == "git"
    assert row2["origin_url"] == f"file://{src_repo}"
    assert row2["origin_commit"] == sha

    # Repo re-cloned
    repo2 = storage2 / "datasets" / ds_name / "repo"
    assert repo2.exists(), "Repo must be re-cloned after import"
    cloned_sha = subprocess.check_output(
        ["git", "rev-parse", "HEAD"], cwd=repo2, text=True
    ).strip()
    assert cloned_sha == sha, f"SHA mismatch: {cloned_sha!r} != {sha!r}"

    # materialized_at populated
    row2_fresh = await db2.get_dataset(ds_name)
    assert row2_fresh["materialized_at"] is not None, "materialized_at must be set"

    # datasets_rehydrated summary
    assert ds_name in summary["datasets_rehydrated"]

    # Labels round-trip byte-identical (by ID set + field equality)
    labels2 = await db2.list_dataset_labels(ds_name)
    assert len(labels2) == 1
    assert labels2[0]["id"] == label["id"]
    assert labels2[0]["cwe_id"] == label["cwe_id"]


# ---------------------------------------------------------------------------
# 2. test_round_trip_derived_dataset_chain
# ---------------------------------------------------------------------------


async def test_round_trip_derived_dataset_chain(tmp_path: Path) -> None:
    """Source has kind='derived' whose base is kind='git'. Both rows in source DB.

    Export → wipe → import. Assert both rows imported, base materialized first,
    derived materialized via injection replay.
    """
    src_repo = tmp_path / "origin_repo"
    sha = _init_git_repo(src_repo, content="def foo():\n    pass\n")

    db = Database(tmp_path / "src.db")
    await db.init()
    coord = _make_coordinator(tmp_path, db)

    _install_templates(coord.storage_root)
    templates_version = coord.templates_version

    base_name = "chain-base"
    derived_name = f"chain-base_injected_{TEMPLATE_ID}"
    now = datetime.now(UTC).isoformat()

    # Base dataset
    await db.create_dataset({
        "name": base_name,
        "kind": "git",
        "origin_url": f"file://{src_repo}",
        "origin_commit": sha,
        "created_at": now,
    })
    base_label = _make_label_row(str(uuid.uuid4()), base_name)
    await db.append_dataset_labels([base_label])

    # Materialize base on source
    repo_dir = coord.storage_root / "datasets" / base_name / "repo"
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", f"file://{src_repo}", str(repo_dir)],
        check=True, capture_output=True,
    )
    await db.update_dataset_materialized_at(base_name, now)

    # Derived dataset
    recipe = {
        "templates_version": templates_version,
        "applications": [
            {
                "template_id": TEMPLATE_ID,
                "target_file": "app.py",
                "substitutions": None,
            }
        ],
    }
    await db.create_dataset({
        "name": derived_name,
        "kind": "derived",
        "base_dataset": base_name,
        "recipe_json": json.dumps(recipe),
        "created_at": now,
    })
    derived_label = _make_label_row(
        str(uuid.uuid4()), derived_name, source="injected"
    )
    await db.append_dataset_labels([derived_label])

    # Materialize derived on source (copy base + inject)
    derived_repo = coord.storage_root / "datasets" / derived_name / "repo"
    derived_repo.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(repo_dir, derived_repo)
    await db.update_dataset_materialized_at(derived_name, now)

    # Create experiment referencing derived dataset
    exp_id = "rt-chain-exp-001"
    run_id, exp_row, run_row = _seed_experiment(coord.storage_root, exp_id, derived_name)
    await db.import_experiment_rows(exp_row, [run_row])

    # Export
    out_path = tmp_path / "bundle.secrev.zip"
    await async_write_bundle(db, coord.storage_root, exp_id, out_path=out_path)

    # Wipe
    db2 = Database(tmp_path / "dst.db")
    await db2.init()
    storage2 = tmp_path / "storage2"
    storage2.mkdir()
    # Install same templates on destination so templates_version matches
    _install_templates(storage2)

    coord2 = ExperimentCoordinator(
        k8s_client=None,
        storage_root=storage2,
        concurrency_caps={},
        worker_image="worker:latest",
        namespace="default",
        db=db2,
        reporter=MarkdownReportGenerator(),
        cost_calculator=CostCalculator(pricing={
            "gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0)
        }),
        default_cap=4,
    )

    summary = await async_apply_bundle(
        db2, storage2, out_path,
        conflict_policy="reject",
        materialize=coord2.materialize_dataset,
    )

    # Both rows imported
    base_row2 = await db2.get_dataset(base_name)
    derived_row2 = await db2.get_dataset(derived_name)
    assert base_row2 is not None, "Base dataset row must exist"
    assert derived_row2 is not None, "Derived dataset row must exist"

    # Both repos materialized
    base_repo2 = storage2 / "datasets" / base_name / "repo"
    derived_repo2 = storage2 / "datasets" / derived_name / "repo"
    assert base_repo2.exists(), "Base repo must be materialized"
    assert derived_repo2.exists(), "Derived repo must be materialized"

    # datasets_rehydrated contains both
    rehydrated = set(summary["datasets_rehydrated"])
    assert base_name in rehydrated, f"base_name missing from rehydrated: {rehydrated}"
    assert derived_name in rehydrated, f"derived_name missing from rehydrated: {rehydrated}"

    # Labels round-trip
    base_labels2 = await db2.list_dataset_labels(base_name)
    assert len(base_labels2) == 1
    assert base_labels2[0]["id"] == base_label["id"]

    derived_labels2 = await db2.list_dataset_labels(derived_name)
    assert len(derived_labels2) == 1
    assert derived_labels2[0]["id"] == derived_label["id"]


# ---------------------------------------------------------------------------
# 3. test_materialization_failure_warns
# ---------------------------------------------------------------------------


async def test_materialization_failure_warns(tmp_path: Path) -> None:
    """origin_url pointing to a non-existent path: row imported,
    materialized_at NULL, warnings contains clone failure message.
    """
    db = Database(tmp_path / "src.db")
    await db.init()
    coord = _make_coordinator(tmp_path, db)

    ds_name = "bad-url-ds"
    now = datetime.now(UTC).isoformat()

    # Bad origin_url (non-existent path)
    await db.create_dataset({
        "name": ds_name,
        "kind": "git",
        "origin_url": f"file:///nonexistent/path/that/does/not/exist_{uuid.uuid4().hex}",
        "origin_commit": "abc123",
        "created_at": now,
    })
    label = _make_label_row(str(uuid.uuid4()), ds_name)
    await db.append_dataset_labels([label])

    # Create experiment referencing dataset
    exp_id = "rt-fail-exp-001"
    run_id, exp_row, run_row = _seed_experiment(coord.storage_root, exp_id, ds_name)
    await db.import_experiment_rows(exp_row, [run_row])

    out_path = tmp_path / "bundle.secrev.zip"
    await async_write_bundle(db, coord.storage_root, exp_id, out_path=out_path)

    # Import to fresh DB/storage
    db2 = Database(tmp_path / "dst.db")
    await db2.init()
    storage2 = tmp_path / "storage2"
    storage2.mkdir()
    coord2 = ExperimentCoordinator(
        k8s_client=None,
        storage_root=storage2,
        concurrency_caps={},
        worker_image="worker:latest",
        namespace="default",
        db=db2,
        reporter=MarkdownReportGenerator(),
        cost_calculator=CostCalculator(pricing={
            "gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0)
        }),
        default_cap=4,
    )

    summary = await async_apply_bundle(
        db2, storage2, out_path,
        conflict_policy="reject",
        materialize=coord2.materialize_dataset,
    )

    # Row imported
    row2 = await db2.get_dataset(ds_name)
    assert row2 is not None, "Dataset row must still be imported"

    # materialized_at must be NULL (clone failed)
    assert row2["materialized_at"] is None, (
        f"materialized_at should be NULL after clone failure, got {row2['materialized_at']!r}"
    )

    # warnings contains failure message
    warnings = summary["warnings"]
    assert any(ds_name in w for w in warnings), (
        f"Expected warning mentioning {ds_name!r}, got: {warnings}"
    )

    # datasets_rehydrated does NOT contain the name
    assert ds_name not in summary["datasets_rehydrated"]


# ---------------------------------------------------------------------------
# 4. test_templates_version_mismatch_warns
# ---------------------------------------------------------------------------


async def test_templates_version_mismatch_warns(tmp_path: Path) -> None:
    """Derived dataset with mismatched templates_version: row imported,
    materialized_at NULL, warnings contain 'templates_version mismatch'.
    """
    src_repo = tmp_path / "origin_repo"
    sha = _init_git_repo(src_repo)

    db = Database(tmp_path / "src.db")
    await db.init()
    coord = _make_coordinator(tmp_path, db)
    _install_templates(coord.storage_root)

    base_name = "mismatch-base"
    derived_name = f"mismatch-base_injected_{TEMPLATE_ID}"
    now = datetime.now(UTC).isoformat()

    # Base
    await db.create_dataset({
        "name": base_name,
        "kind": "git",
        "origin_url": f"file://{src_repo}",
        "origin_commit": sha,
        "created_at": now,
    })
    repo_dir = coord.storage_root / "datasets" / base_name / "repo"
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", f"file://{src_repo}", str(repo_dir)],
        check=True, capture_output=True,
    )
    await db.update_dataset_materialized_at(base_name, now)

    # Derived with deliberately wrong templates_version
    recipe_with_bad_version = {
        "templates_version": "this-is-a-wrong-version-deadbeef",
        "applications": [
            {
                "template_id": TEMPLATE_ID,
                "target_file": "app.py",
                "substitutions": None,
            }
        ],
    }
    await db.create_dataset({
        "name": derived_name,
        "kind": "derived",
        "base_dataset": base_name,
        "recipe_json": json.dumps(recipe_with_bad_version),
        "created_at": now,
    })
    await db.update_dataset_materialized_at(derived_name, now)

    # Experiment referencing derived
    exp_id = "rt-mismatch-exp-001"
    run_id, exp_row, run_row = _seed_experiment(coord.storage_root, exp_id, derived_name)
    await db.import_experiment_rows(exp_row, [run_row])

    out_path = tmp_path / "bundle.secrev.zip"
    await async_write_bundle(db, coord.storage_root, exp_id, out_path=out_path)

    # Destination: templates present but templates_version will differ
    db2 = Database(tmp_path / "dst.db")
    await db2.init()
    storage2 = tmp_path / "storage2"
    storage2.mkdir()
    _install_templates(storage2)  # installs real templates, not matching "wrong-version"

    coord2 = ExperimentCoordinator(
        k8s_client=None,
        storage_root=storage2,
        concurrency_caps={},
        worker_image="worker:latest",
        namespace="default",
        db=db2,
        reporter=MarkdownReportGenerator(),
        cost_calculator=CostCalculator(pricing={
            "gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0)
        }),
        default_cap=4,
    )

    summary = await async_apply_bundle(
        db2, storage2, out_path,
        conflict_policy="reject",
        materialize=coord2.materialize_dataset,
    )

    # Row imported
    row2 = await db2.get_dataset(derived_name)
    assert row2 is not None, "Derived dataset row must still be imported"

    # materialized_at must be NULL
    assert row2["materialized_at"] is None, (
        f"materialized_at should be NULL after templates_version mismatch, "
        f"got {row2['materialized_at']!r}"
    )

    # warnings contain "templates_version mismatch"
    warnings = summary["warnings"]
    assert any("templates_version mismatch" in w for w in warnings), (
        f"Expected 'templates_version mismatch' in warnings, got: {warnings}"
    )

    # datasets_rehydrated does NOT contain the derived name
    assert derived_name not in summary["datasets_rehydrated"], (
        f"derived_name must not be in datasets_rehydrated after mismatch: "
        f"{summary['datasets_rehydrated']}"
    )


# ---------------------------------------------------------------------------
# 5. test_dataset_label_round_trip_full_fidelity
# ---------------------------------------------------------------------------


async def test_dataset_label_round_trip_full_fidelity(tmp_path: Path) -> None:
    """Labels exercising every GroundTruthLabel field (including optional ones)
    round-trip through bundle and are deep-equal after import.
    """
    src_repo = tmp_path / "origin_repo"
    sha = _init_git_repo(src_repo)

    db = Database(tmp_path / "src.db")
    await db.init()
    coord = _make_coordinator(tmp_path, db)

    ds_name = "full-fidelity-ds"
    now = datetime.now(UTC).isoformat()

    await db.create_dataset({
        "name": ds_name,
        "kind": "git",
        "origin_url": f"file://{src_repo}",
        "origin_commit": sha,
        "created_at": now,
    })

    # Clone so materialize on destination works
    repo_dir = coord.storage_root / "datasets" / ds_name / "repo"
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", f"file://{src_repo}", str(repo_dir)],
        check=True, capture_output=True,
    )
    await db.update_dataset_materialized_at(ds_name, now)

    # Labels with all optional fields set
    full_label = {
        "id": str(uuid.uuid4()),
        "dataset_name": ds_name,
        "dataset_version": "v2",
        "file_path": "src/auth.py",
        "line_start": 42,
        "line_end": 55,
        "cwe_id": "CWE-79",
        "vuln_class": "xss",
        "severity": "high",
        "description": "Reflected XSS via user-controlled data in response",
        "source": "cve_patch",
        "source_ref": "CVE-2026-99999",
        "confidence": "confirmed",
        "created_at": now,
        "notes": "Discovered during manual code review",
        "introduced_in_diff": True,
        "patch_lines_changed": 7,
    }
    await db.append_dataset_labels([full_label])

    # Experiment
    exp_id = "rt-fidelity-exp-001"
    run_id, exp_row, run_row = _seed_experiment(coord.storage_root, exp_id, ds_name)
    await db.import_experiment_rows(exp_row, [run_row])

    out_path = tmp_path / "bundle.secrev.zip"
    await async_write_bundle(db, coord.storage_root, exp_id, out_path=out_path)

    db2 = Database(tmp_path / "dst.db")
    await db2.init()
    storage2 = tmp_path / "storage2"
    storage2.mkdir()
    coord2 = ExperimentCoordinator(
        k8s_client=None,
        storage_root=storage2,
        concurrency_caps={},
        worker_image="worker:latest",
        namespace="default",
        db=db2,
        reporter=MarkdownReportGenerator(),
        cost_calculator=CostCalculator(pricing={
            "gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0)
        }),
        default_cap=4,
    )

    await async_apply_bundle(
        db2, storage2, out_path,
        conflict_policy="reject",
        materialize=coord2.materialize_dataset,
    )

    labels2 = await db2.list_dataset_labels(ds_name)
    assert len(labels2) == 1, f"Expected 1 label, got {len(labels2)}"
    lbl = labels2[0]

    # Deep equality on every field
    assert lbl["id"] == full_label["id"]
    assert lbl["dataset_version"] == full_label["dataset_version"]
    assert lbl["file_path"] == full_label["file_path"]
    assert lbl["line_start"] == full_label["line_start"]
    assert lbl["line_end"] == full_label["line_end"]
    assert lbl["cwe_id"] == full_label["cwe_id"]
    assert lbl["vuln_class"] == full_label["vuln_class"]
    assert lbl["severity"] == full_label["severity"]
    assert lbl["description"] == full_label["description"]
    assert lbl["source"] == full_label["source"]
    assert lbl["source_ref"] == full_label["source_ref"]
    assert lbl["confidence"] == full_label["confidence"]
    assert lbl["notes"] == full_label["notes"]
    assert lbl["introduced_in_diff"] == full_label["introduced_in_diff"]
    assert lbl["patch_lines_changed"] == full_label["patch_lines_changed"]


# ---------------------------------------------------------------------------
# 6. test_get_labels_with_filters_after_import
# ---------------------------------------------------------------------------


async def test_get_labels_with_filters_after_import(tmp_path: Path) -> None:
    """After import, GET /datasets/{name}/labels?cwe=CWE-79&severity=high
    returns the filtered subset from DB (proves Phase 2B wiring post-import).
    """
    src_repo = tmp_path / "origin_repo"
    sha = _init_git_repo(src_repo)

    db = Database(tmp_path / "src.db")
    await db.init()
    coord = _make_coordinator(tmp_path, db)

    ds_name = "filter-test-ds"
    now = datetime.now(UTC).isoformat()

    await db.create_dataset({
        "name": ds_name,
        "kind": "git",
        "origin_url": f"file://{src_repo}",
        "origin_commit": sha,
        "created_at": now,
    })
    repo_dir = coord.storage_root / "datasets" / ds_name / "repo"
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", f"file://{src_repo}", str(repo_dir)],
        check=True, capture_output=True,
    )
    await db.update_dataset_materialized_at(ds_name, now)

    # Three labels: one matching both filters, one matching only CWE, one neither
    label_match = _make_label_row(str(uuid.uuid4()), ds_name, cwe_id="CWE-79", severity="high")
    label_cwe_only = _make_label_row(
        str(uuid.uuid4()), ds_name, cwe_id="CWE-79", severity="medium"
    )
    label_other = _make_label_row(str(uuid.uuid4()), ds_name, cwe_id="CWE-89", severity="low")
    await db.append_dataset_labels([label_match, label_cwe_only, label_other])

    exp_id = "rt-filter-exp-001"
    run_id, exp_row, run_row = _seed_experiment(coord.storage_root, exp_id, ds_name)
    await db.import_experiment_rows(exp_row, [run_row])

    out_path = tmp_path / "bundle.secrev.zip"
    await async_write_bundle(db, coord.storage_root, exp_id, out_path=out_path)

    db2 = Database(tmp_path / "dst.db")
    await db2.init()
    storage2 = tmp_path / "storage2"
    storage2.mkdir()
    coord2 = ExperimentCoordinator(
        k8s_client=None,
        storage_root=storage2,
        concurrency_caps={},
        worker_image="worker:latest",
        namespace="default",
        db=db2,
        reporter=MarkdownReportGenerator(),
        cost_calculator=CostCalculator(pricing={
            "gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0)
        }),
        default_cap=4,
    )

    await async_apply_bundle(
        db2, storage2, out_path,
        conflict_policy="reject",
        materialize=coord2.materialize_dataset,
    )

    # Use the API endpoint to verify filter wiring
    with patch.object(coord_module, "coordinator", coord2):
        with patch.object(coord2, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                resp = client.get(
                    f"/datasets/{ds_name}/labels?cwe=CWE-79&severity=high"
                )
    assert resp.status_code == 200, resp.text
    filtered = resp.json()
    assert len(filtered) == 1, f"Expected 1 matching label, got {len(filtered)}: {filtered}"
    assert filtered[0]["id"] == label_match["id"]


# ---------------------------------------------------------------------------
# 7. test_idempotent_label_reimport
# ---------------------------------------------------------------------------


async def test_idempotent_label_reimport(tmp_path: Path) -> None:
    """Import two separate bundles that reference the same dataset and labels.

    Proves that ``append_dataset_labels`` is idempotent (INSERT OR IGNORE on PK):
    importing the same label rows a second time does not grow the table.
    Also verifies that ``dataset_labels_imported`` in the summary reflects the
    input count (not just newly inserted rows) on each call.
    """
    src_repo = tmp_path / "origin_repo"
    sha = _init_git_repo(src_repo)

    # --- Source DB: one dataset with two labels, referenced by two experiments ---
    db = Database(tmp_path / "src.db")
    await db.init()
    coord = _make_coordinator(tmp_path, db)

    ds_name = "idempotent-ds"
    now = datetime.now(UTC).isoformat()

    await db.create_dataset({
        "name": ds_name,
        "kind": "git",
        "origin_url": f"file://{src_repo}",
        "origin_commit": sha,
        "created_at": now,
    })
    repo_dir = coord.storage_root / "datasets" / ds_name / "repo"
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", f"file://{src_repo}", str(repo_dir)],
        check=True, capture_output=True,
    )
    await db.update_dataset_materialized_at(ds_name, now)

    label1 = _make_label_row(str(uuid.uuid4()), ds_name)
    label2 = _make_label_row(str(uuid.uuid4()), ds_name, file_path="other.py")
    await db.append_dataset_labels([label1, label2])

    # Experiment A
    exp_id_a = "rt-idempotent-exp-A"
    _, exp_row_a, run_row_a = _seed_experiment(coord.storage_root, exp_id_a, ds_name)
    await db.import_experiment_rows(exp_row_a, [run_row_a])

    # Experiment B (different ID, same dataset + same labels)
    exp_id_b = "rt-idempotent-exp-B"
    _, exp_row_b, run_row_b = _seed_experiment(coord.storage_root, exp_id_b, ds_name)
    await db.import_experiment_rows(exp_row_b, [run_row_b])

    out_path_a = tmp_path / "bundle_a.secrev.zip"
    out_path_b = tmp_path / "bundle_b.secrev.zip"
    await async_write_bundle(db, coord.storage_root, exp_id_a, out_path=out_path_a)
    await async_write_bundle(db, coord.storage_root, exp_id_b, out_path=out_path_b)

    # --- Destination DB: import both bundles ---
    db2 = Database(tmp_path / "dst.db")
    await db2.init()
    storage2 = tmp_path / "storage2"
    storage2.mkdir()
    coord2 = ExperimentCoordinator(
        k8s_client=None,
        storage_root=storage2,
        concurrency_caps={},
        worker_image="worker:latest",
        namespace="default",
        db=db2,
        reporter=MarkdownReportGenerator(),
        cost_calculator=CostCalculator(pricing={
            "gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0)
        }),
        default_cap=4,
    )

    # First import: experiment A (introduces dataset row + 2 labels)
    summary1 = await async_apply_bundle(
        db2, storage2, out_path_a,
        conflict_policy="reject",
        materialize=coord2.materialize_dataset,
    )
    assert summary1["dataset_labels_imported"] == 2

    count_after_first = len(await db2.list_dataset_labels(ds_name))
    assert count_after_first == 2

    # Second import: experiment B references the same dataset + same label IDs.
    # Dataset row conflicts → rename policy creates an alias row.
    # Label rows carry the original dataset_name; INSERT OR IGNORE deduplicates by PK.
    summary2 = await async_apply_bundle(
        db2, storage2, out_path_b,
        conflict_policy="rename",
        materialize=coord2.materialize_dataset,
    )
    # dataset_labels_imported reflects input count (idempotent via INSERT OR IGNORE)
    assert summary2["dataset_labels_imported"] == 2

    count_after_second = len(await db2.list_dataset_labels(ds_name))
    assert count_after_second == 2, (
        f"Label count must not grow on re-import (idempotent): {count_after_second}"
    )


# ---------------------------------------------------------------------------
# 8. test_no_labels_json_files_anywhere
# ---------------------------------------------------------------------------


async def test_no_labels_json_files_anywhere(tmp_path: Path) -> None:
    """After running a full round-trip, no labels.json or labels.jsonl files
    exist anywhere under storage_root.
    """
    src_repo = tmp_path / "origin_repo"
    sha = _init_git_repo(src_repo)

    db = Database(tmp_path / "src.db")
    await db.init()
    coord = _make_coordinator(tmp_path, db)

    ds_name = "no-json-ds"
    now = datetime.now(UTC).isoformat()

    await db.create_dataset({
        "name": ds_name,
        "kind": "git",
        "origin_url": f"file://{src_repo}",
        "origin_commit": sha,
        "created_at": now,
    })
    repo_dir = coord.storage_root / "datasets" / ds_name / "repo"
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", f"file://{src_repo}", str(repo_dir)],
        check=True, capture_output=True,
    )
    await db.update_dataset_materialized_at(ds_name, now)

    label = _make_label_row(str(uuid.uuid4()), ds_name)
    await db.append_dataset_labels([label])

    exp_id = "rt-nojson-exp-001"
    run_id, exp_row, run_row = _seed_experiment(coord.storage_root, exp_id, ds_name)
    await db.import_experiment_rows(exp_row, [run_row])

    out_path = tmp_path / "bundle.secrev.zip"
    await async_write_bundle(db, coord.storage_root, exp_id, out_path=out_path)

    db2 = Database(tmp_path / "dst.db")
    await db2.init()
    storage2 = tmp_path / "storage2"
    storage2.mkdir()
    coord2 = ExperimentCoordinator(
        k8s_client=None,
        storage_root=storage2,
        concurrency_caps={},
        worker_image="worker:latest",
        namespace="default",
        db=db2,
        reporter=MarkdownReportGenerator(),
        cost_calculator=CostCalculator(pricing={
            "gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0)
        }),
        default_cap=4,
    )

    await async_apply_bundle(
        db2, storage2, out_path,
        conflict_policy="reject",
        materialize=coord2.materialize_dataset,
    )

    # Assert no labels.json or labels.jsonl anywhere in either storage root
    for root in (coord.storage_root, storage2):
        json_files = list(root.rglob("labels.json"))
        jsonl_files = list(root.rglob("labels.jsonl"))
        assert not json_files, f"Unexpected labels.json found: {json_files}"
        assert not jsonl_files, f"Unexpected labels.jsonl found: {jsonl_files}"


# ---------------------------------------------------------------------------
# 9. test_rematerialize_endpoint_happy_path
# ---------------------------------------------------------------------------


async def test_rematerialize_endpoint_happy_path(tmp_path: Path) -> None:
    """Create kind='git' dataset, materialize, rm -rf repo, call rematerialize.
    Assert repo re-cloned and response includes fresh materialized_at.
    """
    src_repo = tmp_path / "origin_repo"
    sha = _init_git_repo(src_repo)

    db = Database(tmp_path / "test.db")
    await db.init()
    coord = _make_coordinator(tmp_path, db)

    ds_name = "rematerialize-happy-ds"
    now = datetime.now(UTC).isoformat()

    await db.create_dataset({
        "name": ds_name,
        "kind": "git",
        "origin_url": f"file://{src_repo}",
        "origin_commit": sha,
        "created_at": now,
    })

    # Initial materialization
    repo_dir = coord.storage_root / "datasets" / ds_name / "repo"
    await coord.materialize_dataset(ds_name)
    assert repo_dir.exists(), "Repo must exist after initial materialize"

    # rm -rf the repo
    shutil.rmtree(repo_dir)
    assert not repo_dir.exists(), "Repo must be gone"

    # Call rematerialize endpoint
    with patch.object(coord_module, "coordinator", coord):
        with patch.object(coord, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                resp = client.post(f"/datasets/{ds_name}/rematerialize")

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "materialized_at" in data, f"Response missing 'materialized_at': {data}"
    assert data["materialized_at"] is not None, "materialized_at must be populated"

    # Repo re-cloned
    assert repo_dir.exists(), "Repo must be re-cloned after rematerialize"


# ---------------------------------------------------------------------------
# 10. test_rematerialize_endpoint_404
# ---------------------------------------------------------------------------


async def test_rematerialize_endpoint_404(tmp_path: Path) -> None:
    """Call rematerialize on a name with no row → 404."""
    db = Database(tmp_path / "test.db")
    await db.init()
    coord = _make_coordinator(tmp_path, db)

    with patch.object(coord_module, "coordinator", coord):
        with patch.object(coord, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post("/datasets/nonexistent-ds-xyz/rematerialize")

    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# 11. test_rematerialize_endpoint_templates_version_mismatch
# ---------------------------------------------------------------------------


async def test_rematerialize_endpoint_templates_version_mismatch(tmp_path: Path) -> None:
    """Derived dataset with mismatched templates_version → 409 from rematerialize."""
    src_repo = tmp_path / "origin_repo"
    sha = _init_git_repo(src_repo)

    db = Database(tmp_path / "test.db")
    await db.init()
    coord = _make_coordinator(tmp_path, db)
    _install_templates(coord.storage_root)

    base_name = "rematerialize-base"
    derived_name = f"rematerialize-base_injected_{TEMPLATE_ID}"
    now = datetime.now(UTC).isoformat()

    # Base dataset (materialized)
    await db.create_dataset({
        "name": base_name,
        "kind": "git",
        "origin_url": f"file://{src_repo}",
        "origin_commit": sha,
        "created_at": now,
    })
    repo_dir = coord.storage_root / "datasets" / base_name / "repo"
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "clone", f"file://{src_repo}", str(repo_dir)],
        check=True, capture_output=True,
    )
    await db.update_dataset_materialized_at(base_name, now)

    # Derived dataset with deliberately wrong templates_version
    bad_recipe = {
        "templates_version": "this-version-does-not-match-anything",
        "applications": [
            {"template_id": TEMPLATE_ID, "target_file": "app.py", "substitutions": None}
        ],
    }
    await db.create_dataset({
        "name": derived_name,
        "kind": "derived",
        "base_dataset": base_name,
        "recipe_json": json.dumps(bad_recipe),
        "created_at": now,
    })

    with patch.object(coord_module, "coordinator", coord):
        with patch.object(coord, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=False) as client:
                resp = client.post(f"/datasets/{derived_name}/rematerialize")

    assert resp.status_code == 409, (
        f"Expected 409 for templates_version mismatch, got {resp.status_code}: {resp.text}"
    )
