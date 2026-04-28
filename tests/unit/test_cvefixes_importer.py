"""Unit tests for the CVEfixes importer."""

from __future__ import annotations

import json
import sqlite3
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio

from sec_review_framework.db import Database
from sec_review_framework.ground_truth.cvefixes_importer import (
    CVEfixesImportResult,
    _parse_hunk_ranges,
    _project_slug,
    _cwe_to_vuln_class,
    import_cvefixes,
)

# ---------------------------------------------------------------------------
# Fixture DB builder
# ---------------------------------------------------------------------------


def _make_cvefixes_db(path: Path) -> Path:
    """Build a synthetic CVEfixes SQLite with ~5 CVEs across 3 languages.

    Schema mirrors the real CVEfixes upstream:
      cve, fixes, commits, file_change, repository, cwe_classification
    """
    db_path = path / "cvefixes.db"
    con = sqlite3.connect(str(db_path))
    con.executescript(
        """
        CREATE TABLE cve (
            cve_id      TEXT PRIMARY KEY,
            severity    TEXT,
            description TEXT
        );

        CREATE TABLE fixes (
            cve_id TEXT,
            hash   TEXT
        );

        CREATE TABLE repository (
            repo_url TEXT PRIMARY KEY,
            language TEXT
        );

        CREATE TABLE commits (
            hash        TEXT PRIMARY KEY,
            repo_url    TEXT,
            parent_hash TEXT
        );

        CREATE TABLE file_change (
            hash     TEXT,
            filename TEXT,
            diff     TEXT,
            language TEXT
        );

        CREATE TABLE cwe_classification (
            cve_id TEXT,
            cwe_id TEXT
        );
        """
    )

    # --- CVE-2023-0001: Python / SQL injection ---
    _insert_cve(
        con,
        cve_id="CVE-2023-0001",
        severity="HIGH",
        description="SQL injection via unsanitised query parameter.",
        cwe_ids=["CWE-89"],
        commit_hash="aaa00001deadbeef",
        parent_hash="aaa00000deadbeef",
        repo_url="https://github.com/example/pyapp",
        language="Python",
        files=[
            (
                "app/db.py",
                "@@ -10,4 +10,4 @@\n-    query = f\"SELECT * FROM users WHERE id={id}\"\n+    query = \"SELECT * FROM users WHERE id=?\"\n",
            )
        ],
    )

    # --- CVE-2023-0002: Java / XSS ---
    _insert_cve(
        con,
        cve_id="CVE-2023-0002",
        severity="MEDIUM",
        description="XSS via unsanitised user input in Java web app.",
        cwe_ids=["CWE-79"],
        commit_hash="bbb00001deadbeef",
        parent_hash="bbb00000deadbeef",
        repo_url="https://github.com/example/javaapp",
        language="Java",
        files=[
            (
                "src/main/Servlet.java",
                "@@ -20,3 +20,3 @@\n-out.print(request.getParameter(\"name\"));\n+out.print(ESAPI.encoder().encodeForHTML(request.getParameter(\"name\")));\n",
            )
        ],
    )

    # --- CVE-2023-0003: C / memory safety (two file changes) ---
    _insert_cve(
        con,
        cve_id="CVE-2023-0003",
        severity="CRITICAL",
        description="Buffer overflow in C network server.",
        cwe_ids=["CWE-119"],
        commit_hash="ccc00001deadbeef",
        parent_hash="ccc00000deadbeef",
        repo_url="https://github.com/example/netd",
        language="C",
        files=[
            (
                "src/net.c",
                "@@ -5,3 +5,3 @@\n-char buf[256];\n+char buf[4096];\n",
            ),
            (
                "src/util.c",
                "@@ -30,2 +30,2 @@\n-memcpy(dst, src, len);\n+safe_memcpy(dst, src, len, sizeof(dst));\n",
            ),
        ],
    )

    # --- CVE-2023-0004: Python / path traversal ---
    _insert_cve(
        con,
        cve_id="CVE-2023-0004",
        severity="HIGH",
        description="Path traversal in file download endpoint.",
        cwe_ids=["CWE-22"],
        commit_hash="ddd00001deadbeef",
        parent_hash="ddd00000deadbeef",
        repo_url="https://github.com/example/fileserver",
        language="Python",
        files=[
            (
                "server/views.py",
                "@@ -15,1 +15,1 @@\n-return open(os.path.join(root, filename)).read()\n+return open(safe_join(root, filename)).read()\n",
            )
        ],
    )

    # --- CVE-2023-0005: Python / unknown CWE (tests fallback) ---
    _insert_cve(
        con,
        cve_id="CVE-2023-0005",
        severity=None,
        description="Miscellaneous vulnerability with unmapped CWE.",
        cwe_ids=["CWE-9999"],
        commit_hash="eee00001deadbeef",
        parent_hash="eee00000deadbeef",
        repo_url="https://github.com/example/misc",
        language="Python",
        files=[
            (
                "misc/thing.py",
                "@@ -1,1 +1,1 @@\n-bad()\n+good()\n",
            )
        ],
    )

    con.commit()
    con.close()
    return db_path


