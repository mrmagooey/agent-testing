"""Tests for FindingParser — extracts and validates Findings from LLM output."""

from __future__ import annotations

import json
import textwrap

import pytest

from sec_review_framework.data.findings import Finding, Severity, VulnClass
from sec_review_framework.strategies.common import FindingParser


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

EXPERIMENT_ID = "test-exp-001"
PRODUCED_BY = "test_strategy"


def _wrap_json(data) -> str:
    """Wrap arbitrary data in a ```json ... ``` block."""
    return f"```json\n{json.dumps(data)}\n```"


def _valid_finding_dict(**overrides) -> dict:
    base = {
        "file_path": "app/views.py",
        "line_start": 10,
        "line_end": 12,
        "vuln_class": "sqli",
        "cwe_ids": ["CWE-89"],
        "severity": "high",
        "title": "SQL Injection",
        "description": "Raw query concatenation",
        "recommendation": "Use parameterized queries",
        "confidence": 0.9,
    }
    base.update(overrides)
    return base


def _parse(llm_output: str) -> list[Finding]:
    return FindingParser().parse(llm_output, EXPERIMENT_ID, PRODUCED_BY)


# ---------------------------------------------------------------------------
# Core parsing
# ---------------------------------------------------------------------------


def test_parse_valid_json_block_returns_findings():
    """A well-formed ```json block produces the expected number of Finding objects."""
    raw = _wrap_json([_valid_finding_dict(), _valid_finding_dict(file_path="app/models.py")])
    findings = _parse(raw)
    assert len(findings) == 2


def test_parse_valid_single_finding():
    raw = _wrap_json([_valid_finding_dict()])
    findings = _parse(raw)
    assert len(findings) == 1
    assert findings[0].file_path == "app/views.py"
    assert findings[0].vuln_class == VulnClass.SQLI
    assert findings[0].severity == Severity.HIGH


def test_parse_no_json_block_returns_empty():
    """Output with no fenced block → empty list."""
    findings = _parse("I found no issues.")
    assert findings == []


def test_parse_invalid_json_returns_empty():
    """Malformed JSON inside the block → empty list."""
    findings = _parse("```json\n{not valid json!!}\n```")
    assert findings == []


def test_parse_non_array_json_returns_empty():
    """A JSON object (not array) at top-level → empty list."""
    findings = _parse(_wrap_json({"file_path": "app.py", "vuln_class": "sqli"}))
    assert findings == []


def test_parse_mixed_valid_and_invalid_entries():
    """Valid entries mixed with invalid ones — only valid ones returned."""
    items = [
        _valid_finding_dict(),
        {"bad": "entry"},                  # missing required fields
        _valid_finding_dict(file_path="b.py"),
    ]
    findings = _parse(_wrap_json(items))
    assert len(findings) == 2


def test_parse_missing_file_path_skips_entry():
    """Entry without file_path is silently skipped."""
    item = _valid_finding_dict()
    del item["file_path"]
    findings = _parse(_wrap_json([item]))
    assert findings == []


def test_parse_missing_title_skips_entry():
    """Entry without title is silently skipped."""
    item = _valid_finding_dict()
    del item["title"]
    findings = _parse(_wrap_json([item]))
    assert findings == []


def test_parse_invalid_vuln_class_skips_entry():
    """Unknown vuln_class enum value → entry skipped."""
    item = _valid_finding_dict(vuln_class="definitely_not_real")
    findings = _parse(_wrap_json([item]))
    assert findings == []


def test_parse_invalid_severity_skips_entry():
    """Unknown severity value → entry skipped."""
    item = _valid_finding_dict(severity="apocalyptic")
    findings = _parse(_wrap_json([item]))
    assert findings == []


# ---------------------------------------------------------------------------
# Metadata stamping
# ---------------------------------------------------------------------------


def test_parse_each_finding_gets_unique_uuid():
    """Every finding must have a unique UUID id."""
    items = [_valid_finding_dict(), _valid_finding_dict(file_path="b.py")]
    findings = _parse(_wrap_json(items))
    ids = [f.id for f in findings]
    assert len(set(ids)) == len(ids)


def test_parse_experiment_id_stamped_on_every_finding():
    items = [_valid_finding_dict(), _valid_finding_dict(file_path="b.py")]
    findings = _parse(_wrap_json(items))
    for f in findings:
        assert f.experiment_id == EXPERIMENT_ID


def test_parse_produced_by_stamped_on_every_finding():
    findings = _parse(_wrap_json([_valid_finding_dict()]))
    assert findings[0].produced_by == PRODUCED_BY


# ---------------------------------------------------------------------------
# Optional / default fields
# ---------------------------------------------------------------------------


def test_parse_optional_fields_default_to_none():
    """line_end and recommendation are optional; they default to None."""
    item = {k: v for k, v in _valid_finding_dict().items()}
    del item["line_end"]
    del item["recommendation"]
    findings = _parse(_wrap_json([item]))
    assert len(findings) == 1
    assert findings[0].line_end is None
    assert findings[0].recommendation is None


def test_parse_confidence_as_float():
    """confidence is stored as a float."""
    item = _valid_finding_dict(confidence=0.75)
    findings = _parse(_wrap_json([item]))
    assert isinstance(findings[0].confidence, float)
    assert findings[0].confidence == pytest.approx(0.75)


def test_parse_raw_llm_output_captured():
    """raw_llm_output is the full original LLM string, not just the JSON block."""
    raw = "Some preamble text.\n" + _wrap_json([_valid_finding_dict()])
    findings = _parse(raw)
    assert findings[0].raw_llm_output == raw


def test_parse_cwe_ids_preserved():
    item = _valid_finding_dict(cwe_ids=["CWE-89", "CWE-564"])
    findings = _parse(_wrap_json([item]))
    assert findings[0].cwe_ids == ["CWE-89", "CWE-564"]
