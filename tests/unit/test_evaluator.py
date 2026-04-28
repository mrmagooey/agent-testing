"""Unit tests for FileLevelEvaluator — bipartite matching and metric computation."""

from datetime import UTC, datetime

import pytest

from sec_review_framework.data.evaluation import (
    GroundTruthLabel,
    GroundTruthSource,
    MatchStatus,
)
from sec_review_framework.data.findings import Finding, Severity, VulnClass
from sec_review_framework.evaluation.evaluator import FileLevelEvaluator


def _make_finding(
    id: str,
    file_path: str = "app/views.py",
    vuln_class: VulnClass = VulnClass.SQLI,
    line_start: int = 10,
    line_end: int = 15,
    confidence: float = 0.9,
) -> Finding:
    return Finding(
        id=id,
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        vuln_class=vuln_class,
        severity=Severity.HIGH,
        title=f"Finding {id}",
        description=f"app/views.py:{line_start} SQL query injection via user input.",
        confidence=confidence,
        raw_llm_output="",
        produced_by="single_agent",
        experiment_id="test-exp",
    )


def _make_label(
    id: str,
    file_path: str = "app/views.py",
    vuln_class: VulnClass = VulnClass.SQLI,
    line_start: int = 10,
    line_end: int = 15,
) -> GroundTruthLabel:
    return GroundTruthLabel(
        id=id,
        dataset_version="1.0.0",
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        cwe_id="CWE-89",
        vuln_class=vuln_class,
        severity=Severity.HIGH,
        description="SQL injection",
        source=GroundTruthSource.CVE_PATCH,
        confidence="confirmed",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


# ---------------------------------------------------------------------------
# Test: perfect match — all findings are TPs
# ---------------------------------------------------------------------------


def test_perfect_match_all_true_positives():
    """When findings exactly match labels, all should be TP with precision=recall=1."""
    evaluator = FileLevelEvaluator(experiment_id="exp1", dataset_version="1.0.0")

    findings = [_make_finding("f1", file_path="app/views.py", vuln_class=VulnClass.SQLI)]
    labels = [_make_label("l1", file_path="app/views.py", vuln_class=VulnClass.SQLI)]

    result = evaluator.evaluate(findings, labels)

    assert result.true_positives == 1
    assert result.false_positives == 0
    assert result.false_negatives == 0
    assert result.precision == pytest.approx(1.0)
    assert result.recall == pytest.approx(1.0)
    assert result.f1 == pytest.approx(1.0)
    assert all(
        mf.match_status == MatchStatus.TRUE_POSITIVE
        for mf in result.matched_findings
    )


# ---------------------------------------------------------------------------
# Test: no matching labels — all findings are FPs
# ---------------------------------------------------------------------------


def test_no_matches_all_false_positives():
    """Findings on a different file from labels should all be FPs."""
    evaluator = FileLevelEvaluator(experiment_id="exp1", dataset_version="1.0.0")

    findings = [_make_finding("f1", file_path="app/other.py", vuln_class=VulnClass.XSS)]
    labels = [_make_label("l1", file_path="app/views.py", vuln_class=VulnClass.SQLI)]

    result = evaluator.evaluate(findings, labels)

    assert result.true_positives == 0
    assert result.false_positives == 1
    assert result.false_negatives == 1
    assert result.precision == pytest.approx(0.0)
    assert result.recall == pytest.approx(0.0)
    assert result.f1 == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# Test: partial match — mixed TP and FP
# ---------------------------------------------------------------------------


def test_partial_match():
    """Two findings, one matches, one does not. Expect 1 TP + 1 FP + 1 FN."""
    evaluator = FileLevelEvaluator(experiment_id="exp1", dataset_version="1.0.0")

    findings = [
        _make_finding("f1", file_path="app/views.py", vuln_class=VulnClass.SQLI, line_start=10, line_end=15),
        _make_finding("f2", file_path="app/unrelated.py", vuln_class=VulnClass.XSS, line_start=5, line_end=8),
    ]
    labels = [
        _make_label("l1", file_path="app/views.py", vuln_class=VulnClass.SQLI, line_start=10, line_end=15),
        _make_label("l2", file_path="app/auth.py", vuln_class=VulnClass.AUTH_BYPASS, line_start=20, line_end=25),
    ]

    result = evaluator.evaluate(findings, labels)

    assert result.true_positives == 1
    assert result.false_positives == 1
    assert result.false_negatives == 1
    assert result.precision == pytest.approx(0.5)
    assert result.recall == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Test: bipartite matching outperforms naive greedy for a file with 2 labels
# ---------------------------------------------------------------------------


def test_bipartite_matching_advantage_over_greedy():
    """
    Scenario: one file has two labels (sqli + xss). Two findings match the same
    file. Optimal bipartite matching should assign each finding to its correct
    label, yielding 2 TPs. A naive greedy matcher might assign both findings to
    the same label (sqli), leaving the xss label unmatched — only 1 TP.
    """
    evaluator = FileLevelEvaluator(experiment_id="exp1", dataset_version="1.0.0")

    # Finding 1 is a sqli finding; Finding 2 is an xss finding — both on same file
    findings = [
        _make_finding("f-sqli", file_path="app/views.py", vuln_class=VulnClass.SQLI, line_start=10, line_end=15),
        _make_finding("f-xss", file_path="app/views.py", vuln_class=VulnClass.XSS, line_start=30, line_end=35),
    ]
    # Two labels on same file
    labels = [
        _make_label("l-sqli", file_path="app/views.py", vuln_class=VulnClass.SQLI, line_start=10, line_end=15),
        _make_label("l-xss", file_path="app/views.py", vuln_class=VulnClass.XSS, line_start=30, line_end=35),
    ]

    result = evaluator.evaluate(findings, labels)

    # Optimal matching: 2 TPs (one per label-finding pair)
    assert result.true_positives == 2
    assert result.false_positives == 0
    assert result.false_negatives == 0
    assert result.precision == pytest.approx(1.0)
    assert result.recall == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Test: empty inputs
# ---------------------------------------------------------------------------


def test_empty_inputs_returns_zero_result():
    evaluator = FileLevelEvaluator(experiment_id="exp1", dataset_version="1.0.0")
    result = evaluator.evaluate([], [])

    assert result.true_positives == 0
    assert result.false_positives == 0
    assert result.false_negatives == 0
    assert result.precision == 0.0
    assert result.recall == 0.0
    assert result.f1 == 0.0
