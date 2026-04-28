"""Unit tests for the Bandit functional test importer.

Uses a synthetic mini Bandit repo layout — no real Bandit clone needed.
"""

from __future__ import annotations

import json
import warnings
from pathlib import Path

import pytest
import pytest_asyncio

from sec_review_framework.db import Database
from sec_review_framework.ground_truth.bandit_functional_importer import (
    BanditFunctionalImportResult,
    _extract_test_methods,
    _rule_to_cwe,
    import_bandit_functional,
)

# ---------------------------------------------------------------------------
# Fake test_functional.py content
#
# 6 test methods covering:
#   - positive with explicit expect_issues (line numbers)     → test_sql_injection
#   - positive with multiple issues (same file)               → test_subprocess_calls
#   - positive with only severity counts (no expect_issues)   → test_yaml_load
#   - negative (zero expected issues)                         → test_nosec_example
#   - negative (empty expect dict)                            → test_tmpfile_safe
#   - positive with an unmappable rule                        → test_unknown_rule
# ---------------------------------------------------------------------------

_TEST_FUNCTIONAL_PY = """\
import unittest


class FunctionalTests(unittest.TestCase):

    def setUp(self):
        super().setUp()

    def test_sql_injection(self):
        '''SQL injection example - expect findings.'''
        self.run_example('example_sql.py')
        self.check_example(
            'example_sql.py',
            expect={
                'SEVERITY': {'HIGH': 2, 'MEDIUM': 0, 'LOW': 0},
                'CONFIDENCE': {'HIGH': 2, 'MEDIUM': 0, 'LOW': 0},
            },
            expect_issues=[
                {'test_id': 'B608', 'lineno': 10},
                {'test_id': 'B608', 'lineno': 20},
            ],
        )

    def test_subprocess_calls(self):
        '''Subprocess with shell=True - multiple rules.'''
        self.run_example('subprocess_shell.py')
        self.check_example(
            'subprocess_shell.py',
            expect={
                'SEVERITY': {'HIGH': 1, 'MEDIUM': 1, 'LOW': 0},
                'CONFIDENCE': {'HIGH': 2, 'MEDIUM': 0, 'LOW': 0},
            },
            expect_issues=[
                {'test_id': 'B602', 'lineno': 5},
                {'test_id': 'B404', 'lineno': 1},
            ],
        )

    def test_yaml_load(self):
        '''yaml.load is dangerous - positive from severity counts only.'''
        self.run_example('yaml_load.py')
        self.check_example(
            'yaml_load.py',
            expect={
                'SEVERITY': {'HIGH': 0, 'MEDIUM': 1, 'LOW': 0},
                'CONFIDENCE': {'HIGH': 0, 'MEDIUM': 1, 'LOW': 0},
            },
        )

    def test_nosec_example(self):
        '''nosec comment suppresses all warnings - negative.'''
        self.run_example('nosec.py')
        self.check_example(
            'nosec.py',
            expect={
                'SEVERITY': {'HIGH': 0, 'MEDIUM': 0, 'LOW': 0},
                'CONFIDENCE': {'HIGH': 0, 'MEDIUM': 0, 'LOW': 0},
            },
        )

    def test_tmpfile_safe(self):
        '''tempfile.mkstemp is safe - negative.'''
        self.run_example('tmpfile_mkstemp.py')
        self.check_example(
            'tmpfile_mkstemp.py',
            expect={
                'SEVERITY': {'HIGH': 0, 'MEDIUM': 0, 'LOW': 0},
                'CONFIDENCE': {'HIGH': 0, 'MEDIUM': 0, 'LOW': 0},
            },
        )

    def test_unknown_rule(self):
        '''An unknown rule ID - should warn but not fail.'''
        self.run_example('example_zzz.py')
        self.check_example(
            'example_zzz.py',
            expect={
                'SEVERITY': {'HIGH': 1, 'MEDIUM': 0, 'LOW': 0},
                'CONFIDENCE': {'HIGH': 1, 'MEDIUM': 0, 'LOW': 0},
            },
            expect_issues=[
                {'test_id': 'B999', 'lineno': 7},
            ],
        )
"""

