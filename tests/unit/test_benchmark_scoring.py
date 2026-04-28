"""Unit tests for the polarity-aware benchmark scoring module.

Covers:
  - Small fixture (5 pos + 5 neg across 2 CWEs): sample-size guard fires,
    warning is emitted, counts are correct.
  - Large fixture (≥25 per polarity per CWE): metrics are computed when
    sample size is adequate.
  - Backward-compat: no negative labels → function returns None.
  - Each of TP, FP, TN, FN are exercised with correct counts.
  - Out-of-scope findings (CWE not in any label) do not affect scorecard.
  - Line-range matching (overlap vs. no overlap on positive labels).
  - Aggregate across CWEs.
"""

from __future__ import annotations

import pytest

from sec_review_framework.evaluation.benchmark_scoring import (
    BenchmarkScorecard,
    compute_benchmark_scorecard,
)
from sec_review_framework.data.findings import Finding, Severity, VulnClass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _pos_label(
    cwe_id: str,
    file_path: str,
    line_start: int | None = None,
    line_end: int | None = None,
    dataset_name: str = "ds",
) -> dict:
    row = {
        "id": f"pos-{cwe_id}-{file_path}",
        "dataset_name": dataset_name,
        "dataset_version": "v1",
        "file_path": file_path,
        "cwe_id": cwe_id,
        "vuln_class": "sqli",
        "severity": "high",
        "description": "test",
        "source": "cve_patch",
        "confidence": "confirmed",
        "created_at": "2024-01-01T00:00:00+00:00",
    }
    if line_start is not None:
        row["line_start"] = line_start
        row["line_end"] = line_end
    return row


def _neg_label(cwe_id: str, file_path: str, dataset_name: str = "ds") -> dict:
    return {
        "id": f"neg-{cwe_id}-{file_path}",
        "dataset_name": dataset_name,
        "dataset_version": "v1",
        "file_path": file_path,
        "cwe_id": cwe_id,
        "vuln_class": "sqli",
        "source": "benchmark",
        "created_at": "2024-01-01T00:00:00+00:00",
    }


def _finding(
    cwe_id: str,
    file_path: str,
    line_start: int | None = None,
    line_end: int | None = None,
) -> Finding:
    return Finding(
        id=f"f-{cwe_id}-{file_path}",
        file_path=file_path,
        vuln_class=VulnClass.SQLI,
        cwe_ids=[cwe_id],
        severity=Severity.HIGH,
        title="test finding",
        description="desc",
        confidence=0.8,
        line_start=line_start,
        line_end=line_end,
    )


# ---------------------------------------------------------------------------
# Backward-compat: no negative labels → returns None
# ---------------------------------------------------------------------------

def test_returns_none_when_no_negative_labels():
    """Backward-compat: datasets without negative labels produce None."""
    pos = [_pos_label("CWE-89", "app/views.py")]
    findings = [_finding("CWE-89", "app/views.py")]
    result = compute_benchmark_scorecard(
        findings=findings,
        positive_labels=pos,
        negative_labels=[],  # no negatives
        dataset_name="cve-dataset",
    )
    assert result is None


# ---------------------------------------------------------------------------
# Small fixture (5 pos + 5 neg, 2 CWEs): sample-size guard fires
# ---------------------------------------------------------------------------

