"""Tests for patch-size stratification in compute_metrics and label propagation."""

from __future__ import annotations

from datetime import UTC, datetime

from sec_review_framework.data.evaluation import (
    EvidenceQuality,
    GroundTruthLabel,
    GroundTruthSource,
    MatchedFinding,
    MatchStatus,
)
from sec_review_framework.data.findings import Finding, Severity, VulnClass
from sec_review_framework.evaluation.metrics import (
    PATCH_SIZE_BUCKETS,
    UNKNOWN_BUCKET,
    _bucket_for,
    compute_metrics,
)


def _label(id: str, patch_lines: int | None) -> GroundTruthLabel:
    return GroundTruthLabel(
        id=id,
        dataset_version="v1",
        file_path=f"app/{id}.py",
        line_start=1,
        line_end=1,
        cwe_id="CWE-89",
        vuln_class=VulnClass.SQLI,
        severity=Severity.HIGH,
        description="x",
        source=GroundTruthSource.CVE_PATCH,
        confidence="confirmed",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        patch_lines_changed=patch_lines,
    )


def _finding(id: str, file_path: str) -> Finding:
    return Finding(
        id=id,
        file_path=file_path,
        line_start=1,
        line_end=1,
        vuln_class=VulnClass.SQLI,
        severity=Severity.HIGH,
        title="f",
        description="f",
        confidence=0.9,
        raw_llm_output="",
        produced_by="t",
        experiment_id="exp",
    )


def _matched(finding: Finding, label: GroundTruthLabel | None, status: MatchStatus) -> MatchedFinding:
    return MatchedFinding(
        finding=finding,
        matched_label=label,
        match_status=status,
        file_match=label is not None,
        line_overlap=label is not None,
        evidence_quality=EvidenceQuality.STRONG,
    )


def test_bucket_for_boundaries():
    assert _bucket_for(1) == "1"
    assert _bucket_for(2) == "2-10"
    assert _bucket_for(10) == "2-10"
    assert _bucket_for(11) == "11-50"
    assert _bucket_for(50) == "11-50"
    assert _bucket_for(51) == "51-200"
    assert _bucket_for(200) == "51-200"
    assert _bucket_for(201) == "200+"
    assert _bucket_for(100_000) == "200+"


def test_bucket_for_none_and_zero_go_to_unknown():
    assert _bucket_for(None) == UNKNOWN_BUCKET
    assert _bucket_for(0) == UNKNOWN_BUCKET
    assert _bucket_for(-5) == UNKNOWN_BUCKET


def test_strata_recall_per_bucket():
    labels = [
        _label("a", 1),     # single-line, will be TP
        _label("b", 1),     # single-line, will be FN
        _label("c", 25),    # medium, will be TP
        _label("d", 120),   # large, will be FN
    ]
    f_a = _finding("fa", labels[0].file_path)
    f_c = _finding("fc", labels[2].file_path)
    matched = [
        _matched(f_a, labels[0], MatchStatus.TRUE_POSITIVE),
        _matched(f_c, labels[2], MatchStatus.TRUE_POSITIVE),
    ]
    unmatched = [labels[1], labels[3]]

    result = compute_metrics(
        matched=matched,
        unmatched_labels=unmatched,
        all_labels=labels,
        total_file_count=10,
        experiment_id="exp",
        dataset_version="v1",
    )

    by_bucket = {s.bucket: s for s in result.patch_size_strata}

    assert by_bucket["1"].total_labels == 2
    assert by_bucket["1"].true_positives == 1
    assert by_bucket["1"].false_negatives == 1
    assert by_bucket["1"].recall == 0.5

    assert by_bucket["11-50"].total_labels == 1
    assert by_bucket["11-50"].true_positives == 1
    assert by_bucket["11-50"].recall == 1.0

    assert by_bucket["51-200"].total_labels == 1
    assert by_bucket["51-200"].true_positives == 0
    assert by_bucket["51-200"].recall == 0.0

    # No labels fell into these buckets, so they must not appear.
    assert "2-10" not in by_bucket
    assert "200+" not in by_bucket
    assert UNKNOWN_BUCKET not in by_bucket


def test_strata_unknown_bucket_when_patch_size_missing():
    labels = [_label("a", None), _label("b", 5)]
    f_a = _finding("fa", labels[0].file_path)
    matched = [_matched(f_a, labels[0], MatchStatus.TRUE_POSITIVE)]
    result = compute_metrics(
        matched=matched,
        unmatched_labels=[labels[1]],
        all_labels=labels,
        total_file_count=5,
        experiment_id="exp",
        dataset_version="v1",
    )
    by_bucket = {s.bucket: s for s in result.patch_size_strata}
    assert by_bucket[UNKNOWN_BUCKET].total_labels == 1
    assert by_bucket[UNKNOWN_BUCKET].recall == 1.0
    assert by_bucket["2-10"].total_labels == 1
    assert by_bucket["2-10"].recall == 0.0


def test_strata_ignores_false_positives():
    labels = [_label("a", 1)]
    f_fp = _finding("fp", "other.py")
    matched = [_matched(f_fp, None, MatchStatus.FALSE_POSITIVE)]
    result = compute_metrics(
        matched=matched,
        unmatched_labels=labels,
        all_labels=labels,
        total_file_count=5,
        experiment_id="exp",
        dataset_version="v1",
    )
    # FP does not create or populate a stratum.
    by_bucket = {s.bucket: s for s in result.patch_size_strata}
    assert set(by_bucket.keys()) == {"1"}
    assert by_bucket["1"].true_positives == 0
    assert by_bucket["1"].recall == 0.0


def test_strata_empty_when_no_labels():
    result = compute_metrics(
        matched=[],
        unmatched_labels=[],
        all_labels=[],
        total_file_count=0,
        experiment_id="exp",
        dataset_version="v1",
    )
    assert result.patch_size_strata == []


def test_bucket_order_preserved_in_output():
    labels = [
        _label("a", 300),
        _label("b", 1),
        _label("c", 100),
        _label("d", 5),
    ]
    result = compute_metrics(
        matched=[],
        unmatched_labels=labels,
        all_labels=labels,
        total_file_count=10,
        experiment_id="exp",
        dataset_version="v1",
    )
    order = [s.bucket for s in result.patch_size_strata]
    canonical = [name for name, _, _ in PATCH_SIZE_BUCKETS]
    # Emitted buckets must appear in the canonical left-to-right order.
    assert order == [b for b in canonical if b in order]
