"""Unit tests for the CrossVul importer.

All tests use synthesised fixtures — no real CrossVul data, no real diffs,
no network access.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from sec_review_framework.db import Database
from sec_review_framework.ground_truth.crossvul_importer import (
    CrossVulImportResult,
    _diff_cache_path,
    _parse_manifest,
    _project_slug,
    _split_diff_by_file,
    import_crossvul,
)


# ---------------------------------------------------------------------------
# Helpers: manifest and diff cache builders
# ---------------------------------------------------------------------------

_SAMPLE_RECORDS: list[dict[str, Any]] = [
    {
        "cve_id": "CVE-2021-0001",
        "language": "Python",
        "project_url": "https://github.com/example/pyapp",
        "fix_commit": "aaa00001deadbeef11223344",
        "cwe_id": "CWE-89",
        "severity": "HIGH",
    },
    {
        "cve_id": "CVE-2021-0002",
        "language": "Java",
        "project_url": "https://github.com/example/javaapp",
        "fix_commit": "bbb00001deadbeef11223344",
        "cwe_id": "CWE-79",
        "severity": "MEDIUM",
    },
    {
        "cve_id": "CVE-2021-0003",
        "language": "C",
        "project_url": "https://github.com/example/cserver",
        "fix_commit": "ccc00001deadbeef11223344",
        "cwe_id": "CWE-119",
        "severity": "CRITICAL",
    },
    {
        "cve_id": "CVE-2021-0004",
        "language": "PHP",
        "project_url": "https://github.com/example/phpapp",
        "fix_commit": "ddd00001deadbeef11223344",
        "cwe_id": "CWE-22",
        "severity": "HIGH",
    },
    {
        "cve_id": "CVE-2021-0005",
        "language": "JavaScript",
        "project_url": "https://github.com/example/jsapp",
        "fix_commit": "eee00001deadbeef11223344",
        "cwe_id": "CWE-9999",  # unknown CWE → tests fallback
        "severity": None,
    },
]

# A minimal but valid unified diff with one hunk
_SIMPLE_DIFF_TEMPLATE = textwrap.dedent(
    """\
    diff --git a/{filename} b/{filename}
    index aabbcc..ddeeff 100644
    --- a/{filename}
    +++ b/{filename}
    @@ -{old_start},1 +{old_start},1 @@
    -bad_line()
    +good_line()
    """
)

# A diff with two hunks (for the C CVE test)
_TWO_HUNK_DIFF = textwrap.dedent(
    """\
    diff --git a/src/net.c b/src/net.c
    index aabbcc..ddeeff 100644
    --- a/src/net.c
    +++ b/src/net.c
    @@ -5,1 +5,1 @@
    -char buf[256];
    +char buf[4096];
    @@ -30,2 +30,2 @@
    -memcpy(dst, src, len);
    +safe_memcpy(dst, src, len, sizeof(dst));
    """
)

# Diff with no @@ hunk headers (tests graceful fallback)
_NO_HUNK_DIFF = textwrap.dedent(
    """\
    diff --git a/setup.py b/setup.py
    new file mode 100644
    --- /dev/null
    +++ b/setup.py
    """
)


def _write_manifest(tmp_path: Path, records: list[dict]) -> Path:
    """Write a JSON manifest and return its path."""
    manifest = tmp_path / "crossvul.json"
    manifest.write_text(json.dumps(records), encoding="utf-8")
    return manifest


def _populate_diff_cache(
    fix_clone_root: Path,
    records: list[dict],
    *,
    diff_override: dict[str, str] | None = None,
    skip_cve_ids: set[str] | None = None,
) -> None:
    """Pre-populate the diff cache for the given records.

    Args:
        fix_clone_root: Root dir for the cache.
        records: List of manifest records.
        diff_override: Map of cve_id → diff text to use instead of the default.
        skip_cve_ids: CVE IDs to intentionally leave out of the cache.
    """
    if diff_override is None:
        diff_override = {}
    if skip_cve_ids is None:
        skip_cve_ids = set()

    for rec in records:
        cve_id = rec["cve_id"]
        if cve_id in skip_cve_ids:
            continue
        project_url = rec["project_url"]
        fix_commit = rec["fix_commit"]
        cache_path = _diff_cache_path(fix_clone_root, project_url, fix_commit)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if cve_id in diff_override:
            diff_text = diff_override[cve_id]
        elif cve_id == "CVE-2021-0003":
            diff_text = _TWO_HUNK_DIFF
        else:
            filename = f"src/main.{rec['language'].lower()}"
            diff_text = _SIMPLE_DIFF_TEMPLATE.format(filename=filename, old_start=10)
        cache_path.write_text(diff_text, encoding="utf-8")


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
def fix_clone_root(tmp_path: Path) -> Path:
    root = tmp_path / "diff_cache"
    root.mkdir()
    return root


@pytest.fixture
def manifest(tmp_path: Path) -> Path:
    return _write_manifest(tmp_path, _SAMPLE_RECORDS)


@pytest.fixture
def populated_cache(fix_clone_root: Path) -> Path:
    _populate_diff_cache(fix_clone_root, _SAMPLE_RECORDS)
    return fix_clone_root


# ---------------------------------------------------------------------------
# 1. Happy path: 5 CVEs → 5 datasets, ≥5 labels
# ---------------------------------------------------------------------------


async def test_import_five_cves_produces_correct_counts(
    db: Database,
    manifest: Path,
    populated_cache: Path,
) -> None:
    """Importing 5 CVEs produces 5 datasets and ≥5 labels (one per file×hunk)."""
    result = await import_crossvul(
        db,
        manifest_path=manifest,
        fix_clone_root=populated_cache,
    )

    assert isinstance(result, CrossVulImportResult)
    assert result.imported_cves == 5, f"Expected 5, got {result.imported_cves}; errors={result.errors}"
    assert result.imported_datasets == 5
    assert result.imported_labels >= 5
    assert result.errors == []


# ---------------------------------------------------------------------------
# 2. metadata_json.language is set correctly per CVE
# ---------------------------------------------------------------------------


async def test_metadata_language_per_dataset(
    db: Database,
    manifest: Path,
    populated_cache: Path,
) -> None:
    """Each imported dataset carries the correct language in metadata_json."""
    await import_crossvul(db, manifest_path=manifest, fix_clone_root=populated_cache)

    all_datasets = await db.list_datasets()
    languages_seen: set[str] = set()
    for ds in all_datasets:
        meta = json.loads(ds.get("metadata_json") or "{}")
        assert "language" in meta, f"Dataset {ds['name']} missing language key"
        languages_seen.add(meta["language"])

    assert "Python" in languages_seen
    assert "Java" in languages_seen
    assert "C" in languages_seen
    assert "PHP" in languages_seen
    assert "JavaScript" in languages_seen


# ---------------------------------------------------------------------------
# 3. Idempotency: running twice produces same row counts, no duplicates
# ---------------------------------------------------------------------------


async def test_idempotency_no_duplicate_rows(
    db: Database,
    manifest: Path,
    populated_cache: Path,
) -> None:
    """Running import_crossvul twice on the same manifest is fully idempotent."""
    first = await import_crossvul(db, manifest_path=manifest, fix_clone_root=populated_cache)
    second = await import_crossvul(db, manifest_path=manifest, fix_clone_root=populated_cache)

    # Second run must create no new datasets/labels
    assert second.imported_datasets == 0
    assert second.imported_labels == 0

    # Total datasets in DB must equal first run's count
    all_datasets = await db.list_datasets()
    assert len(all_datasets) == first.imported_datasets

    # Total labels must equal first run's label count
    total_labels = 0
    for ds in all_datasets:
        labels = await db.list_dataset_labels(ds["name"])
        total_labels += len(labels)
    assert total_labels == first.imported_labels


# ---------------------------------------------------------------------------
# 4. Language filter restricts the import
# ---------------------------------------------------------------------------


async def test_language_filter_python_only(
    db: Database,
    tmp_path: Path,
    fix_clone_root: Path,
) -> None:
    """languages=['python'] imports only the Python CVE (1 of 5)."""
    manifest = _write_manifest(tmp_path, _SAMPLE_RECORDS)
    _populate_diff_cache(fix_clone_root, _SAMPLE_RECORDS)

    result = await import_crossvul(
        db,
        manifest_path=manifest,
        fix_clone_root=fix_clone_root,
        languages=["python"],
    )

    assert result.imported_cves == 1  # Only CVE-2021-0001 is Python
    all_datasets = await db.list_datasets()
    for ds in all_datasets:
        meta = json.loads(ds.get("metadata_json") or "{}")
        assert meta.get("language", "").lower() == "python"


async def test_language_filter_case_insensitive(
    db: Database,
    tmp_path: Path,
    fix_clone_root: Path,
) -> None:
    """Language filter is case-insensitive."""
    manifest = _write_manifest(tmp_path, _SAMPLE_RECORDS)
    _populate_diff_cache(fix_clone_root, _SAMPLE_RECORDS)

    result = await import_crossvul(
        db,
        manifest_path=manifest,
        fix_clone_root=fix_clone_root,
        languages=["JAVA"],
    )
    assert result.imported_cves == 1


# ---------------------------------------------------------------------------
# 5. A CVE whose diff cache file is missing is skipped with a reason
# ---------------------------------------------------------------------------


async def test_missing_diff_cache_skipped_with_reason(
    db: Database,
    tmp_path: Path,
    fix_clone_root: Path,
) -> None:
    """A CVE with no cached diff and no network is skipped, not raised."""
    # Only populate cache for 4 of 5 CVEs; leave CVE-2021-0005 out
    _populate_diff_cache(
        fix_clone_root,
        _SAMPLE_RECORDS,
        skip_cve_ids={"CVE-2021-0005"},
    )
    manifest = _write_manifest(tmp_path, _SAMPLE_RECORDS)

    # Patch _fetch_github_patch to simulate no-network
    import sec_review_framework.ground_truth.crossvul_importer as mod

    def _no_network(project_url: str, commit_hash: str) -> str:
        raise RuntimeError(f"cache miss: no network for {commit_hash[:8]}")

    original_fetch = mod._fetch_github_patch
    mod._fetch_github_patch = _no_network  # type: ignore[assignment]
    try:
        result = await import_crossvul(
            db,
            manifest_path=manifest,
            fix_clone_root=fix_clone_root,
        )
    finally:
        mod._fetch_github_patch = original_fetch  # type: ignore[assignment]

    # 4 should import; 1 should be skipped
    assert result.imported_cves == 4
    assert result.skipped_cves == 1
    # The skipped CVE should have a reason
    assert sum(result.skipped_reasons.values()) == 1


# ---------------------------------------------------------------------------
# 6. A CVE with a malformed diff is appended to errors; import continues
# ---------------------------------------------------------------------------


async def test_malformed_diff_appended_to_errors(
    db: Database,
    tmp_path: Path,
    fix_clone_root: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A CVE with a diff that raises during parsing is recorded in errors."""
    import sec_review_framework.ground_truth.crossvul_importer as mod

    original_parse = mod._parse_hunk_ranges
    call_count = 0

    def patched_parse(diff_text: str) -> list[tuple[int, int]]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ValueError("Simulated malformed diff")
        return original_parse(diff_text)

    monkeypatch.setattr(mod, "_parse_hunk_ranges", patched_parse)

    _populate_diff_cache(fix_clone_root, _SAMPLE_RECORDS)
    manifest = _write_manifest(tmp_path, _SAMPLE_RECORDS)

    result = await import_crossvul(db, manifest_path=manifest, fix_clone_root=fix_clone_root)

    # Exactly one error; the other 4 CVEs import cleanly
    assert len(result.errors) == 1
    assert result.imported_cves == 4


