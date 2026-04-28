"""Unit tests for the OWASP BenchmarkPython importer."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from sec_review_framework.db import Database
from sec_review_framework.ground_truth.benchmark_python_importer import (
    ImportResult,
    import_benchmark_python,
)

# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------

# 10 rows across 4 categories: pathtraver, sqli, xss, cmdi
# Mix of positives and negatives.
_CSV_CONTENT = """\
# test name, category, real vulnerability, cwe, Benchmark version: 0.1, 2026-01-9
BenchmarkTest00001,pathtraver,true,22
BenchmarkTest00002,pathtraver,true,22
BenchmarkTest00003,pathtraver,false,22
BenchmarkTest00004,sqli,true,89
BenchmarkTest00005,sqli,false,89
BenchmarkTest00006,xss,true,79
BenchmarkTest00007,xss,false,79
BenchmarkTest00008,cmdi,true,78
BenchmarkTest00009,cmdi,false,78
BenchmarkTest00010,cmdi,true,78
"""

# File contents: each file has a distinct number of lines so we can assert
# line_end correctly.
_FILE_CONTENTS: dict[str, str] = {
    "BenchmarkTest00001.py": "line1\nline2\nline3\n",           # 3 lines
    "BenchmarkTest00002.py": "line1\nline2\n",                  # 2 lines
    "BenchmarkTest00003.py": "pass\n",                          # 1 line
    "BenchmarkTest00004.py": "import sqlite3\npass\n",          # 2 lines
    "BenchmarkTest00005.py": "pass\n",                          # 1 line
    "BenchmarkTest00006.py": "x = 1\ny = 2\nz = 3\nw = 4\n",  # 4 lines
    "BenchmarkTest00007.py": "pass\n",                          # 1 line
    "BenchmarkTest00008.py": "import os\npass\n",               # 2 lines
    "BenchmarkTest00009.py": "pass\n",                          # 1 line
    "BenchmarkTest00010.py": "a\nb\nc\nd\ne\n",                 # 5 lines
}

# Expected line counts for the positive tests.
_POSITIVE_TESTS = {
    "BenchmarkTest00001": 3,
    "BenchmarkTest00002": 2,
    "BenchmarkTest00004": 2,
    "BenchmarkTest00006": 4,
    "BenchmarkTest00008": 2,
    "BenchmarkTest00010": 5,
}

# Expected negative tests.
_NEGATIVE_TESTS = {
    "BenchmarkTest00003",
    "BenchmarkTest00005",
    "BenchmarkTest00007",
    "BenchmarkTest00009",
}

# Expected CWE IDs per test (derived from CSV).
_EXPECTED_CWES = {
    "BenchmarkTest00001": "CWE-22",
    "BenchmarkTest00002": "CWE-22",
    "BenchmarkTest00003": "CWE-22",
    "BenchmarkTest00004": "CWE-89",
    "BenchmarkTest00005": "CWE-89",
    "BenchmarkTest00006": "CWE-79",
    "BenchmarkTest00007": "CWE-79",
    "BenchmarkTest00008": "CWE-78",
    "BenchmarkTest00009": "CWE-78",
    "BenchmarkTest00010": "CWE-78",
}

# Expected vuln_class per test.
_EXPECTED_VULN_CLASS = {
    "BenchmarkTest00001": "pathtraver",
    "BenchmarkTest00002": "pathtraver",
    "BenchmarkTest00003": "pathtraver",
    "BenchmarkTest00004": "sqli",
    "BenchmarkTest00005": "sqli",
    "BenchmarkTest00006": "xss",
    "BenchmarkTest00007": "xss",
    "BenchmarkTest00008": "cmdi",
    "BenchmarkTest00009": "cmdi",
    "BenchmarkTest00010": "cmdi",
}

_PINNED_COMMIT = "9f0d34945a8872220957a4f99cb2721cd9036a6b"
_DATASET_NAME = "owasp-benchmark-python-v0.1"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> Database:
    """Fresh in-process SQLite database for each test."""
    database = Database(tmp_path / "test.db")
    await database.init()
    return database


@pytest.fixture
def csv_path(tmp_path: Path) -> Path:
    """Write the fixture CSV to a temp file and return its path."""
    p = tmp_path / "expectedresults-0.1.csv"
    p.write_text(_CSV_CONTENT)
    return p


@pytest.fixture
def testcode_dir(tmp_path: Path) -> Path:
    """Create fake testcode/ directory with stub .py files."""
    d = tmp_path / "testcode"
    d.mkdir()
    for fname, content in _FILE_CONTENTS.items():
        (d / fname).write_text(content)
    return d


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _run_importer(db: Database, csv_path: Path, testcode_dir: Path) -> ImportResult:
    return await import_benchmark_python(
        db,
        origin_commit=_PINNED_COMMIT,
        csv_path=csv_path,
        testcode_dir=testcode_dir,
    )


# ---------------------------------------------------------------------------
# Tests: counts
# ---------------------------------------------------------------------------


async def test_positive_and_negative_counts(
    db: Database, csv_path: Path, testcode_dir: Path
):
    """Correct positive and negative counts are returned."""
    result = await _run_importer(db, csv_path, testcode_dir)

    assert result.positives_count == len(_POSITIVE_TESTS)  # 6
    assert result.negatives_count == len(_NEGATIVE_TESTS)   # 4
    assert result.dataset_name == _DATASET_NAME


# ---------------------------------------------------------------------------
# Tests: positive label properties
# ---------------------------------------------------------------------------


async def test_positive_labels_source(
    db: Database, csv_path: Path, testcode_dir: Path
):
    """Every positive label has source='benchmark'."""
    await _run_importer(db, csv_path, testcode_dir)
    labels = await db.list_dataset_labels(_DATASET_NAME)
    for label in labels:
        assert label["source"] == "benchmark"


async def test_positive_labels_cwe_ids(
    db: Database, csv_path: Path, testcode_dir: Path
):
    """Each positive label has the right cwe_id."""
    await _run_importer(db, csv_path, testcode_dir)
    labels = await db.list_dataset_labels(_DATASET_NAME)
    label_by_ref = {lbl["source_ref"]: lbl for lbl in labels}

    for test_name in _POSITIVE_TESTS:
        assert test_name in label_by_ref, f"Missing label for {test_name}"
        assert label_by_ref[test_name]["cwe_id"] == _EXPECTED_CWES[test_name]


async def test_positive_labels_vuln_class(
    db: Database, csv_path: Path, testcode_dir: Path
):
    """Each positive label has the right vuln_class."""
    await _run_importer(db, csv_path, testcode_dir)
    labels = await db.list_dataset_labels(_DATASET_NAME)
    label_by_ref = {lbl["source_ref"]: lbl for lbl in labels}

    for test_name in _POSITIVE_TESTS:
        assert label_by_ref[test_name]["vuln_class"] == _EXPECTED_VULN_CLASS[test_name]


async def test_positive_labels_line_range(
    db: Database, csv_path: Path, testcode_dir: Path
):
    """Positive labels have line_start=1 and line_end matching the file's line count."""
    await _run_importer(db, csv_path, testcode_dir)
    labels = await db.list_dataset_labels(_DATASET_NAME)
    label_by_ref = {lbl["source_ref"]: lbl for lbl in labels}

    for test_name, expected_lines in _POSITIVE_TESTS.items():
        label = label_by_ref[test_name]
        assert label["line_start"] == 1, f"{test_name}: expected line_start=1"
        assert label["line_end"] == expected_lines, (
            f"{test_name}: expected line_end={expected_lines}, got {label['line_end']}"
        )


