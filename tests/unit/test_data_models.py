"""Tests for FindingIdentity, ExperimentMatrix.expand(), PromptSnapshot.capture(), and RunResult serialization."""

from __future__ import annotations

import json

import pytest

from sec_review_framework.data.experiment import (
    ExperimentMatrix,
    ExperimentRun,
    PromptSnapshot,
    ReviewProfileName,
    RunResult,
    RunStatus,
    StrategyName,
    ToolExtension,
    ToolVariant,
    VerificationVariant,
)
from sec_review_framework.data.findings import (
    Finding,
    FindingIdentity,
    LINE_BUCKET_SIZE,
    Severity,
    StrategyOutput,
    VulnClass,
)


# ---------------------------------------------------------------------------
# FindingIdentity tests
# ---------------------------------------------------------------------------


def _make_finding(line_start: int | None, file_path: str = "app.py") -> Finding:
    return Finding(
        id="f1",
        file_path=file_path,
        line_start=line_start,
        vuln_class=VulnClass.SQLI,
        severity=Severity.HIGH,
        title="SQL Injection",
        description="desc",
        confidence=0.9,
        raw_llm_output="raw",
        produced_by="test",
        experiment_id="exp-1",
    )


def test_finding_identity_bucket_lines_0_to_9():
    """Lines 0–9 should all map to bucket 0."""
    for line in [0, 1, 5, 9]:
        f = _make_finding(line)
        ident = FindingIdentity.from_finding(f)
        assert ident.line_bucket == 0, f"Expected bucket 0 for line {line}"


def test_finding_identity_bucket_lines_10_to_19():
    """Lines 10–19 should all map to bucket 1."""
    for line in [10, 11, 15, 19]:
        f = _make_finding(line)
        ident = FindingIdentity.from_finding(f)
        assert ident.line_bucket == 1, f"Expected bucket 1 for line {line}"


def test_finding_identity_bucket_line_100():
    """Line 100 → bucket 10."""
    f = _make_finding(100)
    assert FindingIdentity.from_finding(f).line_bucket == 10


def test_finding_identity_none_line_start_maps_to_bucket_0():
    """None line_start should map to bucket 0 (treats as 0)."""
    f = _make_finding(None)
    ident = FindingIdentity.from_finding(f)
    assert ident.line_bucket == 0


def test_finding_identity_str_format():
    """__str__ should produce 'file:<vuln_class>:L<lo>-<hi>' format."""
    f = _make_finding(25)
    ident = FindingIdentity.from_finding(f)
    # bucket = 25 // 10 = 2, lo = 20, hi = 29
    s = str(ident)
    assert s.startswith("app.py:")
    assert "L20-29" in s


def test_finding_identity_str_format_bucket_0():
    """Bucket 0 → L0-9 in the string representation."""
    f = _make_finding(5)
    ident = FindingIdentity.from_finding(f)
    s = str(ident)
    assert s.startswith("app.py:")
    assert "L0-9" in s


def test_finding_identity_contains_file_and_vuln_class():
    f = _make_finding(42, file_path="views.py")
    ident = FindingIdentity.from_finding(f)
    assert ident.file_path == "views.py"
    assert ident.vuln_class == "sqli"


# ---------------------------------------------------------------------------
# ExperimentMatrix.expand() tests
# ---------------------------------------------------------------------------


def _minimal_matrix(**overrides) -> ExperimentMatrix:
    defaults = dict(
        experiment_id="experiment-1",
        dataset_name="ds",
        dataset_version="1.0",
        model_ids=["model-a"],
        strategies=[StrategyName.SINGLE_AGENT],
        tool_variants=[ToolVariant.WITH_TOOLS],
        review_profiles=[ReviewProfileName.DEFAULT],
        verification_variants=[VerificationVariant.NONE],
        parallel_modes=[False],
    )
    defaults.update(overrides)
    return ExperimentMatrix(**defaults)


