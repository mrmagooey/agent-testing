"""Evaluation and verification data models."""

from datetime import datetime
from enum import Enum

from pydantic import BaseModel

from sec_review_framework.data.findings import Finding, Severity, VulnClass


# --- Ground Truth ---


class GroundTruthSource(str, Enum):
    CVE_PATCH = "cve_patch"
    INJECTED = "injected"
    MANUAL = "manual"


class GroundTruthLabel(BaseModel):
    """A labeled vulnerability in a dataset."""

    id: str
    dataset_version: str
    file_path: str
    line_start: int
    line_end: int
    cwe_id: str
    vuln_class: VulnClass
    severity: Severity
    description: str
    source: GroundTruthSource
    source_ref: str | None = None
    confidence: str  # "confirmed" | "likely" | "suspected"
    created_at: datetime
    notes: str | None = None
    introduced_in_diff: bool | None = None
    patch_lines_changed: int | None = None


# --- Evaluation ---


class MatchStatus(str, Enum):
    TRUE_POSITIVE = "true_positive"
    FALSE_POSITIVE = "false_positive"
    UNLABELED_REAL = "unlabeled_real"


class EvidenceQuality(str, Enum):
    STRONG = "strong"
    ADEQUATE = "adequate"
    WEAK = "weak"
    NOT_ASSESSED = "not_assessed"


class MatchedFinding(BaseModel):
    """A finding matched (or not) against a ground truth label."""

    finding: Finding
    matched_label: GroundTruthLabel | None
    match_status: MatchStatus
    file_match: bool
    line_overlap: bool
    evidence_quality: EvidenceQuality = EvidenceQuality.NOT_ASSESSED


class StratifiedMetric(BaseModel):
    """Label-attributable metrics for a single stratum (e.g. patch-size bucket).

    Precision/FPR are not reported per-stratum: false positives have no matched
    label and therefore cannot be attributed to a bucket. Overall precision/FPR
    remain on EvaluationResult.
    """

    bucket: str
    total_labels: int
    true_positives: int
    false_negatives: int
    recall: float


class EvaluationResult(BaseModel):
    """Metrics and details from evaluating findings against ground truth."""

    experiment_id: str
    dataset_version: str
    total_labels: int
    total_findings: int
    true_positives: int
    false_positives: int
    false_negatives: int
    unlabeled_real_count: int
    precision: float
    recall: float
    f1: float
    false_positive_rate: float
    matched_findings: list[MatchedFinding]
    unmatched_labels: list[GroundTruthLabel]
    evidence_quality_counts: dict[str, int]
    patch_size_strata: list[StratifiedMetric] = []


# --- Verification ---


class VerificationOutcome(str, Enum):
    VERIFIED = "verified"
    REJECTED = "rejected"
    UNCERTAIN = "uncertain"


class VerifiedFinding(BaseModel):
    """A finding with verification decision and evidence."""

    finding: Finding
    outcome: VerificationOutcome
    evidence: str
    cited_lines: list[str]


class VerificationResult(BaseModel):
    """Output of the verification pass."""

    verified: list[VerifiedFinding]
    rejected: list[VerifiedFinding]
    uncertain: list[VerifiedFinding]
    total_candidates: int
    verification_tokens: int


# --- Statistics ---


class ConfidenceInterval(BaseModel):
    """Wilson score confidence interval."""

    point_estimate: float
    lower: float
    upper: float
    alpha: float = 0.05