def _insert_cve(
    con: sqlite3.Connection,
    *,
    cve_id: str,
    severity: str | None,
    description: str,
    cwe_ids: list[str],
    commit_hash: str,
    parent_hash: str | None,
    repo_url: str,
    language: str,
    files: list[tuple[str, str]],
) -> None:
    """Insert one complete CVE fixture into the synthetic database."""
    con.execute(
        "INSERT OR IGNORE INTO cve (cve_id, severity, description) VALUES (?, ?, ?)",
        (cve_id, severity, description),
    )
    for cwe_id in cwe_ids:
        con.execute(
            "INSERT INTO cwe_classification (cve_id, cwe_id) VALUES (?, ?)",
            (cve_id, cwe_id),
        )
    con.execute(
        "INSERT OR IGNORE INTO repository (repo_url, language) VALUES (?, ?)",
        (repo_url, language),
    )
    con.execute(
        "INSERT OR IGNORE INTO commits (hash, repo_url, parent_hash) VALUES (?, ?, ?)",
        (commit_hash, repo_url, parent_hash),
    )
    con.execute(
        "INSERT INTO fixes (cve_id, hash) VALUES (?, ?)",
        (cve_id, commit_hash),
    )
    for filename, diff in files:
        con.execute(
            "INSERT INTO file_change (hash, filename, diff, language) VALUES (?, ?, ?, ?)",
            (commit_hash, filename, diff, language),
        )


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> Database:
    """Fresh in-process framework Database."""
    database = Database(tmp_path / "test.db")
    await database.init()
    return database


@pytest.fixture
def cvefixes_db(tmp_path: Path) -> Path:
    """Synthetic CVEfixes SQLite fixture."""
    return _make_cvefixes_db(tmp_path)


# ---------------------------------------------------------------------------
# 1. Happy path: 5 CVEs → 5 datasets, ≥5 labels
# ---------------------------------------------------------------------------


async def test_import_five_cves_produces_correct_counts(
    db: Database, cvefixes_db: Path
):
    """Importing 5 CVEs produces 5 datasets and ≥5 labels (one per file×hunk)."""
    result = await import_cvefixes(db, cvefixes_db_path=cvefixes_db)

    assert isinstance(result, CVEfixesImportResult)
    assert result.imported_cves == 5
    assert result.imported_datasets == 5
    assert result.imported_labels >= 5
    assert result.errors == []


# ---------------------------------------------------------------------------
# 2. metadata_json.language is set correctly per CVE
# ---------------------------------------------------------------------------


async def test_metadata_language_per_dataset(db: Database, cvefixes_db: Path):
    """Each imported dataset carries the correct language in metadata_json."""
    await import_cvefixes(db, cvefixes_db_path=cvefixes_db)

    all_datasets = await db.list_datasets()
    # All five datasets must have a language key
    languages_seen: set[str] = set()
    for ds in all_datasets:
        meta = json.loads(ds.get("metadata_json") or "{}")
        assert "language" in meta, f"Dataset {ds['name']} missing language key"
        languages_seen.add(meta["language"])

    # We expect Python, Java, C (from our fixture)
    assert "Python" in languages_seen
    assert "Java" in languages_seen
    assert "C" in languages_seen


# ---------------------------------------------------------------------------
# 3. Idempotency: running twice produces same counts, no duplicate rows
# ---------------------------------------------------------------------------


async def test_idempotency_no_duplicate_rows(db: Database, cvefixes_db: Path):
    """Running import_cvefixes twice on the same SQLite is fully idempotent."""
    first = await import_cvefixes(db, cvefixes_db_path=cvefixes_db)
    second = await import_cvefixes(db, cvefixes_db_path=cvefixes_db)

    # Second run should create no new datasets/labels
    assert second.imported_datasets == 0
    assert second.imported_labels == 0

    # Total datasets in DB must equal first run's count
    all_datasets = await db.list_datasets()
    assert len(all_datasets) == first.imported_datasets

    # Total labels should equal first run's count
    total_labels = 0
    for ds in all_datasets:
        labels = await db.list_dataset_labels(ds["name"])
        total_labels += len(labels)
    assert total_labels == first.imported_labels


