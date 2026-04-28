"""Tests for deduplicate() — merges overlapping findings, returns StrategyOutput."""

from __future__ import annotations

from sec_review_framework.data.findings import Finding, Severity, VulnClass
from sec_review_framework.strategies.common import deduplicate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_ID_COUNTER = 0


def _make_finding(
    file_path: str = "app.py",
    vuln_class: VulnClass = VulnClass.SQLI,
    line_start: int | None = 10,
    line_end: int | None = None,
    confidence: float = 0.5,
    fid: str | None = None,
) -> Finding:
    global _ID_COUNTER
    _ID_COUNTER += 1
    return Finding(
        id=fid or f"f-{_ID_COUNTER}",
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        vuln_class=vuln_class,
        severity=Severity.MEDIUM,
        title="Test Finding",
        description="desc",
        confidence=confidence,
        raw_llm_output="raw",
        produced_by="test",
        experiment_id="exp-1",
    )


# ---------------------------------------------------------------------------
# Basic passthrough / merging
# ---------------------------------------------------------------------------


def test_no_duplicates_passthrough():
    """Completely distinct findings (different files) are not merged."""
    f1 = _make_finding(file_path="a.py", line_start=1)
    f2 = _make_finding(file_path="b.py", line_start=1)
    result = deduplicate([f1, f2])
    assert result.post_dedup_count == 2
    assert result.pre_dedup_count == 2


def test_identical_location_and_class_keeps_highest_confidence():
    """Two findings with identical file/vuln_class and overlapping lines → keep higher confidence."""
    f1 = _make_finding(confidence=0.7, line_start=10, line_end=12, fid="high")
    f2 = _make_finding(confidence=0.3, line_start=10, line_end=12, fid="low")
    result = deduplicate([f1, f2])
    assert result.post_dedup_count == 1
    assert result.findings[0].id == "high"


def test_within_5_line_window_are_merged():
    """Findings within 5 lines of each other (same file/class) are merged into one."""
    # line_end defaults to line_start when None, so distance = 5 - 10 ... overlap check
    # f1: line_start=10, f2: line_start=14 → distance = 14 - 10 = 4, within MERGE_WINDOW=5
    f1 = _make_finding(line_start=10, line_end=10, confidence=0.9, fid="f1")
    f2 = _make_finding(line_start=14, line_end=14, confidence=0.5, fid="f2")
    result = deduplicate([f1, f2])
    assert result.post_dedup_count == 1
    assert result.findings[0].id == "f1"


def test_outside_window_kept_separate():
    """Findings more than 5 lines apart are NOT merged."""
    # f1: line_start=10, f2: line_start=20 → distance = 10, > MERGE_WINDOW=5
    f1 = _make_finding(line_start=10, line_end=10, confidence=0.9)
    f2 = _make_finding(line_start=20, line_end=20, confidence=0.8)
    result = deduplicate([f1, f2])
    assert result.post_dedup_count == 2


def test_different_vuln_class_same_file_not_merged():
    """Same file, different vuln_class → two separate findings."""
    f1 = _make_finding(file_path="app.py", vuln_class=VulnClass.SQLI, line_start=10)
    f2 = _make_finding(file_path="app.py", vuln_class=VulnClass.XSS, line_start=10)
    result = deduplicate([f1, f2])
    assert result.post_dedup_count == 2


def test_different_file_same_class_not_merged():
    """Same vuln_class, different file → two separate findings."""
    f1 = _make_finding(file_path="a.py", vuln_class=VulnClass.SQLI, line_start=10)
    f2 = _make_finding(file_path="b.py", vuln_class=VulnClass.SQLI, line_start=10)
    result = deduplicate([f1, f2])
    assert result.post_dedup_count == 2


# ---------------------------------------------------------------------------
# Dedup log
# ---------------------------------------------------------------------------


def test_dedup_log_records_kept_and_merged_ids():
    """dedup_log captures kept_finding_id and merged_finding_ids."""
    f1 = _make_finding(line_start=10, confidence=0.9, fid="kept")
    f2 = _make_finding(line_start=12, confidence=0.3, fid="dropped")
    result = deduplicate([f1, f2])
    assert len(result.dedup_log) == 1
    entry = result.dedup_log[0]
    assert entry.kept_finding_id == "kept"
    assert "dropped" in entry.merged_finding_ids


def test_dedup_log_empty_when_no_merges():
    """No merges → dedup_log is empty."""
    f1 = _make_finding(file_path="a.py")
    f2 = _make_finding(file_path="b.py")
    result = deduplicate([f1, f2])
    assert result.dedup_log == []


def test_pre_post_counts_accurate():
    """pre_dedup_count and post_dedup_count reflect actual counts."""
    findings = [
        _make_finding(line_start=10, confidence=0.9),
        _make_finding(line_start=10, confidence=0.5),
        _make_finding(file_path="other.py", line_start=10),
    ]
    result = deduplicate(findings)
    assert result.pre_dedup_count == 3
    assert result.post_dedup_count == 2


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_none_line_start_handling():
    """Findings with line_start=None are grouped at effective line 0."""
    f1 = _make_finding(line_start=None, confidence=0.8, fid="none1")
    f2 = _make_finding(line_start=None, confidence=0.4, fid="none2")
    # Both at effective line 0, same file/class → merge
    result = deduplicate([f1, f2])
    assert result.post_dedup_count == 1
    assert result.findings[0].id == "none1"


def test_empty_list_returns_empty_output():
    """Empty input → StrategyOutput with zeros and no findings."""
    result = deduplicate([])
    assert result.findings == []
    assert result.pre_dedup_count == 0
    assert result.post_dedup_count == 0
    assert result.dedup_log == []


def test_single_finding_returned_unchanged():
    """One finding → no merging, returned as-is."""
    f = _make_finding(fid="solo")
    result = deduplicate([f])
    assert result.post_dedup_count == 1
    assert result.findings[0].id == "solo"
    assert result.dedup_log == []


def test_cluster_transitivity():
    """A→B within window, B→C within window, so A, B, C all collapse to one cluster."""
    # A: 10, B: 14 (within 5 of A), C: 18 (within 5 of B)
    # Note: greedy merge checks each new finding against existing cluster members,
    # so B gets added to A's cluster first, then C is checked against both A and B.
    f_a = _make_finding(line_start=10, line_end=10, confidence=0.9, fid="A")
    f_b = _make_finding(line_start=14, line_end=14, confidence=0.6, fid="B")
    f_c = _make_finding(line_start=18, line_end=18, confidence=0.3, fid="C")
    result = deduplicate([f_a, f_b, f_c])
    assert result.post_dedup_count == 1
    assert result.findings[0].id == "A"
    assert result.pre_dedup_count == 3