def test_expand_2x2_produces_4_runs():
    """2 models × 2 strategies (with one tool/profile/verif/parallel each) = 4 runs."""
    matrix = _minimal_matrix(
        model_ids=["model-a", "model-b"],
        strategies=[StrategyName.SINGLE_AGENT, StrategyName.PER_FILE],
    )
    runs = matrix.expand()
    assert len(runs) == 4


def test_expand_2x2x2_produces_8_runs():
    """2 models × 2 strategies × 2 tool_variants = 8 runs."""
    matrix = _minimal_matrix(
        model_ids=["model-a", "model-b"],
        strategies=[StrategyName.SINGLE_AGENT, StrategyName.PER_FILE],
        tool_variants=[ToolVariant.WITH_TOOLS, ToolVariant.WITHOUT_TOOLS],
    )
    runs = matrix.expand()
    assert len(runs) == 8


def test_expand_single_dimension_produces_1_run():
    """Single value in every dimension → exactly 1 run."""
    matrix = _minimal_matrix()
    runs = matrix.expand()
    assert len(runs) == 1


def test_expand_num_repetitions_triples_count():
    """num_repetitions=3 on a 4-run base → 12 total."""
    matrix = _minimal_matrix(
        model_ids=["model-a", "model-b"],
        strategies=[StrategyName.SINGLE_AGENT, StrategyName.PER_FILE],
        num_repetitions=3,
    )
    runs = matrix.expand()
    assert len(runs) == 12


def test_expand_run_id_includes_all_dimensions():
    """Run ID must include experiment_id, model_id, strategy, tool_variant, profile, verif."""
    matrix = _minimal_matrix(
        model_ids=["gpt-4o"],
        strategies=[StrategyName.PER_VULN_CLASS],
        tool_variants=[ToolVariant.WITHOUT_TOOLS],
        review_profiles=[ReviewProfileName.STRICT],
        verification_variants=[VerificationVariant.WITH_VERIFICATION],
    )
    run = matrix.expand()[0]
    assert "experiment-1" in run.id
    assert "gpt-4o" in run.id
    assert "per_vuln_class" in run.id
    assert "without_tools" in run.id
    assert "strict" in run.id
    assert "with_verification" in run.id


def test_expand_no_rep_suffix_when_repetitions_equals_1():
    """With num_repetitions=1, run IDs must NOT contain '_rep'."""
    matrix = _minimal_matrix(num_repetitions=1)
    run = matrix.expand()[0]
    assert "_rep" not in run.id


def test_expand_rep_suffix_present_when_repetitions_gt_1():
    """With num_repetitions > 1, each run ID must contain '_rep<N>'."""
    matrix = _minimal_matrix(num_repetitions=2)
    runs = matrix.expand()
    assert all("_rep" in r.id for r in runs)
    ids = {r.id for r in runs}
    assert any("_rep0" in rid for rid in ids)
    assert any("_rep1" in rid for rid in ids)


def test_expand_strategy_configs_passed_to_runs():
    """strategy_configs dict is forwarded to the matching runs."""
    cfg = {"per_file": {"chunk_size": 100}}
    matrix = _minimal_matrix(
        strategies=[StrategyName.PER_FILE],
        strategy_configs=cfg,
    )
    run = matrix.expand()[0]
    assert run.strategy_config == {"chunk_size": 100}


def test_expand_model_configs_stored_on_matrix():
    """model_configs dict is stored on the matrix and accessible by model_id key."""
    cfg = {"model-a": {"temperature": 0.1}}
    matrix = _minimal_matrix(model_ids=["model-a"], model_configs=cfg)
    # model_config on ExperimentRun clashes with Pydantic v2's reserved name,
    # so we verify the matrix holds the config correctly.
    assert matrix.model_configs["model-a"] == {"temperature": 0.1}


def test_expand_verifier_model_id_propagated():
    """verifier_model_id on the matrix flows through to every run."""
    matrix = _minimal_matrix(verifier_model_id="verifier-xyz")
    run = matrix.expand()[0]
    assert run.verifier_model_id == "verifier-xyz"


