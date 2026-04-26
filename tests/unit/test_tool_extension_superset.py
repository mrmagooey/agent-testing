"""Unit tests for the tool-extension superset check in worker.py.

Tests:
- Strategy with no extensions → check passes regardless of pod state
- Strategy default requires LSP, pod has nothing → fails fast with clear message
- Strategy default has no extensions but an override requires LSP, pod has none → fails
- Pod has LSP + TREE_SITTER, strategy requires only LSP → passes
"""

from __future__ import annotations

from datetime import datetime

import pytest

from sec_review_framework.data.experiment import ToolExtension
from sec_review_framework.data.strategy_bundle import (
    OrchestrationShape,
    OverrideRule,
    StrategyBundleDefault,
    StrategyBundleOverride,
    UserStrategy,
)
from sec_review_framework.worker import check_tool_extension_superset


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_CREATED_AT = datetime(2026, 1, 1, 0, 0, 0)


def _make_strategy(
    default_extensions: frozenset[str] = frozenset(),
    overrides: list[OverrideRule] | None = None,
    shape: OrchestrationShape = OrchestrationShape.SINGLE_AGENT,
) -> UserStrategy:
    """Build a minimal UserStrategy with the given default tool_extensions."""
    # single_agent / diff_review require empty overrides
    if shape in (OrchestrationShape.SINGLE_AGENT, OrchestrationShape.DIFF_REVIEW):
        if overrides:
            raise ValueError("Single-agent shapes cannot have overrides in this helper")
        overrides = []
    else:
        overrides = overrides or []

    return UserStrategy(
        id="test.strategy",
        name="Test Strategy",
        parent_strategy_id=None,
        orchestration_shape=shape,
        default=StrategyBundleDefault(
            system_prompt="sys",
            user_prompt_template="user",
            profile_modifier="",
            model_id="fake-model",
            tools=frozenset(),
            verification="none",
            max_turns=10,
            tool_extensions=default_extensions,
        ),
        overrides=overrides,
        created_at=_CREATED_AT,
        is_builtin=False,
    )


