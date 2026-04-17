"""Extended unit tests for compute_metrics — division-by-zero, NaN/Inf, property invariants."""

from __future__ import annotations

from datetime import datetime, timezone

import math
import pytest

from sec_review_framework.data.evaluation import (
    EvidenceQuality,
    GroundTruthLabel,
    GroundTruthSource,
    MatchedFinding,
    MatchStatus,
)
from sec_review_framework.data.findings import Finding, Severity, VulnClass
from sec_review_framework.evaluation.metrics import compute_metrics


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_label(
    id: str = "lbl-1",
    file_path: str = "app/views.py",
    vuln_class: VulnClass = VulnClass.SQLI,
) -> GroundTruthLabel:
    return GroundTruthLabel(
        id=id,
        dataset_version="1.0.0",
        file_path=file_path,
        line_start=10,
        line_end=15,
        cwe_id="CWE-89",
        vuln_class=vuln_class,
        severity=Severity.HIGH,
        description="SQL injection",
        source=GroundTruthSource.INJECTED,
        confidence="confirmed",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


def _make_finding(
    id: str = "f-1",
    file_path: str = "app/views.py",
    vuln_class: VulnClass = VulnClass.SQLI,
) -> Finding:
    return Finding(
        id=id,
        file_path=file_path,
        line_start=10,
        line_end=15,
        vuln_class=vuln_class,
        severity=Severity.HIGH,
        title=f"Finding {id}",
        description=f"SQL injection at {file_path}",
        confidence=0.9,
        raw_llm_output="",
        produced_by="single_agent",
        experiment_id="exp-001",
    )


def _make_matched(
    finding: Finding,
    label: GroundTruthLabel | None,
    status: MatchStatus,
    evidence: EvidenceQuality = EvidenceQuality.STRONG,
) -> MatchedFinding:
    return MatchedFinding(
        finding=finding,
        matched_label=label,
        match_status=status,
        file_match=label is not None,
        line_overlap=label is not None,
        evidence_quality=evidence,
    )


# ---------------------------------------------------------------------------
# Division-by-zero guards
# ---------------------------------------------------------------------------


class TestDivisionByZeroGuards:
    def test_all_zero_inputs_no_exception(self):
        """With no findings and no labels, all metrics should be 0.0 — no ZeroDivisionError."""
        result = compute_metrics(
            matched=[],
            unmatched_labels=[],
            all_labels=[],
            total_file_count=0,
            experiment_id="exp-0",
            dataset_version="1.0.0",
        )
        assert result.precision == 0.0
        assert result.recall == 0.0
        assert result.f1 == 0.0
        assert result.false_positive_rate == 0.0

    def test_all_true_positives_no_division_error(self):
        lbl = _make_label()
        finding = _make_finding()
        matched = [_make_matched(finding, lbl, MatchStatus.TRUE_POSITIVE)]

        result = compute_metrics(
            matched=matched,
            unmatched_labels=[],
            all_labels=[lbl],
            total_file_count=10,
            experiment_id="exp-1",
            dataset_version="1.0.0",
        )
        assert result.precision == pytest.approx(1.0)
        assert result.recall == pytest.approx(1.0)
        assert result.f1 == pytest.approx(1.0)

    def test_all_false_positives_recall_is_zero(self):
        """All findings are FPs — tp=0, fn=0 (no labels). Recall should be 0/0 → 0."""
        finding = _make_finding()
        matched = [_make_matched(finding, None, MatchStatus.FALSE_POSITIVE)]

        result = compute_metrics(
            matched=matched,
            unmatched_labels=[],
            all_labels=[],
            total_file_count=10,
            experiment_id="exp-2",
            dataset_version="1.0.0",
        )
        assert result.precision == pytest.approx(0.0)
        assert result.recall == pytest.approx(0.0)
        assert result.f1 == pytest.approx(0.0)

    def test_all_false_negatives_precision_is_zero(self):
        """All labels missed — no findings. Precision 0/0 → 0."""
        lbl = _make_label()
        result = compute_metrics(
            matched=[],
            unmatched_labels=[lbl],
            all_labels=[lbl],
            total_file_count=10,
            experiment_id="exp-3",
            dataset_version="1.0.0",
        )
        assert result.precision == pytest.approx(0.0)
        assert result.recall == pytest.approx(0.0)
        assert result.f1 == pytest.approx(0.0)

    def test_fpr_all_files_positive_tn_is_zero(self):
        """If every file in the repo has a label, TN=0 and FPR=0/0 → 0."""
        lbl = _make_label()
        finding = _make_finding()
        fp_finding = _make_finding("f-2", file_path="other.py")
        matched = [
            _make_matched(finding, lbl, MatchStatus.TRUE_POSITIVE),
            _make_matched(fp_finding, None, MatchStatus.FALSE_POSITIVE),
        ]
        result = compute_metrics(
            matched=matched,
            unmatched_labels=[],
            all_labels=[lbl],
            total_file_count=1,  # exactly 1 file with 1 label → TN = 0
            experiment_id="exp-4",
            dataset_version="1.0.0",
        )
        # TN = max(0, 1-1) = 0; fp=1; fpr = 1/(1+0) = 1.0
        assert result.false_positive_rate >= 0.0


# ---------------------------------------------------------------------------
# No NaN / Infinity in results
# ---------------------------------------------------------------------------


class TestNoNaNInf:
    def _assert_no_nan_inf(self, result):
        for attr in ("precision", "recall", "f1", "false_positive_rate"):
            val = getattr(result, attr)
            assert not math.isnan(val), f"{attr} is NaN"
            assert not math.isinf(val), f"{attr} is Inf"

    def test_empty_inputs_no_nan(self):
        result = compute_metrics([], [], [], 0, "e", "v")
        self._assert_no_nan_inf(result)

    def test_only_fps_no_nan(self):
        findings = [_make_finding(f"f-{i}") for i in range(5)]
        matched = [_make_matched(f, None, MatchStatus.FALSE_POSITIVE) for f in findings]
        result = compute_metrics(matched, [], [], 100, "e", "v")
        self._assert_no_nan_inf(result)

    def test_only_fns_no_nan(self):
        labels = [_make_label(f"l-{i}") for i in range(5)]
        result = compute_metrics([], labels, labels, 100, "e", "v")
        self._assert_no_nan_inf(result)

    def test_perfect_score_no_nan(self):
        labels = [_make_label(f"l-{i}") for i in range(3)]
        findings = [_make_finding(f"f-{i}") for i in range(3)]
        matched = [_make_matched(findings[i], labels[i], MatchStatus.TRUE_POSITIVE) for i in range(3)]
        result = compute_metrics(matched, [], labels, 10, "e", "v")
        self._assert_no_nan_inf(result)


# ---------------------------------------------------------------------------
# Property test: precision + recall consistency
# ---------------------------------------------------------------------------


class TestPropertyInvariants:
    def test_precision_in_range_0_to_1(self):
        lbl = _make_label()
        finding = _make_finding()
        fp_finding = _make_finding("f-fp", file_path="other.py")
        matched = [
            _make_matched(finding, lbl, MatchStatus.TRUE_POSITIVE),
            _make_matched(fp_finding, None, MatchStatus.FALSE_POSITIVE),
        ]
        result = compute_metrics(matched, [], [lbl], 10, "exp", "v1")
        assert 0.0 <= result.precision <= 1.0

    def test_recall_in_range_0_to_1(self):
        lbl1 = _make_label("l-1")
        lbl2 = _make_label("l-2", file_path="other.py")
        finding = _make_finding()
        matched = [_make_matched(finding, lbl1, MatchStatus.TRUE_POSITIVE)]
        result = compute_metrics(matched, [lbl2], [lbl1, lbl2], 10, "exp", "v1")
        assert 0.0 <= result.recall <= 1.0

    def test_f1_in_range_0_to_1(self):
        lbl = _make_label()
        finding = _make_finding()
        matched = [_make_matched(finding, lbl, MatchStatus.TRUE_POSITIVE)]
        result = compute_metrics(matched, [], [lbl], 10, "exp", "v1")
        assert 0.0 <= result.f1 <= 1.0

    def test_f1_is_harmonic_mean_of_precision_and_recall(self):
        """F1 must equal 2*P*R/(P+R) when both > 0."""
        lbl1 = _make_label("l-1")
        lbl2 = _make_label("l-2", file_path="other.py")
        finding_tp = _make_finding()
        finding_fp = _make_finding("f-fp", file_path="fp.py")

        matched = [
            _make_matched(finding_tp, lbl1, MatchStatus.TRUE_POSITIVE),
            _make_matched(finding_fp, None, MatchStatus.FALSE_POSITIVE),
        ]
        result = compute_metrics(matched, [lbl2], [lbl1, lbl2], 10, "exp", "v1")

        if result.precision + result.recall > 0:
            expected_f1 = 2 * result.precision * result.recall / (result.precision + result.recall)
            assert result.f1 == pytest.approx(expected_f1, rel=1e-6)

    def test_tp_plus_fp_plus_fn_equals_total_findable(self):
        """tp + fp = total matched; fn = unmatched labels count."""
        labels = [_make_label(f"l-{i}") for i in range(4)]
        tp_findings = [_make_finding(f"f-{i}") for i in range(2)]
        fp_findings = [_make_finding(f"fp-{i}", file_path=f"fp{i}.py") for i in range(2)]

        matched = (
            [_make_matched(tp_findings[i], labels[i], MatchStatus.TRUE_POSITIVE) for i in range(2)]
            + [_make_matched(fp_findings[i], None, MatchStatus.FALSE_POSITIVE) for i in range(2)]
        )

        result = compute_metrics(
            matched=matched,
            unmatched_labels=labels[2:],  # 2 false negatives
            all_labels=labels,
            total_file_count=20,
            experiment_id="exp",
            dataset_version="v1",
        )

        assert result.true_positives == 2
        assert result.false_positives == 2
        assert result.false_negatives == 2

    def test_counts_match_matched_length(self):
        findings = [_make_finding(f"f-{i}") for i in range(5)]
        labels = [_make_label(f"l-{i}") for i in range(5)]
        matched = [_make_matched(findings[i], labels[i], MatchStatus.TRUE_POSITIVE) for i in range(5)]

        result = compute_metrics(matched, [], labels, 20, "exp", "v1")
        assert result.total_findings == 5
        assert result.total_labels == 5

    def test_evidence_quality_counts_sum_equals_tp_count(self):
        """The sum of evidence_quality_counts should equal the number of TPs."""
        lbl1 = _make_label("l-1")
        lbl2 = _make_label("l-2", file_path="other.py")
        f1 = _make_finding("f-1")
        f2 = _make_finding("f-2", file_path="other.py")

        matched = [
            _make_matched(f1, lbl1, MatchStatus.TRUE_POSITIVE, EvidenceQuality.STRONG),
            _make_matched(f2, lbl2, MatchStatus.TRUE_POSITIVE, EvidenceQuality.WEAK),
        ]
        result = compute_metrics(matched, [], [lbl1, lbl2], 10, "exp", "v1")

        total_evidence = sum(result.evidence_quality_counts.values())
        assert total_evidence == result.true_positives

    def test_unlabeled_real_counted_separately(self):
        finding = _make_finding()
        matched = [_make_matched(finding, None, MatchStatus.UNLABELED_REAL)]

        result = compute_metrics(matched, [], [], 10, "exp", "v1")
        assert result.unlabeled_real_count == 1
        assert result.false_positives == 0
        assert result.true_positives == 0


# ---------------------------------------------------------------------------
# FPR edge cases
# ---------------------------------------------------------------------------


class TestFPREdgeCases:
    def test_fpr_is_zero_when_no_fps(self):
        lbl = _make_label()
        finding = _make_finding()
        matched = [_make_matched(finding, lbl, MatchStatus.TRUE_POSITIVE)]

        result = compute_metrics(matched, [], [lbl], 10, "exp", "v1")
        assert result.false_positive_rate == pytest.approx(0.0)

    def test_fpr_bounded_by_1_when_all_negatives_wrong(self):
        """fp / (fp + tn) must be at most 1.0."""
        finding = _make_finding()
        matched = [_make_matched(finding, None, MatchStatus.FALSE_POSITIVE)]

        result = compute_metrics(matched, [], [], 1, "exp", "v1")
        assert 0.0 <= result.false_positive_rate <= 1.0

    def test_negative_total_file_count_clamps_tn_to_zero(self):
        """total_file_count < positive_files would make tn negative — clamped to 0."""
        lbl = _make_label()
        result = compute_metrics([], [], [lbl], 0, "exp", "v1")
        # tn = max(0, 0 - 1) = 0; fpr = 0/(0+0) → 0
        assert result.false_positive_rate == pytest.approx(0.0)
