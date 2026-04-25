"""Unit tests for the datasets and dataset_labels Database methods."""

from __future__ import annotations

import sqlite3
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


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GIT_ROW = {
    "name": "linux-cve-2024-0001",
    "kind": "git",
    "origin_url": "https://github.com/torvalds/linux",
    "origin_commit": "abc123def456",
    "origin_ref": "refs/tags/v6.8",
    "cve_id": "CVE-2024-0001",
    "created_at": "2026-01-01T00:00:00",
}

_DERIVED_ROW = {
    "name": "linux-cve-2024-0001-patch",
    "kind": "derived",
    "base_dataset": "linux-cve-2024-0001",
    "recipe_json": '{"patch": "apply_cve_patch"}',
    "created_at": "2026-01-02T00:00:00",
}

_LABEL_BASE = {
    "id": "label-001",
    "dataset_name": "linux-cve-2024-0001",
    "dataset_version": "v1",
    "file_path": "kernel/sched/core.c",
    "line_start": 100,
    "line_end": 120,
    "cwe_id": "CWE-416",
    "vuln_class": "use_after_free",
    "severity": "high",
    "description": "Use after free in scheduler",
    "source": "cve_patch",
    "confidence": "high",
    "created_at": "2026-01-01T00:00:00",
}


async def _insert_git_dataset(db: Database, name: str = "linux-cve-2024-0001") -> dict:
    row = {**_GIT_ROW, "name": name}
    await db.create_dataset(row)
    return row


async def _insert_derived_dataset(db: Database) -> dict:
    await _insert_git_dataset(db)
    await db.create_dataset(_DERIVED_ROW)
    return _DERIVED_ROW


# ---------------------------------------------------------------------------
# 1. create_dataset happy path — kind='git'
# ---------------------------------------------------------------------------


async def test_create_dataset_git_round_trip(db: Database):
    """create_dataset for kind='git' persists and get_dataset returns correct dict."""
    await db.create_dataset(_GIT_ROW)
    result = await db.get_dataset("linux-cve-2024-0001")
    assert result is not None
    assert result["name"] == "linux-cve-2024-0001"
    assert result["kind"] == "git"
    assert result["origin_url"] == _GIT_ROW["origin_url"]
    assert result["origin_commit"] == _GIT_ROW["origin_commit"]
    assert result["origin_ref"] == _GIT_ROW["origin_ref"]
    assert result["cve_id"] == "CVE-2024-0001"
    assert result["metadata_json"] == "{}"
    assert result["materialized_at"] is None


# ---------------------------------------------------------------------------
# 2. create_dataset happy path — kind='derived'
# ---------------------------------------------------------------------------


async def test_create_dataset_derived_round_trip(db: Database):
    """create_dataset for kind='derived' persists and get_dataset returns correct dict."""
    await _insert_derived_dataset(db)
    result = await db.get_dataset("linux-cve-2024-0001-patch")
    assert result is not None
    assert result["kind"] == "derived"
    assert result["base_dataset"] == "linux-cve-2024-0001"
    assert result["recipe_json"] == '{"patch": "apply_cve_patch"}'
    assert result["origin_url"] is None
    assert result["origin_commit"] is None


# ---------------------------------------------------------------------------
# 3. create_dataset rejects bad kind / missing origin / missing recipe
# ---------------------------------------------------------------------------


async def test_create_dataset_rejects_bad_kind(db: Database):
    """Inserting an invalid kind raises an IntegrityError (CHECK constraint)."""
    row = {**_GIT_ROW, "name": "bad-kind", "kind": "s3"}
    with pytest.raises(Exception):
        await db.create_dataset(row)


async def test_create_dataset_git_missing_origin_url(db: Database):
    """Git dataset without origin_url violates CHECK constraint."""
    row = {
        "name": "missing-url",
        "kind": "git",
        "origin_commit": "abc123",
        "created_at": "2026-01-01T00:00:00",
    }
    with pytest.raises(Exception):
        await db.create_dataset(row)


