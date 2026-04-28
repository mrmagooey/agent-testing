"""Unit tests for the NIST SARD importer.

Uses a synthetic SARD-shaped extracted directory built in tmp (no real SARD
content is vendored).  All fixtures are self-contained and deterministic.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import aiosqlite
import pytest
import pytest_asyncio

from sec_review_framework.db import Database
from sec_review_framework.ground_truth.sard_importer import (
    SARDImportResult,
    _find_manifest,
    _normalise_language,
    _safe_path,
    import_sard,
)
from sec_review_framework.ground_truth._source_check_migration import (
    ensure_negative_source_check_includes,
    ensure_source_check_includes,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FAKE_SHA256 = "a" * 64  # 64-char hex string (valid sha256 length)
_FAKE_ARCHIVE_URL = "https://samate.nist.gov/SARD/downloads/test-suite-fake.zip"


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> Database:
    """Fresh in-process SQLite database for each test."""
    database = Database(tmp_path / "test.db")
    await database.init()
    return database


def _make_sard_dir(
    base: Path,
    testcases: list[dict],
    *,
    manifest_name: str = "manifest.xml",
) -> Path:
    """Create a synthetic SARD extracted directory with ``manifest.xml``.

    Each element of ``testcases`` should be a dict with:
        id       : str
        language : str  (SARD attribute value, e.g. "C", "Java", "Python")
        files    : list[str]  — relative paths
        flaws    : list[dict] — [{line, cwe}]; empty list → fixed testcase
        fix      : bool       — if True and no flaws, add <fix/> element
    """
    root = ET.Element("manifest")

    for tc in testcases:
        tc_elem = ET.SubElement(root, "testcase")
        tc_elem.set("id", str(tc["id"]))
        tc_elem.set("language", tc["language"])

        for fp in tc.get("files", []):
            f_elem = ET.SubElement(tc_elem, "file")
            f_elem.set("path", fp)
            f_elem.set("language", tc["language"])

        flaws = tc.get("flaws", [])
        for flaw in flaws:
            fl_elem = ET.SubElement(tc_elem, "flaw")
            fl_elem.set("line", str(flaw.get("line", 1)))
            fl_elem.set("cwe", flaw.get("cwe", "CWE-UNKNOWN"))

        if tc.get("fix", False) and not flaws:
            ET.SubElement(tc_elem, "fix")

    tree = ET.ElementTree(root)
    sard_dir = base / "sard_extracted"
    sard_dir.mkdir(parents=True, exist_ok=True)
    ET.indent(tree)
    tree.write(str(sard_dir / manifest_name), encoding="unicode", xml_declaration=False)

    return sard_dir


# ---------------------------------------------------------------------------
# Synthetic testcase definitions
# ---------------------------------------------------------------------------

# 6 testcases across C, Java, Python mixing vulnerable and fixed.
_TC_C_VULN_1 = {
    "id": "100001",
    "language": "C",
    "files": ["CWE121/CWE121_bad.c"],
    "flaws": [{"line": 42, "cwe": "CWE-121"}],
    "fix": False,
}
_TC_C_FIX_1 = {
    "id": "100002",
    "language": "C",
    "files": ["CWE121/CWE121_good.c"],
    "flaws": [],
    "fix": True,
}
_TC_JAVA_VULN_1 = {
    "id": "200001",
    "language": "Java",
    "files": ["CWE89/CWE89_bad.java"],
    "flaws": [{"line": 15, "cwe": "CWE-89"}],
    "fix": False,
}
_TC_JAVA_VULN_2 = {
    "id": "200002",
    "language": "Java",
    "files": ["CWE89/CWE89_bad2.java"],
    "flaws": [{"line": 30, "cwe": "CWE-89"}, {"line": 45, "cwe": "CWE-89"}],
    "fix": False,
}
_TC_JAVA_FIX_1 = {
    "id": "200003",
    "language": "Java",
    "files": ["CWE89/CWE89_good.java"],
    "flaws": [],
    "fix": True,
}
_TC_PYTHON_VULN_1 = {
    "id": "300001",
    "language": "Python",
    "files": ["CWE22/CWE22_bad.py"],
    "flaws": [{"line": 10, "cwe": "CWE-22"}],
    "fix": False,
}

_ALL_TESTCASES = [
    _TC_C_VULN_1,
    _TC_C_FIX_1,
    _TC_JAVA_VULN_1,
    _TC_JAVA_VULN_2,
    _TC_JAVA_FIX_1,
    _TC_PYTHON_VULN_1,
]


# ---------------------------------------------------------------------------
# 1. _normalise_language helper
# ---------------------------------------------------------------------------


def test_normalise_language_known_codes():
    assert _normalise_language("C") == "c"
    assert _normalise_language("Cpp") == "cpp"
    assert _normalise_language("Java") == "java"
    assert _normalise_language("Python") == "python"
    assert _normalise_language("Php") == "php"
    # Case variants
    assert _normalise_language("CPP") == "cpp"
    assert _normalise_language("PHP") == "php"


def test_normalise_language_unknown_lowercases():
    assert _normalise_language("Ruby") == "ruby"
    assert _normalise_language("RUST") == "rust"


# ---------------------------------------------------------------------------
# 2. _safe_path helper
# ---------------------------------------------------------------------------


def test_safe_path_accepts_valid_relative(tmp_path: Path):
    result = _safe_path("CWE121/CWE121_bad.c", tmp_path)
    assert result is not None
    assert result == (tmp_path / "CWE121" / "CWE121_bad.c").resolve()


def test_safe_path_rejects_dotdot(tmp_path: Path):
    assert _safe_path("../etc/passwd", tmp_path) is None
    assert _safe_path("../../secret", tmp_path) is None


def test_safe_path_rejects_absolute(tmp_path: Path):
    assert _safe_path("/etc/passwd", tmp_path) is None


def test_safe_path_rejects_empty(tmp_path: Path):
    assert _safe_path("", tmp_path) is None


# ---------------------------------------------------------------------------
# 3. _find_manifest
# ---------------------------------------------------------------------------


def test_find_manifest_direct(tmp_path: Path):
    sard_dir = _make_sard_dir(tmp_path, [_TC_C_VULN_1])
    manifest = _find_manifest(sard_dir)
    assert manifest is not None
    assert manifest.name == "manifest.xml"


def test_find_manifest_nested(tmp_path: Path):
    """manifest.xml one level deep is found."""
    sard_dir = tmp_path / "sard_root"
    sard_dir.mkdir()
    nested = sard_dir / "Juliet_v1.3"
    nested.mkdir()
    (nested / "manifest.xml").write_text("<manifest/>")
    result = _find_manifest(sard_dir)
    assert result == nested / "manifest.xml"


def test_find_manifest_missing_returns_none(tmp_path: Path):
    empty = tmp_path / "empty_sard"
    empty.mkdir()
    assert _find_manifest(empty) is None


# ---------------------------------------------------------------------------
# 4. import_sard — dataset_labels count for vulnerable testcases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_sard_positive_label_count(db: Database, tmp_path: Path):
    """Vulnerable testcases produce the expected number of dataset_labels rows."""
    sard_dir = _make_sard_dir(tmp_path, _ALL_TESTCASES)

    result = await import_sard(
        db,
        sard_archive_path=sard_dir,
        archive_url=_FAKE_ARCHIVE_URL,
        archive_sha256=_FAKE_SHA256,
    )

    # C: 1 vuln (1 file × 1 flaw) = 1 label
    # Java: 200001 (1 file × 1 flaw) = 1, 200002 (1 file × 2 flaws) = 2 → 3
    # Python: 1 vuln (1 file × 1 flaw) = 1
    # Total positive labels: 1 + 3 + 1 = 5
    assert result.imported_labels == 5
    assert result.errors == []


@pytest.mark.asyncio
async def test_import_sard_labels_in_db(db: Database, tmp_path: Path):
    """dataset_labels rows exist in the DB after import."""
    sard_dir = _make_sard_dir(tmp_path, [_TC_JAVA_VULN_1])
    await import_sard(
        db,
        sard_archive_path=sard_dir,
        archive_url=_FAKE_ARCHIVE_URL,
        archive_sha256=_FAKE_SHA256,
    )
    ds_name_java = f"nist-sard-java-{_FAKE_SHA256[:12]}"
    labels = await db.list_dataset_labels(ds_name_java)
    assert len(labels) == 1
    label = labels[0]
    assert label["cwe_id"] == "CWE-89"
    assert label["line_start"] == 15
    assert label["source"] == "sard"
    assert label["source_ref"] == "SARD-200001"


# ---------------------------------------------------------------------------
# 5. import_sard — dataset_negative_labels count for fixed testcases
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_sard_negative_label_count(db: Database, tmp_path: Path):
    """Fixed testcases produce the expected number of dataset_negative_labels rows."""
    sard_dir = _make_sard_dir(tmp_path, _ALL_TESTCASES)

    result = await import_sard(
        db,
        sard_archive_path=sard_dir,
        archive_url=_FAKE_ARCHIVE_URL,
        archive_sha256=_FAKE_SHA256,
    )

    # _TC_C_FIX_1: 1 file → 1 negative label
    # _TC_JAVA_FIX_1: 1 file → 1 negative label
    # Total: 2
    assert result.imported_negative_labels == 2
    assert result.errors == []


@pytest.mark.asyncio
async def test_import_sard_negative_labels_in_db(db: Database, tmp_path: Path):
    """dataset_negative_labels rows exist in the DB after import."""
    sard_dir = _make_sard_dir(tmp_path, [_TC_C_FIX_1])
    await import_sard(
        db,
        sard_archive_path=sard_dir,
        archive_url=_FAKE_ARCHIVE_URL,
        archive_sha256=_FAKE_SHA256,
    )
    ds_name_c = f"nist-sard-c-{_FAKE_SHA256[:12]}"
    neg_labels = await db.list_dataset_negative_labels(ds_name_c)
    assert len(neg_labels) == 1
    neg = neg_labels[0]
    assert neg["source"] == "sard"
    assert neg["source_ref"] == "SARD-100002"


# ---------------------------------------------------------------------------
# 6. Per-language datasets get the right metadata_json.language
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_per_language_datasets_have_correct_language(db: Database, tmp_path: Path):
    """Each language shard dataset has the correct language in metadata_json."""
    sard_dir = _make_sard_dir(tmp_path, _ALL_TESTCASES)
    await import_sard(
        db,
        sard_archive_path=sard_dir,
        archive_url=_FAKE_ARCHIVE_URL,
        archive_sha256=_FAKE_SHA256,
    )
    import json

    for lang in ("c", "java", "python"):
        ds_name = f"nist-sard-{lang}-{_FAKE_SHA256[:12]}"
        ds = await db.get_dataset(ds_name)
        assert ds is not None, f"Dataset for language {lang!r} not found"
        meta = json.loads(ds["metadata_json"])
        assert meta["language"] == lang, f"Expected language={lang!r}, got {meta['language']!r}"
        assert meta["benchmark"] == "nist-sard"
        assert ds["kind"] == "archive"
        assert ds["archive_url"] == _FAKE_ARCHIVE_URL
        assert ds["archive_sha256"] == _FAKE_SHA256


# ---------------------------------------------------------------------------
# 7. Idempotency
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_sard_idempotent(db: Database, tmp_path: Path):
    """Running import twice produces the same counts; no duplicate rows."""
    sard_dir = _make_sard_dir(tmp_path, _ALL_TESTCASES)

    r1 = await import_sard(
        db,
        sard_archive_path=sard_dir,
        archive_url=_FAKE_ARCHIVE_URL,
        archive_sha256=_FAKE_SHA256,
    )
    r2 = await import_sard(
        db,
        sard_archive_path=sard_dir,
        archive_url=_FAKE_ARCHIVE_URL,
        archive_sha256=_FAKE_SHA256,
    )

    # Second run creates no new datasets (they already exist)
    assert r2.imported_datasets == 0

    # Check actual DB row counts to confirm no duplicates
    ds_name_java = f"nist-sard-java-{_FAKE_SHA256[:12]}"
    labels = await db.list_dataset_labels(ds_name_java)
    # Java: 200001 (1 label) + 200002 (2 labels) = 3
    assert len(labels) == 3

    neg_labels = await db.list_dataset_negative_labels(ds_name_java)
    assert len(neg_labels) == 1

    # Total DB label count must not grow on second run
    assert r2.imported_labels == r1.imported_labels  # INSERT OR IGNORE, same rows
    assert r2.imported_negative_labels == r1.imported_negative_labels


# ---------------------------------------------------------------------------
# 8. Language filter excludes other languages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_language_filter_excludes_other_languages(db: Database, tmp_path: Path):
    """Passing languages=['java'] excludes C and Python testcases."""
    sard_dir = _make_sard_dir(tmp_path, _ALL_TESTCASES)

    result = await import_sard(
        db,
        sard_archive_path=sard_dir,
        archive_url=_FAKE_ARCHIVE_URL,
        archive_sha256=_FAKE_SHA256,
        languages=["java"],
    )

    # Only Java testcases imported
    assert result.imported_datasets == 1  # one dataset for Java
    # Java positives: 200001 (1), 200002 (2) = 3
    assert result.imported_labels == 3
    # Java negatives: 200003 (1) = 1
    assert result.imported_negative_labels == 1

    # No C or Python datasets created
    ds_c = await db.get_dataset(f"nist-sard-c-{_FAKE_SHA256[:12]}")
    ds_py = await db.get_dataset(f"nist-sard-python-{_FAKE_SHA256[:12]}")
    assert ds_c is None
    assert ds_py is None


# ---------------------------------------------------------------------------
# 9. Source CHECK migration — 'sard' is accepted and idempotent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_source_check_migration_adds_sard(db: Database):
    """ensure_source_check_includes('sard') allows sard labels to be inserted."""
    await ensure_source_check_includes(db, "sard")

    # Insert a label with source='sard' — should not raise
    async with aiosqlite.connect(db.db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = OFF")
        await conn.execute(
            """
            INSERT INTO dataset_labels (
                id, dataset_name, dataset_version, file_path,
                line_start, line_end, cwe_id, vuln_class, severity,
                description, source, confidence, created_at
            ) VALUES (
                'test-sard-label', '_probe_ds', 'v1', 'foo.c',
                1, 1, 'CWE-121', 'other', 'MEDIUM',
                'test', 'sard', 'HIGH', '2026-01-01T00:00:00'
            )
            """
        )
        await conn.commit()

    # Check it's in the DB
    async with aiosqlite.connect(db.db_path) as conn:
        async with conn.execute(
            "SELECT id FROM dataset_labels WHERE source = 'sard'"
        ) as cur:
            rows = await cur.fetchall()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_source_check_migration_idempotent(db: Database):
    """Calling ensure_source_check_includes twice does not raise."""
    await ensure_source_check_includes(db, "sard")
    await ensure_source_check_includes(db, "sard")  # should not raise or duplicate


@pytest.mark.asyncio
async def test_negative_source_check_migration_adds_sard(db: Database):
    """ensure_negative_source_check_includes('sard') allows sard neg labels."""
    await ensure_negative_source_check_includes(db, "sard")

    async with aiosqlite.connect(db.db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = OFF")
        await conn.execute(
            """
            INSERT INTO dataset_negative_labels (
                id, dataset_name, dataset_version, file_path,
                cwe_id, vuln_class, source, created_at
            ) VALUES (
                'test-sard-neg', '_probe_ds', 'v1', 'foo.c',
                'CWE-121', 'other', 'sard', '2026-01-01T00:00:00'
            )
            """
        )
        await conn.commit()

    async with aiosqlite.connect(db.db_path) as conn:
        async with conn.execute(
            "SELECT id FROM dataset_negative_labels WHERE source = 'sard'"
        ) as cur:
            rows = await cur.fetchall()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_negative_source_check_migration_idempotent(db: Database):
    """Calling ensure_negative_source_check_includes twice does not raise."""
    await ensure_negative_source_check_includes(db, "sard")
    await ensure_negative_source_check_includes(db, "sard")


# ---------------------------------------------------------------------------
# 10. Unmapped CWE falls back to vuln_class="other"
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unmapped_cwe_falls_back_to_other(db: Database, tmp_path: Path):
    """A testcase with a CWE that is not in the YAML map gets vuln_class='other'."""
    tc_unknown_cwe = {
        "id": "999001",
        "language": "C",
        "files": ["CWE9999/CWE9999_bad.c"],
        "flaws": [{"line": 1, "cwe": "CWE-9999"}],
        "fix": False,
    }
    sard_dir = _make_sard_dir(tmp_path, [tc_unknown_cwe])

    await import_sard(
        db,
        sard_archive_path=sard_dir,
        archive_url=_FAKE_ARCHIVE_URL,
        archive_sha256=_FAKE_SHA256,
    )

    ds_name_c = f"nist-sard-c-{_FAKE_SHA256[:12]}"
    labels = await db.list_dataset_labels(ds_name_c)
    assert len(labels) == 1
    assert labels[0]["vuln_class"] == "other"
    assert labels[0]["cwe_id"] == "CWE-9999"


# ---------------------------------------------------------------------------
# 11. Path traversal in manifest is safely rejected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_path_traversal_rejected(db: Database, tmp_path: Path):
    """Testcases with traversal paths (../../) are skipped; no error raised."""
    tc_traversal = {
        "id": "666001",
        "language": "C",
        "files": ["../../etc/passwd"],
        "flaws": [{"line": 1, "cwe": "CWE-22"}],
        "fix": False,
    }
    sard_dir = _make_sard_dir(tmp_path, [tc_traversal])

    result = await import_sard(
        db,
        sard_archive_path=sard_dir,
        archive_url=_FAKE_ARCHIVE_URL,
        archive_sha256=_FAKE_SHA256,
    )

    # Should be skipped, not imported, not errored
    assert result.imported_labels == 0
    assert result.errors == []
    assert result.skipped_testcases >= 1


# ---------------------------------------------------------------------------
# 12. max_testcases cap
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_testcases_cap(db: Database, tmp_path: Path):
    """max_testcases limits the number of processed testcases."""
    sard_dir = _make_sard_dir(tmp_path, _ALL_TESTCASES)

    result = await import_sard(
        db,
        sard_archive_path=sard_dir,
        archive_url=_FAKE_ARCHIVE_URL,
        archive_sha256=_FAKE_SHA256,
        max_testcases=2,
    )

    # Only 2 testcases are processed (C_VULN_1 and C_FIX_1)
    total = result.imported_labels + result.imported_negative_labels + result.skipped_testcases
    assert total <= 2


# ---------------------------------------------------------------------------
# 13. dataset kind='archive' columns are set correctly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dataset_archive_columns(db: Database, tmp_path: Path):
    """Archive datasets have the correct archive_url, archive_sha256, archive_format."""
    sard_dir = _make_sard_dir(tmp_path, [_TC_C_VULN_1])

    await import_sard(
        db,
        sard_archive_path=sard_dir,
        archive_url=_FAKE_ARCHIVE_URL,
        archive_sha256=_FAKE_SHA256,
        archive_format="zip",
    )

    ds_name_c = f"nist-sard-c-{_FAKE_SHA256[:12]}"
    ds = await db.get_dataset(ds_name_c)
    assert ds is not None
    assert ds["kind"] == "archive"
    assert ds["archive_url"] == _FAKE_ARCHIVE_URL
    assert ds["archive_sha256"] == _FAKE_SHA256
    assert ds["archive_format"] == "zip"
    # Git-specific and derived-specific columns must be NULL
    assert ds["origin_url"] is None
    assert ds["origin_commit"] is None
    assert ds["base_dataset"] is None