def _make_per_file_strategy_with_override(
    default_extensions: frozenset[str],
    override_extensions: frozenset[str],
) -> UserStrategy:
    """Build a per_file strategy with one override that has tool_extensions."""
    overrides = [
        OverrideRule(
            key="*.py",
            override=StrategyBundleOverride(
                tool_extensions=override_extensions,
            ),
        )
    ]
    return UserStrategy(
        id="test.per_file_override",
        name="Test Per File Override",
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.PER_FILE,
        default=StrategyBundleDefault(
            system_prompt="sys",
            user_prompt_template="user {file_path} {file_content} {finding_output_format}",
            profile_modifier="",
            model_id="fake-model",
            tools=frozenset(),
            verification="none",
            max_turns=10,
            tool_extensions=default_extensions,
        ),
        overrides=overrides,
        created_at=_CREATED_AT,
        is_builtin=False,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSuperset:
    def test_no_extensions_always_passes(self):
        """Strategy requiring no extensions passes regardless of pod state."""
        strategy = _make_strategy(default_extensions=frozenset())
        # Pod has nothing — should still pass
        check_tool_extension_superset("run-001", strategy, frozenset())

    def test_no_extensions_with_full_pod_passes(self):
        """Strategy requiring no extensions passes even when pod has all extensions."""
        strategy = _make_strategy(default_extensions=frozenset())
        check_tool_extension_superset(
            "run-002",
            strategy,
            frozenset({ToolExtension.LSP, ToolExtension.TREE_SITTER, ToolExtension.DEVDOCS}),
        )

    def test_default_requires_lsp_pod_empty_fails(self):
        """Strategy default requires LSP but pod has nothing → RuntimeError."""
        strategy = _make_strategy(default_extensions=frozenset({"lsp"}))
        with pytest.raises(RuntimeError) as exc_info:
            check_tool_extension_superset("run-lsp-fail", strategy, frozenset())
        assert "run-lsp-fail" in str(exc_info.value)
        assert "lsp" in str(exc_info.value)

    def test_default_empty_override_requires_lsp_pod_empty_fails(self):
        """Default has no extensions but an override requires LSP — union is checked."""
        strategy = _make_per_file_strategy_with_override(
            default_extensions=frozenset(),
            override_extensions=frozenset({"lsp"}),
        )
        with pytest.raises(RuntimeError) as exc_info:
            check_tool_extension_superset("run-override-lsp", strategy, frozenset())
        assert "run-override-lsp" in str(exc_info.value)
        assert "lsp" in str(exc_info.value)

    def test_pod_has_superset_passes(self):
        """Pod has LSP + TREE_SITTER, strategy requires only LSP → passes."""
        strategy = _make_strategy(default_extensions=frozenset({"lsp"}))
        check_tool_extension_superset(
            "run-superset-ok",
            strategy,
            frozenset({ToolExtension.LSP, ToolExtension.TREE_SITTER}),
        )

    def test_pod_has_lsp_strategy_requires_tree_sitter_fails(self):
        """Pod has LSP but strategy requires TREE_SITTER → fails."""
        strategy = _make_strategy(default_extensions=frozenset({"tree_sitter"}))
        with pytest.raises(RuntimeError) as exc_info:
            check_tool_extension_superset(
                "run-tree-sitter-fail",
                strategy,
                frozenset({ToolExtension.LSP}),
            )
        assert "tree_sitter" in str(exc_info.value)

    def test_error_message_contains_run_id(self):
        """The error message must contain the run ID for easy log correlation."""
        strategy = _make_strategy(default_extensions=frozenset({"lsp"}))
        with pytest.raises(RuntimeError) as exc_info:
            check_tool_extension_superset("my-specific-run-id-123", strategy, frozenset())
        assert "my-specific-run-id-123" in str(exc_info.value)

    def test_union_of_default_and_override_extensions(self):
        """Both default and override extensions are unioned for the check."""
        # Default requires LSP, override requires TREE_SITTER
        # Pod has only LSP → should fail because TREE_SITTER is missing
        strategy = _make_per_file_strategy_with_override(
            default_extensions=frozenset({"lsp"}),
            override_extensions=frozenset({"tree_sitter"}),
        )
        with pytest.raises(RuntimeError) as exc_info:
            check_tool_extension_superset(
                "run-union-fail",
                strategy,
                frozenset({ToolExtension.LSP}),  # has LSP but not TREE_SITTER
            )
        assert "tree_sitter" in str(exc_info.value)

    def test_pod_has_all_required_passes(self):
        """Pod has exactly the required extensions → passes."""
        strategy = _make_per_file_strategy_with_override(
            default_extensions=frozenset({"lsp"}),
            override_extensions=frozenset({"tree_sitter"}),
        )
        # Pod has both LSP and TREE_SITTER
        check_tool_extension_superset(
            "run-all-ok",
            strategy,
            frozenset({ToolExtension.LSP, ToolExtension.TREE_SITTER}),
        )

    def test_unknown_extension_value_raises_with_typo_hint(self):
        """An unrecognised extension string (typo in strategy bundle) raises RuntimeError."""
        strategy = _make_strategy(default_extensions=frozenset({"lsp", "not_a_real_ext"}))
        with pytest.raises(RuntimeError) as exc_info:
            check_tool_extension_superset(
                "run-unknown-ext",
                strategy,
                frozenset({ToolExtension.LSP}),
            )
        msg = str(exc_info.value)
        assert "not_a_real_ext" in msg, "Error message must name the unknown extension"
        assert "typo" in msg.lower() or "unknown" in msg.lower()

    def test_multiple_unknown_extension_values_all_listed(self):
        """Every unknown extension string must appear in the error message."""
        strategy = _make_strategy(
            default_extensions=frozenset({"lsp", "bad_one", "bad_two"})
        )
        with pytest.raises(RuntimeError) as exc_info:
            check_tool_extension_superset("run-multi-unknown", strategy, frozenset({ToolExtension.LSP}))
        msg = str(exc_info.value)
        assert "bad_one" in msg
        assert "bad_two" in msg
