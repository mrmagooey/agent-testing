"""Tests for wilson_ci and StatisticalAnalyzer (evaluation/statistics.py)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from sec_review_framework.data.evaluation import (
    ConfidenceInterval,
    EvaluationResult,
    EvidenceQuality,
    GroundTruthLabel,
    GroundTruthSource,
    MatchedFinding,
    MatchStatus,
)
from sec_review_framework.data.findings import (
    Finding,
    Severity,
    VulnClass,
)
from sec_review_framework.evaluation.statistics import StatisticalAnalyzer, wilson_ci

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_label(label_id: str = "lbl-1") -> GroundTruthLabel:
    return GroundTruthLabel(
        id=label_id,
        dataset_version="1.0.0",
        file_path="app/views.py",
        line_start=10,
        line_end=15,
        cwe_id="CWE-89",
        vuln_class=VulnClass.SQLI,
        severity=Severity.HIGH,
        description="SQL injection",
        source=GroundTruthSource.CVE_PATCH,
        confidence="confirmed",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _make_finding(finding_id: str = "f-1") -> Finding:
    return Finding(
        id=finding_id,
        file_path="app/views.py",
        line_start=10,
        vuln_class=VulnClass.SQLI,
        severity=Severity.HIGH,
        title="SQL Injection",
        description="...",
        confidence=0.9,
        raw_llm_output="",
        produced_by="single_agent",
        experiment_id="exp-001",
    )


def _make_eval_result(
    tp: int = 1,
    fp: int = 0,
    fn: int = 0,
    matched_findings: list | None = None,
) -> EvaluationResult:
    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) > 0 else 0.0
    return EvaluationResult(
        experiment_id="exp-001",
        dataset_version="1.0.0",
        total_labels=tp + fn,
        total_findings=tp + fp,
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        unlabeled_real_count=0,
        precision=precision,
        recall=recall,
        f1=f1,
        false_positive_rate=fp / (fp + (tp + fn - tp)) if (fp + (tp + fn - tp)) > 0 else 0.0,
        matched_findings=matched_findings or [],
        unmatched_labels=[],
        evidence_quality_counts={},
    )


# ---------------------------------------------------------------------------
# wilson_ci
# ---------------------------------------------------------------------------


def test_wilson_ci_perfect_score_ci_near_one():
    """10/10 successes → point_estimate=1.0 and lower bound close to 1.0."""
    ci = wilson_ci(10, 10)
    assert ci.point_estimate == pytest.approx(1.0)
    assert ci.lower > 0.7  # Wilson lower bound for 10/10 at 95% CI


def test_wilson_ci_zero_successes_point_estimate_is_zero():
    """0/10 successes → point_estimate=0.0."""
    ci = wilson_ci(0, 10)
    assert ci.point_estimate == pytest.approx(0.0)


def test_wilson_ci_zero_trials_returns_all_zeros():
    """n=0 → degenerate CI with all zeros."""
    ci = wilson_ci(0, 0)
    assert ci.point_estimate == 0.0
    assert ci.lower == 0.0
    assert ci.upper == 0.0


@pytest.mark.parametrize("successes,n", [
    (0, 10),
    (5, 10),
    (10, 10),
    (1, 100),
    (50, 100),
])
def test_wilson_ci_lower_le_point_estimate_le_upper(successes: int, n: int):
    ci = wilson_ci(successes, n)
    assert ci.lower <= ci.point_estimate <= ci.upper


# ---------------------------------------------------------------------------
# StatisticalAnalyzer
# ---------------------------------------------------------------------------


def test_precision_ci_aggregates_across_evaluation_results():
    results = [
        _make_eval_result(tp=3, fp=1, fn=1),
        _make_eval_result(tp=2, fp=2, fn=1),
    ]
    analyzer = StatisticalAnalyzer()
    ci = analyzer.precision_ci(results)
    assert isinstance(ci, ConfidenceInterval)
    # Total TP=5, FP=3 → precision = 5/8 ≈ 0.625
    assert ci.point_estimate == pytest.approx(5 / 8, abs=0.01)


def test_recall_ci_aggregates_across_evaluation_results():
    results = [
        _make_eval_result(tp=3, fp=1, fn=2),
        _make_eval_result(tp=2, fp=0, fn=3),
    ]
    analyzer = StatisticalAnalyzer()
    ci = analyzer.recall_ci(results)
    assert isinstance(ci, ConfidenceInterval)
    # Total TP=5, FN=5 → recall = 5/10 = 0.5
    assert ci.point_estimate == pytest.approx(0.5, abs=0.01)


def test_mcnemar_identical_detections_not_significant():
    """When both models detect exactly the same labels, McNemar's test is not significant."""
    label = _make_label("lbl-a")
    finding = _make_finding("f-a")

    mf = MatchedFinding(
        finding=finding,
        matched_label=label,
        match_status=MatchStatus.TRUE_POSITIVE,
        file_match=True,
        line_overlap=True,
        evidence_quality=EvidenceQuality.STRONG,
    )

    result = _make_eval_result(tp=1, fp=0, fn=0, matched_findings=[mf])

    analyzer = StatisticalAnalyzer()
    out = analyzer.mcnemar_test([result], [result], [label])

    assert out["significant"] is False
    assert out["statistic"] == pytest.approx(0.0)
    assert out["p_value"] == pytest.approx(1.0)


def test_mcnemar_different_detections_returns_valid_statistic():
    """When models detect different labels, McNemar returns a numeric result."""
    label_a = _make_label("lbl-a")
    label_b = _make_label("lbl-b")
    label_b.id = "lbl-b"

    finding_a = _make_finding("f-a")
    finding_b = _make_finding("f-b")

    # Model A detects label_a but not label_b.
    mf_a = MatchedFinding(
        finding=finding_a,
        matched_label=label_a,
        match_status=MatchStatus.TRUE_POSITIVE,
        file_match=True,
        line_overlap=True,
    )
    result_a = _make_eval_result(tp=1, fp=0, fn=1, matched_findings=[mf_a])

    # Model B detects label_b but not label_a.
    mf_b = MatchedFinding(
        finding=finding_b,
        matched_label=label_b,
        match_status=MatchStatus.TRUE_POSITIVE,
        file_match=True,
        line_overlap=True,
    )
    result_b = _make_eval_result(tp=1, fp=0, fn=1, matched_findings=[mf_b])

    analyzer = StatisticalAnalyzer()
    out = analyzer.mcnemar_test([result_a], [result_b], [label_a, label_b])

    assert "statistic" in out
    assert "p_value" in out
    assert isinstance(out["statistic"], float)
    assert isinstance(out["p_value"], float)
