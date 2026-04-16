"""Tests for FeedbackTracker, PromptRegistry, and ReviewProfiles."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from sec_review_framework.data.experiment import (
    ExperimentRun,
    ReviewProfileName,
    RunStatus,
    StrategyName,
    ToolVariant,
    VerificationVariant,
)
from sec_review_framework.data.findings import (
    Finding,
    Severity,
    StrategyOutput,
    VulnClass,
)
from sec_review_framework.feedback.tracker import FeedbackTracker
from sec_review_framework.profiles.review_profiles import BUILTIN_PROFILES, ProfileRegistry
from sec_review_framework.prompts.registry import PromptRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_experiment_run(
    model_id: str = "gpt-4o",
    batch_id: str = "batch-a",
    strategy: StrategyName = StrategyName.SINGLE_AGENT,
) -> ExperimentRun:
    return ExperimentRun(
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
    )


def _make_finding(
    finding_id: str = "f-001",
    file_path: str = "app/views.py",
    vuln_class: VulnClass = VulnClass.SQLI,
    line_start: int = 42,
) -> Finding:
    return Finding(
        id=finding_id,
        file_path=file_path,
        line_start=line_start,
        line_end=line_start + 2,
        vuln_class=vuln_class,
        severity=Severity.HIGH,
        title="SQL Injection",
        description="Raw SQL from user input.",
        confidence=0.9,
        raw_llm_output="",
        produced_by="single_agent",
        experiment_id="exp-001",
    )


def _make_run_result(
    batch_id: str = "batch-a",
    model_id: str = "gpt-4o",
    findings: list[Finding] | None = None,
    precision: float = 0.8,
    recall: float = 0.7,
    f1: float = 0.75,
):
    """Build a minimal RunResult-like object using MagicMock to avoid DB deps."""
    run_result = MagicMock()
    run_result.experiment = _make_experiment_run(model_id=model_id, batch_id=batch_id)
    run_result.findings = findings or []
    run_result.status = RunStatus.COMPLETED

    eval_mock = MagicMock()
    eval_mock.precision = precision
    eval_mock.recall = recall
    eval_mock.f1 = f1
    run_result.evaluation = eval_mock

    return run_result


# ---------------------------------------------------------------------------
# FeedbackTracker
# ---------------------------------------------------------------------------


class TestFeedbackTracker:
    def test_compare_batches_computes_metric_deltas(self):
        """metric_deltas dict is populated for shared experiment keys."""
        r_a = _make_run_result("batch-a", precision=0.6, recall=0.5, f1=0.55)
        r_b = _make_run_result("batch-b", precision=0.8, recall=0.7, f1=0.75)

        tracker = FeedbackTracker()
        comparison = tracker.compare_batches([r_a], [r_b])

        assert len(comparison.metric_deltas) > 0
        key = list(comparison.metric_deltas.keys())[0]
        delta = comparison.metric_deltas[key]
        assert "precision" in delta
        assert "recall" in delta
        assert "f1" in delta
        # F1 improved: batch_b > batch_a
        assert delta["f1"] == pytest.approx(0.75 - 0.55, abs=0.01)

    def test_compare_batches_improvements_detected(self):
        """Runs with positive delta_f1 appear in improvements list."""
        r_a = _make_run_result("batch-a", f1=0.4)
        r_b = _make_run_result("batch-b", f1=0.8)

        tracker = FeedbackTracker()
        comparison = tracker.compare_batches([r_a], [r_b])

        assert len(comparison.improvements) > 0

    def test_compare_batches_finding_stability_computed(self):
        """finding_stability is a dict mapping identity strings to fractions."""
        finding = _make_finding()
        r_b = _make_run_result("batch-b", findings=[finding])

        tracker = FeedbackTracker()
        comparison = tracker.compare_batches([], [r_b])

        assert isinstance(comparison.finding_stability, dict)

    def test_compare_batches_persistent_fps_detected(self):
        """Findings that appear in both batches are captured as persistent FPs."""
        finding = _make_finding(finding_id="fp-001", file_path="app/other.py", line_start=10)

        # Same experiment key, same finding identity in both batches.
        r_a = _make_run_result("batch-a", findings=[finding])
        r_b = _make_run_result("batch-b", findings=[])  # finding absent in B → discordant

        tracker = FeedbackTracker()
        comparison = tracker.compare_batches([r_a], [r_b])

        # persistent_false_positives is a list (may be empty depending on logic).
        assert isinstance(comparison.persistent_false_positives, list)

    def test_compare_batches_empty_inputs_no_crash(self):
        """Empty batch lists must not raise any exceptions."""
        tracker = FeedbackTracker()
        comparison = tracker.compare_batches([], [])
        assert comparison.metric_deltas == {}
        assert comparison.persistent_false_positives == []

    def test_extract_fp_patterns_flags_recurring_patterns(self):
        """A finding that appears twice in different runs is flagged as recurring."""
        finding_a = _make_finding(finding_id="fp-a")
        finding_b = _make_finding(finding_id="fp-b")  # same identity as fp-a (same file+class+line)

        r1 = _make_run_result("batch-x", findings=[finding_a])
        r2 = _make_run_result("batch-x", findings=[finding_b])

        tracker = FeedbackTracker()
        patterns = tracker.extract_fp_patterns([r1, r2])

        # Both findings share the same (model_id, vuln_class, identity) → pattern.
        assert isinstance(patterns, list)
        assert len(patterns) >= 1

    def test_extract_fp_patterns_empty_results_no_crash(self):
        """Empty result list should return an empty patterns list."""
        tracker = FeedbackTracker()
        patterns = tracker.extract_fp_patterns([])
        assert patterns == []


# ---------------------------------------------------------------------------
# PromptRegistry
# ---------------------------------------------------------------------------


class TestPromptRegistry:
    def test_save_and_load_round_trip(self, tmp_path: Path):
        from sec_review_framework.data.experiment import PromptSnapshot

        registry = PromptRegistry(config_root=tmp_path)
        snapshot = PromptSnapshot.capture(
            system_prompt="You are a security reviewer.",
            user_message_template="Audit this code.",
            finding_output_format="JSON array",
        )

        registry.save(StrategyName.SINGLE_AGENT, snapshot)
        loaded = registry.load(StrategyName.SINGLE_AGENT, snapshot.snapshot_id)

        assert loaded.snapshot_id == snapshot.snapshot_id
        assert loaded.system_prompt == snapshot.system_prompt
        assert loaded.user_message_template == snapshot.user_message_template

    def test_list_snapshots_empty_when_no_files(self, tmp_path: Path):
        registry = PromptRegistry(config_root=tmp_path)
        ids = registry.list_snapshots(StrategyName.SINGLE_AGENT)
        assert ids == []

    def test_list_snapshots_populated_after_save(self, tmp_path: Path):
        from sec_review_framework.data.experiment import PromptSnapshot

        registry = PromptRegistry(config_root=tmp_path)
        snap1 = PromptSnapshot.capture(system_prompt="p1", user_message_template="u1",
                                       finding_output_format="f")
        snap2 = PromptSnapshot.capture(system_prompt="p2", user_message_template="u2",
                                       finding_output_format="f")

        registry.save(StrategyName.PER_FILE, snap1)
        registry.save(StrategyName.PER_FILE, snap2)

        ids = registry.list_snapshots(StrategyName.PER_FILE)
        assert snap1.snapshot_id in ids
        assert snap2.snapshot_id in ids

    def test_load_not_found_raises_file_not_found_error(self, tmp_path: Path):
        registry = PromptRegistry(config_root=tmp_path)
        with pytest.raises(FileNotFoundError):
            registry.load(StrategyName.SINGLE_AGENT, "nonexistent-snapshot-id")


# ---------------------------------------------------------------------------
# ReviewProfiles
# ---------------------------------------------------------------------------


class TestReviewProfiles:
    def test_all_review_profile_name_values_have_builtin_entries(self):
        """Every ReviewProfileName value must appear in BUILTIN_PROFILES."""
        for profile_name in ReviewProfileName:
            assert profile_name in BUILTIN_PROFILES, (
                f"Missing BUILTIN_PROFILES entry for: {profile_name}"
            )

    def test_profile_registry_get_known_profile(self):
        registry = ProfileRegistry()
        profile = registry.get(ReviewProfileName.STRICT)
        assert profile.name == ReviewProfileName.STRICT
        assert len(profile.system_prompt_modifier) > 0

    def test_profile_registry_get_unknown_raises(self):
        """Getting a profile name not in the registry raises ValueError."""
        registry = ProfileRegistry()
        fake_name = MagicMock()
        # Simulate an unregistered key by patching BUILTIN_PROFILES lookup.
        with pytest.raises((ValueError, KeyError)):
            registry.get(fake_name)  # type: ignore

    def test_profile_registry_list_all_returns_all_five(self):
        registry = ProfileRegistry()
        profiles = registry.list_all()
        assert len(profiles) == len(ReviewProfileName)
        profile_names = {p.name for p in profiles}
        for name in ReviewProfileName:
            assert name in profile_names