# ---------------------------------------------------------------------------
# Tests: negative label properties
# ---------------------------------------------------------------------------


async def test_negative_labels_source(
    db: Database, csv_path: Path, testcode_dir: Path
):
    """Every negative label has source='benchmark'."""
    await _run_importer(db, csv_path, testcode_dir)
    neg_labels = await db.list_dataset_negative_labels(_DATASET_NAME)
    for label in neg_labels:
        assert label["source"] == "benchmark"


async def test_negative_labels_cwe_and_vuln_class(
    db: Database, csv_path: Path, testcode_dir: Path
):
    """Each negative label has the right cwe_id and vuln_class."""
    await _run_importer(db, csv_path, testcode_dir)
    neg_labels = await db.list_dataset_negative_labels(_DATASET_NAME)
    label_by_ref = {lbl["source_ref"]: lbl for lbl in neg_labels}

    for test_name in _NEGATIVE_TESTS:
        assert test_name in label_by_ref, f"Missing negative label for {test_name}"
        assert label_by_ref[test_name]["cwe_id"] == _EXPECTED_CWES[test_name]
        assert label_by_ref[test_name]["vuln_class"] == _EXPECTED_VULN_CLASS[test_name]


# ---------------------------------------------------------------------------
# Tests: idempotency
# ---------------------------------------------------------------------------


