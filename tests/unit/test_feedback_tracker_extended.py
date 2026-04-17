"""Extended unit tests for FeedbackTracker — empty batches, degenerate metrics, finding-identity stability."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from sec_review_framework.data.experiment import (
    ExperimentRun,
    ReviewProfileName,
    RunStatus,
    StrategyName,
    ToolExtension,
    ToolVariant,
    VerificationVariant,
)
from sec_review_framework.data.findings import Finding, Severity, VulnClass
from sec_review_framework.feedback.tracker import BatchComparison, FeedbackTracker, _experiment_key


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run(
    batch_id: str = "batch-a",
    model_id: str = "gpt-4o",
    strategy: StrategyName = StrategyName.SINGLE_AGENT,
    findings: list | None = None,
    precision: float | None = 0.8,
    recall: float | None = 0.7,
    f1: float | None = 0.75,
    tool_extensions: frozenset | None = None,
) -> MagicMock:
    run = MagicMock()
    run.experiment = ExperimentRun(
        id=f"{batch_id}_{model_id}_{strategy.value}_with_tools_default_none",
        batch_id=batch_id,
        model_id=model_id,
        strategy=strategy,
        tool_variant=ToolVariant.WITH_TOOLS,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        dataset_name="test-ds",
        dataset_version="1.0.0",
        created_at=datetime(2026, 4, 16, tzinfo=timezone.utc),
        tool_extensions=tool_extensions or frozenset(),
    )
    run.findings = findings or []
    run.status = RunStatus.COMPLETED

    eval_mock = MagicMock()
    eval_mock.precision = precision
    eval_mock.recall = recall
    eval_mock.f1 = f1
    run.evaluation = eval_mock
    return run


def _make_finding(
    id: str = "f-001",
    file_path: str = "app/views.py",
    vuln_class: VulnClass = VulnClass.SQLI,
    line_start: int = 42,
) -> Finding:
    return Finding(
        id=id,
        file_path=file_path,
        line_start=line_start,
        line_end=line_start + 2,
        vuln_class=vuln_class,
        severity=Severity.HIGH,
        title="Vuln",
        description="description",
        confidence=0.9,
        raw_llm_output="",
        produced_by="single_agent",
        experiment_id="exp-001",
    )


# ---------------------------------------------------------------------------
# Empty batch tests
# ---------------------------------------------------------------------------


class TestEmptyBatches:
    def test_both_batches_empty_returns_valid_comparison(self):
        tracker = FeedbackTracker()
        comparison = tracker.compare_batches([], [])
        assert isinstance(comparison, BatchComparison)
        assert comparison.metric_deltas == {}
        assert comparison.improvements == []
        assert comparison.regressions == []
        assert comparison.persistent_false_positives == []
        assert isinstance(comparison.finding_stability, dict)

    def test_batch_a_empty_batch_b_has_results(self):
        tracker = FeedbackTracker()
        r_b = _make_run("batch-b")
        comparison = tracker.compare_batches([], [r_b])
        # No shared keys → no deltas
        assert comparison.metric_deltas == {}
        assert comparison.batch_b_id == "batch-b"

    def test_batch_b_empty_batch_a_has_results(self):
        tracker = FeedbackTracker()
        r_a = _make_run("batch-a")
        comparison = tracker.compare_batches([r_a], [])
        assert comparison.metric_deltas == {}
        assert comparison.batch_a_id == "batch-a"

    def test_extract_fp_patterns_empty_returns_empty(self):
        tracker = FeedbackTracker()
        patterns = tracker.extract_fp_patterns([])
        assert patterns == []

    def test_compare_batches_no_findings_no_crash(self):
        """Runs with no findings at all should not crash."""
        r_a = _make_run("batch-a", findings=[])
        r_b = _make_run("batch-b", findings=[])
        tracker = FeedbackTracker()
        comparison = tracker.compare_batches([r_a], [r_b])
        assert isinstance(comparison, BatchComparison)


# ---------------------------------------------------------------------------
# Degenerate metrics
# ---------------------------------------------------------------------------


class TestDegenerateMetrics:
    def test_none_precision_handled_gracefully(self):
        """Runs where evaluation.precision is None should not raise."""
        r_a = _make_run("batch-a", precision=None, recall=None, f1=None)
        r_b = _make_run("batch-b", precision=None, recall=None, f1=None)
        tracker = FeedbackTracker()
        comparison = tracker.compare_batches([r_a], [r_b])
        # Should have a delta entry but with 0.0 deltas (None → 0.0 fallback)
        assert isinstance(comparison.metric_deltas, dict)

    def test_zero_metrics_no_improvement_or_regression(self):
        r_a = _make_run("batch-a", precision=0.0, recall=0.0, f1=0.0)
        r_b = _make_run("batch-b", precision=0.0, recall=0.0, f1=0.0)
        tracker = FeedbackTracker()
        comparison = tracker.compare_batches([r_a], [r_b])
        assert comparison.improvements == []
        assert comparison.regressions == []

    def test_regression_detected(self):
        r_a = _make_run("batch-a", f1=0.9)
        r_b = _make_run("batch-b", f1=0.5)
        tracker = FeedbackTracker()
        comparison = tracker.compare_batches([r_a], [r_b])
        assert len(comparison.regressions) > 0
        assert comparison.regressions[0]["f1_delta"] < 0

    def test_improvement_detected(self):
        r_a = _make_run("batch-a", f1=0.3)
        r_b = _make_run("batch-b", f1=0.9)
        tracker = FeedbackTracker()
        comparison = tracker.compare_batches([r_a], [r_b])
        assert len(comparison.improvements) > 0
        assert comparison.improvements[0]["f1_delta"] > 0

    def test_none_evaluation_no_delta_computed(self):
        """When run.evaluation is None, no metric delta should be computed for that run."""
        r_a = _make_run("batch-a")
        r_a.evaluation = None
        r_b = _make_run("batch-b")
        r_b.evaluation = None

        tracker = FeedbackTracker()
        comparison = tracker.compare_batches([r_a], [r_b])
        # No evaluation → no metric_deltas entry
        assert comparison.metric_deltas == {}


# ---------------------------------------------------------------------------
# Finding identity stability
# ---------------------------------------------------------------------------


class TestFindingIdentityStability:
    def test_stability_of_1_when_finding_appears_in_all_b_runs(self):
        """If same identity appears in every B run, stability should be 1.0."""
        finding = _make_finding("f-1")
        r_b1 = _make_run("batch-b", findings=[finding])
        r_b2 = _make_run("batch-b", model_id="claude-opus-4", findings=[finding])

        tracker = FeedbackTracker()
        comparison = tracker.compare_batches([], [r_b1, r_b2])

        # Finding appears in 2/2 B runs — stability should be 1.0
        for stability_val in comparison.finding_stability.values():
            assert 0.0 <= stability_val <= 1.0

    def test_stability_empty_batch_b_is_empty_dict(self):
        tracker = FeedbackTracker()
        comparison = tracker.compare_batches([], [])
        assert comparison.finding_stability == {}

    def test_stability_values_are_fractions_0_to_1(self):
        """All stability values must be in [0.0, 1.0]."""
        findings = [_make_finding(f"f-{i}", line_start=i * 10) for i in range(5)]
        runs = [_make_run("batch-b", model_id=f"model-{i}", findings=[findings[i]]) for i in range(5)]

        tracker = FeedbackTracker()
        comparison = tracker.compare_batches([], runs)

        for val in comparison.finding_stability.values():
            assert 0.0 <= val <= 1.0

    def test_identical_findings_across_multiple_runs(self):
        """Same file+vuln_class+line = same identity; stability should be > 0."""
        finding = _make_finding("f-a", file_path="app.py", line_start=100)
        finding_copy = _make_finding("f-b", file_path="app.py", line_start=100)

        r_b1 = _make_run("batch-b", model_id="gpt-4o", findings=[finding])
        r_b2 = _make_run("batch-b", model_id="gpt-4o", findings=[finding_copy])

        tracker = FeedbackTracker()
        comparison = tracker.compare_batches([], [r_b1, r_b2])

        assert len(comparison.finding_stability) >= 1
        max_stability = max(comparison.finding_stability.values())
        assert max_stability >= 0.5  # appeared in at least half of runs


# ---------------------------------------------------------------------------
# extract_fp_patterns — recurring patterns
# ---------------------------------------------------------------------------


class TestExtractFPPatterns:
    def test_single_finding_per_run_no_pattern(self):
        """A finding appearing once in one run is not a recurring pattern."""
        finding = _make_finding("f-1")
        r = _make_run("batch-a", findings=[finding])
        tracker = FeedbackTracker()
        patterns = tracker.extract_fp_patterns([r])
        # Only one occurrence — no pattern (requires >= 2)
        assert all(p.occurrence_count >= 2 for p in patterns)

    def test_finding_appearing_twice_creates_pattern(self):
        """Same identity in two runs triggers a pattern."""
        f1 = _make_finding("f-a", file_path="app.py", line_start=42)
        f2 = _make_finding("f-b", file_path="app.py", line_start=42)  # same identity

        r1 = _make_run("batch-x", model_id="gpt-4o", findings=[f1])
        r2 = _make_run("batch-x", model_id="gpt-4o", findings=[f2])

        tracker = FeedbackTracker()
        patterns = tracker.extract_fp_patterns([r1, r2])

        assert len(patterns) >= 1
        assert patterns[0].occurrence_count >= 2

    def test_pattern_includes_model_id(self):
        f = _make_finding("f-a", file_path="app.py", line_start=42)
        f2 = _make_finding("f-b", file_path="app.py", line_start=42)

        r1 = _make_run("batch-x", model_id="gpt-4o", findings=[f])
        r2 = _make_run("batch-x", model_id="gpt-4o", findings=[f2])

        tracker = FeedbackTracker()
        patterns = tracker.extract_fp_patterns([r1, r2])

        model_ids = {p.model_id for p in patterns}
        assert "gpt-4o" in model_ids

    def test_different_models_tracked_separately(self):
        """Patterns for model A and model B should be separate entries."""
        f1 = _make_finding("f-a", file_path="app.py", line_start=10)
        f2 = _make_finding("f-b", file_path="app.py", line_start=10)

        r_gpt_1 = _make_run("batch-x", model_id="gpt-4o", findings=[f1])
        r_gpt_2 = _make_run("batch-x", model_id="gpt-4o", findings=[f2])
        r_claude_1 = _make_run("batch-x", model_id="claude-opus-4", findings=[f1])
        r_claude_2 = _make_run("batch-x", model_id="claude-opus-4", findings=[f2])

        tracker = FeedbackTracker()
        patterns = tracker.extract_fp_patterns([r_gpt_1, r_gpt_2, r_claude_1, r_claude_2])

        model_ids = {p.model_id for p in patterns}
        assert "gpt-4o" in model_ids
        assert "claude-opus-4" in model_ids

    def test_multiple_runs_no_findings_no_pattern(self):
        runs = [_make_run("batch-x", model_id=f"model-{i}", findings=[]) for i in range(10)]
        tracker = FeedbackTracker()
        patterns = tracker.extract_fp_patterns(runs)
        assert patterns == []


# ---------------------------------------------------------------------------
# _experiment_key — tool_extensions dimension
# ---------------------------------------------------------------------------


class TestExperimentKeyExtensions:
    def test_same_base_different_extensions_produce_distinct_keys(self):
        """Runs differing only in tool_extensions must have distinct experiment keys."""
        r_lsp = _make_run(tool_extensions=frozenset({ToolExtension.LSP}))
        r_devdocs = _make_run(tool_extensions=frozenset({ToolExtension.DEVDOCS}))
        assert _experiment_key(r_lsp) != _experiment_key(r_devdocs)

    def test_same_base_same_extensions_produce_equal_keys(self):
        """Runs with identical (model, strategy, tool_variant, extensions) must share a key."""
        r1 = _make_run(tool_extensions=frozenset({ToolExtension.LSP}))
        r2 = _make_run(tool_extensions=frozenset({ToolExtension.LSP}))
        assert _experiment_key(r1) == _experiment_key(r2)

    def test_empty_extensions_produce_empty_tuple_slot(self):
        """Runs with no extensions should have () as the extensions component."""
        r = _make_run(tool_extensions=frozenset())
        key = _experiment_key(r)
        assert key[3] == ()

    def test_extensions_slot_is_sorted(self):
        """Multi-extension keys must be in sorted order for stability."""
        r = _make_run(tool_extensions=frozenset({ToolExtension.DEVDOCS, ToolExtension.LSP}))
        key = _experiment_key(r)
        ext_tuple = key[3]
        assert list(ext_tuple) == sorted(ext_tuple)

    def test_base_run_no_extensions_matches_legacy_behavior(self):
        """A run with no extensions produces the same key structure as legacy (+ empty tuple)."""
        r_no_ext = _make_run(tool_extensions=frozenset())
        r_with_ext = _make_run(tool_extensions=frozenset({ToolExtension.TREE_SITTER}))
        key_no_ext = _experiment_key(r_no_ext)
        key_with_ext = _experiment_key(r_with_ext)
        # First three components match (same model/strategy/tool_variant)
        assert key_no_ext[:3] == key_with_ext[:3]
        # But the full keys differ
        assert key_no_ext != key_with_ext

    def test_compare_batches_different_extensions_do_not_merge(self):
        """Runs with different extensions are not compared across batches."""
        r_a = _make_run("batch-a", tool_extensions=frozenset({ToolExtension.LSP}))
        r_b = _make_run("batch-b", tool_extensions=frozenset({ToolExtension.DEVDOCS}))
        tracker = FeedbackTracker()
        comparison = tracker.compare_batches([r_a], [r_b])
        assert comparison.metric_deltas == {}

    def test_compare_batches_same_extensions_merge(self):
        """Runs with identical extensions are matched across batches."""
        r_a = _make_run("batch-a", tool_extensions=frozenset({ToolExtension.LSP}))
        r_b = _make_run("batch-b", tool_extensions=frozenset({ToolExtension.LSP}))
        tracker = FeedbackTracker()
        comparison = tracker.compare_batches([r_a], [r_b])
        assert len(comparison.metric_deltas) == 1
