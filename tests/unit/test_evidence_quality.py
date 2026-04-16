"""Unit tests for EvidenceQualityAssessor (heuristic variant)."""

from datetime import datetime, timezone

import pytest

from sec_review_framework.data.evaluation import (
    EvidenceQuality,
    GroundTruthLabel,
    GroundTruthSource,
)
from sec_review_framework.data.findings import Finding, Severity, VulnClass
from sec_review_framework.evaluation.evidence import EvidenceQualityAssessor


@pytest.fixture
def assessor() -> EvidenceQualityAssessor:
    return EvidenceQualityAssessor()


def _make_label(
    vuln_class: VulnClass = VulnClass.SQLI,
    file_path: str = "app/views.py",
    line_start: int = 40,
    line_end: int = 46,
) -> GroundTruthLabel:
    return GroundTruthLabel(
        id="label-001",
        dataset_version="1.0.0",
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        cwe_id="CWE-89",
        vuln_class=vuln_class,
        severity=Severity.HIGH,
        description="SQL injection via unsanitized input",
        source=GroundTruthSource.CVE_PATCH,
        confidence="confirmed",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


def _make_finding(
    description: str,
    file_path: str = "app/views.py",
    vuln_class: VulnClass = VulnClass.SQLI,
    line_start: int = 40,
    line_end: int = 46,
) -> Finding:
    return Finding(
        id="f-001",
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        vuln_class=vuln_class,
        severity=Severity.HIGH,
        title="SQL Injection",
        description=description,
        confidence=0.9,
        raw_llm_output="",
        produced_by="single_agent",
        experiment_id="test-exp",
    )


# ---------------------------------------------------------------------------
# STRONG evidence: all 3 signals present
# ---------------------------------------------------------------------------


def test_strong_evidence(assessor):
    """
    STRONG requires all three signals:
    - file:line citation in description
    - mechanism keywords present
    - line overlap with label
    """
    description = (
        "app/views.py:42 constructs a raw SQL query by concatenating user input "
        "directly into the query string without parameterized statements. "
        "An attacker can inject arbitrary SQL via the `q` GET parameter."
    )
    finding = _make_finding(description=description, line_start=40, line_end=46)
    label = _make_label(line_start=40, line_end=46)

    quality = assessor.assess(finding, label)
    assert quality == EvidenceQuality.STRONG


# ---------------------------------------------------------------------------
# ADEQUATE evidence: 2 of 3 signals
# ---------------------------------------------------------------------------


def test_adequate_evidence_no_citation(assessor):
    """
    ADEQUATE: mechanism keywords + line overlap, but no file:line citation.
    """
    description = (
        "The search endpoint builds a raw SQL query with user input injected "
        "directly — no parameterized statements or prepared statement used."
    )
    finding = _make_finding(description=description, line_start=40, line_end=46)
    label = _make_label(line_start=40, line_end=46)

    quality = assessor.assess(finding, label)
    assert quality == EvidenceQuality.ADEQUATE


def test_adequate_evidence_no_line_overlap(assessor):
    """
    ADEQUATE: file:line citation + mechanism keywords, but finding is on different lines.
    """
    description = (
        "app/views.py:42 uses parameterized query approach incorrectly; "
        "the SQL injection occurs via concatenation in the query string."
    )
    # Finding lines don't overlap with label (40-46)
    finding = _make_finding(description=description, line_start=100, line_end=110)
    label = _make_label(line_start=40, line_end=46)

    quality = assessor.assess(finding, label)
    assert quality == EvidenceQuality.ADEQUATE


# ---------------------------------------------------------------------------
# WEAK evidence: fewer than 2 signals
# ---------------------------------------------------------------------------


def test_weak_evidence(assessor):
    """
    WEAK: no file:line citation, no mechanism keywords, no line overlap.
    """
    description = "This might be a vulnerability that needs review."
    finding = _make_finding(description=description, line_start=200, line_end=210)
    label = _make_label(line_start=40, line_end=46)

    quality = assessor.assess(finding, label)
    assert quality == EvidenceQuality.WEAK


def test_weak_evidence_mechanism_only_no_overlap_no_citation(assessor):
    """
    WEAK: only mechanism keyword present (score=1), still WEAK.
    """
    description = "There is a sql injection somewhere in the codebase."
    finding = _make_finding(description=description, line_start=200, line_end=210)
    label = _make_label(line_start=40, line_end=46)

    quality = assessor.assess(finding, label)
    assert quality == EvidenceQuality.WEAK


# ---------------------------------------------------------------------------
# Different vuln classes — mechanism keywords
# ---------------------------------------------------------------------------


def test_xss_mechanism_keywords(assessor):
    """XSS findings with 'escape' + file:line + line overlap → STRONG."""
    description = "app/template.html:10 renders user content without HTML escape, enabling cross-site scripting."
    finding = _make_finding(
        description=description,
        file_path="app/template.html",
        vuln_class=VulnClass.XSS,
        line_start=10,
        line_end=10,
    )
    label = _make_label(
        vuln_class=VulnClass.XSS,
        file_path="app/template.html",
        line_start=8,
        line_end=12,
    )
    quality = assessor.assess(finding, label)
    assert quality == EvidenceQuality.STRONG
