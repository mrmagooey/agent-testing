"""Unit tests for the MITRE CWE Demonstrative Examples importer.

All tests use synthetic CWEC XML fixtures — no real network access.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
import pytest_asyncio

from sec_review_framework.db import Database
from sec_review_framework.ground_truth.mitre_demonstrative_examples_importer import (
    MitreDemoExamplesImportResult,
    import_mitre_demonstrative_examples,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def db(tmp_path: Path) -> Database:
    """Fresh in-process SQLite database for each test."""
    database = Database(tmp_path / "test.db")
    await database.init()
    return database


def _write_cwec_xml(path: Path, content: str) -> Path:
    """Write a synthetic CWEC XML file to *path* and return the Path."""
    path.write_text(dedent(content), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Synthetic XML builders
# ---------------------------------------------------------------------------

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


def _make_weakness(
    cwe_id: str,
    examples: list[dict],
) -> str:
    """Build a <Weakness> XML fragment with Demonstrative_Examples.

    Each entry in *examples* is a dict with keys:
      - intro: str (Intro_Text)
      - codes: list of (nature, language, code_text) tuples
    """
    lines = [f'    <Weakness ID="{cwe_id}" Name="Test CWE {cwe_id}">']
    if examples:
        lines.append("      <Demonstrative_Examples>")
        for ex in examples:
            lines.append("        <Demonstrative_Example>")
            lines.append(f"          <Intro_Text>{ex['intro']}</Intro_Text>")
            for nature, lang, code in ex.get("codes", []):
                lines.append(
                    f'          <Example_Code Nature="{nature}" Language="{lang}">'
                )
                lines.append(f"            {code}")
                lines.append("          </Example_Code>")
            lines.append("        </Demonstrative_Example>")
        lines.append("      </Demonstrative_Examples>")
    lines.append("    </Weakness>")
    return "\n".join(lines)


def _build_cwec(weaknesses: list[str]) -> str:
    return _CWEC_HEADER + "\n".join(weaknesses) + "\n" + _CWEC_FOOTER


# ---------------------------------------------------------------------------
# Standard fixture: 5 CWEs, 3 languages, mixed paired/unpaired
# ---------------------------------------------------------------------------

_STANDARD_WEAKNESSES = [
    # CWE-89: SQL injection — C (bad only) + Java (bad + good)
    _make_weakness("89", [
        {
            "intro": "User input is used directly in SQL query.",
            "codes": [
                ("Bad", "C", "char query[100]; sprintf(query, sql, input);"),
                ("Bad", "Java", "stmt.executeQuery(\"SELECT * FROM users WHERE id=\" + id);"),
                ("Good", "Java", "PreparedStatement ps = conn.prepareStatement(sql);"),
            ],
        },
    ]),
    # CWE-79: XSS — JavaScript (bad + good)
    _make_weakness("79", [
        {
            "intro": "Reflected XSS via unescaped output.",
            "codes": [
                ("Bad", "JavaScript", "document.write(location.search);"),
                ("Good", "JavaScript", "element.textContent = location.search;"),
            ],
        },
    ]),
    # CWE-22: Path traversal — Python (bad) + PHP (bad)
    _make_weakness("22", [
        {
            "intro": "Path traversal in file open.",
            "codes": [
                ("Bad", "Python", "open(os.path.join(base, filename))"),
                ("Bad", "PHP", 'include($_GET["file"]);'),
            ],
        },
    ]),
    # CWE-119: Buffer overflow — C (bad only, no paired good)
    _make_weakness("119", [
        {
            "intro": "No bounds checking on buffer copy.",
            "codes": [
                ("Bad", "C", "strcpy(buf, input);"),
            ],
        },
        {
            "intro": "Integer overflow leads to small allocation.",
            "codes": [
                ("Bad", "C", "malloc(count * sizeof(int));"),
                ("Good", "C", "if (count > SIZE_MAX / sizeof(int)) abort();"),
            ],
        },
    ]),
    # CWE-798: Hardcoded credentials — Java (bad + good)
    _make_weakness("798", [
        {
            "intro": "Hardcoded password in source.",
            "codes": [
                ("Bad", "Java", 'String pw = "secret123";'),
                ("Good", "Java", "String pw = System.getenv(\"DB_PASSWORD\");"),
            ],
        },
    ]),
]

_STANDARD_XML = _build_cwec(_STANDARD_WEAKNESSES)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ARCHIVE_KWARGS = {
    "cwec_version": "4.99",
    "archive_url": "https://cwe.mitre.org/data/xml/cwec_v4.99.xml.zip",
    "archive_sha256": "abc123" * 10 + "ef",
}


async def _import_standard(db: Database, xml_path: Path, **extra) -> MitreDemoExamplesImportResult:
    return await import_mitre_demonstrative_examples(
        db,
        cwec_xml_path=xml_path,
        **_ARCHIVE_KWARGS,
        **extra,
    )


# ---------------------------------------------------------------------------
# Test: correct positive + negative counts
# ---------------------------------------------------------------------------


async def test_import_correct_positive_count(db: Database, tmp_path: Path):
    """Positive labels: one per <Example_Code Nature='Bad'>."""
    xml_path = _write_cwec_xml(tmp_path / "cwec.xml", _STANDARD_XML)
    result = await _import_standard(db, xml_path)

    # CWE-89: 1 C bad + 1 Java bad = 2
    # CWE-79: 1 JS bad = 1
    # CWE-22: 1 Python bad + 1 PHP bad = 2
    # CWE-119: 1 C bad (ex0) + 1 C bad (ex1) = 2
    # CWE-798: 1 Java bad = 1
    # Total = 8
    assert result.positives_inserted == 8
    labels = await db.list_dataset_labels(result.dataset_name)
    assert len(labels) == 8


async def test_import_correct_negative_count(db: Database, tmp_path: Path):
    """Negative labels: one per <Example_Code Nature='Good'>."""
    xml_path = _write_cwec_xml(tmp_path / "cwec.xml", _STANDARD_XML)
    result = await _import_standard(db, xml_path)

    # CWE-89: 1 Java good
    # CWE-79: 1 JS good
    # CWE-119: 1 C good (ex1 only)
    # CWE-798: 1 Java good
    # Total = 4
    assert result.negatives_inserted == 4
    negs = await db.list_dataset_negative_labels(result.dataset_name)
    assert len(negs) == 4


# ---------------------------------------------------------------------------
# Test: pairing — CWE with both Bad and Good emits both positive + negative
# ---------------------------------------------------------------------------


async def test_pairing_bad_and_good_emits_both(db: Database, tmp_path: Path):
    """A CWE with paired Bad+Good produces both a positive and a negative label."""
    xml = _build_cwec([_make_weakness("79", [
        {
            "intro": "XSS example.",
            "codes": [
                ("Bad", "JavaScript", "document.write(input);"),
                ("Good", "JavaScript", "element.textContent = input;"),
            ],
        },
    ])])
    xml_path = _write_cwec_xml(tmp_path / "cwec.xml", xml)
    result = await _import_standard(db, xml_path)

    assert result.positives_inserted == 1
    assert result.negatives_inserted == 1

    labels = await db.list_dataset_labels(result.dataset_name)
    negs = await db.list_dataset_negative_labels(result.dataset_name)

    assert len(labels) == 1
    assert len(negs) == 1
    assert labels[0]["cwe_id"] == "CWE-79"
    assert negs[0]["cwe_id"] == "CWE-79"


# ---------------------------------------------------------------------------
# Test: unpaired — CWE with only Bad emits only a positive
# ---------------------------------------------------------------------------


async def test_unpaired_bad_only_emits_positive_only(db: Database, tmp_path: Path):
    """A CWE with only Bad examples produces only positive labels."""
    xml = _build_cwec([_make_weakness("119", [
        {
            "intro": "Buffer overflow.",
            "codes": [
                ("Bad", "C", "strcpy(buf, input);"),
            ],
        },
    ])])
    xml_path = _write_cwec_xml(tmp_path / "cwec.xml", xml)
    result = await _import_standard(db, xml_path)

    assert result.positives_inserted == 1
    assert result.negatives_inserted == 0

    negs = await db.list_dataset_negative_labels(result.dataset_name)
    assert negs == []


# ---------------------------------------------------------------------------
# Test: multiple languages per CWE produce separate labels
# ---------------------------------------------------------------------------


async def test_multiple_languages_produce_separate_labels(db: Database, tmp_path: Path):
    """Multiple languages in one example each produce their own label row."""
    xml = _build_cwec([_make_weakness("22", [
        {
            "intro": "Path traversal.",
            "codes": [
                ("Bad", "Python", "open(path)"),
                ("Bad", "PHP", "include(path);"),
                ("Bad", "Java", "new File(path)"),
            ],
        },
    ])])
    xml_path = _write_cwec_xml(tmp_path / "cwec.xml", xml)
    result = await _import_standard(db, xml_path)

    assert result.positives_inserted == 3

    labels = await db.list_dataset_labels(result.dataset_name)
    file_paths = {row["file_path"] for row in labels}

    # Each language must have a distinct file path
    assert len(file_paths) == 3
    assert any(".py" in fp for fp in file_paths)
    assert any(".php" in fp for fp in file_paths)
    assert any(".java" in fp for fp in file_paths)


# ---------------------------------------------------------------------------
# Test: idempotency
# ---------------------------------------------------------------------------


async def test_import_is_idempotent(db: Database, tmp_path: Path):
    """Running the import twice produces the same counts (INSERT OR IGNORE)."""
    xml_path = _write_cwec_xml(tmp_path / "cwec.xml", _STANDARD_XML)

    result1 = await _import_standard(db, xml_path)
    result2 = await _import_standard(db, xml_path)

    # Counts from the second run match the first (rows were ignored, not doubled)
    assert result2.positives_inserted == result1.positives_inserted
    assert result2.negatives_inserted == result1.negatives_inserted

    # Database has exactly the same number of rows after the second run
    labels_after = await db.list_dataset_labels(result1.dataset_name)
    negs_after = await db.list_dataset_negative_labels(result1.dataset_name)
    assert len(labels_after) == result1.positives_inserted
    assert len(negs_after) == result1.negatives_inserted


# ---------------------------------------------------------------------------
# Test: language filter excludes non-listed languages
# ---------------------------------------------------------------------------


async def test_language_filter_excludes_non_listed(db: Database, tmp_path: Path):
    """languages=['C'] only imports Bad/Good codes with Language='C'."""
    xml_path = _write_cwec_xml(tmp_path / "cwec.xml", _STANDARD_XML)
    result = await _import_standard(db, xml_path, languages=["C"])

    labels = await db.list_dataset_labels(result.dataset_name)
    # All file paths should end in .c
    for label in labels:
        assert label["file_path"].endswith(".c"), (
            f"Expected .c extension, got: {label['file_path']}"
        )

    # Only C bad codes: CWE-89 (1 C), CWE-119 ex0 (1 C) + CWE-119 ex1 (1 C) = 3
    # (CWE-22 has Python and PHP, not C)
    assert result.positives_inserted == 3

    negs = await db.list_dataset_negative_labels(result.dataset_name)
    for neg in negs:
        assert neg["file_path"].endswith(".c")
    # CWE-119 ex1 has a C good code = 1
    assert result.negatives_inserted == 1


# ---------------------------------------------------------------------------
# Test: CWE filter excludes non-listed CWEs
# ---------------------------------------------------------------------------


async def test_cwe_filter_excludes_non_listed(db: Database, tmp_path: Path):
    """cwe_filter=['CWE-89', 'CWE-79'] restricts to only those two CWEs."""
    xml_path = _write_cwec_xml(tmp_path / "cwec.xml", _STANDARD_XML)
    result = await _import_standard(db, xml_path, cwe_filter=["CWE-89", "CWE-79"])

    labels = await db.list_dataset_labels(result.dataset_name)
    cwe_ids = {row["cwe_id"] for row in labels}
    assert cwe_ids == {"CWE-89", "CWE-79"}

    negs = await db.list_dataset_negative_labels(result.dataset_name)
    neg_cwes = {row["cwe_id"] for row in negs}
    assert neg_cwes <= {"CWE-89", "CWE-79"}


# ---------------------------------------------------------------------------
# Test: source CHECK migration adds 'mitre_demo'
# ---------------------------------------------------------------------------


async def test_source_check_migration_adds_mitre_demo(db: Database, tmp_path: Path):
    """After ensure_source_check_includes, the CHECK constraint accepts 'mitre_demo'."""
    from sec_review_framework.ground_truth._source_check_migration import (
        ensure_negative_source_check_includes,
        ensure_source_check_includes,
    )

    await ensure_source_check_includes(db, "mitre_demo")
    await ensure_negative_source_check_includes(db, "mitre_demo")

    import aiosqlite

    # Assertion: the CHECK constraint now allows 'mitre_demo' on both tables.
    # We confirm by querying sqlite_schema for the table DDL.
    async with aiosqlite.connect(db.db_path) as conn:
        async with conn.execute(
            "SELECT sql FROM sqlite_schema WHERE type='table' AND name='dataset_labels'"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert "'mitre_demo'" in row[0], "'mitre_demo' not in dataset_labels CHECK"

        async with conn.execute(
            "SELECT sql FROM sqlite_schema WHERE type='table' AND name='dataset_negative_labels'"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        assert "'mitre_demo'" in row[0], "'mitre_demo' not in dataset_negative_labels CHECK"


async def test_source_check_migration_is_idempotent(db: Database, tmp_path: Path):
    """ensure_source_check_includes called twice does not raise."""
    from sec_review_framework.ground_truth._source_check_migration import (
        ensure_negative_source_check_includes,
        ensure_source_check_includes,
    )

    await ensure_source_check_includes(db, "mitre_demo")
    await ensure_source_check_includes(db, "mitre_demo")  # must not raise

    await ensure_negative_source_check_includes(db, "mitre_demo")
    await ensure_negative_source_check_includes(db, "mitre_demo")  # must not raise


# ---------------------------------------------------------------------------
# Test: dataset row is created with kind='archive'
# ---------------------------------------------------------------------------


async def test_dataset_kind_is_archive(db: Database, tmp_path: Path):
    """The created dataset row has kind='archive' with archive metadata."""
    xml_path = _write_cwec_xml(tmp_path / "cwec.xml", _STANDARD_XML)
    result = await _import_standard(db, xml_path)

    ds = await db.get_dataset(result.dataset_name)
    assert ds is not None
    assert ds["kind"] == "archive"
    assert ds["archive_url"] == _ARCHIVE_KWARGS["archive_url"]
    assert ds["archive_sha256"] == _ARCHIVE_KWARGS["archive_sha256"]
    assert ds["archive_format"] == "zip"

    meta = __import__("json").loads(ds["metadata_json"])
    assert meta["benchmark"] == "mitre-cwe-demo"
    assert meta["cwec_version"] == "4.99"
    assert meta["language"] == "multi"


# ---------------------------------------------------------------------------
# Test: file path scheme is correct
# ---------------------------------------------------------------------------


async def test_file_path_scheme(db: Database, tmp_path: Path):
    """File paths follow examples/<cwe_id>/<ex_idx>/<nature>.<ext> scheme."""
    xml = _build_cwec([_make_weakness("89", [
        {
            "intro": "SQL injection.",
            "codes": [
                ("Bad", "Java", "stmt.executeQuery(sql + id);"),
                ("Good", "Java", "PreparedStatement ps = conn.prepareStatement(sql);"),
            ],
        },
    ])])
    xml_path = _write_cwec_xml(tmp_path / "cwec.xml", xml)
    result = await _import_standard(db, xml_path)

    labels = await db.list_dataset_labels(result.dataset_name)
    negs = await db.list_dataset_negative_labels(result.dataset_name)

    assert len(labels) == 1
    assert len(negs) == 1

    assert labels[0]["file_path"] == "examples/CWE-89/0/bad.java"
    assert negs[0]["file_path"] == "examples/CWE-89/0/good.java"


# ---------------------------------------------------------------------------
# Test: line_end = number of lines in code snippet
# ---------------------------------------------------------------------------


async def test_line_end_matches_code_line_count(db: Database, tmp_path: Path):
    """line_end for a positive label equals the number of lines in the code."""
    multiline_code = "line1\nline2\nline3\nline4\nline5"
    xml = _build_cwec([_make_weakness("89", [
        {
            "intro": "SQL injection.",
            "codes": [("Bad", "Python", multiline_code)],
        },
    ])])
    xml_path = _write_cwec_xml(tmp_path / "cwec.xml", xml)
    result = await _import_standard(db, xml_path)

    labels = await db.list_dataset_labels(result.dataset_name)
    assert len(labels) == 1
    assert labels[0]["line_start"] == 1
    assert labels[0]["line_end"] == 5


# ---------------------------------------------------------------------------
# Test: CWE with only Body_Text and no Example_Code produces zero labels
# ---------------------------------------------------------------------------


async def test_cwe_with_no_example_code_produces_zero_labels(db: Database, tmp_path: Path):
    """CWEs without <Example_Code> blocks produce zero positive or negative labels."""
    # A weakness that has Demonstrative_Examples but no Example_Code
    xml = _build_cwec([
        """\
    <Weakness ID="200" Name="Info Exposure">
      <Demonstrative_Examples>
        <Demonstrative_Example>
          <Intro_Text>This is a description only.</Intro_Text>
          <Body_Text>Some prose, no code.</Body_Text>
        </Demonstrative_Example>
      </Demonstrative_Examples>
    </Weakness>""",
    ])
    xml_path = _write_cwec_xml(tmp_path / "cwec.xml", xml)
    result = await _import_standard(db, xml_path)

    assert result.positives_inserted == 0
    assert result.negatives_inserted == 0
    assert result.cwe_count == 0  # No weaknesses had importable codes


# ---------------------------------------------------------------------------
# Test: multiple examples per CWE have distinct example indices
# ---------------------------------------------------------------------------


async def test_multiple_examples_per_cwe_distinct_indices(db: Database, tmp_path: Path):
    """Multiple Demonstrative_Example children get distinct ex_idx values."""
    xml = _build_cwec([_make_weakness("119", [
        {
            "intro": "Buffer overflow example A.",
            "codes": [("Bad", "C", "strcpy(dst, src);")],
        },
        {
            "intro": "Buffer overflow example B.",
            "codes": [("Bad", "C", "gets(buf);")],
        },
    ])])
    xml_path = _write_cwec_xml(tmp_path / "cwec.xml", xml)
    result = await _import_standard(db, xml_path)

    assert result.positives_inserted == 2

    labels = await db.list_dataset_labels(result.dataset_name)
    file_paths = {row["file_path"] for row in labels}
    assert "examples/CWE-119/0/bad.c" in file_paths
    assert "examples/CWE-119/1/bad.c" in file_paths


# ---------------------------------------------------------------------------
# Test: source_ref format
# ---------------------------------------------------------------------------


async def test_source_ref_format(db: Database, tmp_path: Path):
    """source_ref follows the CWE-N::exI::bad / good format."""
    xml = _build_cwec([_make_weakness("79", [
        {
            "intro": "XSS.",
            "codes": [
                ("Bad", "JavaScript", "document.write(x);"),
                ("Good", "JavaScript", "el.textContent = x;"),
            ],
        },
    ])])
    xml_path = _write_cwec_xml(tmp_path / "cwec.xml", xml)
    result = await _import_standard(db, xml_path)

    labels = await db.list_dataset_labels(result.dataset_name)
    negs = await db.list_dataset_negative_labels(result.dataset_name)

    assert labels[0]["source_ref"] == "CWE-79::ex0::bad"
    assert negs[0]["source_ref"] == "CWE-79::ex0::good"


# ---------------------------------------------------------------------------
# Test: description is trimmed to 500 chars from intro_text
# ---------------------------------------------------------------------------


async def test_description_trimmed_to_500_chars(db: Database, tmp_path: Path):
    """description field is truncated to 500 characters from Intro_Text."""
    long_intro = "X" * 600
    xml = _build_cwec([_make_weakness("89", [
        {
            "intro": long_intro,
            "codes": [("Bad", "C", "char q[100];")],
        },
    ])])
    xml_path = _write_cwec_xml(tmp_path / "cwec.xml", xml)
    result = await _import_standard(db, xml_path)

    labels = await db.list_dataset_labels(result.dataset_name)
    assert len(labels[0]["description"]) == 500


# ---------------------------------------------------------------------------
# Test: combined language + CWE filter
# ---------------------------------------------------------------------------


async def test_combined_language_and_cwe_filter(db: Database, tmp_path: Path):
    """Language and CWE filters can be applied together."""
    xml_path = _write_cwec_xml(tmp_path / "cwec.xml", _STANDARD_XML)
    result = await _import_standard(
        db, xml_path, languages=["Java"], cwe_filter=["CWE-89"]
    )

    labels = await db.list_dataset_labels(result.dataset_name)
    # CWE-89 has 1 Java bad code
    assert result.positives_inserted == 1
    assert labels[0]["cwe_id"] == "CWE-89"
    assert labels[0]["file_path"].endswith(".java")
