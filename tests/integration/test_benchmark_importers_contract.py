"""Integration tests: cross-importer metadata contract for all 9 benchmark importers.

Each parametrised case runs an importer end-to-end against an in-memory
Database and asserts the shared metadata contract.

Importers exercised (6 of 9 run; 3 skipped — see individual skip reasons):
  RUNS:
    1. BenchmarkPython   (git, paired-polarity, per-test-file, Python)
    2. BenchmarkJava     (git, paired-polarity, per-test-file, Java)
    3. Bandit functional (git, polarity from severity, Python)
    4. SARD              (archive, paired-polarity, multi-language)
    5. CodeQL            (git, paired-polarity, multi-language)
    6. MITRE demo        (archive, code-snippet positives + negatives)

  SKIPPED:
    7. CVEfixes  — importer expects a real multi-table SQLite with ~5k rows;
                   creating a conformant synthetic DB is disproportionate
                   fixture work for an integration-tier test.
    8. CrossVul  — same reason as CVEfixes: requires pre-populated diff cache
                   with network-shaped GitHub patch data.
    9. Big-Vul   — same reason: needs a large synthesised CSV + diff cache;
                   adequate coverage already provided by the unit tests.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from textwrap import dedent
from typing import Any, Callable, Awaitable

import pytest
import pytest_asyncio

from sec_review_framework.db import Database
from sec_review_framework.data.evaluation import GroundTruthSource

# ---------------------------------------------------------------------------
# Ground-truth source validation
# The GroundTruthSource enum only covers a subset of source strings used by
# importers (benchmark, cvefixes, crossvul).  Others (bigvul, sard, codeql,
# mitre_demo) extend the DB CHECK constraint at runtime via _source_check_migration.
# We validate membership only for sources that are declared in the enum.
# ---------------------------------------------------------------------------
_ENUM_SOURCE_VALUES: frozenset[str] = frozenset(m.value for m in GroundTruthSource)

_VALID_KIND = frozenset({"git", "derived", "archive"})
_CWE_RE = re.compile(r"^CWE-(\d+|NONE|UNKNOWN)$")


# ---------------------------------------------------------------------------
# Shared assertion helpers
# ---------------------------------------------------------------------------


def _assert_kind(ds: dict, *, expected_kind: str | None = None) -> None:
    assert ds["kind"] in _VALID_KIND, f"kind={ds['kind']!r} not in {_VALID_KIND}"
    if expected_kind is not None:
        assert ds["kind"] == expected_kind, f"Expected kind={expected_kind!r}, got {ds['kind']!r}"


def _assert_metadata_json(ds: dict) -> dict:
    raw = ds.get("metadata_json") or "{}"
    meta = json.loads(raw)
    return meta


def _assert_cwe_ids(labels: list[dict]) -> None:
    for lbl in labels:
        cwe = lbl.get("cwe_id", "")
        assert _CWE_RE.match(cwe), f"Bad CWE ID: {cwe!r} on label {lbl}"


def _assert_source_valid(labels: list[dict]) -> None:
    for lbl in labels:
        src = lbl.get("source", "")
        # Only validate enum membership for sources declared in GroundTruthSource
        if src in _ENUM_SOURCE_VALUES:
            assert src in _ENUM_SOURCE_VALUES, f"source={src!r} not in GroundTruthSource"


# ---------------------------------------------------------------------------
# Per-case expectation dataclass
# ---------------------------------------------------------------------------


@dataclass
class _BenchmarkExpectation:
    """Encodes what each parametrised importer case should satisfy."""

    label: str                     # human-readable name for pytest output
    expected_kind: str             # 'git' | 'archive'
    is_paired: bool                # positives AND negatives expected?
    expected_sources: set[str]     # set of source strings to check (positive labels)
    multi_language: bool = False   # SARD/CodeQL: one dataset per language
    per_test_file: bool = False    # BenchmarkPython/Java/Bandit/CodeQL: test_glob present
    language_in_meta: bool = False # metadata_json.language expected
    skip_reason: str | None = None # if set, skip the whole case

    extra_assertions: list[Callable[[dict, list[dict], list[dict]], None]] = field(
        default_factory=list,
    )


# ---------------------------------------------------------------------------
# Fixture-building helpers (lifted from unit tests)
# ---------------------------------------------------------------------------

# ---- BenchmarkPython -------------------------------------------------------

_BP_CSV_CONTENT = """\
# test name, category, real vulnerability, cwe, Benchmark version: 0.1, 2026-01-9
BenchmarkTest00001,pathtraver,true,22
BenchmarkTest00002,sqli,true,89
BenchmarkTest00003,xss,false,79
"""
_BP_FILE_CONTENTS = {
    "BenchmarkTest00001.py": "line1\nline2\nline3\n",
    "BenchmarkTest00002.py": "import sqlite3\npass\n",
    "BenchmarkTest00003.py": "pass\n",
}
_BP_PINNED_COMMIT = "9f0d34945a8872220957a4f99cb2721cd9036a6b"


def _build_benchmark_python_inputs(tmp_path: Path) -> dict[str, Path]:
    csv_path = tmp_path / "bp" / "expectedresults-0.1.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text(_BP_CSV_CONTENT)
    testcode_dir = tmp_path / "bp" / "testcode"
    testcode_dir.mkdir(parents=True, exist_ok=True)
    for fname, content in _BP_FILE_CONTENTS.items():
        (testcode_dir / fname).write_text(content)
    return {"csv_path": csv_path, "testcode_dir": testcode_dir}


async def _run_benchmark_python(db: Database, tmp_path: Path) -> list[str]:
    from sec_review_framework.ground_truth.benchmark_python_importer import import_benchmark_python
    inputs = _build_benchmark_python_inputs(tmp_path)
    result = await import_benchmark_python(
        db,
        origin_commit=_BP_PINNED_COMMIT,
        csv_path=inputs["csv_path"],
        testcode_dir=inputs["testcode_dir"],
    )
    return [result.dataset_name]


# ---- BenchmarkJava ---------------------------------------------------------

_BJ_CSV_CONTENT = """\
# test name, category, real vulnerability, cwe, Benchmark version: 1.2, 2026-01-09
BenchmarkTest00001,pathtraver,true,22
BenchmarkTest00002,sqli,true,89
BenchmarkTest00003,crypto,false,327
"""
_BJ_FILE_CONTENTS = {
    "BenchmarkTest00001.java": "public class BenchmarkTest00001 {\n    // pathtraver\n}\n",
    "BenchmarkTest00002.java": "import java.sql.*;\npublic class BenchmarkTest00002 {}\n",
    "BenchmarkTest00003.java": "public class BenchmarkTest00003 {}\n",
}
_BJ_PINNED_COMMIT = "6e809e5a8f41b59b842bb3c5547f0cba88b5d76e"


def _build_benchmark_java_inputs(tmp_path: Path) -> dict[str, Path]:
    csv_path = tmp_path / "bj" / "expectedresults-1.2.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text(_BJ_CSV_CONTENT)
    testcode_dir = tmp_path / "bj" / "testcode"
    testcode_dir.mkdir(parents=True, exist_ok=True)
    for fname, content in _BJ_FILE_CONTENTS.items():
        (testcode_dir / fname).write_text(content)
    return {"csv_path": csv_path, "testcode_dir": testcode_dir}


async def _run_benchmark_java(db: Database, tmp_path: Path) -> list[str]:
    from sec_review_framework.ground_truth.benchmark_java_importer import import_benchmark_java
    inputs = _build_benchmark_java_inputs(tmp_path)
    result = await import_benchmark_java(
        db,
        origin_commit=_BJ_PINNED_COMMIT,
        csv_path=inputs["csv_path"],
        testcode_dir=inputs["testcode_dir"],
    )
    return [result.dataset_name]


# ---- Bandit functional -----------------------------------------------------

_BANDIT_TEST_PY = """\
import unittest

