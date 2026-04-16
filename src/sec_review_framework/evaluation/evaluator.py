"""Evaluator ABC and FileLevelEvaluator with bipartite matching."""

from abc import ABC, abstractmethod

import numpy as np
from scipy.optimize import linear_sum_assignment

from sec_review_framework.data.evaluation import (
    EvaluationResult,
    EvidenceQuality,
    GroundTruthLabel,
    MatchedFinding,
    MatchStatus,
)
from sec_review_framework.data.findings import Finding


class Evaluator(ABC):
    """Abstract base class for evaluation strategies."""

    @abstractmethod
    def evaluate(
        self,
        findings: list[Finding],
        labels: list[GroundTruthLabel],
    ) -> EvaluationResult:
        """Evaluate findings against ground truth labels."""
        ...


class FileLevelEvaluator(Evaluator):
    """
    Bipartite matching between findings and labels.

    Score considers: file path (required), vuln_class (high weight),
    line overlap (medium weight). Uses scipy linear_sum_assignment for
    optimal global matching.
    """

    def __init__(
        self,
        evidence_assessor=None,
        total_file_count: int = 0,
        experiment_id: str = "",
        dataset_version: str = "",
    ) -> None:
        self._evidence_assessor = evidence_assessor
        self._total_file_count = total_file_count
        self._experiment_id = experiment_id
        self._dataset_version = dataset_version

    def _get_evidence_assessor(self):
        """Return the evidence assessor, creating a default heuristic one if needed."""
        if self._evidence_assessor is not None:
            return self._evidence_assessor
        # Import here to avoid circular imports
        from sec_review_framework.evaluation.evidence import EvidenceQualityAssessor

        return EvidenceQualityAssessor()

    def _match_score(self, finding: Finding, label: GroundTruthLabel) -> float:
        """Score a (finding, label) pair. Returns -1.0 if file path doesn't match."""
        if finding.file_path != label.file_path:
            return -1.0  # hard constraint — never cross-file match

        score = 0.0

        # Vuln class match (high weight)
        if finding.vuln_class == label.vuln_class:
            score += 4.0

        # Line overlap (medium weight)
        if finding.line_start is not None and label.line_start is not None:
            f_end = finding.line_end or finding.line_start
            tolerance = 5
            label_range = range(label.line_start - tolerance, label.line_end + tolerance + 1)
            finding_range = range(finding.line_start, f_end + 1)
            overlap = len(set(finding_range) & set(label_range))
            if overlap > 0:
                score += 2.0 * min(overlap / max(len(finding_range), 1), 1.0)

        return score

    def _check_line_overlap(self, finding: Finding, label: GroundTruthLabel) -> bool:
        """Check whether the finding's line range overlaps the label's line range."""
        if finding.line_start is None or finding.line_end is None:
            return False
        return not (finding.line_end < label.line_start or finding.line_start > label.line_end)

    def evaluate(
        self,
        findings: list[Finding],
        labels: list[GroundTruthLabel],
    ) -> EvaluationResult:
        """
        Build NxM score matrix, run optimal bipartite assignment, then
        classify unmatched findings as FP and unmatched labels as FN.
        """
        if not findings and not labels:
            return self._empty_result()

        # Build score matrix: rows = findings, cols = labels
        n_findings, n_labels = len(findings), len(labels)
        scores = np.full((n_findings, n_labels), -1.0)
        for i, finding in enumerate(findings):
            for j, label in enumerate(labels):
                scores[i, j] = self._match_score(finding, label)

        # Optimal assignment (negate for minimization, mask infeasible pairs)
        cost = np.where(scores < 0, 1e9, -scores)
        matched_findings: list[MatchedFinding] = []
        matched_finding_indices: set[int] = set()
        matched_label_indices: set[int] = set()

        if n_findings > 0 and n_labels > 0:
            row_ind, col_ind = linear_sum_assignment(cost)
            for r, c in zip(row_ind, col_ind):
                if scores[r, c] > 0:  # must have at least file match + one signal
                    line_overlap = self._check_line_overlap(findings[r], labels[c])
                    matched_findings.append(
                        MatchedFinding(
                            finding=findings[r],
                            matched_label=labels[c],
                            match_status=MatchStatus.TRUE_POSITIVE,
                            file_match=True,
                            line_overlap=line_overlap,
                        )
                    )
                    matched_finding_indices.add(r)
                    matched_label_indices.add(c)

        # Unmatched findings → false positives
        for i, finding in enumerate(findings):
            if i not in matched_finding_indices:
                matched_findings.append(
                    MatchedFinding(
                        finding=finding,
                        matched_label=None,
                        match_status=MatchStatus.FALSE_POSITIVE,
                        file_match=False,
                        line_overlap=False,
                    )
                )

        unmatched_labels = [
            label for j, label in enumerate(labels) if j not in matched_label_indices
        ]

        # Assess evidence quality for true positives
        evidence_assessor = self._get_evidence_assessor()
        for mf in matched_findings:
            if mf.match_status == MatchStatus.TRUE_POSITIVE:
                mf.evidence_quality = evidence_assessor.assess(mf.finding, mf.matched_label)

        return self._compute_metrics(matched_findings, unmatched_labels, labels)

    def _compute_metrics(
        self,
        matched: list[MatchedFinding],
        unmatched_labels: list[GroundTruthLabel],
        all_labels: list[GroundTruthLabel],
    ) -> EvaluationResult:
        """Delegate to the compute_metrics helper."""
        from sec_review_framework.evaluation.metrics import compute_metrics

        return compute_metrics(
            matched=matched,
            unmatched_labels=unmatched_labels,
            all_labels=all_labels,
            total_file_count=self._total_file_count,
            experiment_id=self._experiment_id,
            dataset_version=self._dataset_version,
        )

    def _empty_result(self) -> EvaluationResult:
        """Return a zeroed result when both inputs are empty."""
        return EvaluationResult(
            experiment_id=self._experiment_id,
            dataset_version=self._dataset_version,
            total_labels=0,
            total_findings=0,
            true_positives=0,
            false_positives=0,
            false_negatives=0,
            unlabeled_real_count=0,
            precision=0.0,
            recall=0.0,
            f1=0.0,
            false_positive_rate=0.0,
            matched_findings=[],
            unmatched_labels=[],
            evidence_quality_counts={eq.value: 0 for eq in EvidenceQuality},
        )
