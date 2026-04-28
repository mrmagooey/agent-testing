"""Unit tests for LabelStore stub and Database negative-label API."""

from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from sec_review_framework.db import Database
from sec_review_framework.ground_truth.models import LabelStore


@pytest.fixture
def datasets_root(tmp_path: Path) -> Path:
    """Create a temporary datasets root directory."""
    return tmp_path / "datasets"


@pytest.fixture
def label_store(datasets_root: Path) -> LabelStore:
    return LabelStore(datasets_root=datasets_root)


def test_label_store_construction_succeeds(datasets_root: Path):
    """LabelStore can be constructed without error (backward-compat)."""
    store = LabelStore(datasets_root=datasets_root)
    assert store is not None


def test_label_store_load_raises_not_implemented(label_store: LabelStore):
    """LabelStore.load raises NotImplementedError — use Database.list_dataset_labels."""
    with pytest.raises(NotImplementedError, match="list_dataset_labels"):
        label_store.load("any-dataset")


def test_label_store_load_with_version_raises_not_implemented(label_store: LabelStore):
    """LabelStore.load with version raises NotImplementedError."""
    with pytest.raises(NotImplementedError):
        label_store.load("any-dataset", version="v1")


def test_label_store_append_raises_not_implemented(label_store: LabelStore):
    """LabelStore.append raises NotImplementedError — use Database.append_dataset_labels."""
    with pytest.raises(NotImplementedError, match="append_dataset_labels"):
        label_store.append("any-dataset", [])


# ---------------------------------------------------------------------------
# Database.append_dataset_negative_labels / list_dataset_negative_labels
# ---------------------------------------------------------------------------

# Reuse the same git-dataset row shape from test_database_datasets conventions.
_GIT_DATASET = {
    "name": "linux-cve-2024-0001",
    "kind": "git",
    "origin_url": "https://github.com/torvalds/linux",
    "origin_commit": "abc123def456",
    "origin_ref": "refs/tags/v6.8",
    "cve_id": "CVE-2024-0001",
    "created_at": "2026-01-01T00:00:00",
}

_NEG_LABEL = {
    "id": "neg-001",
    "dataset_name": "linux-cve-2024-0001",
    "dataset_version": "v1",
    "file_path": "kernel/sched/fair.c",
    "cwe_id": "CWE-416",
    "vuln_class": "use_after_free",
    "source": "benchmark",
    "created_at": "2026-01-01T00:00:00",
}


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> Database:
    """Fresh in-process SQLite Database for each test."""
    database = Database(tmp_path / "test.db")
    await database.init()
    return database


async def _insert_dataset(database: Database) -> None:
    await database.create_dataset(_GIT_DATASET)


# ---------------------------------------------------------------------------
# 1. Single-label round-trip
# ---------------------------------------------------------------------------


async def test_append_negative_labels_single_round_trip(db: Database):
    """append + list round-trip for a single negative label."""
    await _insert_dataset(db)
    await db.append_dataset_negative_labels([_NEG_LABEL])
    rows = await db.list_dataset_negative_labels("linux-cve-2024-0001")
    assert len(rows) == 1
    row = rows[0]
    assert row["id"] == "neg-001"
    assert row["file_path"] == "kernel/sched/fair.c"
    assert row["cwe_id"] == "CWE-416"
    assert row["vuln_class"] == "use_after_free"
    assert row["source"] == "benchmark"
    assert row["created_at"] == "2026-01-01T00:00:00"


# ---------------------------------------------------------------------------
# 2. Batch insert and insertion-order preservation
# ---------------------------------------------------------------------------


async def test_append_negative_labels_batch(db: Database):
    """Batch of negatives lists back; insertion order preserved by rowid."""
    await _insert_dataset(db)
    batch = [
        {**_NEG_LABEL, "id": f"neg-{i:03d}", "file_path": f"file{i}.c"}
        for i in range(5)
    ]
    await db.append_dataset_negative_labels(batch)
    rows = await db.list_dataset_negative_labels("linux-cve-2024-0001")
    assert len(rows) == 5
    # SQLite preserves insertion order for non-sorted queries within a single batch.
    returned_ids = [r["id"] for r in rows]
    assert returned_ids == [f"neg-{i:03d}" for i in range(5)]


# ---------------------------------------------------------------------------
# 3. Filter by dataset_version
# ---------------------------------------------------------------------------


async def test_list_negative_labels_filter_version(db: Database):
    """Passing dataset_version filters to only that version."""
    await _insert_dataset(db)
    label_v1 = {**_NEG_LABEL, "id": "neg-v1", "dataset_version": "v1"}
    label_v2 = {**_NEG_LABEL, "id": "neg-v2", "dataset_version": "v2"}
    await db.append_dataset_negative_labels([label_v1, label_v2])

    rows_v1 = await db.list_dataset_negative_labels("linux-cve-2024-0001", dataset_version="v1")
    assert len(rows_v1) == 1
    assert rows_v1[0]["id"] == "neg-v1"

    rows_v2 = await db.list_dataset_negative_labels("linux-cve-2024-0001", dataset_version="v2")
    assert len(rows_v2) == 1
    assert rows_v2[0]["id"] == "neg-v2"

    rows_all = await db.list_dataset_negative_labels("linux-cve-2024-0001")
    assert len(rows_all) == 2