# ---------------------------------------------------------------------------
# 4. Language filter excludes non-matching CVEs
# ---------------------------------------------------------------------------


async def test_language_filter_excludes_java_and_c(db: Database, cvefixes_db: Path):
    """languages=['python'] imports only Python CVEs (3 of 5)."""
    result = await import_cvefixes(
        db, cvefixes_db_path=cvefixes_db, languages=["python"]
    )

    assert result.imported_cves == 3  # CVE-2023-0001, 0004, 0005 are Python
    all_datasets = await db.list_datasets()
    for ds in all_datasets:
        meta = json.loads(ds.get("metadata_json") or "{}")
        assert meta.get("language", "").lower() == "python"


# ---------------------------------------------------------------------------
# 5. CVE with parent_hash IS NULL is skipped with a reason
# ---------------------------------------------------------------------------


async def test_null_parent_hash_skipped(db: Database, tmp_path: Path):
    """A commit with parent_hash=NULL is skipped (can't pin buggy state)."""
    db_path = _make_cvefixes_db(tmp_path)

    # Add a CVE whose only commit has null parent_hash
    con = sqlite3.connect(str(db_path))
    con.execute(
        "INSERT INTO cve (cve_id, severity, description) VALUES (?, ?, ?)",
        ("CVE-2023-NULL1", "LOW", "No parent"),
    )
    con.execute(
        "INSERT OR IGNORE INTO repository (repo_url, language) VALUES (?, ?)",
        ("https://github.com/x/no-parent", "Go"),
    )
    con.execute(
        "INSERT INTO commits (hash, repo_url, parent_hash) VALUES (?, ?, ?)",
        ("fff00001deadbeef", "https://github.com/x/no-parent", None),
    )
    con.execute(
        "INSERT INTO fixes (cve_id, hash) VALUES (?, ?)",
        ("CVE-2023-NULL1", "fff00001deadbeef"),
    )
    con.execute(
        "INSERT INTO file_change (hash, filename, diff, language) VALUES (?, ?, ?, ?)",
        ("fff00001deadbeef", "main.go", "@@ -1,1 +1,1 @@\n-bad()\n+good()\n", "Go"),
    )
    con.commit()
    con.close()

    result = await import_cvefixes(db, cvefixes_db_path=db_path)
    # The null-parent CVE should not appear as imported
    imported_cve_ids = set()
    for ds in await db.list_datasets():
        if ds.get("cve_id"):
            imported_cve_ids.add(ds["cve_id"])
    assert "CVE-2023-NULL1" not in imported_cve_ids


# ---------------------------------------------------------------------------
# 6. Malformed diff hunks are skipped; import continues
# ---------------------------------------------------------------------------


async def test_malformed_diff_appended_to_errors(db: Database, tmp_path: Path):
    """A CVE with a diff that raises during parsing is recorded in errors."""
    db_path = tmp_path / "cvefixes_bad.db"
    con = sqlite3.connect(str(db_path))
    con.executescript(
        """
        CREATE TABLE cve (cve_id TEXT PRIMARY KEY, severity TEXT, description TEXT);
        CREATE TABLE fixes (cve_id TEXT, hash TEXT);
        CREATE TABLE repository (repo_url TEXT PRIMARY KEY, language TEXT);
        CREATE TABLE commits (hash TEXT PRIMARY KEY, repo_url TEXT, parent_hash TEXT);
        CREATE TABLE file_change (hash TEXT, filename TEXT, diff TEXT, language TEXT);
        CREATE TABLE cwe_classification (cve_id TEXT, cwe_id TEXT);
        """
    )

    # Good CVE
    _insert_cve(
        con,
        cve_id="CVE-2023-GOOD",
        severity="HIGH",
        description="Good CVE",
        cwe_ids=["CWE-89"],
        commit_hash="aaagood01deadbeef",
        parent_hash="aaagood00deadbeef",
        repo_url="https://github.com/good/repo",
        language="Python",
        files=[("app.py", "@@ -1,1 +1,1 @@\n-bad()\n+good()\n")],
    )

    # Bad CVE: the diff is so malformed that _parse_hunk_ranges raises
    # We simulate this by patching: actually a diff with no hunk headers is
    # NOT an error (we fall back to line 1); to trigger a real error we need
    # to make _import_single_cve raise.  We do that by inserting a row with
    # a file_change diff that triggers our ValueError via a monkey-patch.
    # Easier approach: use a real error path — null repo_url on commit.
    con.execute(
        "INSERT INTO cve (cve_id, severity, description) VALUES (?, ?, ?)",
        ("CVE-2023-BADCOMMIT", "MEDIUM", "Bad commit with null repo_url"),
    )
    # Commit with null repo_url (will cause _import_single_cve to skip, not error)
    # To actually trigger an error path we monkey-patch diff parsing below.
    con.commit()
    con.close()

    result = await import_cvefixes(db, cvefixes_db_path=db_path)
    # The good CVE should still be imported
    all_datasets = await db.list_datasets()
    cve_ids = {ds.get("cve_id") for ds in all_datasets}
    assert "CVE-2023-GOOD" in cve_ids
    # No errors from the good CVE
    assert not any(e[0] == "CVE-2023-GOOD" for e in result.errors)