# ---------------------------------------------------------------------------
# ExperimentRun.effective_verifier_model
# ---------------------------------------------------------------------------


def test_effective_verifier_model_uses_verifier_when_set():
    run = ExperimentRun(
        id="r1",
        experiment_id="e1",
        model_id="primary-model",
        strategy=StrategyName.SINGLE_AGENT,
        tool_variant=ToolVariant.WITH_TOOLS,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.WITH_VERIFICATION,
        verifier_model_id="verifier-model",
        dataset_name="ds",
        dataset_version="1.0",
    )
    assert run.effective_verifier_model == "verifier-model"


def test_effective_verifier_model_falls_back_to_model_id():
    run = ExperimentRun(
        id="r1",
        experiment_id="e1",
        model_id="primary-model",
        strategy=StrategyName.SINGLE_AGENT,
        tool_variant=ToolVariant.WITH_TOOLS,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        verifier_model_id=None,
        dataset_name="ds",
        dataset_version="1.0",
    )
    assert run.effective_verifier_model == "primary-model"


# ---------------------------------------------------------------------------
# RunResult JSON serialization round-trip
# ---------------------------------------------------------------------------


def test_run_result_json_round_trip(sample_run_result: RunResult):
    """model_dump_json → model_validate_json must produce an equal object."""
    json_str = sample_run_result.model_dump_json()
    restored = RunResult.model_validate_json(json_str)

    assert restored.status == sample_run_result.status
    assert restored.tool_call_count == sample_run_result.tool_call_count
    assert restored.estimated_cost_usd == sample_run_result.estimated_cost_usd
    assert restored.experiment.model_id == sample_run_result.experiment.model_id
    assert len(restored.findings) == len(sample_run_result.findings)
    assert restored.findings[0].id == sample_run_result.findings[0].id


# ---------------------------------------------------------------------------
# PromptSnapshot.capture()
# ---------------------------------------------------------------------------


def test_prompt_snapshot_capture_deterministic():
    """Same inputs always produce the same snapshot_id."""
    snap1 = PromptSnapshot.capture(
        system_prompt="You are a security reviewer.",
        user_message_template="Review this.",
        finding_output_format="JSON",
    )
    snap2 = PromptSnapshot.capture(
        system_prompt="You are a security reviewer.",
        user_message_template="Review this.",
        finding_output_format="JSON",
    )
    assert snap1.snapshot_id == snap2.snapshot_id


def test_prompt_snapshot_capture_different_inputs_differ():
    """Different inputs must produce different snapshot_ids."""
    snap1 = PromptSnapshot.capture(
        system_prompt="Prompt A",
        user_message_template="Template",
        finding_output_format="JSON",
    )
    snap2 = PromptSnapshot.capture(
        system_prompt="Prompt B",
        user_message_template="Template",
        finding_output_format="JSON",
    )
    assert snap1.snapshot_id != snap2.snapshot_id


def test_prompt_snapshot_id_is_16_chars():
    """snapshot_id is the first 16 hex chars of the SHA-256 digest."""
    snap = PromptSnapshot.capture(
        system_prompt="s",
        user_message_template="u",
        finding_output_format="f",
    )
    assert len(snap.snapshot_id) == 16


# ---------------------------------------------------------------------------
# ToolExtension / tool_extension_sets tests
# ---------------------------------------------------------------------------


def test_expand_tool_extension_sets_produces_correct_run_ids():
    """Matrix with 3 extension sets, 1 model, 1 strategy → 3 runs with correct suffixes."""
    matrix = _minimal_matrix(
        tool_extension_sets=[
            frozenset(),
            frozenset({ToolExtension.LSP}),
            frozenset({ToolExtension.TREE_SITTER, ToolExtension.DEVDOCS}),
        ],
    )
    runs = matrix.expand()
    assert len(runs) == 3
    run_ids = [r.id for r in runs]

    # Empty set: no suffix at all (backwards-compat)
    base = "experiment-1_model-a_single_agent_with_tools_default_none"
    assert base in run_ids
    assert f"{base}_ext-lsp" in run_ids
    assert f"{base}_ext-devdocs+tree_sitter" in run_ids