def test_small_fixture_sample_size_warning():
    """5 positive + 5 negative labels per CWE triggers the insufficient-sample warning."""
    # CWE-89: 3 pos files, 3 neg files
    # CWE-79: 2 pos files, 2 neg files
    pos = [
        _pos_label("CWE-89", f"app/sqli_{i}.py") for i in range(3)
    ] + [
        _pos_label("CWE-79", f"app/xss_{i}.py") for i in range(2)
    ]
    neg = [
        _neg_label("CWE-89", f"app/clean_sqli_{i}.py") for i in range(3)
    ] + [
        _neg_label("CWE-79", f"app/clean_xss_{i}.py") for i in range(2)
    ]

    # Agent finds one TP for CWE-89 and one FP for CWE-89
    findings = [
        _finding("CWE-89", "app/sqli_0.py"),       # TP: positive label
        _finding("CWE-89", "app/clean_sqli_0.py"),  # FP: negative label
    ]

    result = compute_benchmark_scorecard(
        findings=findings,
        positive_labels=pos,
        negative_labels=neg,
        dataset_name="small-bench",
    )

    assert result is not None
    assert isinstance(result, BenchmarkScorecard)
    assert result.dataset_name == "small-bench"

    cwe89 = next(c for c in result.per_cwe if c.cwe_id == "CWE-89")
    cwe79 = next(c for c in result.per_cwe if c.cwe_id == "CWE-79")

    # CWE-89: TP=1, FP=1, FN=2, TN=2
    assert cwe89.tp == 1
    assert cwe89.fp == 1
    assert cwe89.fn == 2
    assert cwe89.tn == 2
    assert cwe89.warning is not None
    assert "insufficient sample size" in cwe89.warning
    # Precision/recall/f1 should be None due to sample size guard
    assert cwe89.precision is None
    assert cwe89.recall is None
    assert cwe89.f1 is None

    # CWE-79: TN=2, FN=2, TP=0, FP=0
    assert cwe79.tp == 0
    assert cwe79.fp == 0
    assert cwe79.fn == 2
    assert cwe79.tn == 2
    assert cwe79.warning is not None

    # Aggregate should still be computed
    agg = result.aggregate
    assert agg["tp"] == 1
    assert agg["fp"] == 1
    assert agg["fn"] == 4
    assert agg["tn"] == 4


# ---------------------------------------------------------------------------
# Large fixture (≥25 per polarity per CWE): metrics ARE computed
# ---------------------------------------------------------------------------

def _make_large_fixture(n_pos: int = 30, n_neg: int = 30, cwe: str = "CWE-89"):
    """Return (positive_labels, negative_labels, expected counts dict)."""
    pos = [_pos_label(cwe, f"app/vuln_{i}.py") for i in range(n_pos)]
    neg = [_neg_label(cwe, f"app/clean_{i}.py") for i in range(n_neg)]
    return pos, neg


def test_large_fixture_metrics_computed():
    """When ≥25 labels per polarity, metrics are emitted (not None)."""
    n_pos, n_neg = 30, 30
    cwe = "CWE-89"
    pos, neg = _make_large_fixture(n_pos, n_neg, cwe)

    # Agent hits 20/30 positive files (TP=20, FN=10)
    # and 5/30 negative files (FP=5, TN=25)
    findings = (
        [_finding(cwe, f"app/vuln_{i}.py") for i in range(20)]
        + [_finding(cwe, f"app/clean_{i}.py") for i in range(5)]
    )

    result = compute_benchmark_scorecard(
        findings=findings,
        positive_labels=pos,
        negative_labels=neg,
        dataset_name="large-bench",
    )

    assert result is not None
    assert len(result.per_cwe) == 1
    sc = result.per_cwe[0]

    assert sc.cwe_id == cwe
    assert sc.tp == 20
    assert sc.fn == 10
    assert sc.fp == 5
    assert sc.tn == 25
    assert sc.warning is None  # no warning for large sample

    # Precision = 20/(20+5) = 0.8
    assert sc.precision == pytest.approx(20 / 25)
    # Recall = 20/(20+10) = 0.667
    assert sc.recall == pytest.approx(20 / 30)
    # F1 = 2*0.8*(2/3) / (0.8+2/3)
    expected_f1 = 2 * (20 / 25) * (20 / 30) / ((20 / 25) + (20 / 30))
    assert sc.f1 == pytest.approx(expected_f1)
    # FP rate = 5/(5+25) = 0.167
    assert sc.fp_rate == pytest.approx(5 / 30)
    # OWASP score = TPR - FPR = 20/30 - 5/30 = 15/30 = 0.5
    assert sc.owasp_score == pytest.approx((20 / 30) - (5 / 30))