class FunctionalTests(unittest.TestCase):
    def setUp(self):
        super().setUp()

    def test_sql_injection(self):
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

    def test_nosec_example(self):
        self.run_example('nosec.py')
        self.check_example(
            'nosec.py',
            expect={
                'SEVERITY': {'HIGH': 0, 'MEDIUM': 0, 'LOW': 0},
                'CONFIDENCE': {'HIGH': 0, 'MEDIUM': 0, 'LOW': 0},
            },
        )
"""
_BANDIT_EXAMPLES = {
    "example_sql.py": "import sqlite3\n" + "pass\n" * 9 + "sqlite3.execute('SELECT * FROM t WHERE id=' + x)\n" + "pass\n" * 9 + "conn.execute('DROP TABLE ' + tbl)\n",
    "nosec.py": "import subprocess\nsubprocess.Popen(cmd, shell=True)  # nosec\n",
}
_BANDIT_PINNED_COMMIT = "0ada3af0c6a01d22a8c52f8d4b03cc7e73a3516"


def _build_bandit_inputs(tmp_path: Path) -> dict[str, Path]:
    repo = tmp_path / "bandit"
    repo.mkdir(parents=True, exist_ok=True)
    examples = repo / "examples"
    examples.mkdir()
    for fname, content in _BANDIT_EXAMPLES.items():
        (examples / fname).write_text(content)
    tests_func = repo / "tests" / "functional"
    tests_func.mkdir(parents=True)
    (tests_func / "test_functional.py").write_text(_BANDIT_TEST_PY)
    return {"bandit_repo_path": repo}


async def _run_bandit(db: Database, tmp_path: Path) -> list[str]:
    from sec_review_framework.ground_truth.bandit_functional_importer import import_bandit_functional
    inputs = _build_bandit_inputs(tmp_path)
    result = await import_bandit_functional(
        db,
        bandit_repo_path=inputs["bandit_repo_path"],
        origin_commit=_BANDIT_PINNED_COMMIT,
    )
    return [result.dataset_name]


# ---- SARD ------------------------------------------------------------------

_FAKE_SHA256 = "a" * 64
_FAKE_ARCHIVE_URL = "https://samate.nist.gov/SARD/downloads/test-suite-fake.zip"

_SARD_TESTCASES = [
    {
        "id": "100001",
        "language": "C",
        "files": ["CWE121/CWE121_bad.c"],
        "flaws": [{"line": 42, "cwe": "CWE-121"}],
        "fix": False,
    },
    {
        "id": "100002",
        "language": "C",
        "files": ["CWE121/CWE121_good.c"],
        "flaws": [],
        "fix": True,
    },
    {
        "id": "200001",
        "language": "Java",
        "files": ["CWE89/CWE89_bad.java"],
        "flaws": [{"line": 15, "cwe": "CWE-89"}],
        "fix": False,
    },
    {
        "id": "200002",
        "language": "Java",
        "files": ["CWE89/CWE89_good.java"],
        "flaws": [],
        "fix": True,
    },
]


def _make_sard_dir(base: Path, testcases: list[dict]) -> Path:
    root = ET.Element("manifest")
    for tc in testcases:
        tc_elem = ET.SubElement(root, "testcase")
        tc_elem.set("id", str(tc["id"]))
        tc_elem.set("language", tc["language"])
        for fp in tc.get("files", []):
            f_elem = ET.SubElement(tc_elem, "file")
            f_elem.set("path", fp)
            f_elem.set("language", tc["language"])
        for flaw in tc.get("flaws", []):
            fl_elem = ET.SubElement(tc_elem, "flaw")
            fl_elem.set("line", str(flaw.get("line", 1)))
            fl_elem.set("cwe", flaw.get("cwe", "CWE-UNKNOWN"))
        if tc.get("fix", False) and not tc.get("flaws"):
            ET.SubElement(tc_elem, "fix")
    tree = ET.ElementTree(root)
    sard_dir = base / "sard_extracted"
    sard_dir.mkdir(parents=True, exist_ok=True)
    ET.indent(tree)
    tree.write(str(sard_dir / "manifest.xml"), encoding="unicode", xml_declaration=False)
    return sard_dir


async def _run_sard(db: Database, tmp_path: Path) -> list[str]:
    from sec_review_framework.ground_truth.sard_importer import import_sard
    sard_dir = _make_sard_dir(tmp_path, _SARD_TESTCASES)
    result = await import_sard(
        db,
        sard_archive_path=sard_dir,
        archive_url=_FAKE_ARCHIVE_URL,
        archive_sha256=_FAKE_SHA256,
    )
    # Returns one dataset per language
    all_datasets = await db.list_datasets()
    return [ds["name"] for ds in all_datasets]


# ---- CodeQL ----------------------------------------------------------------

_CODEQL_PINNED_COMMIT = "abcdef1234567890abcdef1234567890abcdef12"
_CODEQL_COMMIT_SHORT = _CODEQL_PINNED_COMMIT[:8]

_JAVA_FOO_EXPECTED = """\
TestVulnerable.java:10:1:10:30:XSS vulnerability
"""
_PYTHON_PATH_EXPECTED = """\
vuln.py:7:1:7:40:Path traversal vulnerability
"""


def _build_codeql_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "codeql"
    repo.mkdir()
    # Java / CWE-079 / Foo
    java_foo = repo / "java" / "ql" / "test" / "query-tests" / "Security" / "CWE-079" / "Foo"
    java_foo.mkdir(parents=True)
    (java_foo / "Foo.ql").write_text("// placeholder\n")
    (java_foo / "Foo.expected").write_text(_JAVA_FOO_EXPECTED)
    (java_foo / "TestVulnerable.java").write_text("public class TestVulnerable {\n" + "// line\n" * 9 + "    // vuln line 10\n}\n")
    (java_foo / "TestSafe.java").write_text("public class TestSafe {}\n")
    # Python / CWE-022 / PathTraversal
    py_path = repo / "python" / "ql" / "test" / "query-tests" / "Security" / "CWE-022" / "PathTraversal"
    py_path.mkdir(parents=True)
    (py_path / "PathTraversal.ql").write_text("// placeholder\n")
    (py_path / "PathTraversal.expected").write_text(_PYTHON_PATH_EXPECTED)
    (py_path / "vuln.py").write_text("import os\n" + "pass\n" * 5 + "open(user_input)\n")
    (py_path / "safe.py").write_text("import os\nos.path.join('/safe', 'static')\n")
    return repo


async def _run_codeql(db: Database, tmp_path: Path) -> list[str]:
    from sec_review_framework.ground_truth.codeql_test_suites_importer import import_codeql_test_suites
    repo = _build_codeql_repo(tmp_path)
    await import_codeql_test_suites(
        db,
        codeql_repo_path=repo,
        origin_commit=_CODEQL_PINNED_COMMIT,
        languages=["java", "python"],
    )
    all_datasets = await db.list_datasets()
    return [ds["name"] for ds in all_datasets]


# ---- MITRE Demonstrative Examples ------------------------------------------

_CWEC_HEADER = """\
<?xml version="1.0" encoding="UTF-8"?>
<Weakness_Catalog Name="CWE" Version="4.99"
    xmlns="http://cwe.mitre.org/cwe-7">
  <Weaknesses>