# ---------------------------------------------------------------------------
# 7. source CHECK migration is idempotent across cvefixes + crossvul
# ---------------------------------------------------------------------------


async def test_source_check_migration_idempotent_crossvul(
    db: Database,
    manifest: Path,
    populated_cache: Path,
) -> None:
    """Running import_crossvul twice does not fail due to duplicate migration."""
    await import_crossvul(db, manifest_path=manifest, fix_clone_root=populated_cache)
    await import_crossvul(db, manifest_path=manifest, fix_clone_root=populated_cache)
    # No exception → idempotency holds


async def test_source_check_migration_cvefixes_then_crossvul(
    db: Database,
    manifest: Path,
    populated_cache: Path,
    tmp_path: Path,
) -> None:
    """Running CVEfixes importer then CrossVul importer in sequence is safe."""
    # Build a minimal synthetic CVEfixes SQLite
    import sqlite3

    cvefixes_db = tmp_path / "cvefixes.db"
    con = sqlite3.connect(str(cvefixes_db))
    con.executescript(
        """
        CREATE TABLE cve (cve_id TEXT PRIMARY KEY, severity TEXT, description TEXT);
        CREATE TABLE fixes (cve_id TEXT, hash TEXT);
        CREATE TABLE repository (repo_url TEXT PRIMARY KEY, language TEXT);
        CREATE TABLE commits (hash TEXT PRIMARY KEY, repo_url TEXT, parent_hash TEXT);
        CREATE TABLE file_change (hash TEXT, filename TEXT, diff TEXT, language TEXT);
        CREATE TABLE cwe_classification (cve_id TEXT, cwe_id TEXT);

        INSERT INTO cve VALUES ('CVE-2020-0001', 'HIGH', 'desc');
        INSERT INTO repository VALUES ('https://github.com/cv/repo', 'Python');
        INSERT INTO commits VALUES ('abc0001deadbeef', 'https://github.com/cv/repo', 'abc0000deadbeef');
        INSERT INTO fixes VALUES ('CVE-2020-0001', 'abc0001deadbeef');
        INSERT INTO file_change VALUES (
            'abc0001deadbeef', 'app.py',
            '@@ -1,1 +1,1 @@\n-bad()\n+good()\n', 'Python'
        );
        """
    )
    con.commit()
    con.close()

    from sec_review_framework.ground_truth.cvefixes_importer import import_cvefixes

    # Run CVEfixes first
    await import_cvefixes(db, cvefixes_db_path=cvefixes_db)

    # Now run CrossVul — must not fail even though 'cvefixes' CHECK is already wider
    result = await import_crossvul(db, manifest_path=manifest, fix_clone_root=populated_cache)
    assert result.imported_cves == 5
    assert result.errors == []

    # Verify both source values are present in the DB
    all_datasets = await db.list_datasets()
    sources: set[str] = set()
    for ds in all_datasets:
        labels = await db.list_dataset_labels(ds["name"])
        for lbl in labels:
            sources.add(lbl["source"])
    assert "cvefixes" in sources
    assert "crossvul" in sources