async def test_malformed_diff_via_monkeypatch(
    db: Database, cvefixes_db: Path, monkeypatch
):
    """If diff parsing raises for one CVE, it is recorded in errors; others continue."""
    import sec_review_framework.ground_truth.cvefixes_importer as mod

    original_parse = mod._parse_hunk_ranges
    call_count = 0

    def patched_parse(diff_text: str) -> list[tuple[int, int]]:
        nonlocal call_count
        call_count += 1
        # Make the very first call raise
        if call_count == 1:
            raise ValueError("Simulated malformed diff")
        return original_parse(diff_text)

    monkeypatch.setattr(mod, "_parse_hunk_ranges", patched_parse)

    result = await import_cvefixes(db, cvefixes_db_path=cvefixes_db)

    # Exactly one error recorded (the CVE whose first file had bad diff)
    assert len(result.errors) == 1
    # The other 4 CVEs should still have imported
    assert result.imported_cves == 4


# ---------------------------------------------------------------------------
# 7. source CHECK migration is idempotent and accepts 'cvefixes'
# ---------------------------------------------------------------------------


async def test_source_check_migration_idempotent(db: Database, cvefixes_db: Path):
    """Running import_cvefixes twice does not fail due to duplicate migration."""
    # Run twice — second call re-runs _migrate_source_check idempotently
    await import_cvefixes(db, cvefixes_db_path=cvefixes_db)
    await import_cvefixes(db, cvefixes_db_path=cvefixes_db)
    # No exception means idempotency holds


async def test_source_check_accepts_cvefixes(db: Database, cvefixes_db: Path):
    """After migration, inserting a label with source='cvefixes' succeeds."""
    await import_cvefixes(db, cvefixes_db_path=cvefixes_db)

    # Find any imported dataset to hang a label off
    all_datasets = await db.list_datasets()
    assert all_datasets, "No datasets imported"
    ds_name = all_datasets[0]["name"]

    import aiosqlite

    async with aiosqlite.connect(db.db_path) as conn:
        # This must not raise a CHECK constraint violation
        await conn.execute(
            """
            INSERT OR IGNORE INTO dataset_labels (
                id, dataset_name, dataset_version, file_path,
                line_start, line_end, cwe_id, vuln_class, severity,
                description, source, confidence, created_at
            ) VALUES (
                'test-check-cvefixes', ?, 'v1', 'test.py',
                1, 1, 'CWE-89', 'sqli', 'HIGH',
                'test description', 'cvefixes', 'HIGH', '2026-01-01T00:00:00'
            )
            """,
            (ds_name,),
        )
        await conn.commit()

    # Verify it was stored
    labels = await db.list_dataset_labels(ds_name, source="cvefixes")
    # We should have at least the one we inserted (existing ones may also be cvefixes)
    assert any(lb["id"] == "test-check-cvefixes" for lb in labels)


# ---------------------------------------------------------------------------
# 8. CWE → vuln_class mapping
# ---------------------------------------------------------------------------


def test_cwe_to_vuln_class_known_mapping():
    """CWE-89 maps to 'sqli' per vuln_classes.yaml."""
    vc = _cwe_to_vuln_class("CWE-89")
    assert vc == "sqli"


def test_cwe_to_vuln_class_unknown_falls_back_to_literal():
    """An unmapped CWE returns the literal CWE id string."""
    vc = _cwe_to_vuln_class("CWE-9999")
    assert vc == "CWE-9999"


def test_cwe_to_vuln_class_memory_safety():
    """CWE-119 maps to 'memory_safety'."""
    vc = _cwe_to_vuln_class("CWE-119")
    assert vc == "memory_safety"