# ---------------------------------------------------------------------------
# TN: negative labels with no agent finding
# ---------------------------------------------------------------------------

def test_true_negatives_counted_correctly():
    """Negative-label files not hit by agent count as TN."""
    neg = [_neg_label("CWE-89", "app/clean.py")]
    findings = []  # agent reports nothing

    result = compute_benchmark_scorecard(
        findings=findings,
        positive_labels=[],
        negative_labels=neg,
        dataset_name="ds",
    )
    assert result is not None
    sc = result.per_cwe[0]
    assert sc.tn == 1
    assert sc.fp == 0
    assert sc.tp == 0
    assert sc.fn == 0


# ---------------------------------------------------------------------------
# FN: positive labels with no agent finding
# ---------------------------------------------------------------------------

def test_false_negatives_counted_correctly():
    """Positive labels not hit by the agent count as FN."""
    pos = [_pos_label("CWE-89", "app/vuln.py")]
    neg = [_neg_label("CWE-89", "app/clean.py")]  # need at least one neg for scorecard
    findings = []  # agent reports nothing

    result = compute_benchmark_scorecard(
        findings=findings,
        positive_labels=pos,
        negative_labels=neg,
        dataset_name="ds",
    )
    assert result is not None
    sc = result.per_cwe[0]
    assert sc.fn == 1
    assert sc.tn == 1
    assert sc.tp == 0
    assert sc.fp == 0


# ---------------------------------------------------------------------------
# Out-of-scope findings don't affect scorecard
# ---------------------------------------------------------------------------

def test_out_of_scope_finding_ignored():
    """A finding whose CWE is not in any label set is silently ignored."""
    pos = [_pos_label("CWE-89", "app/vuln.py")]
    neg = [_neg_label("CWE-89", "app/clean.py")]
    findings = [
        _finding("CWE-999", "app/vuln.py"),   # CWE not in labels
        _finding("CWE-999", "app/clean.py"),  # CWE not in labels
    ]

    result = compute_benchmark_scorecard(
        findings=findings,
        positive_labels=pos,
        negative_labels=neg,
        dataset_name="ds",
    )
    assert result is not None
    sc = result.per_cwe[0]
    # CWE-999 doesn't exist in any label, so agent does nothing for CWE-89
    assert sc.tp == 0
    assert sc.fp == 0
    assert sc.fn == 1
    assert sc.tn == 1


# ---------------------------------------------------------------------------
# Line-range overlap: TP requires overlapping lines when label has line range
# ---------------------------------------------------------------------------

def test_tp_requires_line_overlap_when_label_has_lines():
    """A finding on the correct file but wrong line range does not score as TP."""
    # Positive label: CWE-89, lines 10-20
    pos = [_pos_label("CWE-89", "app/vuln.py", line_start=10, line_end=20)]
    neg = [_neg_label("CWE-89", "app/clean.py")]

    # Finding: correct file, but lines 50-60 (no overlap with 10-20)
    findings = [_finding("CWE-89", "app/vuln.py", line_start=50, line_end=60)]

    result = compute_benchmark_scorecard(
        findings=findings,
        positive_labels=pos,
        negative_labels=neg,
        dataset_name="ds",
    )
    assert result is not None
    sc = result.per_cwe[0]
    # No line overlap → not counted as TP; it's also not FP (positive file, not negative)
    # The finding is out-of-scope (wrong line range on positive file)
    assert sc.tp == 0
    assert sc.fn == 1   # label not hit
    assert sc.fp == 0


def test_tp_on_overlapping_lines():
    """A finding on the correct file with overlapping lines scores as TP."""
    pos = [_pos_label("CWE-89", "app/vuln.py", line_start=10, line_end=20)]
    neg = [_neg_label("CWE-89", "app/clean.py")]
    findings = [_finding("CWE-89", "app/vuln.py", line_start=15, line_end=25)]

    result = compute_benchmark_scorecard(
        findings=findings,
        positive_labels=pos,
        negative_labels=neg,
        dataset_name="ds",
    )
    assert result is not None
    sc = result.per_cwe[0]
    assert sc.tp == 1
    assert sc.fn == 0


