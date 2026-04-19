"""Unit tests for ExperimentMatrix run_id sanitization (slashed model IDs)."""

import pytest

from sec_review_framework.data.experiment import (
    ExperimentMatrix,
    ReviewProfileName,
    StrategyName,
    ToolVariant,
    VerificationVariant,
)


def _minimal_matrix(**overrides) -> ExperimentMatrix:
    """Helper to create a minimal ExperimentMatrix with sensible defaults."""
    defaults = dict(
        batch_id="batch-1",
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


def test_run_id_with_slashed_model_id_no_forward_slashes():
    """A matrix with a slashed model ID should expand to runs with no '/' in the run_id."""
    matrix = _minimal_matrix(
        model_ids=["openrouter/meta-llama/llama-3.1-8b-instruct"],
    )
    runs = matrix.expand()
    assert len(runs) == 1
    run_id = runs[0].id
    assert "/" not in run_id, f"run_id contains '/': {run_id}"


def test_run_id_slashed_model_replaced_with_dashes():
    """Slashes in model_id should be replaced with '--'."""
    matrix = _minimal_matrix(
        model_ids=["openrouter/meta-llama/llama-3.1-8b-instruct"],
    )
    runs = matrix.expand()
    run_id = runs[0].id
    # The safe_model_id should have "/" replaced with "--"
    expected_safe = "openrouter--meta-llama--llama-3.1-8b-instruct"
    assert expected_safe in run_id, f"Expected '{expected_safe}' in '{run_id}'"


def test_run_id_without_slashes_unchanged():
    """Model IDs without slashes should remain unchanged in the run_id."""
    matrix = _minimal_matrix(
        model_ids=["gpt-4o"],
    )
    runs = matrix.expand()
    run_id = runs[0].id
    assert "gpt-4o" in run_id, f"Expected 'gpt-4o' in '{run_id}'"


def test_multiple_slashed_models_all_sanitized():
    """Multiple slashed model IDs should all be sanitized independently."""
    matrix = _minimal_matrix(
        model_ids=[
            "openrouter/meta-llama/llama-3.1-8b",
            "provider/org/model-name",
        ],
    )
    runs = matrix.expand()
    assert len(runs) == 2
    for run in runs:
        assert "/" not in run.id, f"run_id contains '/': {run.id}"


def test_run_id_preserves_other_dimensions():
    """Sanitization should not affect other run_id components."""
    matrix = _minimal_matrix(
        batch_id="my-batch",
        model_ids=["openrouter/model"],
        strategies=[StrategyName.PER_FILE],
        tool_variants=[ToolVariant.WITHOUT_TOOLS],
        review_profiles=[ReviewProfileName.STRICT],
    )
    runs = matrix.expand()
    run_id = runs[0].id
    assert "my-batch" in run_id
    assert "openrouter--model" in run_id
    assert "per_file" in run_id
    assert "without_tools" in run_id
    assert "strict" in run_id


def test_run_id_with_extensions_and_slashes():
    """Slashed model IDs should work correctly even with tool extensions."""
    from sec_review_framework.data.experiment import ToolExtension

    matrix = _minimal_matrix(
        model_ids=["openrouter/meta-llama/llama"],
        tool_extension_sets=[frozenset([ToolExtension.TREE_SITTER])],
    )
    runs = matrix.expand()
    assert len(runs) == 1
    run_id = runs[0].id
    assert "/" not in run_id, f"run_id contains '/': {run_id}"
    assert "openrouter--meta-llama--llama" in run_id
    assert "_ext-" in run_id  # Extensions are included


def test_run_id_with_repetitions_and_slashes():
    """Repetitions should work correctly with slashed model IDs."""
    matrix = _minimal_matrix(
        model_ids=["openrouter/meta-llama/llama"],
        num_repetitions=3,
    )
    runs = matrix.expand()
    assert len(runs) == 3
    # All runs should have sanitized model_id
    for run in runs:
        assert "/" not in run.id
        assert "openrouter--meta-llama--llama" in run.id