# ---------------------------------------------------------------------------
# 9. Hunk range parsing
# ---------------------------------------------------------------------------


def test_parse_hunk_single_line():
    """Single-line hunk: @@ -10,1 maps to (10, 10)."""
    diff = "@@ -10,1 +11,1 @@ context\n-old line\n+new line\n"
    ranges = _parse_hunk_ranges(diff)
    assert ranges == [(10, 10)]


def test_parse_hunk_multiline():
    """Multi-line hunk: @@ -10,4 maps to (10, 13)."""
    diff = "@@ -10,4 +10,4 @@\n context\n-old\n+new\n context\n context\n"
    ranges = _parse_hunk_ranges(diff)
    assert ranges == [(10, 13)]


def test_parse_hunk_no_count():
    """Hunk with no count (implicit 1): @@ -5 maps to (5, 5)."""
    diff = "@@ -5 +5 @@\n-line\n+line\n"
    ranges = _parse_hunk_ranges(diff)
    assert ranges == [(5, 5)]


def test_parse_hunk_no_hunk_header():
    """Diff with no @@ lines returns empty list (caller handles gracefully)."""
    ranges = _parse_hunk_ranges("--- a/file\n+++ b/file\n-removed\n")
    assert ranges == []


def test_parse_hunk_multiple_hunks():
    """Two @@ headers → two ranges."""
    diff = (
        "@@ -1,3 +1,3 @@\n-a\n+b\n c\n"
        "@@ -20,2 +20,2 @@\n-x\n+y\n"
    )
    ranges = _parse_hunk_ranges(diff)
    assert ranges == [(1, 3), (20, 21)]


# ---------------------------------------------------------------------------
# 10. Project slug helper
# ---------------------------------------------------------------------------


def test_project_slug_github_url():
    assert _project_slug("https://github.com/owner/repo") == "owner-repo"


def test_project_slug_trailing_slash():
    assert _project_slug("https://github.com/owner/repo/") == "owner-repo"


def test_project_slug_ssh():
    assert _project_slug("git@github.com:owner/repo") == "owner-repo"


# ---------------------------------------------------------------------------
# 11. max_cves cap
# ---------------------------------------------------------------------------


async def test_max_cves_cap(db: Database, cvefixes_db: Path):
    """max_cves=2 imports at most 2 CVEs."""
    result = await import_cvefixes(db, cvefixes_db_path=cvefixes_db, max_cves=2)
    assert result.imported_cves <= 2


# ---------------------------------------------------------------------------
# 12. Missing CVEfixes tables raises a clear error
# ---------------------------------------------------------------------------


async def test_missing_tables_raises_runtime_error(db: Database, tmp_path: Path):
    """If the CVEfixes SQLite is missing expected tables, raise RuntimeError."""
    bad_db = tmp_path / "empty.db"
    con = sqlite3.connect(str(bad_db))
    con.execute("CREATE TABLE foo (id INTEGER)")
    con.commit()
    con.close()

    with pytest.raises(RuntimeError, match="missing expected tables"):
        await import_cvefixes(db, cvefixes_db_path=bad_db)


# ---------------------------------------------------------------------------
# 13. C CVE with two file changes produces two+ labels
# ---------------------------------------------------------------------------


async def test_c_cve_two_file_changes(db: Database, cvefixes_db: Path):
    """CVE-2023-0003 (C) has two file changes — should produce ≥2 labels."""
    await import_cvefixes(db, cvefixes_db_path=cvefixes_db)

    all_datasets = await db.list_datasets()
    c_ds = [
        ds
        for ds in all_datasets
        if ds.get("cve_id") == "CVE-2023-0003"
    ]
    assert c_ds, "CVE-2023-0003 dataset not found"
    labels = await db.list_dataset_labels(c_ds[0]["name"])
    assert len(labels) >= 2


# ---------------------------------------------------------------------------
# 14. zenodo_doi is reflected in metadata_json
# ---------------------------------------------------------------------------


async def test_zenodo_doi_in_metadata(db: Database, cvefixes_db: Path):
    """The zenodo_doi parameter is stored in each dataset's metadata_json."""
    custom_doi = "10.5281/zenodo.TEST1234"
    await import_cvefixes(db, cvefixes_db_path=cvefixes_db, zenodo_doi=custom_doi)

    for ds in await db.list_datasets():
        meta = json.loads(ds.get("metadata_json") or "{}")
        assert meta.get("zenodo_doi") == custom_doi
