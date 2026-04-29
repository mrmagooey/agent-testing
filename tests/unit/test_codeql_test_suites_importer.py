"""Unit tests for the CodeQL test-suite importer.

Uses a synthetic mini codeql layout — no real github/codeql clone needed.

Layout:
    <tmp>/
        java/ql/test/query-tests/Security/
            CWE-079/Foo/
                Foo.ql
                Foo.expected          (2 hits in TestVulnerable.java)
                TestVulnerable.java
                TestSafe.java         (no hits — negative)
            CWE-089/SqlInjection/
                SqlInjection.ql
                SqlInjection.expected (1 hit in BadQuery.java)
                BadQuery.java
                SafeQuery.java
        python/ql/test/query-tests/Security/
            CWE-022/PathTraversal/
                PathTraversal.ql
                PathTraversal.expected (2 hits in vuln.py)
                vuln.py
                safe.py
            CWE-327/WeakCrypto/
                WeakCrypto.ql
                WeakCrypto.expected   (empty — all files are negatives)
                safe_crypto.py
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import pytest_asyncio

from sec_review_framework.db import Database
from sec_review_framework.ground_truth.codeql_test_suites_importer import (
    CodeQLImportResult,
    _normalise_cwe_id,
    _parse_expected_file,
    import_codeql_test_suites,
)

# ---------------------------------------------------------------------------
# .expected file content for each query
# ---------------------------------------------------------------------------

# Java / CWE-079 / Foo: 2 positive hits in TestVulnerable.java, 0 in TestSafe.java
_JAVA_FOO_EXPECTED = """\
TestVulnerable.java:10:1:10:30:XSS vulnerability
TestVulnerable.java:20:5:20:40:Another XSS vulnerability
"""

# Java / CWE-089 / SqlInjection: 1 hit in BadQuery.java, 0 in SafeQuery.java
_JAVA_SQL_EXPECTED = """\
BadQuery.java:15:1:15:50:SQL injection
"""

# Python / CWE-022 / PathTraversal: 2 hits in vuln.py, 0 in safe.py
_PYTHON_PATH_EXPECTED = """\
vuln.py:7:1:7:40:Path traversal vulnerability
vuln.py:14:3:14:45:Another path traversal
"""

# Python / CWE-327 / WeakCrypto: empty expected (all files are negatives)
_PYTHON_CRYPTO_EXPECTED = """\
"""

# ---------------------------------------------------------------------------
# Synthetic source file content
# ---------------------------------------------------------------------------

_JAVA_VULNERABLE = "public class TestVulnerable {\n" + "// placeholder\n" * 18 + "    // vuln at line 10\n" + "// placeholder\n" * 9 + "    // vuln at line 20\n" + "}\n"
_JAVA_SAFE = "public class TestSafe {\n    // nothing to see here\n}\n"
_JAVA_BAD_QUERY = "class BadQuery {\n" + "// line\n" * 13 + "    // sqli at line 15\n" + "}\n"
_JAVA_SAFE_QUERY = "class SafeQuery {\n    // parameterised\n}\n"
_PYTHON_VULN = "import os\n# ...\n" + "pass\n" * 4 + "open(user_input)  # vuln line 7\n" + "pass\n" * 6 + "# ...\n" + "os.path.join('/', user_input)  # vuln line 14\n"
_PYTHON_SAFE = "import os\nos.path.join('/safe', 'static')\n"
_PYTHON_SAFE_CRYPTO = "import hashlib\nhashlib.sha256(b'data').hexdigest()\n"

_PINNED_COMMIT = "abcdef1234567890abcdef1234567890abcdef12"
_COMMIT_SHORT = _PINNED_COMMIT[:8]  # "abcdef12"


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
def codeql_repo(tmp_path: Path) -> Path:
    """Synthetic codeql repo tree with java and python test suites."""
    repo = tmp_path / "codeql"
    repo.mkdir()

    # ---- Java / CWE-079 / Foo ----
    java_foo = repo / "java" / "ql" / "test" / "query-tests" / "Security" / "CWE-079" / "Foo"
    java_foo.mkdir(parents=True)
    (java_foo / "Foo.ql").write_text("// placeholder query\n")
    (java_foo / "Foo.expected").write_text(_JAVA_FOO_EXPECTED)
    (java_foo / "TestVulnerable.java").write_text(_JAVA_VULNERABLE)
    (java_foo / "TestSafe.java").write_text(_JAVA_SAFE)

    # ---- Java / CWE-089 / SqlInjection ----
    java_sql = repo / "java" / "ql" / "test" / "query-tests" / "Security" / "CWE-089" / "SqlInjection"
    java_sql.mkdir(parents=True)
    (java_sql / "SqlInjection.ql").write_text("// placeholder query\n")
    (java_sql / "SqlInjection.expected").write_text(_JAVA_SQL_EXPECTED)
    (java_sql / "BadQuery.java").write_text(_JAVA_BAD_QUERY)
    (java_sql / "SafeQuery.java").write_text(_JAVA_SAFE_QUERY)

    # ---- Python / CWE-022 / PathTraversal ----
    py_path = repo / "python" / "ql" / "test" / "query-tests" / "Security" / "CWE-022" / "PathTraversal"
    py_path.mkdir(parents=True)
    (py_path / "PathTraversal.ql").write_text("// placeholder query\n")
    (py_path / "PathTraversal.expected").write_text(_PYTHON_PATH_EXPECTED)
    (py_path / "vuln.py").write_text(_PYTHON_VULN)
    (py_path / "safe.py").write_text(_PYTHON_SAFE)

    # ---- Python / CWE-327 / WeakCrypto ----
    py_crypto = repo / "python" / "ql" / "test" / "query-tests" / "Security" / "CWE-327" / "WeakCrypto"
    py_crypto.mkdir(parents=True)
    (py_crypto / "WeakCrypto.ql").write_text("// placeholder query\n")
    (py_crypto / "WeakCrypto.expected").write_text(_PYTHON_CRYPTO_EXPECTED)
    (py_crypto / "safe_crypto.py").write_text(_PYTHON_SAFE_CRYPTO)

    return repo


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


async def _run(
    db: Database,
    repo: Path,
    *,
    languages: list[str] | None = None,
    cwe_filter: list[str] | None = None,
) -> CodeQLImportResult:
    return await import_codeql_test_suites(
        db,
        codeql_repo_path=repo,
        origin_commit=_PINNED_COMMIT,
        languages=languages,
        cwe_filter=cwe_filter,
    )


# ---------------------------------------------------------------------------
# Unit tests: helpers
# ---------------------------------------------------------------------------


def test_normalise_cwe_id_strips_leading_zeros():
    assert _normalise_cwe_id("CWE-079") == "CWE-79"
    assert _normalise_cwe_id("CWE-089") == "CWE-89"
    assert _normalise_cwe_id("CWE-022") == "CWE-22"
    assert _normalise_cwe_id("CWE-327") == "CWE-327"


def test_normalise_cwe_id_no_op_when_no_leading_zeros():
    assert _normalise_cwe_id("CWE-79") == "CWE-79"
    assert _normalise_cwe_id("CWE-89") == "CWE-89"


def test_parse_expected_file_two_hits(tmp_path: Path):
    ef = tmp_path / "Foo.expected"
    ef.write_text(_JAVA_FOO_EXPECTED)
    hits, parseable, err = _parse_expected_file(ef)
    assert parseable
    assert err is None
    assert "TestVulnerable.java" in hits
    assert len(hits["TestVulnerable.java"]) == 2
    assert set(hits["TestVulnerable.java"]) == {10, 20}


def test_parse_expected_file_empty_is_parseable(tmp_path: Path):
    ef = tmp_path / "WeakCrypto.expected"
    ef.write_text(_PYTHON_CRYPTO_EXPECTED)
    hits, parseable, err = _parse_expected_file(ef)
    assert parseable
    assert err is None
    assert hits == {}


def test_parse_expected_file_comments_skipped(tmp_path: Path):
    ef = tmp_path / "Commented.expected"
    ef.write_text(
        "# This is a comment\n"
        "// Another comment\n"
        "vuln.py:5:1:5:20:Finding\n"
    )
    hits, parseable, err = _parse_expected_file(ef)
    assert parseable
    assert "vuln.py" in hits
    assert hits["vuln.py"] == [5]


def test_parse_expected_file_continuation_lines_skipped(tmp_path: Path):
    ef = tmp_path / "Cont.expected"
    ef.write_text(
        "vuln.py:5:1:5:20:Finding\n"
        "| continuation of message\n"
        "safe.py:10:1:10:10:Another\n"
    )
    hits, parseable, err = _parse_expected_file(ef)
    assert parseable
    assert hits["vuln.py"] == [5]
    assert hits["safe.py"] == [10]


def test_parse_expected_file_malformed_skips_query(tmp_path: Path):
    """A file where > 50% of lines are unrecognised is unparseable."""
    ef = tmp_path / "Bad.expected"
    # 3 garbage lines, 1 valid line → > 50% unrecognised
    ef.write_text(
        "garbage line with no colon structure at all\n"
        "another garbage without any recognisable format\n"
        "and one more garbage line here\n"
        "vuln.py:5:1:5:20:Valid finding\n"
    )
    hits, parseable, err = _parse_expected_file(ef)
    assert not parseable
    assert err is not None


def test_parse_expected_file_quoted_path(tmp_path: Path):
    ef = tmp_path / "Quoted.expected"
    ef.write_text('"File With Spaces.java":12:1:12:30:Finding\n')
    hits, parseable, err = _parse_expected_file(ef)
    assert parseable
    assert "File With Spaces.java" in hits
    assert hits["File With Spaces.java"] == [12]


# ---------------------------------------------------------------------------
# Tests: positive count
# ---------------------------------------------------------------------------


async def test_positive_count_java(db: Database, codeql_repo: Path):
    """Java positives: 2 from Foo + 1 from SqlInjection = 3 total."""
    result = await _run(db, codeql_repo, languages=["java"])
    assert result.positives_count == 3


async def test_positive_count_python(db: Database, codeql_repo: Path):
    """Python positives: 2 from PathTraversal, 0 from WeakCrypto = 2."""
    result = await _run(db, codeql_repo, languages=["python"])
    assert result.positives_count == 2


# ---------------------------------------------------------------------------
# Tests: negative count
# ---------------------------------------------------------------------------


async def test_negative_count_java(db: Database, codeql_repo: Path):
    """Java negatives: TestSafe.java (CWE-079) + SafeQuery.java (CWE-089) = 2."""
    result = await _run(db, codeql_repo, languages=["java"])
    assert result.negatives_count == 2


async def test_negative_count_python(db: Database, codeql_repo: Path):
    """Python negatives: safe.py (path traversal) + safe_crypto.py (weak crypto) = 2."""
    result = await _run(db, codeql_repo, languages=["python"])
    assert result.negatives_count == 2


# ---------------------------------------------------------------------------
# Tests: one dataset per language with correct metadata
# ---------------------------------------------------------------------------


async def test_one_dataset_per_language_java(db: Database, codeql_repo: Path):
    """Java import creates a dataset named codeql-test-suites-java-<commit8>."""
    await _run(db, codeql_repo, languages=["java"])
    expected_name = f"codeql-test-suites-java-{_COMMIT_SHORT}"
    row = await db.get_dataset(expected_name)
    assert row is not None
    assert row["kind"] == "git"
    assert row["origin_url"] == "https://github.com/github/codeql"
    assert row["origin_commit"] == _PINNED_COMMIT
    assert row["cve_id"] is None


async def test_metadata_json_language_java(db: Database, codeql_repo: Path):
    """metadata_json contains language='java' for the java dataset."""
    await _run(db, codeql_repo, languages=["java"])
    row = await db.get_dataset(f"codeql-test-suites-java-{_COMMIT_SHORT}")
    assert row is not None
    meta = json.loads(row["metadata_json"])
    assert meta["language"] == "java"
    assert meta["benchmark"] == "codeql-test-suites"
    assert meta["iteration"] == "per-test-file"


async def test_metadata_json_language_python(db: Database, codeql_repo: Path):
    """metadata_json contains language='python' for the python dataset."""
    await _run(db, codeql_repo, languages=["python"])
    row = await db.get_dataset(f"codeql-test-suites-python-{_COMMIT_SHORT}")
    assert row is not None
    meta = json.loads(row["metadata_json"])
    assert meta["language"] == "python"


async def test_two_separate_datasets_for_two_languages(db: Database, codeql_repo: Path):
    """Running with two languages creates two separate dataset rows."""
    await _run(db, codeql_repo, languages=["java", "python"])
    java_row = await db.get_dataset(f"codeql-test-suites-java-{_COMMIT_SHORT}")
    python_row = await db.get_dataset(f"codeql-test-suites-python-{_COMMIT_SHORT}")
    assert java_row is not None
    assert python_row is not None


# ---------------------------------------------------------------------------
# Tests: labels stored in DB
# ---------------------------------------------------------------------------


async def test_positive_labels_stored_java(db: Database, codeql_repo: Path):
    """Positive labels are persisted to dataset_labels for java."""
    await _run(db, codeql_repo, languages=["java"])
    labels = await db.list_dataset_labels(f"codeql-test-suites-java-{_COMMIT_SHORT}")
    assert len(labels) == 3


async def test_negative_labels_stored_java(db: Database, codeql_repo: Path):
    """Negative labels are persisted to dataset_negative_labels for java."""
    await _run(db, codeql_repo, languages=["java"])
    neg = await db.list_dataset_negative_labels(f"codeql-test-suites-java-{_COMMIT_SHORT}")
    assert len(neg) == 2


async def test_positive_labels_have_correct_cwe_xss(db: Database, codeql_repo: Path):
    """Foo query labels have CWE-79 (XSS) and vuln_class='xss'."""
    await _run(db, codeql_repo, languages=["java"])
    labels = await db.list_dataset_labels(
        f"codeql-test-suites-java-{_COMMIT_SHORT}",
        cwe="CWE-79",
    )
    assert len(labels) == 2
    for lbl in labels:
        assert lbl["vuln_class"] == "xss"


async def test_positive_labels_have_correct_cwe_sqli(db: Database, codeql_repo: Path):
    """SqlInjection query labels have CWE-89 and vuln_class='sqli'."""
    await _run(db, codeql_repo, languages=["java"])
    labels = await db.list_dataset_labels(
        f"codeql-test-suites-java-{_COMMIT_SHORT}",
        cwe="CWE-89",
    )
    assert len(labels) == 1
    assert labels[0]["vuln_class"] == "sqli"


async def test_positive_labels_source_is_codeql(db: Database, codeql_repo: Path):
    """All positive labels have source='codeql'."""
    await _run(db, codeql_repo, languages=["java"])
    labels = await db.list_dataset_labels(f"codeql-test-suites-java-{_COMMIT_SHORT}")
    for lbl in labels:
        assert lbl["source"] == "codeql"


async def test_negative_labels_source_is_codeql(db: Database, codeql_repo: Path):
    """All negative labels have source='codeql'."""
    await _run(db, codeql_repo, languages=["java"])
    neg = await db.list_dataset_negative_labels(f"codeql-test-suites-java-{_COMMIT_SHORT}")
    for lbl in neg:
        assert lbl["source"] == "codeql"


async def test_positive_label_line_numbers(db: Database, codeql_repo: Path):
    """Positive labels carry the exact line numbers from .expected."""
    await _run(db, codeql_repo, languages=["java"])
    labels = await db.list_dataset_labels(
        f"codeql-test-suites-java-{_COMMIT_SHORT}",
        cwe="CWE-79",
    )
    line_starts = {lbl["line_start"] for lbl in labels}
    assert 10 in line_starts
    assert 20 in line_starts
    # Point labels: line_start == line_end
    for lbl in labels:
        assert lbl["line_start"] == lbl["line_end"]


# ---------------------------------------------------------------------------
# Tests: idempotency
# ---------------------------------------------------------------------------


async def test_idempotency_no_duplicates(db: Database, codeql_repo: Path):
    """Running the importer twice yields the same row counts."""
    await _run(db, codeql_repo, languages=["java"])
    await _run(db, codeql_repo, languages=["java"])

    pos = await db.list_dataset_labels(f"codeql-test-suites-java-{_COMMIT_SHORT}")
    neg = await db.list_dataset_negative_labels(f"codeql-test-suites-java-{_COMMIT_SHORT}")
    assert len(pos) == 3
    assert len(neg) == 2


async def test_idempotency_result_counts_stable(db: Database, codeql_repo: Path):
    """Both runs return the same counts."""
    r1 = await _run(db, codeql_repo, languages=["java"])
    r2 = await _run(db, codeql_repo, languages=["java"])
    assert r1.positives_count == r2.positives_count
    assert r1.negatives_count == r2.negatives_count


# ---------------------------------------------------------------------------
# Tests: language filter
# ---------------------------------------------------------------------------


async def test_language_filter_excludes_other_languages(db: Database, codeql_repo: Path):
    """Specifying languages=['java'] does not create a python dataset."""
    await _run(db, codeql_repo, languages=["java"])
    python_row = await db.get_dataset(f"codeql-test-suites-python-{_COMMIT_SHORT}")
    assert python_row is None


async def test_language_filter_includes_only_listed(db: Database, codeql_repo: Path):
    """Specifying languages=['python'] imports only python data."""
    result = await _run(db, codeql_repo, languages=["python"])
    # Python has 2 positives and 2 negatives
    assert result.positives_count == 2
    assert result.negatives_count == 2


# ---------------------------------------------------------------------------
# Tests: CWE filter
# ---------------------------------------------------------------------------


async def test_cwe_filter_excludes_other_cwes(db: Database, codeql_repo: Path):
    """cwe_filter=['CWE-079'] imports only CWE-079 data from java."""
    result = await _run(db, codeql_repo, languages=["java"], cwe_filter=["CWE-079"])
    # Only Foo query: 2 positives, 1 negative (TestSafe.java)
    assert result.positives_count == 2
    assert result.negatives_count == 1


async def test_cwe_filter_includes_listed_cwe(db: Database, codeql_repo: Path):
    """cwe_filter=['CWE-089'] imports only SQL injection data."""
    result = await _run(db, codeql_repo, languages=["java"], cwe_filter=["CWE-089"])
    assert result.positives_count == 1
    assert result.negatives_count == 1


# ---------------------------------------------------------------------------
# Tests: unmapped CWE falls back to 'other'
# ---------------------------------------------------------------------------


async def test_unmapped_cwe_falls_back_to_other(tmp_path: Path, db: Database):
    """A CWE not in vuln_classes.yaml falls back to vuln_class='other'."""
    # Create a synthetic repo with an unknown CWE (CWE-9999)
    repo = tmp_path / "codeql_unknown"
    repo.mkdir()
    unknown_dir = (
        repo / "python" / "ql" / "test" / "query-tests"
        / "Security" / "CWE-9999" / "UnknownQuery"
    )
    unknown_dir.mkdir(parents=True)
    (unknown_dir / "UnknownQuery.ql").write_text("// placeholder\n")
    (unknown_dir / "UnknownQuery.expected").write_text("vuln.py:5:1:5:20:Finding\n")
    (unknown_dir / "vuln.py").write_text("# vulnerable\n" * 5)

    result = await import_codeql_test_suites(
        db,
        codeql_repo_path=repo,
        origin_commit=_PINNED_COMMIT,
        languages=["python"],
    )
    assert result.errors == []
    assert result.positives_count == 1

    labels = await db.list_dataset_labels(
        f"codeql-test-suites-python-{_COMMIT_SHORT}",
    )
    assert len(labels) == 1
    assert labels[0]["vuln_class"] == "other"


# ---------------------------------------------------------------------------
# Tests: source CHECK migration
# ---------------------------------------------------------------------------


async def test_source_check_migration_allows_codeql(db: Database, codeql_repo: Path):
    """After import, 'codeql' is accepted by the dataset_labels source CHECK."""
    import aiosqlite

    await _run(db, codeql_repo, languages=["java"])

    # Direct probe: inserting a row with source='codeql' must not raise
    async with aiosqlite.connect(db.db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = OFF")
        # Should not raise — CHECK accepts 'codeql'
        await conn.execute(
            """
            INSERT OR IGNORE INTO dataset_labels (
                id, dataset_name, dataset_version, file_path,
                line_start, line_end, cwe_id, vuln_class, severity,
                description, source, confidence, created_at
            ) VALUES (
                '_probe_codeql_test', '_probe_ds', 'v0', 'probe.py',
                1, 1, 'CWE-79', 'xss', 'LOW', 'probe', 'codeql', 'HIGH',
                '2000-01-01T00:00:00'
            )
            """
        )
        await conn.commit()
        await conn.execute("PRAGMA foreign_keys = ON")


async def test_negative_source_check_migration_allows_codeql(
    db: Database, codeql_repo: Path
):
    """After import, 'codeql' is accepted by the dataset_negative_labels source CHECK."""
    import aiosqlite

    await _run(db, codeql_repo, languages=["java"])

    async with aiosqlite.connect(db.db_path) as conn:
        await conn.execute("PRAGMA foreign_keys = OFF")
        await conn.execute(
            """
            INSERT OR IGNORE INTO dataset_negative_labels (
                id, dataset_name, dataset_version, file_path,
                cwe_id, vuln_class, source, created_at
            ) VALUES (
                '_probe_neg_codeql', '_probe_ds', 'v0', 'probe.py',
                'CWE-79', 'xss', 'codeql', '2000-01-01T00:00:00'
            )
            """
        )
        await conn.commit()
        await conn.execute("PRAGMA foreign_keys = ON")


# ---------------------------------------------------------------------------
# Tests: malformed .expected does not abort import
# ---------------------------------------------------------------------------


async def test_malformed_expected_skips_query_without_abort(
    tmp_path: Path, db: Database
):
    """A malformed .expected file skips that query but the import continues."""
    repo = tmp_path / "codeql_malformed"
    repo.mkdir()

    # Good query
    good_dir = (
        repo / "python" / "ql" / "test" / "query-tests"
        / "Security" / "CWE-022" / "GoodQuery"
    )
    good_dir.mkdir(parents=True)
    (good_dir / "GoodQuery.ql").write_text("// placeholder\n")
    (good_dir / "GoodQuery.expected").write_text("vuln.py:3:1:3:10:Finding\n")
    (good_dir / "vuln.py").write_text("# content\n" * 3)

    # Malformed query (> 50% unrecognised lines)
    bad_dir = (
        repo / "python" / "ql" / "test" / "query-tests"
        / "Security" / "CWE-079" / "BadQuery"
    )
    bad_dir.mkdir(parents=True)
    (bad_dir / "BadQuery.ql").write_text("// placeholder\n")
    (bad_dir / "BadQuery.expected").write_text(
        "garbage line with no format\n"
        "more garbage without colon structure\n"
        "yet more garbage without format\n"
        "vuln.py:5:1:5:10:Valid finding\n"
    )
    (bad_dir / "vuln.py").write_text("# vuln\n" * 5)

    result = await import_codeql_test_suites(
        db,
        codeql_repo_path=repo,
        origin_commit=_PINNED_COMMIT,
        languages=["python"],
    )

    # Import must NOT raise — it continues past the malformed query
    # Good query contributed 1 positive
    assert result.positives_count == 1
    # Error list has an entry for the malformed query
    assert any("error_unparseable_expected" in e[0] for e in result.errors)


# ---------------------------------------------------------------------------
# Tests: missing Security/ directory for a language
# ---------------------------------------------------------------------------


async def test_missing_security_dir_returns_empty_result(
    tmp_path: Path, db: Database
):
    """If there is no Security/ directory for a language, the import is a no-op."""
    repo = tmp_path / "empty_codeql"
    repo.mkdir()
    # Create the repo root but no language directories

    result = await import_codeql_test_suites(
        db,
        codeql_repo_path=repo,
        origin_commit=_PINNED_COMMIT,
        languages=["go"],
    )
    assert result.positives_count == 0
    assert result.negatives_count == 0
    assert result.errors == []
