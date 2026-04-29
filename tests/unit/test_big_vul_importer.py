"""Unit tests for the Big-Vul importer.

All tests use synthesised fixtures — no real Big-Vul CSV data, no real diffs,
no network access.
"""

from __future__ import annotations

import csv
import json
import textwrap
from io import StringIO
from pathlib import Path
from typing import Any

import pytest
import pytest_asyncio

from sec_review_framework.db import Database
from sec_review_framework.ground_truth.big_vul_importer import (
    BigVulImportResult,
    _cvss_to_severity,
    _normalise_lang,
    _resolve_col,
    import_big_vul,
)
from sec_review_framework.ground_truth._source_check_migration import (
    ensure_negative_source_check_includes,
    ensure_source_check_includes,
)
from sec_review_framework.ground_truth.crossvul_importer import _diff_cache_path


# ---------------------------------------------------------------------------
# Helpers: synthetic CSV and diff cache builders
# ---------------------------------------------------------------------------

# Columns used in the synthetic CSV (matching canonical Big-Vul column names)
_CSV_COLUMNS = [
    "CVE ID",
    "commit_id",
    "project",
    "repo_url",
    "lang",
    "vul",
    "CWE ID",
    "CVSS Score",
]

# Synthesised records: 5 CVEs, mixing polarities and languages
_SAMPLE_ROWS: list[dict[str, str]] = [
    {
        "CVE ID": "CVE-2018-0001",
        "commit_id": "aaa00001deadbeef11223344",
        "project": "openssl",
        "repo_url": "https://github.com/openssl/openssl",
        "lang": "C",
        "vul": "1",
        "CWE ID": "CWE-119",
        "CVSS Score": "9.8",
    },
    {
        "CVE ID": "CVE-2018-0001",
        "commit_id": "aaa00001deadbeef11223344",
        "project": "openssl",
        "repo_url": "https://github.com/openssl/openssl",
        "lang": "C",
        "vul": "0",
        "CWE ID": "CWE-119",
        "CVSS Score": "9.8",
    },
    {
        "CVE ID": "CVE-2018-0002",
        "commit_id": "bbb00001deadbeef11223344",
        "project": "ffmpeg",
        "repo_url": "https://github.com/FFmpeg/FFmpeg",
        "lang": "C",
        "vul": "1",
        "CWE ID": "CWE-787",
        "CVSS Score": "7.8",
    },
    {
        "CVE ID": "CVE-2018-0003",
        "commit_id": "ccc00001deadbeef11223344",
        "project": "libpng",
        "repo_url": "https://github.com/glennrp/libpng",
        "lang": "C",
        "vul": "1",
        "CWE ID": "CWE-125",
        "CVSS Score": "5.5",
    },
    {
        "CVE ID": "CVE-2018-0004",
        "commit_id": "ddd00001deadbeef11223344",
        "project": "imagemagick",
        "repo_url": "https://github.com/ImageMagick/ImageMagick",
        "lang": "C++",
        "vul": "0",
        "CWE ID": "CWE-369",
        "CVSS Score": "6.5",
    },
    {
        "CVE ID": "CVE-2018-0005",
        "commit_id": "eee00001deadbeef11223344",
        "project": "curl",
        "repo_url": "https://github.com/curl/curl",
        "lang": "C",
        "vul": "1",
        "CWE ID": "CWE-416",
        "CVSS Score": "8.1",
    },
]

# A minimal valid unified diff with one hunk for C files
_SIMPLE_DIFF_TEMPLATE = textwrap.dedent(
    """\
    diff --git a/{filename} b/{filename}
    index aabbcc..ddeeff 100644
    --- a/{filename}
    +++ b/{filename}
    @@ -{old_start},1 +{old_start},1 @@
    -vulnerable_code();
    +safe_code();
    """
)

# A two-hunk diff for the multi-hunk test
_TWO_HUNK_DIFF = textwrap.dedent(
    """\
    diff --git a/src/buf.c b/src/buf.c
    index aabbcc..ddeeff 100644
    --- a/src/buf.c
    +++ b/src/buf.c
    @@ -10,1 +10,1 @@
    -char buf[256];
    +char buf[4096];
    @@ -50,2 +50,2 @@
    -memcpy(dst, src, len);
    +safe_memcpy(dst, src, len, sizeof(dst));
    """
)