# Example file contents (one per test method)
_EXAMPLE_FILES: dict[str, str] = {
    "example_sql.py": "import sqlite3\n" + "pass\n" * 9 + "sqlite3.execute('SELECT * FROM t WHERE id=' + x)\n" + "pass\n" * 9 + "conn.execute('DROP TABLE ' + tbl)\n",
    "subprocess_shell.py": "import subprocess\n" + "pass\n" * 3 + "subprocess.Popen(cmd, shell=True)\n",
    "yaml_load.py": "import yaml\nyaml.load(data)\n",
    "nosec.py": "import subprocess\nsubprocess.Popen(cmd, shell=True)  # nosec\n",
    "tmpfile_mkstemp.py": "import tempfile\ntempfile.mkstemp()\n",
    "example_zzz.py": "pass\n" * 6 + "do_something_risky()\n",
}

_PINNED_COMMIT = "0ada3af0c6a01d22a8c52f8d4b03cc7e73a3516"
_DATASET_NAME = "bandit-functional-tests"


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
def bandit_repo(tmp_path: Path) -> Path:
    """Synthetic Bandit repo tree with examples/ and test_functional.py."""
    repo = tmp_path / "bandit"
    repo.mkdir()

    # examples/ dir
    examples = repo / "examples"
    examples.mkdir()
    for fname, content in _EXAMPLE_FILES.items():
        (examples / fname).write_text(content)

    # tests/functional/ dir
    tests_func = repo / "tests" / "functional"
    tests_func.mkdir(parents=True)
    (tests_func / "test_functional.py").write_text(_TEST_FUNCTIONAL_PY)

    return repo


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _run(db: Database, repo: Path) -> BanditFunctionalImportResult:
    return await import_bandit_functional(
        db,
        bandit_repo_path=repo,
        origin_commit=_PINNED_COMMIT,
    )


# ---------------------------------------------------------------------------
# Tests: parser unit tests (no DB needed)
# ---------------------------------------------------------------------------


def test_extract_test_methods_count():
    """Parser finds 6 test methods in the fake test_functional.py."""
    methods = _extract_test_methods(_TEST_FUNCTIONAL_PY)
    assert len(methods) == 6


def test_extract_positives_and_negatives():
    """Parser correctly identifies positive and negative polarity."""
    methods = _extract_test_methods(_TEST_FUNCTIONAL_PY)
    by_name = {m["method_name"]: m for m in methods}

    assert by_name["test_sql_injection"]["is_positive"] is True
    assert by_name["test_subprocess_calls"]["is_positive"] is True
    assert by_name["test_yaml_load"]["is_positive"] is True  # severity-count positive
    assert by_name["test_nosec_example"]["is_positive"] is False
    assert by_name["test_tmpfile_safe"]["is_positive"] is False


def test_extract_sql_issues_with_linenos():
    """SQL injection test has two issues with explicit line numbers."""
    methods = _extract_test_methods(_TEST_FUNCTIONAL_PY)
    by_name = {m["method_name"]: m for m in methods}

    sql_method = by_name["test_sql_injection"]
    assert len(sql_method["issues"]) == 2
    assert all(iss["rule_id"] == "B608" for iss in sql_method["issues"])
    linenos = {iss["lineno"] for iss in sql_method["issues"]}
    assert linenos == {10, 20}


def test_extract_yaml_load_no_issues():
    """yaml.load test has no expect_issues but is still positive (severity count)."""
    methods = _extract_test_methods(_TEST_FUNCTIONAL_PY)
    by_name = {m["method_name"]: m for m in methods}

    yaml_method = by_name["test_yaml_load"]
    assert yaml_method["is_positive"] is True
    assert yaml_method["issues"] == []  # no explicit issue list


def test_extract_example_file():
    """Parser extracts the correct example filename for each method."""
    methods = _extract_test_methods(_TEST_FUNCTIONAL_PY)
    by_name = {m["method_name"]: m for m in methods}

    assert by_name["test_sql_injection"]["example_file"] == "example_sql.py"
    assert by_name["test_nosec_example"]["example_file"] == "nosec.py"