# ---------------------------------------------------------------------------
# 4. CASCADE delete removes negative labels with parent dataset
# ---------------------------------------------------------------------------


async def test_negative_labels_cascade_delete(db: Database):
    """Deleting the parent dataset removes negative labels via ON DELETE CASCADE."""
    await _insert_dataset(db)
    await db.append_dataset_negative_labels([_NEG_LABEL])
    assert len(await db.list_dataset_negative_labels("linux-cve-2024-0001")) == 1

    async with aiosqlite.connect(db.db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = ON")
        await conn.execute(
            "DELETE FROM datasets WHERE name = ?", ("linux-cve-2024-0001",)
        )
        await conn.commit()

    rows = await db.list_dataset_negative_labels("linux-cve-2024-0001")
    assert rows == []


# ---------------------------------------------------------------------------
# 5. source CHECK rejects invalid values
# ---------------------------------------------------------------------------


async def test_negative_labels_source_check_rejects_invalid(db: Database):
    """source CHECK constraint rejects values other than 'benchmark' and 'manual'."""
    await _insert_dataset(db)
    bad_label = {**_NEG_LABEL, "id": "neg-bad", "source": "cve_patch"}
    with pytest.raises(Exception):
        async with aiosqlite.connect(db.db_path) as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            await conn.execute(
                """INSERT INTO dataset_negative_labels
                   (id, dataset_name, dataset_version, file_path, cwe_id, vuln_class, source, created_at)
                   VALUES (:id, :dataset_name, :dataset_version, :file_path,
                           :cwe_id, :vuln_class, :source, :created_at)""",
                bad_label,
            )
            await conn.commit()


async def test_negative_labels_source_benchmark_accepted(db: Database):
    """source='benchmark' is accepted by the CHECK constraint."""
    await _insert_dataset(db)
    await db.append_dataset_negative_labels([{**_NEG_LABEL, "source": "benchmark"}])
    rows = await db.list_dataset_negative_labels("linux-cve-2024-0001")
    assert rows[0]["source"] == "benchmark"


async def test_negative_labels_source_manual_accepted(db: Database):
    """source='manual' is accepted by the CHECK constraint."""
    await _insert_dataset(db)
    label = {**_NEG_LABEL, "id": "neg-manual", "source": "manual"}
    await db.append_dataset_negative_labels([label])
    rows = await db.list_dataset_negative_labels("linux-cve-2024-0001")
    assert rows[0]["source"] == "manual"


# ---------------------------------------------------------------------------
# 6. Missing required fields raise (KeyError)
# ---------------------------------------------------------------------------


async def test_negative_labels_missing_required_field_raises(db: Database):
    """Omitting a required field raises KeyError (same as positive-method behavior)."""
    await _insert_dataset(db)
    incomplete = {k: v for k, v in _NEG_LABEL.items() if k != "cwe_id"}
    with pytest.raises(KeyError):
        await db.append_dataset_negative_labels([incomplete])


# ---------------------------------------------------------------------------
# 7. Optional fields accept None
# ---------------------------------------------------------------------------


async def test_negative_labels_optional_fields_accept_none(db: Database):
    """source_ref and notes may be None without raising."""
    await _insert_dataset(db)
    label = {**_NEG_LABEL, "source_ref": None, "notes": None}
    await db.append_dataset_negative_labels([label])
    rows = await db.list_dataset_negative_labels("linux-cve-2024-0001")
    assert len(rows) == 1
    assert rows[0]["source_ref"] is None
    assert rows[0]["notes"] is None


async def test_negative_labels_optional_fields_store_values(db: Database):
    """source_ref and notes are persisted when provided."""
    await _insert_dataset(db)
    label = {**_NEG_LABEL, "source_ref": "OWASP-BM-042", "notes": "confirmed safe"}
    await db.append_dataset_negative_labels([label])
    rows = await db.list_dataset_negative_labels("linux-cve-2024-0001")
    assert rows[0]["source_ref"] == "OWASP-BM-042"
    assert rows[0]["notes"] == "confirmed safe"


# ---------------------------------------------------------------------------
# 8. Idempotent on duplicate id (INSERT OR IGNORE)
# ---------------------------------------------------------------------------


async def test_negative_labels_idempotent_on_duplicate_id(db: Database):
    """append called twice with the same id does not raise and keeps one row."""
    await _insert_dataset(db)
    await db.append_dataset_negative_labels([_NEG_LABEL])
    await db.append_dataset_negative_labels([_NEG_LABEL])
    rows = await db.list_dataset_negative_labels("linux-cve-2024-0001")
    assert len(rows) == 1