async def test_idempotency_no_duplicates(
    db: Database, csv_path: Path, testcode_dir: Path
):
    """Running the importer twice yields identical row counts, no duplicates."""
    await _run_importer(db, csv_path, testcode_dir)
    await _run_importer(db, csv_path, testcode_dir)

    pos_labels = await db.list_dataset_labels(_DATASET_NAME)
    neg_labels = await db.list_dataset_negative_labels(_DATASET_NAME)

    assert len(pos_labels) == len(_POSITIVE_TESTS)
    assert len(neg_labels) == len(_NEGATIVE_TESTS)


async def test_idempotency_returns_same_result(
    db: Database, csv_path: Path, testcode_dir: Path
):
    """Both runs return the same ImportResult."""
    r1 = await _run_importer(db, csv_path, testcode_dir)
    r2 = await _run_importer(db, csv_path, testcode_dir)

    assert r1.positives_count == r2.positives_count
    assert r1.negatives_count == r2.negatives_count
    assert r1.dataset_name == r2.dataset_name
    assert r1.dataset_version == r2.dataset_version


# ---------------------------------------------------------------------------
# Tests: unknown category
# ---------------------------------------------------------------------------


async def test_unknown_category_raises(
    db: Database, tmp_path: Path, testcode_dir: Path
):
    """An unrecognised CSV category raises ValueError immediately."""
    bad_csv = tmp_path / "bad.csv"
    bad_csv.write_text(
        "# header\nBenchmarkTest00001,unknowncat,true,999\n"
    )
    with pytest.raises(ValueError, match="Unknown BenchmarkPython category"):
        await import_benchmark_python(
            db,
            origin_commit=_PINNED_COMMIT,
            csv_path=bad_csv,
            testcode_dir=testcode_dir,
        )


# ---------------------------------------------------------------------------
# Tests: datasets row
# ---------------------------------------------------------------------------


async def test_datasets_row_created(
    db: Database, csv_path: Path, testcode_dir: Path
):
    """A datasets row is created with kind='git' and cve_id IS NULL."""
    await _run_importer(db, csv_path, testcode_dir)
    row = await db.get_dataset(_DATASET_NAME)

    assert row is not None
    assert row["kind"] == "git"
    assert row["cve_id"] is None
    assert row["origin_url"] == "https://github.com/OWASP-Benchmark/BenchmarkPython"
    assert row["origin_commit"] == _PINNED_COMMIT


async def test_datasets_row_metadata_json(
    db: Database, csv_path: Path, testcode_dir: Path
):
    """The metadata_json is correctly structured."""
    await _run_importer(db, csv_path, testcode_dir)
    row = await db.get_dataset(_DATASET_NAME)
    assert row is not None

    meta = json.loads(row["metadata_json"])
    assert meta["benchmark"] == "owasp-python"
    assert meta["version"] == "0.1"
    assert meta["iteration"] == "per-test-file"
    assert meta["test_glob"] == "testcode/BenchmarkTest*.py"


# ---------------------------------------------------------------------------
# Tests: file line-count accuracy
# ---------------------------------------------------------------------------


async def test_line_end_matches_file_length(
    db: Database, csv_path: Path, testcode_dir: Path
):
    """line_end for each positive matches the actual number of lines in its file."""
    await _run_importer(db, csv_path, testcode_dir)
    labels = await db.list_dataset_labels(_DATASET_NAME)
    label_by_ref = {lbl["source_ref"]: lbl for lbl in labels}

    for test_name, content in _FILE_CONTENTS.items():
        stem = test_name.replace(".py", "")
        if stem not in _POSITIVE_TESTS:
            continue
        expected_lines = len(content.splitlines())
        assert label_by_ref[stem]["line_end"] == expected_lines, (
            f"{stem}: file has {expected_lines} lines but label has "
            f"line_end={label_by_ref[stem]['line_end']}"
        )
