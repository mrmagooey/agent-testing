"""Performance tests for FileLevelEvaluator and deduplication at scale."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

import pytest

from sec_review_framework.data.evaluation import (
    GroundTruthLabel,
    GroundTruthSource,
)
from sec_review_framework.data.findings import Finding, Severity, VulnClass
from sec_review_framework.evaluation.evaluator import FileLevelEvaluator
from sec_review_framework.strategies.common import deduplicate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VULN_CLASSES = list(VulnClass)
_SEVERITIES = list(Severity)
_SOURCES = list(GroundTruthSource)


def _make_finding(
    *,
    file_path: str,
    vuln_class: VulnClass = VulnClass.SQLI,
    line_start: int = 10,
    line_end: int = 15,
    confidence: float = 0.8,
) -> Finding:
    return Finding(
        id=str(uuid.uuid4()),
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        vuln_class=vuln_class,
        severity=Severity.HIGH,
        title="Test finding",
        description="Test description",
        confidence=confidence,
        raw_llm_output="<raw>",
        produced_by="perf_test",
        experiment_id="perf-exp",
    )


def _make_label(
    *,
    file_path: str,
    vuln_class: VulnClass = VulnClass.SQLI,
    line_start: int = 10,
    line_end: int = 15,
) -> GroundTruthLabel:
    return GroundTruthLabel(
        id=str(uuid.uuid4()),
        dataset_version="1.0.0",
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        cwe_id="CWE-89",
        vuln_class=vuln_class,
        severity=Severity.HIGH,
        description="Test label",
        source=GroundTruthSource.INJECTED,
        confidence="confirmed",
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


def _make_n_matched_pairs(
    n: int,
) -> tuple[list[Finding], list[GroundTruthLabel]]:
    """Return n findings and n labels each on a distinct file, all matchable."""
    findings = []
    labels = []
    for i in range(n):
        path = f"app/module_{i}.py"
        vuln = _VULN_CLASSES[i % len(_VULN_CLASSES)]
        findings.append(_make_finding(file_path=path, vuln_class=vuln, line_start=10, line_end=15))
        labels.append(_make_label(file_path=path, vuln_class=vuln, line_start=10, line_end=15))
    return findings, labels


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.slow
def test_evaluator_100_findings_100_labels() -> None:
    """FileLevelEvaluator handles 100×100 within 2 seconds."""
    findings, labels = _make_n_matched_pairs(100)
    evaluator = FileLevelEvaluator(total_file_count=100, experiment_id="perf", dataset_version="1.0")

    start = time.monotonic()
    result = evaluator.evaluate(findings, labels)
    elapsed = time.monotonic() - start

    assert elapsed < 2.0, f"evaluate() took {elapsed:.2f}s, expected <2s"
    assert result.total_findings == 100
    assert result.total_labels == 100


@pytest.mark.slow
def test_evaluator_500_findings_500_labels() -> None:
    """FileLevelEvaluator handles 500×500 within 10 seconds."""
    findings, labels = _make_n_matched_pairs(500)
    evaluator = FileLevelEvaluator(total_file_count=500, experiment_id="perf", dataset_version="1.0")

    start = time.monotonic()
    result = evaluator.evaluate(findings, labels)
    elapsed = time.monotonic() - start

    assert elapsed < 10.0, f"evaluate() took {elapsed:.2f}s, expected <10s"
    assert result.total_findings == 500
    assert result.total_labels == 500


@pytest.mark.slow
def test_deduplication_10000_findings() -> None:
    """deduplicate() handles 10 000 findings with heavy overlap within 5 seconds.

    Strategy: 50 files × 10 vuln classes = 500 buckets; each bucket gets 20
    findings clustered around the same line range so the overlap logic fires
    many times.
    """
    findings: list[Finding] = []
    n_files = 50
    n_vuln_classes = 10
    per_bucket = 20  # 50 × 10 × 20 = 10 000

    vuln_classes = _VULN_CLASSES[:n_vuln_classes]
    for file_idx in range(n_files):
        path = f"app/service_{file_idx}.py"
        for vuln in vuln_classes:
            base_line = 100
            for offset in range(per_bucket):
                # Spread findings within a small window so many merge together
                line_start = base_line + offset
                findings.append(
                    _make_finding(
                        file_path=path,
                        vuln_class=vuln,
                        line_start=line_start,
                        line_end=line_start + 3,
                        confidence=round(0.5 + (offset % 5) * 0.1, 2),
                    )
                )

    assert len(findings) == 10_000

    start = time.monotonic()
    output = deduplicate(findings)
    elapsed = time.monotonic() - start

    assert elapsed < 5.0, f"deduplicate() took {elapsed:.2f}s, expected <5s"
    assert output.pre_dedup_count == 10_000
    # Heavy overlap: post count should be much smaller than input
    assert output.post_dedup_count < output.pre_dedup_count
