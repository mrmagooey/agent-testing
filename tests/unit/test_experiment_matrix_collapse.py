"""Tests for the collapsed ExperimentMatrix.expand() behaviour.

After the ExperimentMatrix collapse, the matrix has a single axis:
``strategy_ids: list[str]``.  Run IDs have the form::

    {experiment_id}_{strategy_id}[_rep{N}]

The ``_ext-<sorted>`` suffix is entirely absent.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from sec_review_framework.data.experiment import (
    ExperimentMatrix,
    ExperimentRun,
    ToolExtension,
)
from sec_review_framework.data.strategy_bundle import (
    OrchestrationShape,
    OverrideRule,
    StrategyBundleDefault,
    StrategyBundleOverride,
    UserStrategy,
)
from sec_review_framework.strategies.strategy_registry import StrategyRegistry


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_CREATED_AT = datetime(2026, 1, 1, 0, 0, 0)

_BASE_BUNDLE = StrategyBundleDefault(
    system_prompt="sys",
    user_prompt_template="user",
    model_id="claude-opus-4-5",
    tools=frozenset(["read_file"]),
    verification="none",
    max_turns=10,
    tool_extensions=frozenset(),
)


def _make_registry(*strategy_ids: str, **bundles: StrategyBundleDefault) -> StrategyRegistry:
    """Build an in-memory registry.

    If a bundle is provided for a given strategy_id (via kwargs), use it;
    otherwise fall back to ``_BASE_BUNDLE``.
    """
    registry = StrategyRegistry()
    for sid in strategy_ids:
        registry.register(
            UserStrategy(
                id=sid,
                name=sid,
                parent_strategy_id=None,
                orchestration_shape=OrchestrationShape.SINGLE_AGENT,
                default=bundles.get(sid, _BASE_BUNDLE),
                overrides=[],
                created_at=_CREATED_AT,
                is_builtin=False,
            )
        )
    return registry


# ---------------------------------------------------------------------------
# Run-count tests
# ---------------------------------------------------------------------------


def test_expand_one_strategy_one_rep_produces_one_run():
    """Single strategy, 1 rep → exactly 1 run."""
    registry = _make_registry("strat-a")
    matrix = ExperimentMatrix(
        experiment_id="exp",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=["strat-a"],
        num_repetitions=1,
    )
    runs = matrix.expand(registry=registry)
    assert len(runs) == 1


def test_expand_one_strategy_three_reps_produces_three_runs():
    """Single strategy, 3 reps → exactly 3 runs."""
    registry = _make_registry("strat-a")
    matrix = ExperimentMatrix(
        experiment_id="exp",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=["strat-a"],
        num_repetitions=3,
    )
    runs = matrix.expand(registry=registry)
    assert len(runs) == 3


def test_expand_two_strategies_one_rep_produces_two_runs():
    """2 strategies × 1 rep → 2 runs."""
    registry = _make_registry("strat-a", "strat-b")
    matrix = ExperimentMatrix(
        experiment_id="exp",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=["strat-a", "strat-b"],
        num_repetitions=1,
    )
    runs = matrix.expand(registry=registry)
    assert len(runs) == 2


def test_expand_two_strategies_three_reps_produces_six_runs():
    """2 strategies × 3 reps → 6 runs."""
    registry = _make_registry("strat-a", "strat-b")
    matrix = ExperimentMatrix(
        experiment_id="exp",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=["strat-a", "strat-b"],
        num_repetitions=3,
    )
    runs = matrix.expand(registry=registry)
    assert len(runs) == 6


# ---------------------------------------------------------------------------
# Run-ID format tests
# ---------------------------------------------------------------------------


def test_run_id_format_one_rep():
    """With num_repetitions=1, run ID must be ``{exp}_{strategy_id}`` — no rep suffix."""
    registry = _make_registry("builtin.single_agent")
    matrix = ExperimentMatrix(
        experiment_id="my-exp",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=["builtin.single_agent"],
        num_repetitions=1,
    )
    runs = matrix.expand(registry=registry)
    assert runs[0].id == "my-exp_builtin.single_agent"


def test_run_id_format_multiple_reps():
    """With num_repetitions=3, run IDs must be ``{exp}_{strategy_id}_rep{N}``."""
    registry = _make_registry("strat-x")
    matrix = ExperimentMatrix(
        experiment_id="exp",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=["strat-x"],
        num_repetitions=3,
    )
    runs = matrix.expand(registry=registry)
    ids = [r.id for r in runs]
    assert "exp_strat-x_rep0" in ids
    assert "exp_strat-x_rep1" in ids
    assert "exp_strat-x_rep2" in ids


def test_no_rep_suffix_when_repetitions_equals_1():
    """num_repetitions=1 must not produce any ``_rep`` suffix."""
    registry = _make_registry("strat-a")
    matrix = ExperimentMatrix(
        experiment_id="exp",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=["strat-a"],
        num_repetitions=1,
    )
    run = matrix.expand(registry=registry)[0]
    assert "_rep" not in run.id


def test_rep_suffix_present_when_repetitions_gt_1():
    """All run IDs must contain ``_rep<N>`` when num_repetitions > 1."""
    registry = _make_registry("strat-a")
    matrix = ExperimentMatrix(
        experiment_id="exp",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=["strat-a"],
        num_repetitions=4,
    )
    runs = matrix.expand(registry=registry)
    assert all("_rep" in r.id for r in runs)
    rep_indices = {r.repetition_index for r in runs}
    assert rep_indices == {0, 1, 2, 3}


# ---------------------------------------------------------------------------
# _ext- suffix is NEVER present
# ---------------------------------------------------------------------------


def test_ext_suffix_never_present_for_strategy_with_no_extensions():
    """The ``_ext-`` suffix must never appear regardless of tool_extensions."""
    registry = _make_registry("strat-a")
    matrix = ExperimentMatrix(
        experiment_id="exp",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=["strat-a"],
    )
    for run in matrix.expand(registry=registry):
        assert "_ext-" not in run.id


def test_ext_suffix_never_present_even_when_strategy_has_extensions():
    """Even when a strategy has tool_extensions, run IDs must NOT use the old _ext- suffix."""
    bundle_with_lsp = StrategyBundleDefault(
        system_prompt="sys",
        user_prompt_template="user",
        model_id="claude-opus-4-5",
        tools=frozenset(["read_file"]),
        verification="none",
        max_turns=10,
        tool_extensions=frozenset(["lsp", "tree_sitter"]),
    )
    registry = StrategyRegistry()
    registry.register(
        UserStrategy(
            id="strat-with-ext",
            name="strat-with-ext",
            parent_strategy_id=None,
            orchestration_shape=OrchestrationShape.SINGLE_AGENT,
            default=bundle_with_lsp,
            overrides=[],
            created_at=_CREATED_AT,
            is_builtin=False,
        )
    )
    matrix = ExperimentMatrix(
        experiment_id="exp",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=["strat-with-ext"],
    )
    for run in matrix.expand(registry=registry):
        assert "_ext-" not in run.id


# ---------------------------------------------------------------------------
# Run-ID determinism
# ---------------------------------------------------------------------------


def test_run_ids_are_deterministic_across_calls():
    """Calling expand() twice on the same matrix must produce identical IDs."""
    registry = _make_registry("strat-a", "strat-b")
    matrix = ExperimentMatrix(
        experiment_id="exp",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=["strat-a", "strat-b"],
        num_repetitions=2,
    )
    ids_first = [r.id for r in matrix.expand(registry=registry)]
    ids_second = [r.id for r in matrix.expand(registry=registry)]
    assert ids_first == ids_second


# ---------------------------------------------------------------------------
# strategy_id on ExperimentRun
# ---------------------------------------------------------------------------


def test_expanded_runs_carry_strategy_id():
    """Each run must carry the strategy_id for worker consumption."""
    registry = _make_registry("strat-a", "strat-b")
    matrix = ExperimentMatrix(
        experiment_id="exp",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=["strat-a", "strat-b"],
    )
    runs = matrix.expand(registry=registry)
    strategy_ids_found = {r.strategy_id for r in runs}
    assert strategy_ids_found == {"strat-a", "strat-b"}


# ---------------------------------------------------------------------------
# tool_extensions on ExperimentRun — derived from strategy
# ---------------------------------------------------------------------------


def test_tool_extensions_derived_from_strategy_default_bundle():
    """run.tool_extensions must include the strategy default's tool_extensions."""
    bundle = StrategyBundleDefault(
        system_prompt="sys",
        user_prompt_template="user",
        model_id="claude-opus-4-5",
        tools=frozenset(["read_file"]),
        verification="none",
        max_turns=10,
        tool_extensions=frozenset(["lsp"]),
    )
    registry = StrategyRegistry()
    registry.register(
        UserStrategy(
            id="strat-lsp",
            name="strat-lsp",
            parent_strategy_id=None,
            orchestration_shape=OrchestrationShape.SINGLE_AGENT,
            default=bundle,
            overrides=[],
            created_at=_CREATED_AT,
            is_builtin=False,
        )
    )
    matrix = ExperimentMatrix(
        experiment_id="exp",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=["strat-lsp"],
    )
    run = matrix.expand(registry=registry)[0]
    assert ToolExtension.LSP in run.tool_extensions