def test_expand_empty_extension_set_produces_no_suffix():
    """Default matrix (tool_extension_sets=[frozenset()]) produces byte-identical run IDs to pre-extension code."""
    matrix = _minimal_matrix()
    runs = matrix.expand()
    assert len(runs) == 1
    assert runs[0].id == "experiment-1_model-a_single_agent_with_tools_default_none"


def test_tool_extension_sets_powerset_returns_all_subsets():
    """powerset({LSP, DEVDOCS}) must return 4 frozensets (including empty set)."""
    subsets = ExperimentMatrix.tool_extension_sets_powerset({ToolExtension.LSP, ToolExtension.DEVDOCS})
    assert len(subsets) == 4
    assert frozenset() in subsets
    assert frozenset({ToolExtension.LSP}) in subsets
    assert frozenset({ToolExtension.DEVDOCS}) in subsets
    assert frozenset({ToolExtension.LSP, ToolExtension.DEVDOCS}) in subsets


def test_tool_extension_sets_powerset_empty_set():
    """powerset of empty set returns exactly [frozenset()]."""
    subsets = ExperimentMatrix.tool_extension_sets_powerset(set())
    assert subsets == [frozenset()]


def test_experiment_run_tool_extensions_default_is_empty():
    """ExperimentRun.tool_extensions defaults to empty frozenset."""
    run = ExperimentRun(
        id="r1",
        experiment_id="e1",
        model_id="m1",
        strategy=StrategyName.SINGLE_AGENT,
        tool_variant=ToolVariant.WITH_TOOLS,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        dataset_name="ds",
        dataset_version="1.0",
    )
    assert run.tool_extensions == frozenset()


def test_experiment_run_tool_extensions_serializes_sorted():
    """tool_extensions serializes to a sorted list of string values."""
    run = ExperimentRun(
        id="r1",
        experiment_id="e1",
        model_id="m1",
        strategy=StrategyName.SINGLE_AGENT,
        tool_variant=ToolVariant.WITH_TOOLS,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        dataset_name="ds",
        dataset_version="1.0",
        tool_extensions=frozenset({ToolExtension.TREE_SITTER, ToolExtension.LSP}),
    )
    dumped = json.loads(run.model_dump_json())
    assert dumped["tool_extensions"] == ["lsp", "tree_sitter"]


def test_experiment_run_tool_extensions_round_trips():
    """model_dump_json → model_validate_json preserves tool_extensions."""
    run = ExperimentRun(
        id="r1",
        experiment_id="e1",
        model_id="m1",
        strategy=StrategyName.SINGLE_AGENT,
        tool_variant=ToolVariant.WITH_TOOLS,
        review_profile=ReviewProfileName.DEFAULT,
        verification_variant=VerificationVariant.NONE,
        dataset_name="ds",
        dataset_version="1.0",
        tool_extensions=frozenset({ToolExtension.LSP, ToolExtension.DEVDOCS}),
    )
    restored = ExperimentRun.model_validate_json(run.model_dump_json())
    assert restored.tool_extensions == frozenset({ToolExtension.LSP, ToolExtension.DEVDOCS})


# ---------------------------------------------------------------------------
# allow_unavailable_models must not appear in serialised output
# ---------------------------------------------------------------------------

def test_allow_unavailable_models_excluded_from_dump():
    """allow_unavailable_models is a submit-time flag and must not be
    persisted.  Verify model_dump_json() does not include the key."""
    import json

    matrix = _minimal_matrix(allow_unavailable_models=True)
    assert matrix.allow_unavailable_models is True  # attribute readable
    dumped = json.loads(matrix.model_dump_json())
    assert "allow_unavailable_models" not in dumped


def test_allow_unavailable_models_excluded_from_model_dump():
    """model_dump() (dict form) must also exclude the key."""
    matrix = _minimal_matrix(allow_unavailable_models=True)
    dumped = matrix.model_dump()
    assert "allow_unavailable_models" not in dumped