# ---------------------------------------------------------------------------
# 8. C CVE with two hunks produces ≥2 labels
# ---------------------------------------------------------------------------


async def test_c_cve_two_hunks_produces_two_labels(
    db: Database,
    manifest: Path,
    populated_cache: Path,
) -> None:
    """CVE-2021-0003 (C) uses a two-hunk diff → ≥2 labels."""
    await import_crossvul(db, manifest_path=manifest, fix_clone_root=populated_cache)

    all_datasets = await db.list_datasets()
    c_ds = [ds for ds in all_datasets if ds.get("cve_id") == "CVE-2021-0003"]
    assert c_ds, "CVE-2021-0003 dataset not found"
    labels = await db.list_dataset_labels(c_ds[0]["name"])
    assert len(labels) >= 2


# ---------------------------------------------------------------------------
# 9. max_cves cap
# ---------------------------------------------------------------------------


async def test_max_cves_cap(
    db: Database,
    manifest: Path,
    populated_cache: Path,
) -> None:
    """max_cves=2 imports at most 2 CVEs."""
    result = await import_crossvul(
        db,
        manifest_path=manifest,
        fix_clone_root=populated_cache,
        max_cves=2,
    )
    assert result.imported_cves <= 2


# ---------------------------------------------------------------------------
# 10. Missing manifest raises FileNotFoundError
# ---------------------------------------------------------------------------