def test_tool_extensions_union_of_default_and_overrides():
    """run.tool_extensions must be the union of default and all override tool_extensions."""
    from sec_review_framework.data.strategy_bundle import StrategyBundleOverride

    bundle = StrategyBundleDefault(
        system_prompt="sys",
        user_prompt_template="user",
        model_id="claude-opus-4-5",
        tools=frozenset(["read_file"]),
        verification="none",
        max_turns=10,
        tool_extensions=frozenset(["lsp"]),
    )
    override_rule = OverrideRule(
        key="sqli",
        override=StrategyBundleOverride(
            tool_extensions=frozenset(["devdocs"]),
        ),
    )
    registry = StrategyRegistry()
    registry.register(
        UserStrategy(
            id="strat-mixed",
            name="strat-mixed",
            parent_strategy_id=None,
            orchestration_shape=OrchestrationShape.PER_VULN_CLASS,
            default=bundle,
            overrides=[override_rule],
            created_at=_CREATED_AT,
            is_builtin=False,
        )
    )
    matrix = ExperimentMatrix(
        experiment_id="exp",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=["strat-mixed"],
    )
    run = matrix.expand(registry=registry)[0]
    assert ToolExtension.LSP in run.tool_extensions
    assert ToolExtension.DEVDOCS in run.tool_extensions