async def test_create_dataset_git_missing_origin_commit(db: Database):
    """Git dataset without origin_commit violates CHECK constraint."""
    row = {
        "name": "missing-commit",
        "kind": "git",
        "origin_url": "https://example.com/repo",
        "created_at": "2026-01-01T00:00:00",
    }
    with pytest.raises(Exception):
        await db.create_dataset(row)


async def test_create_dataset_derived_missing_base_dataset(db: Database):
    """Derived dataset without base_dataset violates CHECK constraint."""
    row = {
        "name": "missing-base",
        "kind": "derived",
        "recipe_json": '{"op": "filter"}',
        "created_at": "2026-01-01T00:00:00",
    }
    with pytest.raises(Exception):
        await db.create_dataset(row)


async def test_create_dataset_derived_missing_recipe_json(db: Database):
    """Derived dataset without recipe_json violates CHECK constraint."""
    await _insert_git_dataset(db)
    row = {
        "name": "missing-recipe",
        "kind": "derived",
        "base_dataset": "linux-cve-2024-0001",
        "created_at": "2026-01-01T00:00:00",
    }
    with pytest.raises(Exception):
        await db.create_dataset(row)


# ---------------------------------------------------------------------------
# 4. create_dataset rejects duplicate name
# ---------------------------------------------------------------------------


async def test_create_dataset_rejects_duplicate_name(db: Database):
    """Inserting a dataset with an existing name raises IntegrityError."""
    await db.create_dataset(_GIT_ROW)
    with pytest.raises(Exception):
        await db.create_dataset(_GIT_ROW)


# ---------------------------------------------------------------------------
# 5. list_datasets returns newest first by created_at
# ---------------------------------------------------------------------------


async def test_list_datasets_ordered_newest_first(db: Database):
    """list_datasets orders rows by created_at DESC."""
    rows = [
        {**_GIT_ROW, "name": "ds-oldest", "created_at": "2026-01-01T00:00:00"},
        {**_GIT_ROW, "name": "ds-middle", "created_at": "2026-01-02T00:00:00"},
        {**_GIT_ROW, "name": "ds-newest", "created_at": "2026-01-03T00:00:00"},
    ]
    for r in rows:
        await db.create_dataset(r)

    results = await db.list_datasets()
    assert len(results) == 3
    assert results[0]["name"] == "ds-newest"
    assert results[1]["name"] == "ds-middle"
    assert results[2]["name"] == "ds-oldest"


# ---------------------------------------------------------------------------
# 6. update_dataset_materialized_at — updates and is a no-op if missing
# ---------------------------------------------------------------------------


async def test_update_dataset_materialized_at(db: Database):
    """update_dataset_materialized_at sets the field correctly."""
    await _insert_git_dataset(db)
    ts = "2026-06-01T12:00:00"
    await db.update_dataset_materialized_at("linux-cve-2024-0001", ts)
    result = await db.get_dataset("linux-cve-2024-0001")
    assert result["materialized_at"] == ts


async def test_update_dataset_materialized_at_missing_name_is_noop(db: Database):
    """update_dataset_materialized_at on a non-existent name does not raise."""
    # Should complete without raising any exception.
    await db.update_dataset_materialized_at("nonexistent", "2026-06-01T00:00:00")


# ---------------------------------------------------------------------------
# 7. import_datasets with reject policy — no partial inserts on collision
# ---------------------------------------------------------------------------


async def test_import_datasets_reject_no_partial_insert(db: Database):
    """reject policy: collision raises and leaves the DB unchanged."""
    # Pre-insert one dataset so there's a collision.
    await db.create_dataset({**_GIT_ROW, "name": "existing-ds"})

    new_rows = [
        {**_GIT_ROW, "name": "brand-new", "created_at": "2026-02-01T00:00:00"},
        {**_GIT_ROW, "name": "existing-ds", "created_at": "2026-02-02T00:00:00"},
    ]

    with pytest.raises(Exception):
        await db.import_datasets(new_rows, conflict_policy="reject")

    # brand-new must not have been inserted (atomicity).
    assert await db.get_dataset("brand-new") is None