def test_tp_no_line_numbers_on_finding():
    """A finding with no line numbers on a positive file always counts as TP."""
    pos = [_pos_label("CWE-89", "app/vuln.py", line_start=10, line_end=20)]
    neg = [_neg_label("CWE-89", "app/clean.py")]
    findings = [_finding("CWE-89", "app/vuln.py")]  # no line numbers

    result = compute_benchmark_scorecard(
        findings=findings,
        positive_labels=pos,
        negative_labels=neg,
        dataset_name="ds",
    )
    assert result is not None
    sc = result.per_cwe[0]
    assert sc.tp == 1


# ---------------------------------------------------------------------------
# Multiple findings on same negative file: only one FP per file/CWE
# ---------------------------------------------------------------------------

def test_multiple_findings_on_same_neg_file_counted_once():
    """Two findings on the same negative file/CWE count as 1 FP (de-duplication)."""
    neg = [_neg_label("CWE-89", "app/clean.py")]
    findings = [
        _finding("CWE-89", "app/clean.py", line_start=10, line_end=20),
        _finding("CWE-89", "app/clean.py", line_start=30, line_end=40),
    ]

    result = compute_benchmark_scorecard(
        findings=findings,
        positive_labels=[],
        negative_labels=neg,
        dataset_name="ds",
    )
    assert result is not None
    sc = result.per_cwe[0]
    assert sc.fp == 1  # not 2


# ---------------------------------------------------------------------------
# to_dict() shape matches the spec
# ---------------------------------------------------------------------------

def test_to_dict_shape():
    """CWEScorecard.to_dict() includes all required keys."""
    neg = [_neg_label("CWE-89", "app/clean.py")]
    result = compute_benchmark_scorecard(
        findings=[],
        positive_labels=[],
        negative_labels=neg,
        dataset_name="ds",
    )
    assert result is not None
    d = result.to_dict()

    assert "dataset_name" in d
    assert "per_cwe" in d
    assert "aggregate" in d

    cwe_d = d["per_cwe"][0]
    required_keys = {"cwe_id", "tp", "fp", "tn", "fn",
                     "precision", "recall", "f1", "fp_rate", "owasp_score", "warning"}
    assert required_keys == set(cwe_d.keys())

    agg_keys = {"tp", "fp", "tn", "fn", "precision", "recall", "f1", "fp_rate", "owasp_score"}
    assert agg_keys.issubset(set(d["aggregate"].keys()))


# ---------------------------------------------------------------------------
# Multi-CWE aggregate is summed correctly
# ---------------------------------------------------------------------------

def test_aggregate_across_cwes():
    """Aggregate sums TP/FP/TN/FN across all CWEs."""
    pos = [
        _pos_label("CWE-89", "app/sqli.py"),
        _pos_label("CWE-79", "app/xss.py"),
    ]
    neg = [
        _neg_label("CWE-89", "app/clean_sqli.py"),
        _neg_label("CWE-79", "app/clean_xss.py"),
    ]
    findings = [
        _finding("CWE-89", "app/sqli.py"),       # TP for CWE-89
        _finding("CWE-79", "app/clean_xss.py"),  # FP for CWE-79
    ]

    result = compute_benchmark_scorecard(
        findings=findings,
        positive_labels=pos,
        negative_labels=neg,
        dataset_name="ds",
    )
    assert result is not None
    agg = result.aggregate

    # CWE-89: TP=1, FP=0, TN=1, FN=0
    # CWE-79: TP=0, FP=1, TN=0, FN=1
    # Aggregate: TP=1, FP=1, TN=1, FN=1
    assert agg["tp"] == 1
    assert agg["fp"] == 1
    assert agg["tn"] == 1
    assert agg["fn"] == 1