# ---------------------------------------------------------------------------
# verifier_model_id propagated
# ---------------------------------------------------------------------------


def test_verifier_model_id_propagated_to_all_runs():
    """matrix.verifier_model_id must flow to every expanded run."""
    registry = _make_registry("strat-a", "strat-b")
    matrix = ExperimentMatrix(
        experiment_id="exp",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=["strat-a", "strat-b"],
        verifier_model_id="verifier-xyz",
    )
    runs = matrix.expand(registry=registry)
    assert all(r.verifier_model_id == "verifier-xyz" for r in runs)


# ---------------------------------------------------------------------------
# default registry integration
# ---------------------------------------------------------------------------


def test_expand_uses_default_registry_when_none_given():
    """Passing registry=None should fall back to load_default_registry()."""
    matrix = ExperimentMatrix(
        experiment_id="exp",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=["builtin.single_agent"],
    )
    runs = matrix.expand()  # no registry passed
    assert len(runs) == 1
    assert runs[0].strategy_id == "builtin.single_agent"


# ---------------------------------------------------------------------------
# bundle_json embedding (so workers can load user strategies without DB)
# ---------------------------------------------------------------------------


def test_expand_embeds_bundle_json_on_each_run():
    """Every expanded run must carry the canonical bundle JSON."""
    from sec_review_framework.data.strategy_bundle import UserStrategy, canonical_json

    registry = _make_registry("my-custom")
    matrix = ExperimentMatrix(
        experiment_id="exp",
        dataset_name="ds",
        dataset_version="1.0",
        strategy_ids=["my-custom"],
        num_repetitions=2,
    )
    runs = matrix.expand(registry=registry)
    assert len(runs) == 2
    strategy = registry.get("my-custom")
    expected = canonical_json(strategy)
    for run in runs:
        assert run.bundle_json == expected
        # round-trip reconstructs the same UserStrategy
        reconstructed = UserStrategy.model_validate_json(run.bundle_json)
        assert reconstructed.id == strategy.id
        assert reconstructed.default.system_prompt == strategy.default.system_prompt
