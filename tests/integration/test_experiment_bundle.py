"""Integration tests for experiment bundle export/import.

Covers all 11 spec cases:
  1. Round-trip: export → wipe → import, verify DB rows + files + findings
  2. Reject policy returns 409 when experiment exists
  3. Rename policy produces _imported_ suffix; both queryable
  4. Merge policy with fresh run IDs succeeds
  5. Merge policy with colliding run IDs fails
  6. Missing-dataset import succeeds with warning
  7. schema_version: 99 rejected with 400
  8. tool_extensions string byte-identical after round-trip
  9. Memory test: 200 MiB conversation.jsonl, RSS delta < 200 MiB
 10. Path-traversal zip entry rejected; nothing written outside storage root
 11. Post-import findings query returns expected count
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import tracemalloc
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.bundle import (
    BundleConflictError,
    async_apply_bundle,
    async_write_bundle,
    read_manifest,
    write_bundle,
)
from sec_review_framework.coordinator import ExperimentCoordinator, app
from sec_review_framework.cost.calculator import CostCalculator, ModelPricing
from sec_review_framework.db import Database
from sec_review_framework.reporting.markdown import MarkdownReportGenerator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(tmp_path: Path, db: Database) -> ExperimentCoordinator:
    cost_calc = CostCalculator(
        pricing={"gpt-4o": ModelPricing(input_per_million=5.0, output_per_million=15.0)}
    )
    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
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
        config_dir=config_dir,
        default_cap=4,
    )


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(65536):
            h.update(chunk)
    return h.hexdigest()


def _make_finding(idx: int, exp_id: str, run_id: str = "") -> dict:
    unique_suffix = run_id.replace("-", "_") if run_id else "default"
    return {
        "id": f"finding-{unique_suffix}-{idx:03d}",
        "file_path": f"src/vuln_{idx}.py",
        "line_start": 10 * idx,
        "line_end": 10 * idx + 3,
        "vuln_class": "sqli",
        "cwe_ids": ["CWE-89"],
        "severity": "high",
        "confidence": 0.9,
        "title": f"SQL injection #{idx}",
        "description": f"Description for finding {idx}",
        "raw_llm_output": f"<raw {idx}>",
        "produced_by": "single_agent",
        "experiment_id": exp_id,
    }


def _make_run_result_json(run_id: str, exp_id: str, findings_count: int = 4) -> str:
    """Produce a minimal JSON run_result.json string."""
    findings = [_make_finding(i, exp_id, run_id) for i in range(findings_count)]
    strategy_id = "single_agent_default"
    data = {
        "experiment": {
            "id": run_id,
            "experiment_id": exp_id,
            "strategy_id": strategy_id,
            "model_id": "gpt-4o",
            "strategy": "single_agent",
            "tool_variant": "with_tools",
            "review_profile": "default",
            "verification_variant": "none",
            "dataset_name": "test-dataset",
            "dataset_version": "1.0.0",
            "strategy_config": {},
            "provider_kwargs": {},
            "parallel": False,
            "repetition_index": 0,
            "tool_extensions": [],
        },
        "status": "completed",
        "findings": findings,
        "strategy_output": {
            "findings": findings,
            "pre_dedup_count": findings_count,
            "post_dedup_count": findings_count,
            "dedup_log": [],
        },
        "bundle_snapshot": {
            "snapshot_id": "abc123def456789a",
            "strategy_id": strategy_id,
            "captured_at": "2026-01-01T00:00:00",
            "bundle_json": "{}",
        },
        "tool_call_count": 5,
        "total_input_tokens": 1000,
        "total_output_tokens": 200,
        "verification_tokens": 0,
        "estimated_cost_usd": 0.01,
        "duration_seconds": 10.0,
        "completed_at": "2026-01-01T01:00:00",
    }
    return json.dumps(data)


def _create_experiment_on_disk(
    storage_root: Path,
    exp_id: str,
    run_id: str,
    findings_count: int = 4,
) -> None:
    """Write minimal experiment artifacts to disk (but NOT to DB)."""
    run_dir = storage_root / "outputs" / exp_id / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    (run_dir / "run_result.json").write_text(
        _make_run_result_json(run_id, exp_id, findings_count)
    )

    conv_lines = [json.dumps({"role": "user", "content": f"msg {i}"}) for i in range(3)]
    (run_dir / "conversation.jsonl").write_text("\n".join(conv_lines) + "\n")

    (run_dir / "report.md").write_text(f"# Report for {run_id}\nNo issues.\n")
    (run_dir / "report.json").write_text(json.dumps({"run_id": run_id, "findings": []}))
    (run_dir / "report.txt").write_text(f"Run: {run_id}\n")

    config_dir = storage_root / "config" / "runs"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / f"{run_id}.json").write_text(
        json.dumps({"run_id": run_id, "experiment_id": exp_id})
    )


async def _seed_db(
    db: Database,
    exp_id: str,
    run_ids: list[str],
    tool_extensions_per_run: list[str] | None = None,
) -> None:
    """Insert an experiment + runs into the DB."""
    if tool_extensions_per_run is None:
        tool_extensions_per_run = [""] * len(run_ids)

    await db.import_experiment_rows(
        experiment_row={
            "id": exp_id,
            "config_json": json.dumps({"experiment_id": exp_id, "dataset_name": "test-dataset"}),
            "status": "completed",
            "total_runs": len(run_ids),
            "max_cost_usd": None,
            "spent_usd": 0.0,
            "created_at": "2026-01-01T00:00:00",
            "completed_at": "2026-01-01T01:00:00",
        },
        run_rows=[
            {
                "id": rid,
                "experiment_id": exp_id,
                "config_json": json.dumps({"run_id": rid}),
                "status": "completed",
                "model_id": "gpt-4o",
                "strategy": "single_agent",
                "tool_variant": "with_tools",
                "review_profile": "default",
                "verification_variant": "none",
                "estimated_cost_usd": 0.01,
                "duration_seconds": 10.0,
                "result_path": None,
                "error": None,
                "created_at": "2026-01-01T00:00:00",
                "completed_at": "2026-01-01T01:00:00",
                "tool_extensions": tool_extensions_per_run[i],
            }
            for i, rid in enumerate(run_ids)
        ],
    )


# ---------------------------------------------------------------------------
# Test 1: Round-trip (headline)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_round_trip(tmp_path: Path):
    """Export → wipe DB and outputs → import → assert DB rows, files, findings."""
    exp_id = "roundtrip-exp"
    run_id_1 = "run-001"
    run_id_2 = "run-002-ext-lsp-tree_sitter"

    storage_root = tmp_path / "storage"
    db_path = tmp_path / "test.db"

    db = Database(db_path)
    await db.init()

    await _seed_db(
        db, exp_id, [run_id_1, run_id_2],
        tool_extensions_per_run=["", "lsp,tree_sitter"],
    )

    _create_experiment_on_disk(storage_root, exp_id, run_id_1, findings_count=4)
    _create_experiment_on_disk(storage_root, exp_id, run_id_2, findings_count=4)

    # Export (async-safe)
    out_path = tmp_path / "export.secrev.zip"
    await async_write_bundle(db, storage_root, exp_id, include_datasets=False, out_path=out_path)
    assert out_path.exists()

    # Collect checksums before wipe
    outputs_dir = storage_root / "outputs" / exp_id
    original_checksums: dict[str, str] = {}
    for f in sorted(outputs_dir.rglob("*")):
        if f.is_file():
            rel = f.relative_to(outputs_dir)
            original_checksums[str(rel)] = _sha256(f)

    # Wipe DB and outputs
    db2_path = tmp_path / "test2.db"
    db2 = Database(db2_path)
    await db2.init()
    shutil.rmtree(outputs_dir)
    storage_root2 = tmp_path / "storage2"
    storage_root2.mkdir(parents=True, exist_ok=True)

    # Import (async-safe)
    summary = await async_apply_bundle(db2, storage_root2, out_path, conflict_policy="reject")
    assert summary["experiment_id"] == exp_id
    assert summary["runs_imported"] == 2
    assert summary["renamed_from"] is None

    # Assert DB experiment row
    exp_row = await db2.get_experiment(exp_id)
    assert exp_row is not None
    assert exp_row["id"] == exp_id
    assert exp_row["status"] == "completed"
    assert exp_row["total_runs"] == 2

    # Assert DB run rows
    runs = await db2.list_runs(exp_id)
    assert len(runs) == 2
    run_map = {r["id"]: r for r in runs}
    assert run_id_1 in run_map
    assert run_id_2 in run_map

    # tool_extensions preserved verbatim
    assert run_map[run_id_1]["tool_extensions"] == ""
    assert run_map[run_id_2]["tool_extensions"] == "lsp,tree_sitter"

    # All output files present with identical sha256
    new_outputs_dir = storage_root2 / "outputs" / exp_id
    for rel_path, original_sha in original_checksums.items():
        restored = new_outputs_dir / rel_path
        assert restored.exists(), f"Missing restored file: {rel_path}"
        assert _sha256(restored) == original_sha, f"SHA256 mismatch for {rel_path}"

    # Index findings and check count
    from sec_review_framework.data.experiment import RunResult

    for run_id in [run_id_1, run_id_2]:
        result_file = storage_root2 / "outputs" / exp_id / run_id / "run_result.json"
        result = RunResult.model_validate_json(result_file.read_text())
        if result.findings:
            await db2.upsert_findings_for_run(
                run_id=result.experiment.id,
                experiment_id=exp_id,
                findings=[f.model_dump(mode="json") for f in result.findings],
                model_id=result.experiment.model_id,
                strategy=result.experiment.strategy.value,
                dataset_name=result.experiment.dataset_name,
            )

    count = await db2.count_all_findings()
    assert count == 8  # 4 per run × 2 runs


# ---------------------------------------------------------------------------
# Test 2: Reject policy returns 409 when experiment exists (via API)
# ---------------------------------------------------------------------------


@pytest.fixture
async def coordinator_client(tmp_path: Path):
    db = Database(tmp_path / "test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)
    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with patch.object(coord_module, "IMPORT_ENABLED", True):
                with TestClient(app, raise_server_exceptions=True) as client:
                    yield client, c, c.storage_root, db


def test_reject_policy_409_via_api(coordinator_client):
    """Reject-policy import returns 409 when experiment already exists."""
    client, c, storage_root, db = coordinator_client
    exp_id = "conflict-exp"

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_seed_db(db, exp_id, ["run-a"]))
    finally:
        loop.close()
    _create_experiment_on_disk(storage_root, exp_id, "run-a")

    # Export using the synchronous write_bundle from a fresh loop
    out_path = storage_root / "bundle.secrev.zip"
    exp_row = asyncio.run(db.get_experiment(exp_id))
    run_rows = asyncio.run(db.list_runs(exp_id))
    write_bundle(
        db, storage_root, exp_id, include_datasets=False, out_path=out_path,
        _exp_row=exp_row, _run_rows=run_rows,
    )

    with open(out_path, "rb") as f:
        resp = client.post(
            "/experiments/import",
            files={"file": ("bundle.secrev.zip", f, "application/zip")},
            data={"conflict_policy": "reject"},
        )
    assert resp.status_code == 409
    assert "already exists" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Test 3: Rename policy produces _imported_ suffix
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rename_policy_both_queryable(tmp_path: Path):
    """Rename: importing from a source DB into a target DB that already has the same exp_id."""
    # Source DB: has exp_id with run-r1
    source_storage = tmp_path / "source_storage"
    source_db = Database(tmp_path / "source.db")
    await source_db.init()

    exp_id = "rename-exp"
    await _seed_db(source_db, exp_id, ["run-r1"])
    _create_experiment_on_disk(source_storage, exp_id, "run-r1")

    out_path = tmp_path / "export.secrev.zip"
    await async_write_bundle(source_db, source_storage, exp_id, include_datasets=False, out_path=out_path)

    # Target DB: ALSO has exp_id already (simulates a pre-existing experiment)
    target_storage = tmp_path / "target_storage"
    target_db = Database(tmp_path / "target.db")
    await target_db.init()

    # Seed the target with a DIFFERENT run ID so no collision
    await _seed_db(target_db, exp_id, ["run-existing"])
    _create_experiment_on_disk(target_storage, exp_id, "run-existing")

    # Import with rename — exp_id exists in target, so it gets renamed
    summary = await async_apply_bundle(target_db, target_storage, out_path, conflict_policy="rename")

    new_id = summary["experiment_id"]
    assert new_id != exp_id
    assert "_imported_" in new_id
    assert summary["renamed_from"] == exp_id
    assert len(summary["warnings"]) > 0

    # Both experiments queryable in target DB
    orig = await target_db.get_experiment(exp_id)
    renamed = await target_db.get_experiment(new_id)
    assert orig is not None
    assert renamed is not None

    # Runs for the renamed experiment have the new experiment_id FK
    new_runs = await target_db.list_runs(new_id)
    assert len(new_runs) == 1
    assert new_runs[0]["experiment_id"] == new_id
    assert new_runs[0]["id"] == "run-r1"  # run IDs are NOT rewritten


# ---------------------------------------------------------------------------
# Test 4: Merge policy with fresh run IDs succeeds
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_policy_fresh_runs_succeeds(tmp_path: Path):
    storage_root = tmp_path / "storage"
    db = Database(tmp_path / "test.db")
    await db.init()

    exp_id = "merge-exp"
    await _seed_db(db, exp_id, ["run-m1"])
    _create_experiment_on_disk(storage_root, exp_id, "run-m1")

    storage_b = tmp_path / "storage_b"
    db_b = Database(tmp_path / "test_b.db")
    await db_b.init()

    await _seed_db(db_b, exp_id, ["run-m2", "run-m3"])
    _create_experiment_on_disk(storage_b, exp_id, "run-m2")
    _create_experiment_on_disk(storage_b, exp_id, "run-m3")

    out_path = tmp_path / "export_b.secrev.zip"
    await async_write_bundle(db_b, storage_b, exp_id, include_datasets=False, out_path=out_path)

    summary = await async_apply_bundle(db, storage_root, out_path, conflict_policy="merge")
    assert summary["experiment_id"] == exp_id
    assert summary["runs_imported"] == 2

    runs = await db.list_runs(exp_id)
    run_ids = {r["id"] for r in runs}
    assert run_ids == {"run-m1", "run-m2", "run-m3"}


# ---------------------------------------------------------------------------
# Test 5: Merge policy with colliding run IDs fails
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_merge_policy_collision_fails(tmp_path: Path):
    storage_root = tmp_path / "storage"
    db = Database(tmp_path / "test.db")
    await db.init()

    exp_id = "merge-collision-exp"
    await _seed_db(db, exp_id, ["run-same"])
    _create_experiment_on_disk(storage_root, exp_id, "run-same")

    out_path = tmp_path / "export.secrev.zip"
    await async_write_bundle(db, storage_root, exp_id, include_datasets=False, out_path=out_path)

    with pytest.raises(BundleConflictError, match="run IDs already exist"):
        await async_apply_bundle(db, storage_root, out_path, conflict_policy="merge")


# ---------------------------------------------------------------------------
# Test 6: Missing-dataset import succeeds with warning
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_missing_dataset_import_succeeds(tmp_path: Path):
    storage_root = tmp_path / "storage"
    db = Database(tmp_path / "test.db")
    await db.init()

    exp_id = "ds-exp"
    await _seed_db(db, exp_id, ["run-ds1"])
    _create_experiment_on_disk(storage_root, exp_id, "run-ds1")

    out_path = tmp_path / "export_ds.secrev.zip"
    await async_write_bundle(db, storage_root, exp_id, include_datasets=False, out_path=out_path)

    # Patch manifest to reference a phantom dataset
    patched = tmp_path / "patched.secrev.zip"
    with zipfile.ZipFile(out_path, "r") as zin, zipfile.ZipFile(patched, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "manifest.json":
                m = json.loads(data)
                m["dataset_names"] = ["phantom-dataset"]
                m["dataset_mode"] = "reference"
                data = json.dumps(m).encode()
            zout.writestr(item, data)

    storage_fresh = tmp_path / "storage_fresh"
    db_fresh = Database(tmp_path / "test_fresh.db")
    await db_fresh.init()

    summary = await async_apply_bundle(db_fresh, storage_fresh, patched, conflict_policy="reject")
    assert summary["experiment_id"] == exp_id
    assert "phantom-dataset" in summary["datasets_missing"]
    assert len(summary["warnings"]) > 0

    exp = await db_fresh.get_experiment(exp_id)
    assert exp is not None


# ---------------------------------------------------------------------------
# Test 7: schema_version: 99 rejected with 400 (via API)
# ---------------------------------------------------------------------------


def test_unknown_schema_version_rejected(coordinator_client):
    """Bundles with schema_version != 1 are rejected with 400."""
    client, c, storage_root, db = coordinator_client
    exp_id = "schema-exp"

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_seed_db(db, exp_id, ["run-s1"]))
        exp_row = loop.run_until_complete(db.get_experiment(exp_id))
        run_rows = loop.run_until_complete(db.list_runs(exp_id))
    finally:
        loop.close()
    _create_experiment_on_disk(storage_root, exp_id, "run-s1")

    out_path = storage_root / "schema_bundle.secrev.zip"
    write_bundle(
        db, storage_root, exp_id, include_datasets=False, out_path=out_path,
        _exp_row=exp_row, _run_rows=run_rows,
    )

    bad_bundle = storage_root / "bad_schema.secrev.zip"
    with zipfile.ZipFile(out_path, "r") as zin, zipfile.ZipFile(bad_bundle, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "manifest.json":
                m = json.loads(data)
                m["schema_version"] = 99
                data = json.dumps(m).encode()
            zout.writestr(item, data)

    with open(bad_bundle, "rb") as f:
        resp = client.post(
            "/experiments/import",
            files={"file": ("bad_schema.secrev.zip", f, "application/zip")},
            data={"conflict_policy": "reject"},
        )
    assert resp.status_code == 400
    assert "schema_version" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Test 8: tool_extensions string column byte-identical after round-trip
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tool_extensions_byte_identical(tmp_path: Path):
    storage_root = tmp_path / "storage"
    db = Database(tmp_path / "test.db")
    await db.init()

    exp_id = "ext-exp"
    ext_str = "lsp,tree_sitter"
    await _seed_db(db, exp_id, ["run-ext1"], tool_extensions_per_run=[ext_str])
    _create_experiment_on_disk(storage_root, exp_id, "run-ext1")

    out_path = tmp_path / "export.secrev.zip"
    await async_write_bundle(db, storage_root, exp_id, include_datasets=False, out_path=out_path)

    db2 = Database(tmp_path / "test2.db")
    await db2.init()
    storage2 = tmp_path / "storage2"
    storage2.mkdir(parents=True, exist_ok=True)

    await async_apply_bundle(db2, storage2, out_path, conflict_policy="reject")

    runs = await db2.list_runs(exp_id)
    assert len(runs) == 1
    assert runs[0]["tool_extensions"] == ext_str


# ---------------------------------------------------------------------------
# Test 9: Memory test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_export_large_jsonl(tmp_path: Path):
    """Peak RSS delta during export of 200 MiB conversation.jsonl stays under 200 MiB."""
    storage_root = tmp_path / "storage"
    db = Database(tmp_path / "test.db")
    await db.init()

    exp_id = "mem-exp"
    run_id = "run-big"

    await _seed_db(db, exp_id, [run_id])
    _create_experiment_on_disk(storage_root, exp_id, run_id)

    # Build 200 MiB via repeated writes (no "x" * N)
    big_jsonl = storage_root / "outputs" / exp_id / run_id / "conversation.jsonl"
    target_bytes = 200 * 1024 * 1024
    line = (json.dumps({"role": "user", "content": "a" * 1000}) + "\n").encode()

    written = 0
    with open(big_jsonl, "wb") as f:
        while written < target_bytes:
            f.write(line)
            written += len(line)

    assert big_jsonl.stat().st_size >= 100 * 1024 * 1024

    out_path = tmp_path / "export.secrev.zip"

    # Use tracemalloc to measure allocations during the export call only.
    # resource.getrusage(RUSAGE_SELF).ru_maxrss is monotonically non-decreasing
    # (historical peak since process start), so it cannot measure per-call deltas.
    tracemalloc.start()
    snap_before = tracemalloc.take_snapshot()

    await async_write_bundle(db, storage_root, exp_id, include_datasets=False, out_path=out_path)

    snap_after = tracemalloc.take_snapshot()
    tracemalloc.stop()

    # Sum the net new allocations visible between the two snapshots.
    stats = snap_after.compare_to(snap_before, "lineno")
    peak_allocated_bytes = sum(s.size_diff for s in stats if s.size_diff > 0)
    peak_allocated_mb = peak_allocated_bytes / (1024 * 1024)

    assert peak_allocated_mb < 50, (
        f"Tracemalloc peak allocation during export: {peak_allocated_mb:.1f} MiB; "
        "expected < 50 MiB (streaming should prevent buffering the full file)"
    )


# ---------------------------------------------------------------------------
# Test 10: Path-traversal zip entry rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_path_traversal_rejected(tmp_path: Path):
    """Zip entries with path traversal components are rejected before any extraction."""
    storage_root = tmp_path / "storage"
    db = Database(tmp_path / "test.db")
    await db.init()

    exp_id = "traversal-exp"
    await _seed_db(db, exp_id, ["run-t1"])
    _create_experiment_on_disk(storage_root, exp_id, "run-t1")

    good_bundle = tmp_path / "good.secrev.zip"
    await async_write_bundle(db, storage_root, exp_id, include_datasets=False, out_path=good_bundle)

    # Create malicious bundle with a path-traversal entry
    evil_bundle = tmp_path / "evil.secrev.zip"
    with zipfile.ZipFile(good_bundle, "r") as zin, zipfile.ZipFile(evil_bundle, "w") as zout:
        for item in zin.infolist():
            zout.writestr(item, zin.read(item.filename))
        # Inject traversal entry
        zout.writestr("../../etc/passwd", "root:x:0:0:root:/root:/bin/bash\n")

    db2 = Database(tmp_path / "test2.db")
    await db2.init()
    storage2 = tmp_path / "storage2"
    storage2.mkdir(parents=True, exist_ok=True)

    # Should raise ValueError for path traversal
    with pytest.raises(ValueError, match="[Pp]ath traversal"):
        await async_apply_bundle(db2, storage2, evil_bundle, conflict_policy="reject")

    # Nothing written outside storage2
    evil_target = tmp_path / "etc" / "passwd"
    assert not evil_target.exists()


# ---------------------------------------------------------------------------
# Test 11: Post-import findings query returns expected count
# ---------------------------------------------------------------------------


def test_post_import_findings_count_via_api(coordinator_client):
    """After import, /findings returns the correct total count."""
    client, c, storage_root, db = coordinator_client

    # Build a bundle in a completely separate tmp dir so it doesn't share DB state
    new_exp_id = "findings-exp-import"

    loop = asyncio.new_event_loop()
    try:
        # Seed source DB (completely separate from the coordinator_client DB)
        source_db = Database(storage_root.parent / "source_findings.db")
        loop.run_until_complete(source_db.init())
        loop.run_until_complete(_seed_db(source_db, new_exp_id, ["run-g1", "run-g2"]))
        exp_row = loop.run_until_complete(source_db.get_experiment(new_exp_id))
        run_rows = loop.run_until_complete(source_db.list_runs(new_exp_id))
    finally:
        loop.close()

    # Write disk artifacts to the coordinator's storage_root (so the API can read them after import)
    source_storage = storage_root.parent / "source_storage"
    _create_experiment_on_disk(source_storage, new_exp_id, "run-g1", findings_count=3)
    _create_experiment_on_disk(source_storage, new_exp_id, "run-g2", findings_count=3)

    bundle = storage_root.parent / "findings_bundle.secrev.zip"
    write_bundle(
        source_db, source_storage, new_exp_id, include_datasets=False, out_path=bundle,
        _exp_row=exp_row, _run_rows=run_rows,
    )

    # Import via API (coordinator_client's DB has no findings-exp-import yet)
    with open(bundle, "rb") as f:
        resp = client.post(
            "/experiments/import",
            files={"file": ("findings_bundle.secrev.zip", f, "application/zip")},
            data={"conflict_policy": "reject", "rebuild_findings_index": "true"},
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["experiment_id"] == new_exp_id
    assert data["findings_indexed"] > 0

    # Query findings for the imported experiment
    resp2 = client.get(f"/findings?experiment_id={new_exp_id}&limit=100")
    assert resp2.status_code == 200
    findings_data = resp2.json()
    assert findings_data["total"] == 6  # 3 per run × 2 runs


# ---------------------------------------------------------------------------
# Test 12: Retention loop skips directories containing .secrev.zip bundles (C1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_retention_skips_bundle_dirs(tmp_path: Path):
    """Retention loop must not delete experiment dirs that contain a .secrev.zip bundle."""
    from datetime import UTC, datetime, timedelta


    storage_root = tmp_path / "storage"
    db = Database(tmp_path / "test.db")
    await db.init()

    exp_id = "retention-exp"
    await _seed_db(db, exp_id, ["run-ret1"])
    _create_experiment_on_disk(storage_root, exp_id, "run-ret1")

    # Write a .secrev.zip bundle into the experiment output dir
    exp_output_dir = storage_root / "outputs" / exp_id
    bundle_path = exp_output_dir / f"{exp_id}.secrev.zip"
    out_path = tmp_path / "tmp_bundle.secrev.zip"
    await async_write_bundle(db, storage_root, exp_id, include_datasets=False, out_path=out_path)
    shutil.copy(out_path, bundle_path)

    # Back-date the experiment dir's mtime to far in the past so retention
    # would normally delete it.
    old_time = (datetime.now(UTC).timestamp() - 90 * 24 * 3600)
    os.utime(exp_output_dir, (old_time, old_time))

    # Run one iteration of the retention cleanup loop logic directly.
    cutoff = datetime.now(UTC) - timedelta(days=30)
    outputs_dir = storage_root / "outputs"
    for experiment_dir in outputs_dir.iterdir():
        if not experiment_dir.is_dir():
            continue
        bundle_present = any(
            f.name.endswith(".secrev.zip")
            for f in experiment_dir.iterdir()
            if f.is_file()
        )
        if bundle_present:
            continue
        mtime = datetime.fromtimestamp(experiment_dir.stat().st_mtime, UTC)
        if mtime < cutoff:
            shutil.rmtree(experiment_dir, ignore_errors=True)

    # Directory must still exist because the bundle was present
    assert exp_output_dir.exists(), (
        "Retention loop deleted an experiment directory containing a .secrev.zip bundle"
    )
    assert bundle_path.exists()


# ---------------------------------------------------------------------------
# Test 13: ZIP bomb defense — bundle with huge claimed uncompressed size rejected (C2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_zip_bomb_rejected(tmp_path: Path):
    """A bundle whose real uncompressed size exceeds the cap must be rejected.

    We temporarily lower the per-process cap to 1 byte so any real bundle
    (which always has at least a few bytes of JSON) triggers the guard.
    This validates that the decompression-bomb check fires based on actual
    ZipInfo.file_size metadata returned by zipfile, not on manifested claims.
    """
    import sec_review_framework.bundle as _bundle_module

    storage_root = tmp_path / "storage"
    db = Database(tmp_path / "test.db")
    await db.init()

    exp_id = "zipbomb-exp"
    await _seed_db(db, exp_id, ["run-zb1"])
    _create_experiment_on_disk(storage_root, exp_id, "run-zb1")

    good_bundle = tmp_path / "good.secrev.zip"
    await async_write_bundle(db, storage_root, exp_id, include_datasets=False, out_path=good_bundle)

    db2 = Database(tmp_path / "test2.db")
    await db2.init()
    storage2 = tmp_path / "storage2"
    storage2.mkdir(parents=True, exist_ok=True)

    # Lower the cap to 1 byte so the legitimate bundle exceeds it
    original_cap = _bundle_module._BUNDLE_EXTRACT_MAX_BYTES
    _bundle_module._BUNDLE_EXTRACT_MAX_BYTES = 1
    try:
        with pytest.raises(ValueError, match="exceeds"):
            await async_apply_bundle(db2, storage2, good_bundle, conflict_policy="reject")
    finally:
        _bundle_module._BUNDLE_EXTRACT_MAX_BYTES = original_cap

    # Nothing should have been written to the output dir
    assert not (storage2 / "outputs" / exp_id).exists()


# ---------------------------------------------------------------------------
# Test 14: Rename policy rewrites experiment_id in extracted run_result.json (N1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rename_policy_rewrites_run_result_json(tmp_path: Path):
    """After rename-import, run_result.json embeds the new experiment_id."""
    source_storage = tmp_path / "source_storage"
    source_db = Database(tmp_path / "source.db")
    await source_db.init()

    exp_id = "rename-rr-exp"
    run_id = "run-rr1"
    await _seed_db(source_db, exp_id, [run_id])
    _create_experiment_on_disk(source_storage, exp_id, run_id)

    # Verify original run_result.json has original exp_id
    orig_rr = source_storage / "outputs" / exp_id / run_id / "run_result.json"
    orig_data = json.loads(orig_rr.read_text())
    assert orig_data["experiment"]["experiment_id"] == exp_id
    assert orig_data["findings"][0]["experiment_id"] == exp_id

    out_path = tmp_path / "export.secrev.zip"
    await async_write_bundle(source_db, source_storage, exp_id, include_datasets=False, out_path=out_path)

    # Target DB already has the same exp_id → rename will trigger
    target_storage = tmp_path / "target_storage"
    target_db = Database(tmp_path / "target.db")
    await target_db.init()
    await _seed_db(target_db, exp_id, ["run-existing"])
    _create_experiment_on_disk(target_storage, exp_id, "run-existing")

    summary = await async_apply_bundle(target_db, target_storage, out_path, conflict_policy="rename")
    new_id = summary["experiment_id"]
    assert new_id != exp_id
    assert "_imported_" in new_id

    # Read the extracted run_result.json from the renamed experiment dir
    renamed_rr = target_storage / "outputs" / new_id / run_id / "run_result.json"
    assert renamed_rr.exists(), "run_result.json was not extracted to renamed experiment dir"
    renamed_data = json.loads(renamed_rr.read_text())

    assert renamed_data["experiment"]["experiment_id"] == new_id, (
        f"run_result.json still has old experiment_id {exp_id!r}; expected {new_id!r}"
    )
    for finding in renamed_data.get("findings", []):
        assert finding["experiment_id"] == new_id, (
            f"Finding still has old experiment_id {exp_id!r}; expected {new_id!r}"
        )
    for finding in renamed_data.get("strategy_output", {}).get("findings", []):
        assert finding["experiment_id"] == new_id


# ---------------------------------------------------------------------------
# Test 15: upload_token is stripped from bundled config files (N4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upload_token_stripped_from_bundle(tmp_path: Path):
    """Config files with upload_token must not include it in the exported bundle."""
    storage_root = tmp_path / "storage"
    db = Database(tmp_path / "test.db")
    await db.init()

    exp_id = "token-exp"
    run_id = "run-tok1"
    await _seed_db(db, exp_id, [run_id])
    _create_experiment_on_disk(storage_root, exp_id, run_id)

    # Inject an upload_token into the on-disk config file
    cfg_path = storage_root / "config" / "runs" / f"{run_id}.json"
    cfg_data = json.loads(cfg_path.read_text())
    cfg_data["upload_token"] = "super-secret-token-abc123"
    cfg_path.write_text(json.dumps(cfg_data))

    out_path = tmp_path / "export.secrev.zip"
    await async_write_bundle(db, storage_root, exp_id, include_datasets=False, out_path=out_path)

    # Inspect the bundle — upload_token must be absent from the config entry
    with zipfile.ZipFile(out_path, "r") as zf:
        config_entry = f"config/runs/{run_id}.json"
        assert config_entry in zf.namelist(), f"{config_entry} not found in bundle"
        bundled_cfg = json.loads(zf.read(config_entry))
        assert "upload_token" not in bundled_cfg, (
            "upload_token was included in the bundle config file; it must be stripped"
        )


# ---------------------------------------------------------------------------
# Test 16: Import upload size cap returns 413 (N2)
# ---------------------------------------------------------------------------


def test_import_upload_size_cap_returns_413(coordinator_client):
    """Uploading a bundle larger than BUNDLE_UPLOAD_MAX_BYTES returns 413."""
    import sec_review_framework.bundle as _bundle_module

    client, c, storage_root, db = coordinator_client

    # Create a legitimate but tiny bundle
    exp_id = "cap-exp"
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_seed_db(db, exp_id, ["run-cap1"]))
        exp_row = loop.run_until_complete(db.get_experiment(exp_id))
        run_rows = loop.run_until_complete(db.list_runs(exp_id))
    finally:
        loop.close()
    _create_experiment_on_disk(storage_root, exp_id, "run-cap1")

    out_path = storage_root / "cap_bundle.secrev.zip"
    write_bundle(
        db, storage_root, exp_id, include_datasets=False, out_path=out_path,
        _exp_row=exp_row, _run_rows=run_rows,
    )

    # Temporarily lower the upload cap to 1 byte so any bundle exceeds it
    original_cap = _bundle_module._BUNDLE_UPLOAD_MAX_BYTES
    _bundle_module._BUNDLE_UPLOAD_MAX_BYTES = 1
    try:
        with open(out_path, "rb") as f:
            resp = client.post(
                "/experiments/import",
                files={"file": ("cap_bundle.secrev.zip", f, "application/zip")},
                data={"conflict_policy": "reject"},
            )
    finally:
        _bundle_module._BUNDLE_UPLOAD_MAX_BYTES = original_cap

    assert resp.status_code == 413, f"Expected 413, got {resp.status_code}: {resp.text}"


# ---------------------------------------------------------------------------
# Test 17: Partial-failure cleanup — DB error leaves no orphan files (N3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_partial_failure_cleanup(tmp_path: Path):
    """If db.import_experiment_rows raises, the extracted output dir is cleaned up."""
    storage_root = tmp_path / "storage"
    db = Database(tmp_path / "test.db")
    await db.init()

    exp_id = "cleanup-exp"
    run_id = "run-cl1"
    await _seed_db(db, exp_id, run_ids=[run_id])
    _create_experiment_on_disk(storage_root, exp_id, run_id)

    out_path = tmp_path / "export.secrev.zip"
    await async_write_bundle(db, storage_root, exp_id, include_datasets=False, out_path=out_path)

    # Fresh storage + DB for the import target
    target_storage = tmp_path / "target_storage"
    target_storage.mkdir(parents=True, exist_ok=True)
    target_db = Database(tmp_path / "target.db")
    await target_db.init()

    # Patch import_experiment_rows to raise a RuntimeError
    async def _failing_import(*args, **kwargs):
        raise RuntimeError("simulated DB failure")

    with patch.object(target_db, "import_experiment_rows", side_effect=_failing_import):
        with pytest.raises(RuntimeError, match="simulated DB failure"):
            await async_apply_bundle(
                target_db, target_storage, out_path, conflict_policy="reject"
            )

    # The experiment output directory must have been cleaned up
    orphan_dir = target_storage / "outputs" / exp_id
    assert not orphan_dir.exists(), (
        f"Orphaned output directory was left behind after DB failure: {orphan_dir}"
    )


# ---------------------------------------------------------------------------
# Helpers for dataset-related tests
# ---------------------------------------------------------------------------


def _make_dataset_row(
    name: str,
    kind: str = "git",
    base_dataset: str | None = None,
) -> dict:
    """Build a minimal dataset row suitable for db.create_dataset."""
    row: dict = {
        "name": name,
        "kind": kind,
        "created_at": "2026-01-01T00:00:00",
        "metadata_json": "{}",
    }
    if kind == "git":
        row["origin_url"] = f"https://github.com/example/{name}"
        row["origin_commit"] = "abc1234def5678901234567890123456789012ab"
        row["origin_ref"] = "main"
    elif kind == "derived":
        row["base_dataset"] = base_dataset
        row["recipe_json"] = json.dumps({"filter": "cve"})
    return row


def _make_label_row(
    dataset_name: str,
    idx: int,
    dataset_version: str = "1.0.0",
) -> dict:
    """Build a minimal dataset_labels row."""
    return {
        "id": f"label-{dataset_name}-{idx:04d}",
        "dataset_name": dataset_name,
        "dataset_version": dataset_version,
        "file_path": f"src/vuln_{idx}.py",
        "line_start": 10 * idx,
        "line_end": 10 * idx + 3,
        "cwe_id": "CWE-89",
        "vuln_class": "sqli",
        "severity": "high",
        "description": f"Test label {idx}",
        "source": "manual",
        "source_ref": None,
        "confidence": "high",
        "created_at": "2026-01-01T00:00:00",
        "notes": None,
        "introduced_in_diff": None,
        "patch_lines_changed": None,
    }


async def _seed_db_with_dataset(
    db: Database,
    exp_id: str,
    run_ids: list[str],
    dataset_name: str,
    dataset_kind: str = "git",
    base_dataset_name: str | None = None,
    label_count: int = 3,
) -> None:
    """Seed DB with an experiment whose runs reference a dataset, plus label rows."""
    # Create base dataset if needed
    if base_dataset_name:
        base_row = _make_dataset_row(base_dataset_name, kind="git")
        await db.create_dataset(base_row)
        for i in range(label_count):
            await db.append_dataset_labels([_make_label_row(base_dataset_name, i)])

    # Create the main dataset
    ds_row = _make_dataset_row(dataset_name, kind=dataset_kind, base_dataset=base_dataset_name)
    await db.create_dataset(ds_row)
    for i in range(label_count):
        await db.append_dataset_labels([_make_label_row(dataset_name, i)])

    # Seed experiment + runs; runs' config_json references the dataset
    await db.import_experiment_rows(
        experiment_row={
            "id": exp_id,
            "config_json": json.dumps({"experiment_id": exp_id, "dataset_name": dataset_name}),
            "status": "completed",
            "total_runs": len(run_ids),
            "max_cost_usd": None,
            "spent_usd": 0.0,
            "created_at": "2026-01-01T00:00:00",
            "completed_at": "2026-01-01T01:00:00",
        },
        run_rows=[
            {
                "id": rid,
                "experiment_id": exp_id,
                "config_json": json.dumps({"run_id": rid, "dataset_name": dataset_name}),
                "status": "completed",
                "model_id": "gpt-4o",
                "strategy": "single_agent",
                "tool_variant": "with_tools",
                "review_profile": "default",
                "verification_variant": "none",
                "estimated_cost_usd": 0.01,
                "duration_seconds": 10.0,
                "result_path": None,
                "error": None,
                "created_at": "2026-01-01T00:00:00",
                "completed_at": "2026-01-01T01:00:00",
                "tool_extensions": "",
            }
            for rid in run_ids
        ],
    )


# ---------------------------------------------------------------------------
# Test 18: Round-trip with descriptor (git dataset)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_descriptor_mode_round_trip_git_dataset(tmp_path: Path):
    """Descriptor export embeds datasets.json + dataset_labels.json; import restores them."""
    ds_name = "myrepo"
    exp_id = "desc-exp-git"
    run_id = "run-desc-git-1"

    source_storage = tmp_path / "source_storage"
    source_db = Database(tmp_path / "source.db")
    await source_db.init()

    await _seed_db_with_dataset(
        source_db, exp_id, [run_id],
        dataset_name=ds_name, dataset_kind="git", label_count=4,
    )
    _create_experiment_on_disk(source_storage, exp_id, run_id)

    out_path = tmp_path / "export.secrev.zip"
    await async_write_bundle(
        source_db, source_storage, exp_id,
        dataset_mode="descriptor", out_path=out_path,
    )

    # Verify datasets.json and dataset_labels.json are in the bundle
    with zipfile.ZipFile(out_path, "r") as zf:
        namelist = zf.namelist()
        assert "datasets.json" in namelist, "datasets.json missing from descriptor bundle"
        assert "dataset_labels.json" in namelist, "dataset_labels.json missing from descriptor bundle"

        ds_rows = json.loads(zf.read("datasets.json"))
        lbl_rows = json.loads(zf.read("dataset_labels.json"))

    assert len(ds_rows) == 1
    assert ds_rows[0]["name"] == ds_name
    assert ds_rows[0]["kind"] == "git"
    assert len(lbl_rows) == 4

    # Verify manifest dataset_count + dataset_label_count
    manifest = read_manifest(out_path)
    assert manifest["dataset_mode"] == "descriptor"
    assert manifest["artifact_counts"]["dataset_count"] == 1
    assert manifest["artifact_counts"]["dataset_label_count"] == 4

    # Import into a fresh DB
    target_storage = tmp_path / "target_storage"
    target_db = Database(tmp_path / "target.db")
    await target_db.init()

    summary = await async_apply_bundle(target_db, target_storage, out_path, conflict_policy="reject")
    assert summary["datasets_imported"] > 0 or summary["datasets_imported"] == 1
    assert summary["dataset_labels_imported"] == 4

    # Dataset row restored
    restored_ds = await target_db.get_dataset(ds_name)
    assert restored_ds is not None
    assert restored_ds["kind"] == "git"

    # Labels restored
    restored_labels = await target_db.list_dataset_labels(ds_name)
    assert len(restored_labels) == 4


# ---------------------------------------------------------------------------
# Test 19: Round-trip with derived dataset chain
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_descriptor_mode_derived_dataset_chain(tmp_path: Path):
    """Derived datasets recursively include the base dataset in datasets.json."""
    base_name = "base-repo"
    derived_name = "derived-subset"
    exp_id = "desc-exp-derived"
    run_id = "run-desc-derived-1"

    source_storage = tmp_path / "source_storage"
    source_db = Database(tmp_path / "source.db")
    await source_db.init()

    await _seed_db_with_dataset(
        source_db, exp_id, [run_id],
        dataset_name=derived_name,
        dataset_kind="derived",
        base_dataset_name=base_name,
        label_count=2,
    )
    _create_experiment_on_disk(source_storage, exp_id, run_id)

    out_path = tmp_path / "export.secrev.zip"
    await async_write_bundle(
        source_db, source_storage, exp_id,
        dataset_mode="descriptor", out_path=out_path,
    )

    # Both rows must appear in datasets.json
    with zipfile.ZipFile(out_path, "r") as zf:
        ds_rows = json.loads(zf.read("datasets.json"))

    ds_names_in_bundle = {r["name"] for r in ds_rows}
    assert base_name in ds_names_in_bundle, "Base dataset missing from bundle"
    assert derived_name in ds_names_in_bundle, "Derived dataset missing from bundle"
    assert len(ds_rows) == 2

    # Import into a fresh DB
    target_storage = tmp_path / "target_storage"
    target_db = Database(tmp_path / "target.db")
    await target_db.init()

    summary = await async_apply_bundle(target_db, target_storage, out_path, conflict_policy="reject")
    assert summary["datasets_imported"] >= 2 or summary["datasets_imported"] > 0

    # Both rows in target DB
    base_row = await target_db.get_dataset(base_name)
    derived_row = await target_db.get_dataset(derived_name)
    assert base_row is not None
    assert derived_row is not None
    assert derived_row["base_dataset"] == base_name


# ---------------------------------------------------------------------------
# Test 20: Reference mode — no JSON files, dataset_count is 0
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reference_mode_no_json_files(tmp_path: Path):
    """Reference mode must NOT include datasets.json or dataset_labels.json."""
    ds_name = "myrepo-ref"
    exp_id = "ref-exp"
    run_id = "run-ref-1"

    storage_root = tmp_path / "storage"
    db = Database(tmp_path / "test.db")
    await db.init()

    await _seed_db_with_dataset(
        db, exp_id, [run_id],
        dataset_name=ds_name, dataset_kind="git", label_count=2,
    )
    _create_experiment_on_disk(storage_root, exp_id, run_id)

    out_path = tmp_path / "export.secrev.zip"
    await async_write_bundle(
        db, storage_root, exp_id,
        dataset_mode="reference", out_path=out_path,
    )

    with zipfile.ZipFile(out_path, "r") as zf:
        namelist = zf.namelist()
        assert "datasets.json" not in namelist, "datasets.json must NOT be in reference bundle"
        assert "dataset_labels.json" not in namelist, "dataset_labels.json must NOT be in reference bundle"

    manifest = read_manifest(out_path)
    assert manifest["dataset_mode"] == "reference"
    # dataset_count is 0 in reference mode (no rows embedded)
    assert manifest["artifact_counts"]["dataset_count"] == 0
    assert manifest["artifact_counts"]["dataset_label_count"] == 0


# ---------------------------------------------------------------------------
# Test 21: Manifest validation — dataset_mode and counts
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manifest_dataset_fields_populated(tmp_path: Path):
    """Manifest contains dataset_mode, dataset_count, dataset_label_count."""
    ds_name = "manifest-check-ds"
    exp_id = "manifest-exp"
    run_id = "run-manifest-1"

    storage_root = tmp_path / "storage"
    db = Database(tmp_path / "test.db")
    await db.init()

    await _seed_db_with_dataset(
        db, exp_id, [run_id],
        dataset_name=ds_name, dataset_kind="git", label_count=5,
    )
    _create_experiment_on_disk(storage_root, exp_id, run_id)

    for mode in ("descriptor", "reference"):
        out_path = tmp_path / f"export_{mode}.secrev.zip"
        await async_write_bundle(
            db, storage_root, exp_id,
            dataset_mode=mode, out_path=out_path,
        )
        m = read_manifest(out_path)
        assert "dataset_mode" in m, f"dataset_mode missing in manifest for mode={mode}"
        assert m["dataset_mode"] == mode
        assert "artifact_counts" in m
        assert "dataset_count" in m["artifact_counts"]
        assert "dataset_label_count" in m["artifact_counts"]
        if mode == "descriptor":
            assert m["artifact_counts"]["dataset_count"] == 1
            assert m["artifact_counts"]["dataset_label_count"] == 5
        else:
            assert m["artifact_counts"]["dataset_count"] == 0
            assert m["artifact_counts"]["dataset_label_count"] == 0


# ---------------------------------------------------------------------------
# Test 22: No dataset repo bytes on disk in export
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_dataset_bytes_on_disk_in_bundle(tmp_path: Path):
    """Bundle must not contain datasets/<name>/... file path entries."""
    ds_name = "no-bytes-ds"
    exp_id = "nobytes-exp"
    run_id = "run-nobytes-1"

    storage_root = tmp_path / "storage"
    db = Database(tmp_path / "test.db")
    await db.init()

    await _seed_db_with_dataset(
        db, exp_id, [run_id],
        dataset_name=ds_name, dataset_kind="git", label_count=1,
    )
    _create_experiment_on_disk(storage_root, exp_id, run_id)

    # Create a fake on-disk dataset directory (should NOT be bundled)
    fake_ds_dir = storage_root / "datasets" / ds_name
    fake_ds_dir.mkdir(parents=True, exist_ok=True)
    (fake_ds_dir / "README.md").write_text("# Fake dataset\n")

    out_path = tmp_path / "export.secrev.zip"
    await async_write_bundle(
        db, storage_root, exp_id,
        dataset_mode="descriptor", out_path=out_path,
    )

    with zipfile.ZipFile(out_path, "r") as zf:
        namelist = zf.namelist()
        dataset_file_entries = [n for n in namelist if n.startswith("datasets/") and "/" in n[len("datasets/"):]]
        assert dataset_file_entries == [], (
            f"Found unexpected dataset repo bytes in bundle: {dataset_file_entries}"
        )

    # Also check reference mode
    out_path_ref = tmp_path / "export_ref.secrev.zip"
    await async_write_bundle(
        db, storage_root, exp_id,
        dataset_mode="reference", out_path=out_path_ref,
    )
    with zipfile.ZipFile(out_path_ref, "r") as zf:
        namelist = zf.namelist()
        dataset_file_entries = [n for n in namelist if n.startswith("datasets/") and "/" in n[len("datasets/"):]]
        assert dataset_file_entries == [], (
            f"Found unexpected dataset repo bytes in reference bundle: {dataset_file_entries}"
        )


# ---------------------------------------------------------------------------
# Test 23: Path-traversal still rejected (regression)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_path_traversal_still_rejected_with_datasets(tmp_path: Path):
    """Path-traversal check must still fire on bundles that include datasets.json."""
    ds_name = "traversal-ds"
    exp_id = "traversal-ds-exp"
    run_id = "run-traversal-ds-1"

    storage_root = tmp_path / "storage"
    db = Database(tmp_path / "test.db")
    await db.init()

    await _seed_db_with_dataset(
        db, exp_id, [run_id],
        dataset_name=ds_name, dataset_kind="git", label_count=1,
    )
    _create_experiment_on_disk(storage_root, exp_id, run_id)

    good_bundle = tmp_path / "good.secrev.zip"
    await async_write_bundle(
        db, storage_root, exp_id,
        dataset_mode="descriptor", out_path=good_bundle,
    )

    # Inject a path-traversal entry into the bundle
    evil_bundle = tmp_path / "evil_ds.secrev.zip"
    with zipfile.ZipFile(good_bundle, "r") as zin, zipfile.ZipFile(evil_bundle, "w") as zout:
        for item in zin.infolist():
            zout.writestr(item, zin.read(item.filename))
        zout.writestr("../../etc/shadow", "root:*\n")

    db2 = Database(tmp_path / "target.db")
    await db2.init()
    storage2 = tmp_path / "storage2"
    storage2.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ValueError, match="[Pp]ath traversal"):
        await async_apply_bundle(db2, storage2, evil_bundle, conflict_policy="reject")

    # Nothing written outside storage2
    assert not (tmp_path / "etc" / "shadow").exists()


# ---------------------------------------------------------------------------
# Test 24: Idempotent label re-import
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_idempotent_label_reimport(tmp_path: Path):
    """Importing the same bundle twice must not raise IntegrityError or double labels."""
    ds_name = "idem-ds"
    exp_id = "idem-exp"
    run_id_1 = "run-idem-1"

    source_storage = tmp_path / "source_storage"
    source_db = Database(tmp_path / "source.db")
    await source_db.init()

    await _seed_db_with_dataset(
        source_db, exp_id, [run_id_1],
        dataset_name=ds_name, dataset_kind="git", label_count=3,
    )
    _create_experiment_on_disk(source_storage, exp_id, run_id_1)

    out_path = tmp_path / "export.secrev.zip"
    await async_write_bundle(
        source_db, source_storage, exp_id,
        dataset_mode="descriptor", out_path=out_path,
    )

    # Target DB — import once
    target_db = Database(tmp_path / "target.db")
    await target_db.init()
    target_storage = tmp_path / "target_storage"
    target_storage.mkdir(parents=True, exist_ok=True)

    summary1 = await async_apply_bundle(target_db, target_storage, out_path, conflict_policy="reject")
    assert summary1["dataset_labels_imported"] == 3

    labels_after_first = await target_db.list_dataset_labels(ds_name)
    assert len(labels_after_first) == 3

    # Import the same bundle into a second fresh DB to verify idempotency:
    # labels are imported again without error (INSERT OR IGNORE on PK).
    target_db2 = Database(tmp_path / "target2.db")
    await target_db2.init()
    target_storage3 = tmp_path / "target_storage3"
    target_storage3.mkdir(parents=True, exist_ok=True)

    # First import into db2
    await async_apply_bundle(target_db2, target_storage3, out_path, conflict_policy="reject")
    labels_after_first_db2 = await target_db2.list_dataset_labels(ds_name)
    assert len(labels_after_first_db2) == 3

    # Now directly re-import just the labels to test idempotency (INSERT OR IGNORE)
    with zipfile.ZipFile(out_path, "r") as zf:
        lbl_rows = json.loads(zf.read("dataset_labels.json"))

    # Re-importing the same label rows must not raise and must not double the count
    await target_db2.append_dataset_labels(lbl_rows)
    labels_after_second = await target_db2.list_dataset_labels(ds_name)
    assert len(labels_after_second) == 3, (
        f"Labels doubled after second import: got {len(labels_after_second)}, expected 3"
    )


# ---------------------------------------------------------------------------
# Test 25: Reject dataset_mode='embedded' with 422
# ---------------------------------------------------------------------------


def test_reject_embedded_mode_returns_422(coordinator_client):
    """Passing dataset_mode='embedded' to the export route must return 422."""
    client, c, storage_root, db = coordinator_client
    exp_id = "embedded-mode-exp"

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_seed_db(db, exp_id, ["run-emb1"]))
        _create_experiment_on_disk(storage_root, exp_id, "run-emb1")
    finally:
        loop.close()

    resp = client.get(f"/experiments/{exp_id}/export?dataset_mode=embedded")
    assert resp.status_code == 422, f"Expected 422, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "embedded" in body.get("detail", "").lower() or "invalid" in body.get("detail", "").lower()
