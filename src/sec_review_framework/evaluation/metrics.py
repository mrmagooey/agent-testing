"""compute_metrics helper — derives EvaluationResult from matched findings."""

from sec_review_framework.data.evaluation import (
    EvaluationResult,
    EvidenceQuality,
    GroundTruthLabel,
    MatchedFinding,
    MatchStatus,
    StratifiedMetric,
)

# Inclusive lower, inclusive upper; None upper means open-ended.
PATCH_SIZE_BUCKETS: list[tuple[str, int, int | None]] = [
    ("1", 1, 1),
    ("2-10", 2, 10),
    ("11-50", 11, 50),
    ("51-200", 51, 200),
    ("200+", 201, None),
]
UNKNOWN_BUCKET = "unknown"


def _bucket_for(lines: int | None) -> str:
    if lines is None or lines <= 0:
        return UNKNOWN_BUCKET
    for name, lo, hi in PATCH_SIZE_BUCKETS:
        if lines >= lo and (hi is None or lines <= hi):
            return name
    return UNKNOWN_BUCKET


def _compute_patch_size_strata(
    matched: list[MatchedFinding],
    all_labels: list[GroundTruthLabel],
) -> list[StratifiedMetric]:
    """Group labels by patch-size bucket and compute per-bucket recall.

    False positives are excluded — they have no matched label and can't be
    attributed to a bucket.
    """
    label_bucket: dict[str, str] = {label.id: _bucket_for(label.patch_lines_changed) for label in all_labels}
    totals: dict[str, int] = {}
    tps: dict[str, int] = {}
    for label in all_labels:
        b = label_bucket[label.id]
        totals[b] = totals.get(b, 0) + 1

    for m in matched:
        if m.match_status != MatchStatus.TRUE_POSITIVE or m.matched_label is None:
            continue
        b = label_bucket.get(m.matched_label.id, _bucket_for(m.matched_label.patch_lines_changed))
        tps[b] = tps.get(b, 0) + 1

    ordered_buckets = [name for name, _, _ in PATCH_SIZE_BUCKETS] + [UNKNOWN_BUCKET]
    strata: list[StratifiedMetric] = []
    for name in ordered_buckets:
        total = totals.get(name, 0)
        if total == 0:
            continue
        tp = tps.get(name, 0)
        fn = total - tp
        recall = tp / total if total > 0 else 0.0
        strata.append(
            StratifiedMetric(
                bucket=name,
                total_labels=total,
                true_positives=tp,
                false_negatives=fn,
                recall=recall,
            )
        )
    return strata


def compute_metrics(
    matched: list[MatchedFinding],
    unmatched_labels: list[GroundTruthLabel],
    all_labels: list[GroundTruthLabel],
    total_file_count: int,
    experiment_id: str,
    dataset_version: str,
) -> EvaluationResult:
    """
    Compute precision, recall, F1, and FPR from bipartite-matched findings.

    Parameters
    ----------
    matched:
        All MatchedFinding objects (TPs, FPs, and UNLABELED_REALs).
    unmatched_labels:
        Ground truth labels that had no corresponding finding (false negatives).
    all_labels:
        Full label list — used to estimate true-negative file count.
    total_file_count:
        Total number of files in the repository (for FPR denominator).
    experiment_id:
        ID of the experiment run.
    dataset_version:
        Version string of the dataset used.

    Returns
    -------
    EvaluationResult
    """
    tp = sum(1 for m in matched if m.match_status == MatchStatus.TRUE_POSITIVE)
    fp = sum(1 for m in matched if m.match_status == MatchStatus.FALSE_POSITIVE)
    fn = len(unmatched_labels)
    unlabeled_real = sum(1 for m in matched if m.match_status == MatchStatus.UNLABELED_REAL)

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    # FPR: TN estimated as files in repo minus files with labels
    positive_files = len(set(label.file_path for label in all_labels))
    tn = max(0, total_file_count - positive_files)
    fpr = fp / (fp + tn) if (fp + tn) > 0 else 0.0

    # Evidence quality breakdown across all TPs
    tp_findings = [m for m in matched if m.match_status == MatchStatus.TRUE_POSITIVE]
    evidence_quality_counts: dict[str, int] = {eq.value: 0 for eq in EvidenceQuality}
    for mf in tp_findings:
        key = mf.evidence_quality.value
        evidence_quality_counts[key] = evidence_quality_counts.get(key, 0) + 1

    return EvaluationResult(
        experiment_id=experiment_id,
        dataset_version=dataset_version,
        total_labels=len(all_labels),
        total_findings=len(matched),
        true_positives=tp,
        false_positives=fp,
        false_negatives=fn,
        unlabeled_real_count=unlabeled_real,
        precision=precision,
        recall=recall,
        f1=f1,
        false_positive_rate=fpr,
        matched_findings=matched,
        unmatched_labels=unmatched_labels,
        evidence_quality_counts=evidence_quality_counts,
        patch_size_strata=_compute_patch_size_strata(matched, all_labels),
    )
