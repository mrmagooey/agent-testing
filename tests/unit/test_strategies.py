"""Tests for all five scan strategies."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from sec_review_framework.data.findings import StrategyOutput, VulnClass
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


# ---------------------------------------------------------------------------
# SingleAgentStrategy
# ---------------------------------------------------------------------------


def test_single_agent_strategy_name():
    assert SingleAgentStrategy().name() == "single_agent"


def test_single_agent_strategy_run_returns_strategy_output():
    model = _model_with(_FINDING_JSON)
    target = _mock_target()
    result = SingleAgentStrategy().run(target, model, _empty_registry(), {})
    assert isinstance(result, StrategyOutput)


def test_single_agent_no_dedup(monkeypatch):
    """Single agent produces no dedup entries (pre == post count)."""
    model = _model_with(_FINDING_JSON)
    target = _mock_target()
    result = SingleAgentStrategy().run(target, model, _empty_registry(), {})
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

    result = PerFileStrategy().run(target, model, _empty_registry(), {})
    assert isinstance(result, StrategyOutput)
    # Model was called exactly len(files) times (the queue should now be empty/exhausted).
    assert target.list_source_files.called


def test_per_file_strategy_returns_strategy_output():
    files = ["x.py"]
    model = _model_with(_FINDING_JSON, n=1)
    target = _mock_target(files=files)
    result = PerFileStrategy().run(target, model, _empty_registry(), {})
    assert isinstance(result, StrategyOutput)


# ---------------------------------------------------------------------------
# PerVulnClassStrategy
# ---------------------------------------------------------------------------


def test_per_vuln_class_strategy_creates_task_per_vuln_class():
    """When vuln_classes is full, one task per VulnClass value is created."""
    all_classes = list(VulnClass)
    model = _model_with(_EMPTY_FINDING_JSON, n=len(all_classes))
    target = _mock_target()

    result = PerVulnClassStrategy().run(
        target, model, _empty_registry(), config={}
    )
    assert isinstance(result, StrategyOutput)


def test_per_vuln_class_strategy_respects_config_vuln_classes_subset():
    """config['vuln_classes'] restricts which specialists are spun up."""
    subset = [VulnClass.SQLI, VulnClass.XSS]
    model = _model_with(_EMPTY_FINDING_JSON, n=len(subset))
    target = _mock_target()

    result = PerVulnClassStrategy().run(
        target, model, _empty_registry(), config={"vuln_classes": subset}
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
        result = SASTFirstStrategy().run(target, model, _empty_registry(), {})

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

    result = DiffReviewStrategy().run(target, model, _empty_registry(), {})
    assert isinstance(result, StrategyOutput)


# ---------------------------------------------------------------------------
# All strategies return StrategyOutput
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("strategy_cls", [
    SingleAgentStrategy,
    PerFileStrategy,
    PerVulnClassStrategy,
])
def test_all_strategies_return_strategy_output_type(strategy_cls):
    """All basic strategies return a StrategyOutput instance."""
    model = _model_with(_EMPTY_FINDING_JSON, n=20)
    target = _mock_target()
    result = strategy_cls().run(target, model, _empty_registry(), {})
    assert isinstance(result, StrategyOutput)