# ---------------------------------------------------------------------------
# 8. import_datasets with rename — rewrites collisions and base_dataset refs
# ---------------------------------------------------------------------------


async def test_import_datasets_rename_rewrites_collisions(db: Database):
    """rename policy: collision gets suffix; non-collision passes through."""
    await db.create_dataset({**_GIT_ROW, "name": "existing-ds"})

    new_rows = [
        {**_GIT_ROW, "name": "existing-ds", "created_at": "2026-02-01T00:00:00"},
        {**_GIT_ROW, "name": "brand-new", "created_at": "2026-02-02T00:00:00"},
    ]
    final_names = await db.import_datasets(new_rows, conflict_policy="rename")

    assert len(final_names) == 2
    # brand-new should keep its name.
    assert final_names[1] == "brand-new"
    # existing-ds should have been renamed.
    assert final_names[0] != "existing-ds"
    assert "existing-ds_imported_" in final_names[0]

    # Both rows must be in the DB.
    assert await db.get_dataset(final_names[0]) is not None
    assert await db.get_dataset("brand-new") is not None
    # The original "existing-ds" is unmodified.
    assert await db.get_dataset("existing-ds") is not None


async def test_import_datasets_rename_rewrites_base_dataset_reference(db: Database):
    """rename policy rewrites base_dataset pointer when a git parent was renamed."""
    await db.create_dataset({**_GIT_ROW, "name": "already-exists"})

    git_row = {**_GIT_ROW, "name": "already-exists", "created_at": "2026-03-01T00:00:00"}
    derived_row = {
        "name": "child-ds",
        "kind": "derived",
        "base_dataset": "already-exists",
        "recipe_json": '{"op": "slice"}',
        "created_at": "2026-03-02T00:00:00",
    }
    final_names = await db.import_datasets(
        [git_row, derived_row], conflict_policy="rename"
    )

    # The git row was renamed.
    renamed_git = final_names[0]
    assert renamed_git != "already-exists"

    # The derived row's base_dataset should point to the renamed git row.
    child = await db.get_dataset("child-ds")
    assert child is not None
    assert child["base_dataset"] == renamed_git


# ---------------------------------------------------------------------------
# 9. import_datasets with merge — skips collisions, inserts new rows
# ---------------------------------------------------------------------------


async def test_import_datasets_merge_skips_existing_inserts_new(db: Database):
    """merge policy skips existing rows and inserts new ones."""
    existing = {**_GIT_ROW, "name": "already-there"}
    await db.create_dataset(existing)

    new_rows = [
        {**_GIT_ROW, "name": "already-there", "created_at": "2026-04-01T00:00:00"},
        {**_GIT_ROW, "name": "truly-new", "created_at": "2026-04-02T00:00:00"},
    ]
    final_names = await db.import_datasets(new_rows, conflict_policy="merge")

    assert final_names == ["already-there", "truly-new"]
    # already-there should keep its original created_at.
    preserved = await db.get_dataset("already-there")
    assert preserved["created_at"] == _GIT_ROW["created_at"]  # original timestamp
    # truly-new must be inserted.
    assert await db.get_dataset("truly-new") is not None


# ---------------------------------------------------------------------------
# 10. append_dataset_labels — round-trip and idempotent on duplicate id
# ---------------------------------------------------------------------------


async def test_append_dataset_labels_round_trip(db: Database):
    """append_dataset_labels inserts rows retrievable via list_dataset_labels."""
    await _insert_git_dataset(db)
    await db.append_dataset_labels([_LABEL_BASE])
    rows = await db.list_dataset_labels("linux-cve-2024-0001")
    assert len(rows) == 1
    assert rows[0]["id"] == "label-001"
    assert rows[0]["cwe_id"] == "CWE-416"
    assert rows[0]["severity"] == "high"


async def test_append_dataset_labels_idempotent_on_duplicate_id(db: Database):
    """append_dataset_labels called twice with same id does not raise."""
    await _insert_git_dataset(db)
    await db.append_dataset_labels([_LABEL_BASE])
    # Second call with same row must not raise IntegrityError.
    await db.append_dataset_labels([_LABEL_BASE])
    rows = await db.list_dataset_labels("linux-cve-2024-0001")
    assert len(rows) == 1  # still only one row


