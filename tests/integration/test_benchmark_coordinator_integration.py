"""Integration tests: coordinator HTTP layer surfaces benchmark importer data.

Tests two representative importers via the FastAPI TestClient:
  - BenchmarkPython (kind='git')
  - MITRE Demonstrative Examples (kind='archive')

For each:
  1. Run the importer against the coordinator's DB.
  2. GET /datasets → assert the row appears.
  3. GET /datasets/{name}/labels → assert non-empty.

Additional archive-specific assertion:
  - archive_url / archive_sha256 / archive_format present on the returned row
    (checked via db.get_dataset, since there is no GET /datasets/{name} route).
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import sec_review_framework.coordinator as coord_module
from sec_review_framework.coordinator import app
from sec_review_framework.db import Database

from tests.integration.test_coordinator_api import _make_coordinator


# ---------------------------------------------------------------------------
# Coordinator client fixture (re-uses _make_coordinator from established tests)
# ---------------------------------------------------------------------------


@pytest.fixture
async def coord_client(tmp_path: Path):
    db = Database(tmp_path / "coord_test.db")
    await db.init()
    c = _make_coordinator(tmp_path, db)
    with patch.object(coord_module, "coordinator", c):
        with patch.object(c, "reconcile", return_value=None):
            with TestClient(app, raise_server_exceptions=True) as client:
                yield client, c, db, tmp_path


# ---------------------------------------------------------------------------
# Fixture helpers (minimal — same synthetic data as contract tests)
# ---------------------------------------------------------------------------

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


def _build_bp_inputs(tmp_path: Path) -> dict[str, Path]:
    csv_path = tmp_path / "bp" / "expectedresults-0.1.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    csv_path.write_text(_BP_CSV_CONTENT)
    testcode_dir = tmp_path / "bp" / "testcode"
    testcode_dir.mkdir(parents=True, exist_ok=True)
    for fname, content in _BP_FILE_CONTENTS.items():
        (testcode_dir / fname).write_text(content)
    return {"csv_path": csv_path, "testcode_dir": testcode_dir}


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
    """) + _CWEC_FOOTER
    xml_path = tmp_path / "cwec.xml"
    xml_path.write_text(xml_content, encoding="utf-8")
    return xml_path


# ---------------------------------------------------------------------------
# Parametrised cases
# ---------------------------------------------------------------------------

_COORD_CASES = [
    pytest.param("benchmark_python", id="benchmark_python"),
    pytest.param("mitre_demo", id="mitre_demo"),
]


@pytest.mark.integration
@pytest.mark.parametrize("case_id", _COORD_CASES)
async def test_coordinator_surfaces_benchmark_data(case_id: str, coord_client, tmp_path: Path):
    """Coordinator HTTP API surfaces what the importer wrote to DB."""
    client, coordinator, db, tmp_path = coord_client

    if case_id == "benchmark_python":
        from sec_review_framework.ground_truth.benchmark_python_importer import import_benchmark_python
        inputs = _build_bp_inputs(tmp_path)
        result = await import_benchmark_python(
            db,
            origin_commit=_BP_PINNED_COMMIT,
            csv_path=inputs["csv_path"],
            testcode_dir=inputs["testcode_dir"],
        )
        ds_name = result.dataset_name
        expected_kind = "git"
    elif case_id == "mitre_demo":
        from sec_review_framework.ground_truth.mitre_demonstrative_examples_importer import (
            import_mitre_demonstrative_examples,
        )
        xml_path = _build_mitre_xml(tmp_path)
        result = await import_mitre_demonstrative_examples(
            db,
            cwec_xml_path=xml_path,
            **_MITRE_ARCHIVE_KWARGS,
        )
        ds_name = result.dataset_name
        expected_kind = "archive"
    else:
        pytest.fail(f"Unknown case_id: {case_id}")

    # ---- 1. GET /datasets lists the dataset --------------------------------
    resp = client.get("/datasets")
    assert resp.status_code == 200, f"/datasets returned {resp.status_code}"
    datasets_list = resp.json()
    assert isinstance(datasets_list, list), "/datasets response is not a list"
    names_in_list = {row["name"] for row in datasets_list}
    assert ds_name in names_in_list, (
        f"Dataset {ds_name!r} not in /datasets response. Got: {sorted(names_in_list)}"
    )

    # ---- 2. kind and archive metadata via db.get_dataset -------------------
    ds_row = await db.get_dataset(ds_name)
    assert ds_row is not None, f"get_dataset({ds_name!r}) returned None"
    assert ds_row["kind"] == expected_kind, (
        f"Expected kind={expected_kind!r}, got {ds_row['kind']!r}"
    )
    if expected_kind == "archive":
        assert ds_row["archive_url"], f"archive_url missing on {ds_name}"
        assert ds_row["archive_sha256"], f"archive_sha256 missing on {ds_name}"
        assert ds_row["archive_format"], f"archive_format missing on {ds_name}"

    # ---- 3. GET /datasets/{name}/labels returns non-empty ------------------
    resp_labels = client.get(f"/datasets/{ds_name}/labels")
    assert resp_labels.status_code == 200, (
        f"/datasets/{ds_name}/labels returned {resp_labels.status_code}"
    )
    labels = resp_labels.json()
    assert isinstance(labels, list), f"/datasets/{ds_name}/labels is not a list"
    assert len(labels) > 0, (
        f"/datasets/{ds_name}/labels is empty — expected positive labels"
    )
