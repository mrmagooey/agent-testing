"""compute_metrics helper — derives EvaluationResult from matched findings."""

from sec_review_framework.data.evaluation import (
    EvaluationResult,
    EvidenceQuality,
    GroundTruthLabel,
    MatchedFinding,
    MatchStatus,
)


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
    )
