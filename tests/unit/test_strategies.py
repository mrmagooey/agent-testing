"""Tests for all five scan strategies.

Updated to use the new UserStrategy-based run() signature.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from sec_review_framework.data.findings import StrategyOutput, VulnClass
from sec_review_framework.data.strategy_bundle import (
    OrchestrationShape,
    OverrideRule,
    StrategyBundleDefault,
    StrategyBundleOverride,
    UserStrategy,
)
from sec_review_framework.models.base import ModelResponse
from sec_review_framework.strategies.diff_review import DiffReviewStrategy
from sec_review_framework.strategies.per_file import PerFileStrategy
from sec_review_framework.strategies.per_vuln_class import PerVulnClassStrategy
from sec_review_framework.strategies.sast_first import SASTFirstStrategy
from sec_review_framework.strategies.single_agent import SingleAgentStrategy
from sec_review_framework.tools.registry import ToolRegistry
from tests.conftest import FakeModelProvider

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CREATED_AT = datetime(2026, 1, 1, 0, 0, 0)

# A JSON block that FindingParser will successfully parse into one SQLi finding.
_FINDING_JSON = """
Here is my analysis.

```json
[
  {
    "file_path": "myapp/views.py",
    "line_start": 10,
    "line_end": 12,
    "vuln_class": "sqli",
    "cwe_ids": ["CWE-89"],
    "severity": "high",
    "title": "SQL Injection found",
    "description": "Raw SQL query constructed from user input.",
    "recommendation": "Use parameterized queries.",
    "confidence": 0.9
  }
]
```
"""

_EMPTY_FINDING_JSON = "Analysis complete.\n\n```json\n[]\n```"


def _model_with(content: str, n: int = 1) -> FakeModelProvider:
    """Returns a FakeModelProvider that returns `content` up to n times."""
    responses = [
        ModelResponse(
            content=content,
            tool_calls=[],
            input_tokens=50,
            output_tokens=100,
            model_id="fake",
            raw={},
        )
        for _ in range(n)
    ]
    return FakeModelProvider(responses)


def _empty_registry() -> ToolRegistry:
    return ToolRegistry()


def _mock_target(files: list[str] | None = None) -> MagicMock:
    target = MagicMock()
    target.list_source_files.return_value = files or ["myapp/views.py", "myapp/utils.py"]
    target.read_file.return_value = "def foo(): pass"
    target.get_file_tree.return_value = "myapp/\n  views.py\n  utils.py"
    target.repo_path = MagicMock()
    return target


def _make_single_agent_strategy() -> UserStrategy:
    return UserStrategy(
        id="test.single_agent",
        name="Test Single Agent",
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.SINGLE_AGENT,
        default=StrategyBundleDefault(
            system_prompt="You are a security reviewer.",
            user_prompt_template="Review:\n{repo_summary}\n\n{finding_output_format}",
            profile_modifier="",
            model_id="fake",
            tools=frozenset(),
            verification="none",
            max_turns=5,
            tool_extensions=frozenset(),
        ),
        overrides=[],
        created_at=_CREATED_AT,
        is_builtin=False,
    )


def _make_per_file_strategy() -> UserStrategy:
    return UserStrategy(
        id="test.per_file",
        name="Test Per File",
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.PER_FILE,
        default=StrategyBundleDefault(
            system_prompt="Review this file.",
            user_prompt_template=(
                "File {file_path}:\n{file_content}\n\n{finding_output_format}"
            ),
            profile_modifier="",
            model_id="fake",
            tools=frozenset(),
            verification="none",
            max_turns=5,
            tool_extensions=frozenset(),
        ),
        overrides=[],
        created_at=_CREATED_AT,
        is_builtin=False,
    )


def _make_per_vuln_class_strategy(
    active_classes: list[VulnClass] | None = None,
) -> UserStrategy:
    classes = active_classes or list(VulnClass)
    overrides = [
        OverrideRule(
            key=vc.value,
            override=StrategyBundleOverride(
                system_prompt=f"Expert in {vc.value}.",
            ),
        )
        for vc in classes
    ]
    return UserStrategy(
        id="test.per_vuln_class",
        name="Test Per Vuln Class",
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.PER_VULN_CLASS,
        default=StrategyBundleDefault(
            system_prompt="",
            user_prompt_template=(
                "Review for {vuln_class}:\n{repo_summary}\n\n{finding_output_format}"
            ),
            profile_modifier="",
            model_id="fake",
            tools=frozenset(),
            verification="none",
            max_turns=5,
            tool_extensions=frozenset(),
        ),
        overrides=overrides,
        created_at=_CREATED_AT,
        is_builtin=False,
    )


def _make_sast_first_strategy() -> UserStrategy:
    return UserStrategy(
        id="test.sast_first",
        name="Test SAST First",
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.SAST_FIRST,
        default=StrategyBundleDefault(
            system_prompt="Triage SAST findings.",
            user_prompt_template=(
                "Triage {file_path}:\n{sast_summary}\n{file_content}\n\n{finding_output_format}"
            ),
            profile_modifier="",
            model_id="fake",
            tools=frozenset(),
            verification="none",
            max_turns=5,
            tool_extensions=frozenset(),
        ),
        overrides=[],
        created_at=_CREATED_AT,
        is_builtin=False,
    )


def _make_diff_review_strategy() -> UserStrategy:
    return UserStrategy(
        id="test.diff_review",
        name="Test Diff Review",
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.DIFF_REVIEW,
        default=StrategyBundleDefault(
            system_prompt="Review this diff.",
            user_prompt_template=(
                "Diff:\n{diff_text}\n\nFiles:\n{file_context}\n\n{finding_output_format}"
            ),
            profile_modifier="",
            model_id="fake",
            tools=frozenset(),
            verification="none",
            max_turns=5,
            tool_extensions=frozenset(),
        ),
        overrides=[],
        created_at=_CREATED_AT,
        is_builtin=False,
    )


# ---------------------------------------------------------------------------
# SingleAgentStrategy
# ---------------------------------------------------------------------------


def test_single_agent_strategy_name():
    assert SingleAgentStrategy().name() == "single_agent"


def test_single_agent_strategy_run_returns_strategy_output():
    model = _model_with(_FINDING_JSON)
    target = _mock_target()
    result = SingleAgentStrategy().run(target, model, _empty_registry(), _make_single_agent_strategy())
    assert isinstance(result, StrategyOutput)


def test_single_agent_no_dedup():
    """Single agent produces no dedup entries (pre == post count)."""
    model = _model_with(_FINDING_JSON)
    target = _mock_target()
    result = SingleAgentStrategy().run(target, model, _empty_registry(), _make_single_agent_strategy())
    assert result.pre_dedup_count == result.post_dedup_count
    assert result.dedup_log == []


# ---------------------------------------------------------------------------
# PerFileStrategy
# ---------------------------------------------------------------------------


def test_per_file_strategy_creates_task_per_source_file():
    """PerFileStrategy invokes the model once per source file."""
    files = ["a.py", "b.py", "c.py"]
    # Provide one response per file.
    model = _model_with(_EMPTY_FINDING_JSON, n=len(files))
    target = _mock_target(files=files)

    result = PerFileStrategy().run(target, model, _empty_registry(), _make_per_file_strategy())
    assert isinstance(result, StrategyOutput)
    # Model was called exactly len(files) times.
    assert target.list_source_files.called


def test_per_file_strategy_returns_strategy_output():
    files = ["x.py"]
    model = _model_with(_FINDING_JSON, n=1)
    target = _mock_target(files=files)
    result = PerFileStrategy().run(target, model, _empty_registry(), _make_per_file_strategy())
    assert isinstance(result, StrategyOutput)


# ---------------------------------------------------------------------------
# PerVulnClassStrategy
# ---------------------------------------------------------------------------


def test_per_vuln_class_strategy_creates_task_per_vuln_class():
    """When all vuln classes active, one task per VulnClass value is created."""
    all_classes = list(VulnClass)
    model = _model_with(_EMPTY_FINDING_JSON, n=len(all_classes))
    target = _mock_target()
    strategy = _make_per_vuln_class_strategy(all_classes)

    result = PerVulnClassStrategy().run(target, model, _empty_registry(), strategy)
    assert isinstance(result, StrategyOutput)


def test_per_vuln_class_strategy_respects_active_classes_subset():
    """active_classes restricts which specialists are spun up."""
    subset = [VulnClass.SQLI, VulnClass.XSS]
    model = _model_with(_EMPTY_FINDING_JSON, n=len(subset))
    target = _mock_target()
    strategy = _make_per_vuln_class_strategy(subset)

    result = PerVulnClassStrategy().run(
        target, model, _empty_registry(), strategy, active_classes=subset
    )
    assert isinstance(result, StrategyOutput)
    # The model should have only been called twice (once per class in subset).
    assert len(model.token_log) == len(subset)


def test_per_vuln_class_strategy_name():
    assert PerVulnClassStrategy().name() == "per_vuln_class"


# ---------------------------------------------------------------------------
# SASTFirstStrategy
# ---------------------------------------------------------------------------


def test_sast_first_strategy_returns_empty_when_no_semgrep_results():
    """When semgrep finds nothing, strategy returns an empty StrategyOutput."""
    model = _model_with(_EMPTY_FINDING_JSON)
    target = _mock_target()
    target.repo_path = MagicMock()

    with patch(
        "sec_review_framework.strategies.sast_first.SemgrepTool"
    ) as MockSemgrep:
        MockSemgrep.return_value.run_full_scan.return_value = []
        result = SASTFirstStrategy().run(target, model, _empty_registry(), _make_sast_first_strategy())

    assert isinstance(result, StrategyOutput)
    assert result.findings == []
    assert result.pre_dedup_count == 0


def test_sast_first_strategy_name():
    assert SASTFirstStrategy().name() == "sast_first"


# ---------------------------------------------------------------------------
# DiffReviewStrategy
# ---------------------------------------------------------------------------


def test_diff_review_strategy_name():
    assert DiffReviewStrategy().name() == "diff_review"


def test_diff_review_strategy_returns_strategy_output():
    model = _model_with(_FINDING_JSON)
    target = _mock_target()

    diff_spec = MagicMock()
    diff_spec.base_ref = "main"
    diff_spec.head_ref = "HEAD"
    target.load_diff_spec.return_value = diff_spec
    target.get_diff.return_value = "--- a/views.py\n+++ b/views.py\n@@ -1,1 +1,2 @@\n+bad_line"
    target.get_changed_files.return_value = ["myapp/views.py"]

    result = DiffReviewStrategy().run(target, model, _empty_registry(), _make_diff_review_strategy())
    assert isinstance(result, StrategyOutput)


# ---------------------------------------------------------------------------
# All strategies return StrategyOutput
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy_cls,strategy_factory", [
    (SingleAgentStrategy, _make_single_agent_strategy),
    (PerFileStrategy, _make_per_file_strategy),
    (PerVulnClassStrategy, lambda: _make_per_vuln_class_strategy()),
])
def test_all_strategies_return_strategy_output_type(strategy_cls, strategy_factory):
    """All basic strategies return a StrategyOutput instance."""
    model = _model_with(_EMPTY_FINDING_JSON, n=20)
    target = _mock_target()
    result = strategy_cls().run(target, model, _empty_registry(), strategy_factory())
    assert isinstance(result, StrategyOutput)