# ---------------------------------------------------------------------------
# 11. list_dataset_labels filters
# ---------------------------------------------------------------------------


async def _setup_labels(db: Database) -> None:
    """Insert a git dataset and two labels with different attributes."""
    await _insert_git_dataset(db)
    label_a = {
        **_LABEL_BASE,
        "id": "label-a",
        "dataset_version": "v1",
        "cwe_id": "CWE-416",
        "severity": "high",
        "source": "cve_patch",
    }
    label_b = {
        **_LABEL_BASE,
        "id": "label-b",
        "dataset_version": "v2",
        "cwe_id": "CWE-79",
        "severity": "medium",
        "source": "injected",
    }
    await db.append_dataset_labels([label_a, label_b])


async def test_list_dataset_labels_no_filter(db: Database):
    """list_dataset_labels without filters returns all labels for the dataset."""
    await _setup_labels(db)
    rows = await db.list_dataset_labels("linux-cve-2024-0001")
    assert len(rows) == 2


async def test_list_dataset_labels_filter_version(db: Database):
    """Filtering by version returns only matching labels."""
    await _setup_labels(db)
    rows = await db.list_dataset_labels("linux-cve-2024-0001", version="v1")
    assert len(rows) == 1
    assert rows[0]["id"] == "label-a"


async def test_list_dataset_labels_filter_cwe(db: Database):
    """Filtering by cwe returns only matching labels."""
    await _setup_labels(db)
    rows = await db.list_dataset_labels("linux-cve-2024-0001", cwe="CWE-79")
    assert len(rows) == 1
    assert rows[0]["id"] == "label-b"


async def test_list_dataset_labels_filter_severity(db: Database):
    """Filtering by severity returns only matching labels."""
    await _setup_labels(db)
    rows = await db.list_dataset_labels("linux-cve-2024-0001", severity="medium")
    assert len(rows) == 1
    assert rows[0]["id"] == "label-b"


async def test_list_dataset_labels_filter_source(db: Database):
    """Filtering by source returns only matching labels."""
    await _setup_labels(db)
    rows = await db.list_dataset_labels("linux-cve-2024-0001", source="injected")
    assert len(rows) == 1
    assert rows[0]["id"] == "label-b"


async def test_list_dataset_labels_combined_filters(db: Database):
    """All filters combined narrow down to the correct row."""
    await _setup_labels(db)
    rows = await db.list_dataset_labels(
        "linux-cve-2024-0001",
        version="v1",
        cwe="CWE-416",
        severity="high",
        source="cve_patch",
    )
    assert len(rows) == 1
    assert rows[0]["id"] == "label-a"


async def test_list_dataset_labels_filter_no_match(db: Database):
    """Filters that match nothing return an empty list."""
    await _setup_labels(db)
    rows = await db.list_dataset_labels("linux-cve-2024-0001", cwe="CWE-999")
    assert rows == []


async def test_list_dataset_labels_wrong_dataset_name(db: Database):
    """Querying a different dataset name returns no labels."""
    await _setup_labels(db)
    rows = await db.list_dataset_labels("nonexistent-dataset")
    assert rows == []


# ---------------------------------------------------------------------------
# 12. CASCADE delete: deleting a dataset removes its labels
# ---------------------------------------------------------------------------


async def test_cascade_delete_removes_labels(db: Database):
    """Deleting a dataset removes its labels via ON DELETE CASCADE."""
    await _insert_git_dataset(db)
    await db.append_dataset_labels([_LABEL_BASE])
    assert len(await db.list_dataset_labels("linux-cve-2024-0001")) == 1

    # Delete the dataset directly via a raw connection (no db.delete_dataset method yet).
    import aiosqlite

    async with aiosqlite.connect(db.db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute(
            "DELETE FROM datasets WHERE name = ?", ("linux-cve-2024-0001",)
        )
        await conn.commit()

    # Labels must be gone.
    rows = await db.list_dataset_labels("linux-cve-2024-0001")
    assert rows == []