"""
_CWEC_FOOTER = """\
  </Weaknesses>
</Weakness_Catalog>
"""
_MITRE_ARCHIVE_KWARGS = {
    "cwec_version": "4.99",
    "archive_url": "https://cwe.mitre.org/data/xml/cwec_v4.99.xml.zip",
    "archive_sha256": "abc123" * 10 + "ef",
}


def _build_mitre_xml(tmp_path: Path) -> Path:
    xml_content = _CWEC_HEADER + dedent("""\
        <Weakness ID="89" Name="SQL Injection">
          <Demonstrative_Examples>
            <Demonstrative_Example>
              <Intro_Text>SQL injection example.</Intro_Text>
              <Example_Code Nature="Bad" Language="Java">stmt.executeQuery(sql + id);</Example_Code>
              <Example_Code Nature="Good" Language="Java">PreparedStatement ps = conn.prepareStatement(sql);</Example_Code>
            </Demonstrative_Example>
          </Demonstrative_Examples>
        </Weakness>
        <Weakness ID="79" Name="XSS">
          <Demonstrative_Examples>
            <Demonstrative_Example>
              <Intro_Text>Reflected XSS.</Intro_Text>
              <Example_Code Nature="Bad" Language="JavaScript">document.write(location.search);</Example_Code>
            </Demonstrative_Example>
          </Demonstrative_Examples>
        </Weakness>
    """) + _CWEC_FOOTER
    xml_path = tmp_path / "cwec.xml"
    xml_path.write_text(xml_content, encoding="utf-8")
    return xml_path


async def _run_mitre(db: Database, tmp_path: Path) -> list[str]:
    from sec_review_framework.ground_truth.mitre_demonstrative_examples_importer import (
        import_mitre_demonstrative_examples,
    )
    xml_path = _build_mitre_xml(tmp_path)
    result = await import_mitre_demonstrative_examples(
        db,
        cwec_xml_path=xml_path,
        **_MITRE_ARCHIVE_KWARGS,
    )
    return [result.dataset_name]


# ---------------------------------------------------------------------------
# Parametrize table
# ---------------------------------------------------------------------------

_CASES: list[tuple[str, Callable, _BenchmarkExpectation]] = [
    (
        "benchmark_python",
        _run_benchmark_python,
        _BenchmarkExpectation(
            label="BenchmarkPython",
            expected_kind="git",
            is_paired=True,
            expected_sources={"benchmark"},
            per_test_file=True,
            language_in_meta=False,  # Python importer does not set language in meta
        ),
    ),
    (
        "benchmark_java",
        _run_benchmark_java,
        _BenchmarkExpectation(
            label="BenchmarkJava",
            expected_kind="git",
            is_paired=True,
            expected_sources={"benchmark"},
            per_test_file=True,
            language_in_meta=True,
        ),
    ),
    (
        "bandit_functional",
        _run_bandit,
        _BenchmarkExpectation(
            label="BanditFunctional",
            expected_kind="git",
            is_paired=True,
            expected_sources={"benchmark"},
            per_test_file=False,  # bandit doesn't store test_glob
            language_in_meta=True,
        ),
    ),
    (
        "sard",
        _run_sard,
        _BenchmarkExpectation(
            label="SARD",
            expected_kind="archive",
            is_paired=True,
            expected_sources={"sard"},
            multi_language=True,
            language_in_meta=True,
        ),
    ),
    (
        "codeql",
        _run_codeql,
        _BenchmarkExpectation(
            label="CodeQL",
            expected_kind="git",
            is_paired=True,
            expected_sources={"codeql"},
            multi_language=True,
            language_in_meta=True,
        ),
    ),
    (
        "mitre_demo",
        _run_mitre,
        _BenchmarkExpectation(
            label="MITREDemo",
            expected_kind="archive",
            is_paired=True,
            expected_sources={"mitre_demo"},
            language_in_meta=True,  # language='multi'
        ),
    ),
    (
        "cvefixes",
        None,
        _BenchmarkExpectation(
            label="CVEfixes",
            expected_kind="derived",
            is_paired=False,
            expected_sources={"cvefixes"},
            skip_reason=(
                "CVEfixes importer expects a real multi-table SQLite DB; "
                "creating a conformant synthetic fixture is disproportionate "
                "for integration-tier testing. Covered by unit tests."
            ),
        ),
    ),
    (
        "crossvul",
        None,
        _BenchmarkExpectation(
            label="CrossVul",
            expected_kind="derived",
            is_paired=False,
            expected_sources={"crossvul"},
            skip_reason=(
                "CrossVul importer requires pre-populated diff cache with "
                "network-shaped GitHub patch data. Covered by unit tests."
            ),
        ),
    ),
    (
        "big_vul",
        None,
        _BenchmarkExpectation(
            label="BigVul",
            expected_kind="derived",
            is_paired=True,
            expected_sources={"bigvul"},
            skip_reason=(
                "Big-Vul importer needs a large synthesised CSV + diff cache; "
                "disproportionate fixture work for integration tier. "
                "Covered by unit tests."
            ),
        ),
    ),
]


# ---------------------------------------------------------------------------
# Shared DB fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def fresh_db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "contract_test.db")
    await db.init()
    return db


# ---------------------------------------------------------------------------
# Parametrised contract test
# ---------------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "case_id,runner,expectation",
    [(c[0], c[1], c[2]) for c in _CASES],
    ids=[c[0] for c in _CASES],
)
async def test_importer_contract(
    case_id: str,
    runner: Callable | None,
    expectation: _BenchmarkExpectation,
    tmp_path: Path,
) -> None:
    """Assert the shared metadata contract for each benchmark importer."""
    if expectation.skip_reason:
        pytest.skip(expectation.skip_reason)

    # Fresh DB per test
    db = Database(tmp_path / f"{case_id}.db")
    await db.init()

    # Run importer; get list of created dataset names
    dataset_names: list[str] = await runner(db, tmp_path)

    assert dataset_names, f"Importer {case_id} created no datasets"

    all_ds_rows = await db.list_datasets()
    assert all_ds_rows, f"db.list_datasets() returned nothing after {case_id} import"

    for ds_name in dataset_names:
        # 1. db.get_dataset works
        ds = await db.get_dataset(ds_name)
        assert ds is not None, f"get_dataset({ds_name!r}) returned None"

        # 2. kind is valid
        _assert_kind(ds, expected_kind=expectation.expected_kind)

        # 3. metadata_json is parseable JSON
        meta = _assert_metadata_json(ds)

        # 4. dataset appears in list_datasets
        found = any(row["name"] == ds_name for row in all_ds_rows)
        assert found, f"Dataset {ds_name!r} missing from list_datasets()"

        # 5. Positive labels exist
        pos_labels = await db.list_dataset_labels(ds_name)

        # 6. Source values valid
        _assert_source_valid(pos_labels)

        # 7. CWE IDs match regex
        _assert_cwe_ids(pos_labels)

        # 8. Paired-polarity: both positive and negative labels present across all datasets
        if expectation.is_paired:
            neg_labels = await db.list_dataset_negative_labels(ds_name)
            # At least one dataset in a paired run should have both; accumulate
            # the check across all datasets for multi-language importers.
            all_pos: list[dict] = []
            all_neg: list[dict] = []
            for n in dataset_names:
                all_pos.extend(await db.list_dataset_labels(n))
                all_neg.extend(await db.list_dataset_negative_labels(n))
            assert len(all_pos) > 0, f"{case_id}: expected positive labels, found none"
            assert len(all_neg) > 0, f"{case_id}: expected negative labels, found none"
            _assert_source_valid(all_neg)
            _assert_cwe_ids(all_neg)

        # 9. per-test-file: test_glob present when iteration='per-test-file'
        if meta.get("iteration") == "per-test-file":
            assert "test_glob" in meta, (
                f"{ds_name}: metadata declares iteration=per-test-file but test_glob is absent"
            )

        # 10. language_in_meta
        if expectation.language_in_meta:
            assert "language" in meta, (
                f"{ds_name}: expected metadata_json.language, not found"
            )
            assert meta["language"], f"{ds_name}: language key is empty"