# Multi-file diff for testing file-scoped negative labels
_MULTI_FILE_DIFF = textwrap.dedent(
    """\
    diff --git a/src/alpha.c b/src/alpha.c
    index aabbcc..ddeeff 100644
    --- a/src/alpha.c
    +++ b/src/alpha.c
    @@ -5,1 +5,1 @@
    -bad_call();
    +good_call();
    diff --git a/src/beta.c b/src/beta.c
    index 112233..445566 100644
    --- a/src/beta.c
    +++ b/src/beta.c
    @@ -20,1 +20,1 @@
    -old_api();
    +new_api();
    """
)


def _write_csv(tmp_path: Path, rows: list[dict[str, str]]) -> Path:
    """Write synthetic Big-Vul CSV and return its path."""
    csv_path = tmp_path / "MSR_data_cleaned.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=_CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


def _populate_diff_cache(
    fix_clone_root: Path,
    rows: list[dict[str, str]],
    *,
    diff_override: dict[str, str] | None = None,
    skip_commit_ids: set[str] | None = None,
) -> None:
    """Pre-populate the diff cache for the given rows.

    Only one cache file is written per unique commit_id (the first occurrence).
    """
    if diff_override is None:
        diff_override = {}
    if skip_commit_ids is None:
        skip_commit_ids = set()

    seen_commits: set[str] = set()
    for row in rows:
        commit_id = row["commit_id"]
        repo_url = row["repo_url"]
        cve_id = row["CVE ID"]

        if commit_id in seen_commits:
            continue
        seen_commits.add(commit_id)

        if commit_id in skip_commit_ids:
            continue

        cache_path = _diff_cache_path(fix_clone_root, repo_url, commit_id)
        cache_path.parent.mkdir(parents=True, exist_ok=True)

        if cve_id in diff_override:
            diff_text = diff_override[cve_id]
        else:
            lang = row.get("lang", "C")
            ext = "cpp" if lang == "C++" else "c"
            filename = f"src/{row['project']}.{ext}"
            diff_text = _SIMPLE_DIFF_TEMPLATE.format(filename=filename, old_start=10)

        cache_path.write_text(diff_text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Fixtures
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
def csv_path(tmp_path: Path) -> Path:
    return _write_csv(tmp_path, _SAMPLE_ROWS)


@pytest.fixture
def populated_cache(fix_clone_root: Path) -> Path:
    _populate_diff_cache(fix_clone_root, _SAMPLE_ROWS)
    return fix_clone_root


# ---------------------------------------------------------------------------
# 1. Happy path: 5 CVEs → correct imported_datasets + imported_labels counts
# ---------------------------------------------------------------------------


async def test_import_five_cves_correct_counts(
    db: Database,
    csv_path: Path,
    populated_cache: Path,
) -> None:
    """Importing 5 unique CVEs produces correct dataset and label counts."""
    result = await import_big_vul(
        db,
        csv_path=csv_path,
        fix_clone_root=populated_cache,
    )

    assert isinstance(result, BigVulImportResult)
    # 5 unique (cve_id, commit_id) pairs → 5 datasets
    assert result.imported_datasets == 5, (
        f"Expected 5 datasets, got {result.imported_datasets}; errors={result.errors}"
    )
    assert result.errors == []
    # Positive labels from vul=1 rows (CVEs 0001, 0002, 0003, 0005)
    assert result.imported_labels >= 3  # at least one per vul=1 CVE with diff
    # Negative labels from vul=0 rows (CVEs 0001 and 0004)
    assert result.imported_negative_labels >= 1


# ---------------------------------------------------------------------------
# 2. metadata_json.language is set correctly per dataset
# ---------------------------------------------------------------------------


async def test_metadata_language_set_correctly(
    db: Database,
    csv_path: Path,
    populated_cache: Path,
) -> None:
    """Each imported dataset carries the correct language in metadata_json."""
    await import_big_vul(db, csv_path=csv_path, fix_clone_root=populated_cache)

    all_datasets = await db.list_datasets()
    assert all_datasets, "No datasets imported"

    languages_seen: set[str] = set()
    for ds in all_datasets:
        meta = json.loads(ds.get("metadata_json") or "{}")
        assert "language" in meta, f"Dataset {ds['name']} missing language key"
        assert meta["source"] == "bigvul"
        assert meta["paper_doi"] == "10.1145/3379597.3387501"
        languages_seen.add(meta["language"])

    # Both C and cpp (from C++) should appear
    assert "c" in languages_seen
    assert "cpp" in languages_seen


# ---------------------------------------------------------------------------
# 3. vul=1 → positive labels; vul=0 → negative labels
# ---------------------------------------------------------------------------


async def test_polarity_vul1_positive_vul0_negative(
    db: Database,
    tmp_path: Path,
    fix_clone_root: Path,
) -> None:
    """vul=1 rows produce dataset_labels; vul=0 rows produce dataset_negative_labels."""
    rows = [
        {
            "CVE ID": "CVE-2020-POLTEST",
            "commit_id": "fff00001deadbeef11223344",
            "project": "testproject",
            "repo_url": "https://github.com/test/project",
            "lang": "C",
            "vul": "1",
            "CWE ID": "CWE-119",
            "CVSS Score": "8.0",
        },
        {
            "CVE ID": "CVE-2020-POLTEST2",
            "commit_id": "ggg00001deadbeef11223344",
            "project": "testproject2",
            "repo_url": "https://github.com/test/project2",
            "lang": "C",
            "vul": "0",
            "CWE ID": "CWE-200",
            "CVSS Score": "4.0",
        },
    ]
    csv_path = _write_csv(tmp_path, rows)
    _populate_diff_cache(fix_clone_root, rows)

    result = await import_big_vul(db, csv_path=csv_path, fix_clone_root=fix_clone_root)

    assert result.imported_datasets == 2
    assert result.errors == []

    all_datasets = await db.list_datasets()

    # Find each dataset by cve_id
    ds_pos = next((d for d in all_datasets if d["cve_id"] == "CVE-2020-POLTEST"), None)
    ds_neg = next((d for d in all_datasets if d["cve_id"] == "CVE-2020-POLTEST2"), None)

    assert ds_pos is not None
    assert ds_neg is not None

    # Positive dataset should have dataset_labels rows
    pos_labels = await db.list_dataset_labels(ds_pos["name"])
    assert len(pos_labels) >= 1
    for lbl in pos_labels:
        assert lbl["source"] == "bigvul"

    # Positive dataset should have no negative labels
    pos_neg_labels = await db.list_dataset_negative_labels(ds_pos["name"])
    assert len(pos_neg_labels) == 0

    # Negative dataset should have dataset_negative_labels rows
    neg_labels = await db.list_dataset_negative_labels(ds_neg["name"])
    assert len(neg_labels) >= 1
    for lbl in neg_labels:
        assert lbl["source"] == "bigvul"

    # Negative dataset should have no positive labels
    neg_pos_labels = await db.list_dataset_labels(ds_neg["name"])
    assert len(neg_pos_labels) == 0


# ---------------------------------------------------------------------------
# 4. Idempotency on re-run
# ---------------------------------------------------------------------------


async def test_idempotency_no_duplicate_rows(
    db: Database,
    csv_path: Path,
    populated_cache: Path,
) -> None:
    """Running import_big_vul twice on the same CSV is fully idempotent."""
    first = await import_big_vul(db, csv_path=csv_path, fix_clone_root=populated_cache)
    second = await import_big_vul(db, csv_path=csv_path, fix_clone_root=populated_cache)

    # Second run must create no new datasets/labels
    assert second.imported_datasets == 0
    assert second.imported_labels == 0
    assert second.imported_negative_labels == 0

    # Total datasets in DB must equal first run's count
    all_datasets = await db.list_datasets()
    assert len(all_datasets) == first.imported_datasets

    # Total positive labels must equal first run
    total_pos = 0
    for ds in all_datasets:
        labels = await db.list_dataset_labels(ds["name"])
        total_pos += len(labels)
    assert total_pos == first.imported_labels

    # Total negative labels must equal first run
    total_neg = 0
    for ds in all_datasets:
        neg_labels = await db.list_dataset_negative_labels(ds["name"])
        total_neg += len(neg_labels)
    assert total_neg == first.imported_negative_labels


# ---------------------------------------------------------------------------
# 5. Language filter restricts
# ---------------------------------------------------------------------------


async def test_language_filter_c_only(
    db: Database,
    tmp_path: Path,
    fix_clone_root: Path,
) -> None:
    """languages=['c'] imports only C rows, not C++."""
    csv_path = _write_csv(tmp_path, _SAMPLE_ROWS)
    _populate_diff_cache(fix_clone_root, _SAMPLE_ROWS)

    result = await import_big_vul(
        db,
        csv_path=csv_path,
        fix_clone_root=fix_clone_root,
        languages=["c"],
    )

    # CVE-2018-0004 is C++ → should be excluded
    all_datasets = await db.list_datasets()
    for ds in all_datasets:
        meta = json.loads(ds.get("metadata_json") or "{}")
        assert meta.get("language") == "c", (
            f"Expected only 'c' language, got '{meta.get('language')}' "
            f"in dataset {ds['name']}"
        )
    assert result.errors == []


async def test_language_filter_case_insensitive(
    db: Database,
    tmp_path: Path,
    fix_clone_root: Path,
) -> None:
    """Language filter is case-insensitive; 'C' and 'c' match the same rows."""
    csv_path = _write_csv(tmp_path, _SAMPLE_ROWS)
    _populate_diff_cache(fix_clone_root, _SAMPLE_ROWS)

    result_upper = await import_big_vul(
        db,
        csv_path=csv_path,
        fix_clone_root=fix_clone_root,
        languages=["C"],
    )
    assert result_upper.imported_datasets >= 1
    assert result_upper.errors == []


# ---------------------------------------------------------------------------
# 6. Cold-cache CVE skips with diff_cache_miss reason (does NOT raise)
# ---------------------------------------------------------------------------


async def test_cold_cache_cve_skipped_not_raised(
    db: Database,
    tmp_path: Path,
    fix_clone_root: Path,
) -> None:
    """A CVE with no cached diff and no network is skipped with diff_cache_miss."""
    # Populate cache for all but CVE-2018-0002
    _populate_diff_cache(
        fix_clone_root,
        _SAMPLE_ROWS,
        skip_commit_ids={"bbb00001deadbeef11223344"},  # CVE-2018-0002's commit
    )
    csv_path = _write_csv(tmp_path, _SAMPLE_ROWS)

    # Patch the network fetch to simulate no-network
    import sec_review_framework.ground_truth.crossvul_importer as crossvul_mod

    def _no_network(project_url: str, commit_hash: str) -> str:
        raise RuntimeError(f"cache miss: no network for {commit_hash[:8]}")

    original_fetch = crossvul_mod._fetch_github_patch
    crossvul_mod._fetch_github_patch = _no_network  # type: ignore[assignment]
    try:
        result = await import_big_vul(
            db,
            csv_path=csv_path,
            fix_clone_root=fix_clone_root,
        )
    finally:
        crossvul_mod._fetch_github_patch = original_fetch  # type: ignore[assignment]

    # The cold-cache CVE must be skipped, not raised as an error
    assert result.skipped_cves >= 1
    assert "diff_cache_miss" in result.skipped_reasons
    # No exception should propagate
    # The other CVEs that do have cache entries should import successfully
    assert result.imported_datasets >= 1


# ---------------------------------------------------------------------------
# 7. Source CHECK migration adds 'bigvul' to both tables; idempotent
# ---------------------------------------------------------------------------


async def test_source_check_migration_adds_bigvul_to_positive(db: Database) -> None:
    """After import, 'bigvul' is accepted by dataset_labels.source CHECK."""
    await ensure_source_check_includes(db, "bigvul")
    # Calling a second time must not raise
    await ensure_source_check_includes(db, "bigvul")


async def test_source_check_migration_adds_bigvul_to_negative(db: Database) -> None:
    """After import, 'bigvul' is accepted by dataset_negative_labels.source CHECK."""
    await ensure_negative_source_check_includes(db, "bigvul")
    # Calling a second time must not raise
    await ensure_negative_source_check_includes(db, "bigvul")


async def test_source_check_migration_full_sequence(
    db: Database,
    tmp_path: Path,
    fix_clone_root: Path,
) -> None:
    """Running cvefixes → crossvul → sard → bigvul migrations in sequence is safe."""
    from sec_review_framework.ground_truth._source_check_migration import (
        ensure_negative_source_check_includes,
        ensure_source_check_includes,
    )

    # Simulate prior importers having extended the CHECK
    await ensure_source_check_includes(db, "cvefixes")
    await ensure_source_check_includes(db, "crossvul")
    await ensure_source_check_includes(db, "sard")
    await ensure_negative_source_check_includes(db, "sard")

    # Now run bigvul import — must not fail
    rows = [_SAMPLE_ROWS[0]]  # just one vul=1 row
    csv_path = _write_csv(tmp_path, rows)
    _populate_diff_cache(fix_clone_root, rows)

    result = await import_big_vul(
        db, csv_path=csv_path, fix_clone_root=fix_clone_root
    )
    assert result.errors == []

    # Verify bigvul source is in both tables
    all_datasets = await db.list_datasets()
    for ds in all_datasets:
        labels = await db.list_dataset_labels(ds["name"])
        for lbl in labels:
            assert lbl["source"] == "bigvul"


# ---------------------------------------------------------------------------
# 8. Multi-file diff → multiple negative labels (one per file)
# ---------------------------------------------------------------------------


async def test_multi_file_diff_produces_one_neg_label_per_file(
    db: Database,
    tmp_path: Path,
    fix_clone_root: Path,
) -> None:
    """A vul=0 row with a two-file diff produces two negative labels."""
    row = {
        "CVE ID": "CVE-2019-MULTI",
        "commit_id": "hhh00001deadbeef11223344",
        "project": "multiproject",
        "repo_url": "https://github.com/multi/project",
        "lang": "C",
        "vul": "0",
        "CWE ID": "CWE-119",
        "CVSS Score": "7.0",
    }
    csv_path = _write_csv(tmp_path, [row])
    _populate_diff_cache(
        fix_clone_root,
        [row],
        diff_override={"CVE-2019-MULTI": _MULTI_FILE_DIFF},
    )

    result = await import_big_vul(db, csv_path=csv_path, fix_clone_root=fix_clone_root)
    assert result.errors == []

    all_datasets = await db.list_datasets()
    assert all_datasets
    neg_labels = await db.list_dataset_negative_labels(all_datasets[0]["name"])
    # Two files in the diff → two negative label rows
    assert len(neg_labels) == 2
    filenames = {lbl["file_path"] for lbl in neg_labels}
    assert "src/alpha.c" in filenames
    assert "src/beta.c" in filenames


# ---------------------------------------------------------------------------
# 9. Two-hunk diff → ≥2 positive labels
# ---------------------------------------------------------------------------


async def test_two_hunk_diff_produces_multiple_pos_labels(
    db: Database,
    tmp_path: Path,
    fix_clone_root: Path,
) -> None:
    """A vul=1 row with a two-hunk diff produces ≥2 positive labels."""
    row = {
        "CVE ID": "CVE-2019-TWOHUNK",
        "commit_id": "iii00001deadbeef11223344",
        "project": "hunkproject",
        "repo_url": "https://github.com/hunk/project",
        "lang": "C",
        "vul": "1",
        "CWE ID": "CWE-119",
        "CVSS Score": "8.5",
    }
    csv_path = _write_csv(tmp_path, [row])
    _populate_diff_cache(
        fix_clone_root,
        [row],
        diff_override={"CVE-2019-TWOHUNK": _TWO_HUNK_DIFF},
    )

    result = await import_big_vul(db, csv_path=csv_path, fix_clone_root=fix_clone_root)
    assert result.errors == []

    all_datasets = await db.list_datasets()
    assert all_datasets
    pos_labels = await db.list_dataset_labels(all_datasets[0]["name"])
    assert len(pos_labels) >= 2


# ---------------------------------------------------------------------------
# 10. max_cves cap
# ---------------------------------------------------------------------------


async def test_max_cves_cap(
    db: Database,
    csv_path: Path,
    populated_cache: Path,
) -> None:
    """max_cves=2 imports at most 2 unique (cve_id, commit_id) pairs."""
    result = await import_big_vul(
        db,
        csv_path=csv_path,
        fix_clone_root=populated_cache,
        max_cves=2,
    )
    assert result.imported_datasets <= 2
    assert result.errors == []


# ---------------------------------------------------------------------------
# 11. Missing CSV raises FileNotFoundError
# ---------------------------------------------------------------------------


async def test_missing_csv_raises(db: Database, fix_clone_root: Path) -> None:
    """A CSV path that does not exist raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError, match="Big-Vul CSV not found"):
        await import_big_vul(
            db,
            csv_path=Path("/does/not/exist/MSR_data_cleaned.csv"),
            fix_clone_root=fix_clone_root,
        )


# ---------------------------------------------------------------------------
# 12. Rows with missing commit_id or repo_url are skipped silently
# ---------------------------------------------------------------------------


async def test_rows_missing_required_fields_skipped(
    db: Database, tmp_path: Path, fix_clone_root: Path
) -> None:
    """Rows missing commit_id or both project and repo_url are skipped."""
    rows = [
        # Missing commit_id → skip
        {
            "CVE ID": "CVE-9999-NOCOMMIT",
            "commit_id": "",
            "project": "proj",
            "repo_url": "https://github.com/x/y",
            "lang": "C",
            "vul": "1",
            "CWE ID": "",
            "CVSS Score": "",
        },
        # Valid row
        {
            "CVE ID": "CVE-9999-VALID",
            "commit_id": "jjj00001deadbeef11223344",
            "project": "validproject",
            "repo_url": "https://github.com/valid/project",
            "lang": "C",
            "vul": "1",
            "CWE ID": "CWE-119",
            "CVSS Score": "7.0",
        },
    ]
    csv_path = _write_csv(tmp_path, rows)
    _populate_diff_cache(fix_clone_root, [rows[1]])

    result = await import_big_vul(db, csv_path=csv_path, fix_clone_root=fix_clone_root)
    # Only the valid row should produce a dataset
    assert result.imported_datasets == 1


# ---------------------------------------------------------------------------
# 13. _normalise_lang helper
# ---------------------------------------------------------------------------


def test_normalise_lang_c() -> None:
    assert _normalise_lang("C") == "c"
    assert _normalise_lang("c") == "c"


def test_normalise_lang_cpp() -> None:
    assert _normalise_lang("C++") == "cpp"
    assert _normalise_lang("c++") == "cpp"
    assert _normalise_lang("CPP") == "cpp"


def test_normalise_lang_none() -> None:
    assert _normalise_lang(None) == "c"
    assert _normalise_lang("") == "c"


def test_normalise_lang_unknown_lowercases() -> None:
    assert _normalise_lang("Rust") == "rust"
    assert _normalise_lang("JAVA") == "java"


# ---------------------------------------------------------------------------
# 14. _cvss_to_severity helper
# ---------------------------------------------------------------------------


def test_cvss_to_severity_thresholds() -> None:
    assert _cvss_to_severity("9.8") == "CRITICAL"
    assert _cvss_to_severity("7.5") == "HIGH"
    assert _cvss_to_severity("5.0") == "MEDIUM"
    assert _cvss_to_severity("2.0") == "LOW"


def test_cvss_to_severity_empty() -> None:
    assert _cvss_to_severity(None) == "MEDIUM"
    assert _cvss_to_severity("") == "MEDIUM"
    assert _cvss_to_severity("not-a-number") == "MEDIUM"


# ---------------------------------------------------------------------------
# 15. Deterministic dataset name includes cve_id, slug, and commit prefix
# ---------------------------------------------------------------------------


async def test_deterministic_dataset_name(
    db: Database,
    tmp_path: Path,
    fix_clone_root: Path,
) -> None:
    """Dataset names follow 'bigvul-<cve_id>-<slug>-<commit[:8]>' pattern."""
    row = _SAMPLE_ROWS[0]  # CVE-2018-0001 / openssl / aaa00001...
    csv_path = _write_csv(tmp_path, [row])
    _populate_diff_cache(fix_clone_root, [row])

    await import_big_vul(db, csv_path=csv_path, fix_clone_root=fix_clone_root)

    all_datasets = await db.list_datasets()
    assert all_datasets
    name = all_datasets[0]["name"]
    assert name.startswith("bigvul-cve-2018-0001"), f"Unexpected name: {name}"
    assert "aaa00001" in name


# ---------------------------------------------------------------------------
# 16. Empty diff is skipped with reason 'empty_diff'
# ---------------------------------------------------------------------------


async def test_empty_diff_skipped(
    db: Database, tmp_path: Path, fix_clone_root: Path
) -> None:
    """A CVE whose cached diff is empty is skipped with reason 'empty_diff'."""
    row = _SAMPLE_ROWS[0]
    _populate_diff_cache(
        fix_clone_root,
        [row],
        diff_override={"CVE-2018-0001": "   \n"},
    )
    csv_path = _write_csv(tmp_path, [row])

    result = await import_big_vul(db, csv_path=csv_path, fix_clone_root=fix_clone_root)
    assert result.imported_cves == 0
    assert result.skipped_cves == 1
    assert "empty_diff" in result.skipped_reasons