async def test_missing_manifest_raises(db: Database, fix_clone_root: Path) -> None:
    """A manifest path that does not exist raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError, match="CrossVul manifest not found"):
        await import_crossvul(
            db,
            manifest_path=Path("/does/not/exist/crossvul.json"),
            fix_clone_root=fix_clone_root,
        )


# ---------------------------------------------------------------------------
# 11. Invalid JSON manifest raises ValueError
# ---------------------------------------------------------------------------


async def test_invalid_json_manifest_raises(
    db: Database, tmp_path: Path, fix_clone_root: Path
) -> None:
    """A manifest that is not valid JSON raises ValueError."""
    bad_manifest = tmp_path / "bad.json"
    bad_manifest.write_text("not json {{{{", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        await import_crossvul(
            db,
            manifest_path=bad_manifest,
            fix_clone_root=fix_clone_root,
        )


# ---------------------------------------------------------------------------
# 12. Manifest with non-list top-level raises ValueError
# ---------------------------------------------------------------------------


async def test_manifest_not_a_list_raises(
    db: Database, tmp_path: Path, fix_clone_root: Path
) -> None:
    """A manifest whose top-level value is not a JSON array raises ValueError."""
    obj_manifest = tmp_path / "obj.json"
    obj_manifest.write_text('{"key": "value"}', encoding="utf-8")
    with pytest.raises(ValueError, match="JSON array"):
        await import_crossvul(
            db,
            manifest_path=obj_manifest,
            fix_clone_root=fix_clone_root,
        )


# ---------------------------------------------------------------------------
# 13. Manifest with incomplete records are skipped silently
# ---------------------------------------------------------------------------


async def test_incomplete_records_skipped(
    db: Database, tmp_path: Path, fix_clone_root: Path
) -> None:
    """Records missing required fields are ignored during manifest parsing."""
    records = [
        {"cve_id": "CVE-NOLANG", "project_url": "https://github.com/x/y", "fix_commit": "abc123"},
        # Missing cve_id
        {"language": "Python", "project_url": "https://github.com/x/y", "fix_commit": "abc123"},
        # Valid record
        {
            "cve_id": "CVE-2021-0001",
            "language": "Python",
            "project_url": "https://github.com/example/pyapp",
            "fix_commit": "aaa00001deadbeef11223344",
        },
    ]
    manifest = _write_manifest(tmp_path, records)
    _populate_diff_cache(fix_clone_root, [records[2]])

    result = await import_crossvul(db, manifest_path=manifest, fix_clone_root=fix_clone_root)
    assert result.imported_cves == 1  # only the complete record


# ---------------------------------------------------------------------------
# 14. Duplicate records in manifest are deduplicated
# ---------------------------------------------------------------------------


async def test_duplicate_records_deduplicated(
    db: Database, tmp_path: Path, fix_clone_root: Path
) -> None:
    """Duplicate (cve_id, project_url, fix_commit) triples in the manifest count once."""
    rec = _SAMPLE_RECORDS[0]
    records = [rec, rec, rec]  # same record three times
    manifest = _write_manifest(tmp_path, records)
    _populate_diff_cache(fix_clone_root, [rec])

    result = await import_crossvul(db, manifest_path=manifest, fix_clone_root=fix_clone_root)
    assert result.imported_cves == 1
    assert result.imported_datasets == 1


# ---------------------------------------------------------------------------
# 15. No-hunk diff produces one label at line 1 (graceful fallback)
# ---------------------------------------------------------------------------


async def test_no_hunk_diff_produces_fallback_label(
    db: Database, tmp_path: Path, fix_clone_root: Path
) -> None:
    """A diff with no @@ hunk headers produces one label at line 1."""
    rec = _SAMPLE_RECORDS[0]  # Python CVE
    _populate_diff_cache(
        fix_clone_root, [rec], diff_override={"CVE-2021-0001": _NO_HUNK_DIFF}
    )
    manifest = _write_manifest(tmp_path, [rec])

    result = await import_crossvul(db, manifest_path=manifest, fix_clone_root=fix_clone_root)
    assert result.imported_cves == 1

    all_datasets = await db.list_datasets()
    assert all_datasets
    labels = await db.list_dataset_labels(all_datasets[0]["name"])
    assert len(labels) >= 1
    assert labels[0]["line_start"] == 1


# ---------------------------------------------------------------------------
# 16. source='crossvul' in labels
# ---------------------------------------------------------------------------


async def test_labels_have_crossvul_source(
    db: Database,
    manifest: Path,
    populated_cache: Path,
) -> None:
    """All imported labels carry source='crossvul'."""
    await import_crossvul(db, manifest_path=manifest, fix_clone_root=populated_cache)

    all_datasets = await db.list_datasets()
    for ds in all_datasets:
        labels = await db.list_dataset_labels(ds["name"])
        for lbl in labels:
            assert lbl["source"] == "crossvul"


# ---------------------------------------------------------------------------
# 17. _parse_manifest handles alternate field names (aliases)
# ---------------------------------------------------------------------------


def test_parse_manifest_alias_fields(tmp_path: Path) -> None:
    """_parse_manifest accepts alternate field names for core fields."""
    records = [
        {
            "id": "CVE-2021-ALIAS",
            "lang": "Python",
            "repo_url": "https://github.com/x/repo",
            "commit_hash": "aaa00001deadbeef",
        }
    ]
    manifest = tmp_path / "alias.json"
    manifest.write_text(json.dumps(records), encoding="utf-8")
    parsed = _parse_manifest(manifest)
    assert len(parsed) == 1
    assert parsed[0]["cve_id"] == "CVE-2021-ALIAS"
    assert parsed[0]["language"] == "Python"
    assert parsed[0]["project_url"] == "https://github.com/x/repo"
    assert parsed[0]["fix_commit"] == "aaa00001deadbeef"


# ---------------------------------------------------------------------------
# 18. _split_diff_by_file correctly splits a multi-file diff
# ---------------------------------------------------------------------------


def test_split_diff_by_file_two_files() -> None:
    """_split_diff_by_file correctly splits a two-file unified diff."""
    diff = textwrap.dedent(
        """\
        diff --git a/foo.py b/foo.py
        index aabbcc..ddeeff 100644
        --- a/foo.py
        +++ b/foo.py
        @@ -1,1 +1,1 @@
        -bad()
        +good()
        diff --git a/bar.py b/bar.py
        index 112233..445566 100644
        --- a/bar.py
        +++ b/bar.py
        @@ -5,1 +5,1 @@
        -old
        +new
        """
    )
    sections = _split_diff_by_file(diff)
    assert len(sections) == 2
    filenames = [s[0] for s in sections]
    assert "foo.py" in filenames
    assert "bar.py" in filenames


# ---------------------------------------------------------------------------
# 19. _project_slug helper (re-used from cvefixes_importer)
# ---------------------------------------------------------------------------


def test_project_slug_github_url() -> None:
    assert _project_slug("https://github.com/owner/repo") == "owner-repo"


def test_project_slug_trailing_slash() -> None:
    assert _project_slug("https://github.com/owner/repo/") == "owner-repo"


# ---------------------------------------------------------------------------
# 20. Empty diff is skipped
# ---------------------------------------------------------------------------


async def test_empty_diff_skipped(
    db: Database, tmp_path: Path, fix_clone_root: Path
) -> None:
    """A CVE whose cached diff is empty is skipped with reason 'empty_diff'."""
    rec = _SAMPLE_RECORDS[0]
    _populate_diff_cache(fix_clone_root, [rec], diff_override={"CVE-2021-0001": "   \n"})
    manifest = _write_manifest(tmp_path, [rec])

    result = await import_crossvul(db, manifest_path=manifest, fix_clone_root=fix_clone_root)
    assert result.imported_cves == 0
    assert result.skipped_cves == 1
    assert "empty_diff" in result.skipped_reasons