# ---------------------------------------------------------------------------
# Tests: CWE mapping
# ---------------------------------------------------------------------------


def test_common_rule_mappings():
    """Common Bandit rules map to their documented CWEs."""
    assert _rule_to_cwe("B608") == "CWE-89"   # SQL injection
    assert _rule_to_cwe("B301") == "CWE-502"  # pickle → deserialization
    assert _rule_to_cwe("B311") == "CWE-330"  # random → insuf. randomness
    assert _rule_to_cwe("B501") == "CWE-295"  # no cert validation
    assert _rule_to_cwe("B602") == "CWE-78"   # shell=True → OS command inj.


def test_unmappable_rule_warns_and_returns_fallback():
    """An unknown rule ID warns but returns CWE-1 (fallback)."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        cwe = _rule_to_cwe("B999")

    assert cwe == "CWE-1"
    assert len(w) == 1
    assert "no CWE mapping" in str(w[0].message)
    assert "B999" in str(w[0].message)


def test_rule_ids_are_case_insensitive():
    """Rule lookup is case-insensitive (b608 == B608)."""
    assert _rule_to_cwe("b608") == _rule_to_cwe("B608")


# ---------------------------------------------------------------------------
# Tests: import counts
# ---------------------------------------------------------------------------


async def test_positive_negative_split(db: Database, bandit_repo: Path):
    """Correct positive and negative counts are returned."""
    result = await _run(db, bandit_repo)

    # Positives: sql_injection (2 issues), subprocess_calls (2 issues),
    #            yaml_load (1 whole-file), unknown_rule (1)  → 6 label rows
    # Negatives: nosec_example, tmpfile_safe → 2
    assert result.positives_count == 6
    assert result.negatives_count == 2
    assert result.dataset_name == _DATASET_NAME
    assert result.errors == []


async def test_positive_labels_stored(db: Database, bandit_repo: Path):
    """Positive labels are persisted to dataset_labels."""
    await _run(db, bandit_repo)
    labels = await db.list_dataset_labels(_DATASET_NAME)
    assert len(labels) == 6


async def test_negative_labels_stored(db: Database, bandit_repo: Path):
    """Negative labels are persisted to dataset_negative_labels."""
    await _run(db, bandit_repo)
    neg_labels = await db.list_dataset_negative_labels(_DATASET_NAME)
    assert len(neg_labels) == 2


# ---------------------------------------------------------------------------
# Tests: line ranges
# ---------------------------------------------------------------------------


async def test_line_specific_issues_produce_point_labels(
    db: Database, bandit_repo: Path
):
    """Issues with explicit lineno get line_start == line_end == lineno."""
    await _run(db, bandit_repo)
    labels = await db.list_dataset_labels(_DATASET_NAME)

    # B608 at lineno 10 and 20 in example_sql.py
    sql_labels = [
        lbl
        for lbl in labels
        if lbl["file_path"] == "examples/example_sql.py"
        and lbl["cwe_id"] == "CWE-89"
    ]
    assert len(sql_labels) == 2
    for lbl in sql_labels:
        assert lbl["line_start"] == lbl["line_end"]


async def test_line_unspecified_produces_whole_file_label(
    db: Database, bandit_repo: Path
):
    """Issues without lineno get line_start=1 and line_end=<file_lines>."""
    await _run(db, bandit_repo)
    labels = await db.list_dataset_labels(_DATASET_NAME)

    # yaml_load.py has no expect_issues — whole-file label
    yaml_labels = [
        lbl for lbl in labels if "yaml" in lbl["file_path"]
    ]
    assert len(yaml_labels) == 1
    yaml_lbl = yaml_labels[0]
    assert yaml_lbl["line_start"] == 1
    yaml_lines = len(_EXAMPLE_FILES["yaml_load.py"].splitlines())
    assert yaml_lbl["line_end"] == yaml_lines


# ---------------------------------------------------------------------------
# Tests: CWE mapping in stored labels
# ---------------------------------------------------------------------------


async def test_sql_injection_labels_have_correct_cwe(
    db: Database, bandit_repo: Path
):
    """B608 labels have CWE-89."""
    await _run(db, bandit_repo)
    labels = await db.list_dataset_labels(_DATASET_NAME)
    sql_labels = [
        lbl for lbl in labels if "example_sql" in lbl["file_path"]
    ]
    for lbl in sql_labels:
        assert lbl["cwe_id"] == "CWE-89"
        assert lbl["vuln_class"] == "sqli"


async def test_source_is_benchmark(db: Database, bandit_repo: Path):
    """All labels (positive and negative) have source='benchmark'."""
    await _run(db, bandit_repo)
    pos_labels = await db.list_dataset_labels(_DATASET_NAME)
    neg_labels = await db.list_dataset_negative_labels(_DATASET_NAME)
    for lbl in pos_labels:
        assert lbl["source"] == "benchmark"
    for lbl in neg_labels:
        assert lbl["source"] == "benchmark"


# ---------------------------------------------------------------------------
# Tests: idempotency
# ---------------------------------------------------------------------------


async def test_idempotency_no_duplicates(db: Database, bandit_repo: Path):
    """Running the importer twice yields identical row counts."""
    await _run(db, bandit_repo)
    await _run(db, bandit_repo)

    pos_labels = await db.list_dataset_labels(_DATASET_NAME)
    neg_labels = await db.list_dataset_negative_labels(_DATASET_NAME)

    assert len(pos_labels) == 6
    assert len(neg_labels) == 2


async def test_idempotency_returns_same_counts(db: Database, bandit_repo: Path):
    """Both runs return the same counts in BanditFunctionalImportResult."""
    r1 = await _run(db, bandit_repo)
    r2 = await _run(db, bandit_repo)

    assert r1.positives_count == r2.positives_count
    assert r1.negatives_count == r2.negatives_count
    assert r1.dataset_version == r2.dataset_version


# ---------------------------------------------------------------------------
# Tests: datasets row metadata
# ---------------------------------------------------------------------------


async def test_datasets_row_created(db: Database, bandit_repo: Path):
    """datasets row has kind='git', correct origin_url, and NULL cve_id."""
    await _run(db, bandit_repo)
    row = await db.get_dataset(_DATASET_NAME)

    assert row is not None
    assert row["kind"] == "git"
    assert row["cve_id"] is None
    assert row["origin_url"] == "https://github.com/PyCQA/bandit"
    assert row["origin_commit"] == _PINNED_COMMIT


async def test_datasets_row_metadata(db: Database, bandit_repo: Path):
    """metadata_json contains the expected benchmark keys."""
    await _run(db, bandit_repo)
    row = await db.get_dataset(_DATASET_NAME)
    assert row is not None

    meta = json.loads(row["metadata_json"])
    assert meta["language"] == "python"
    assert meta["iteration"] == "per-test-file"
    assert meta["benchmark"] == "bandit-functional"
    assert meta["test_glob"] == "examples/**/*.py"


# ---------------------------------------------------------------------------
# Tests: unmappable rule does not fail import
# ---------------------------------------------------------------------------


async def test_unknown_rule_does_not_fail_import(db: Database, bandit_repo: Path):
    """B999 (unmappable) emits a warning but the import still succeeds."""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        result = await _run(db, bandit_repo)

    # Import must succeed with no errors
    assert result.errors == []
    assert result.positives_count > 0

    # At least one warning must mention B999
    warning_texts = [str(warning.message) for warning in w]
    assert any("B999" in t for t in warning_texts)


# ---------------------------------------------------------------------------
# Tests: missing test_functional.py
# ---------------------------------------------------------------------------


async def test_missing_driver_returns_error(db: Database, tmp_path: Path):
    """If test_functional.py is absent, the import returns an error, not an exception."""
    empty_repo = tmp_path / "empty_bandit"
    empty_repo.mkdir()
    (empty_repo / "examples").mkdir()

    result = await import_bandit_functional(
        db,
        bandit_repo_path=empty_repo,
        origin_commit=_PINNED_COMMIT,
    )

    assert len(result.errors) >= 1
    assert any("test_functional.py" in e[1] for e in result.errors)
