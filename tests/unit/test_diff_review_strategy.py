"""Unit tests for DiffReviewStrategy — edge cases."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from sec_review_framework.strategies.diff_review import DiffReviewStrategy
from sec_review_framework.data.findings import StrategyOutput
from sec_review_framework.data.strategy_bundle import (
    OrchestrationShape,
    StrategyBundleDefault,
    UserStrategy,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_user_strategy(
    system_prompt: str = "You are a security reviewer.",
    user_prompt_template: str = (
        "Review this diff:\n{diff_text}\n\nFiles:\n{file_context}\n\n{finding_output_format}"
    ),
    max_turns: int = 60,
) -> UserStrategy:
    """Build a minimal UserStrategy for diff_review tests."""
    return UserStrategy(
        id="test.diff_review",
        name="Test Diff Review",
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.DIFF_REVIEW,
        default=StrategyBundleDefault(
            system_prompt=system_prompt,
            user_prompt_template=user_prompt_template,
            profile_modifier="",
            model_id="fake-model",
            tools=frozenset(),
            verification="none",
            max_turns=max_turns,
            tool_extensions=frozenset(),
        ),
        overrides=[],
        created_at=datetime(2026, 1, 1),
        is_builtin=False,
    )


def _make_target(
    diff_text: str = "--- a/app.py\n+++ b/app.py\n@@ -1,3 +1,5 @@\n def foo():\n+    vuln = 1\n     pass\n",
    changed_files: list[str] | None = None,
    file_contents: dict[str, str] | None = None,
    load_diff_spec_raises: Exception | None = None,
) -> MagicMock:
    """Build a mock TargetCodebase."""
    target = MagicMock()

    diff_spec = MagicMock()
    diff_spec.base_ref = "HEAD~1"
    diff_spec.head_ref = "HEAD"

    if load_diff_spec_raises:
        target.load_diff_spec.side_effect = load_diff_spec_raises
    else:
        target.load_diff_spec.return_value = diff_spec

    target.get_diff.return_value = diff_text
    target.get_changed_files.return_value = changed_files or ["app.py"]

    _file_contents = file_contents or {"app.py": "def foo():\n    pass\n"}
    target.read_file.side_effect = lambda fp: _file_contents.get(fp, "")

    return target


def _make_model(response_text: str = "```json\n[]\n```") -> MagicMock:
    from sec_review_framework.models.base import ModelResponse

    model = MagicMock()
    model.complete.return_value = ModelResponse(
        content=response_text,
        tool_calls=[],
        input_tokens=100,
        output_tokens=50,
        model_id="fake-model",
        raw={},
    )
    return model


def _make_tools() -> MagicMock:
    tools = MagicMock()
    tools.as_list.return_value = []
    return tools


def _run_strategy(
    target=None,
    model=None,
    tools=None,
    strategy: UserStrategy | None = None,
) -> StrategyOutput:
    if target is None:
        target = _make_target()
    if model is None:
        model = _make_model()
    if tools is None:
        tools = _make_tools()
    if strategy is None:
        strategy = _make_user_strategy()

    impl = DiffReviewStrategy()
    return impl.run(target, model, tools, strategy)


# ---------------------------------------------------------------------------
# Basic happy path
# ---------------------------------------------------------------------------


class TestDiffReviewBasic:
    def test_run_returns_strategy_output(self):
        with patch("sec_review_framework.strategies.diff_review.run_agentic_loop", return_value="```json\n[]\n```"):
            output = _run_strategy()
        assert isinstance(output, StrategyOutput)

    def test_run_empty_diff_returns_no_findings(self):
        target = _make_target(diff_text="", changed_files=[])
        with patch("sec_review_framework.strategies.diff_review.run_agentic_loop", return_value="```json\n[]\n```"):
            output = _run_strategy(target=target)
        assert output.findings == []

    def test_run_calls_load_diff_spec(self):
        target = _make_target()
        model = _make_model()
        with patch("sec_review_framework.strategies.diff_review.run_agentic_loop", return_value="```json\n[]\n```"):
            _run_strategy(target=target, model=model)
        target.load_diff_spec.assert_called_once()

    def test_run_calls_get_diff_with_refs(self):
        target = _make_target()
        with patch("sec_review_framework.strategies.diff_review.run_agentic_loop", return_value="```json\n[]\n```"):
            _run_strategy(target=target)
        target.get_diff.assert_called_once_with("HEAD~1", "HEAD")

    def test_pre_and_post_dedup_count_equal(self):
        """DiffReviewStrategy doesn't deduplicate — pre == post."""
        target = _make_target()
        with patch("sec_review_framework.strategies.diff_review.run_agentic_loop", return_value="```json\n[]\n```"):
            output = _run_strategy(target=target)
        assert output.pre_dedup_count == output.post_dedup_count

    def test_strategy_name_is_diff_review(self):
        strategy = DiffReviewStrategy()
        assert strategy.name() == "diff_review"


# ---------------------------------------------------------------------------
# Missing diff_spec.yaml (no diff_spec)
# ---------------------------------------------------------------------------


class TestMissingDiffSpec:
    def test_missing_diff_spec_raises(self):
        """When load_diff_spec() raises, the strategy should propagate the exception."""
        target = _make_target(load_diff_spec_raises=FileNotFoundError("No diff_spec.yaml"))
        with pytest.raises(FileNotFoundError):
            DiffReviewStrategy().run(target, _make_model(), _make_tools(), _make_user_strategy())


# ---------------------------------------------------------------------------
# Large diff (>10k lines)
# ---------------------------------------------------------------------------


class TestLargeDiff:
    def test_large_diff_does_not_crash(self):
        """Strategy must handle very large diffs without OOM or crash."""
        large_diff = "\n".join(f"+    line_{i} = {i}" for i in range(15000))
        target = _make_target(diff_text=large_diff, changed_files=["big_file.py"])
        target.read_file.return_value = "\n".join(f"line_{i} = {i}" for i in range(15000))

        with patch("sec_review_framework.strategies.diff_review.run_agentic_loop", return_value="```json\n[]\n```"):
            output = _run_strategy(target=target)

        assert isinstance(output, StrategyOutput)


# ---------------------------------------------------------------------------
# Binary and deleted files
# ---------------------------------------------------------------------------


class TestBinaryAndDeletedFiles:
    def test_binary_file_returns_empty_content(self):
        """read_file for a binary file may return empty or raise; strategy should not crash."""
        target = _make_target(
            diff_text="Binary files a/image.png and b/image.png differ\n",
            changed_files=["image.png"],
        )
        target.read_file.return_value = ""  # binary yields empty

        with patch("sec_review_framework.strategies.diff_review.run_agentic_loop", return_value="```json\n[]\n```"):
            output = _run_strategy(target=target)

        assert isinstance(output, StrategyOutput)

    def test_deleted_file_with_empty_content_handled(self):
        """Deleted file content is empty — strategy should handle gracefully."""
        diff = "--- a/old_file.py\n+++ /dev/null\n@@ -1,5 +0,0 @@\n-deleted line\n"
        target = _make_target(diff_text=diff, changed_files=["old_file.py"])
        target.read_file.return_value = ""

        with patch("sec_review_framework.strategies.diff_review.run_agentic_loop", return_value="```json\n[]\n```"):
            output = _run_strategy(target=target)

        assert isinstance(output, StrategyOutput)


# ---------------------------------------------------------------------------
# Max turns config — comes from the UserStrategy bundle
# ---------------------------------------------------------------------------


class TestMaxTurnsConfig:
    def test_max_turns_from_strategy_bundle(self):
        """max_turns comes from the UserStrategy default bundle."""
        target = _make_target()
        model = _make_model()
        tools = _make_tools()
        captured = {}

        def fake_loop(model, tools, system_prompt, user_message, max_turns):
            captured["max_turns"] = max_turns
            return "```json\n[]\n```"

        strategy = _make_user_strategy(max_turns=42)
        with patch("sec_review_framework.strategies.diff_review.run_agentic_loop", side_effect=fake_loop):
            DiffReviewStrategy().run(target, model, tools, strategy)

        assert captured["max_turns"] == 42

    def test_default_max_turns_is_60(self):
        """Default builtin diff_review max_turns is 60."""
        target = _make_target()
        model = _make_model()
        tools = _make_tools()
        captured = {}

        def fake_loop(model, tools, system_prompt, user_message, max_turns):
            captured["max_turns"] = max_turns
            return "```json\n[]\n```"

        strategy = _make_user_strategy(max_turns=60)
        with patch("sec_review_framework.strategies.diff_review.run_agentic_loop", side_effect=fake_loop):
            DiffReviewStrategy().run(target, model, tools, strategy)

        assert captured["max_turns"] == 60

    def test_profile_modifier_appended_to_system_prompt(self):
        """A non-empty profile_modifier should be appended to the system prompt."""
        target = _make_target()
        model = _make_model()
        tools = _make_tools()
        captured = {}

        def fake_loop(model, tools, system_prompt, user_message, max_turns):
            captured["system_prompt"] = system_prompt
            return "```json\n[]\n```"

        strategy = UserStrategy(
            id="test.diff_review_mod",
            name="Test",
            parent_strategy_id=None,
            orchestration_shape=OrchestrationShape.DIFF_REVIEW,
            default=StrategyBundleDefault(
                system_prompt="base prompt",
                user_prompt_template="Review {diff_text}{file_context}{finding_output_format}",
                profile_modifier="focus on injection",
                model_id="fake-model",
                tools=frozenset(),
                verification="none",
                max_turns=60,
                tool_extensions=frozenset(),
            ),
            overrides=[],
            created_at=datetime(2026, 1, 1),
            is_builtin=False,
        )

        with patch("sec_review_framework.strategies.diff_review.run_agentic_loop", side_effect=fake_loop):
            DiffReviewStrategy().run(target, model, tools, strategy)

        assert captured["system_prompt"] == "base prompt\n\nfocus on injection"


# ---------------------------------------------------------------------------
# Diff with multiple changed files
# ---------------------------------------------------------------------------


class TestMultipleChangedFiles:
    def test_all_changed_files_included_in_context(self):
        """File context for every changed file should be built and sent to the model."""
        changed = ["a.py", "b.py", "c.py"]
        file_contents = {"a.py": "content a", "b.py": "content b", "c.py": "content c"}
        target = _make_target(changed_files=changed, file_contents=file_contents)
        model = _make_model()
        captured_user_msg = {}

        def fake_loop(model, tools, system_prompt, user_message, max_turns):
            captured_user_msg["msg"] = user_message
            return "```json\n[]\n```"

        with patch("sec_review_framework.strategies.diff_review.run_agentic_loop", side_effect=fake_loop):
            DiffReviewStrategy().run(target, model, tools=_make_tools(), strategy=_make_user_strategy())

        msg = captured_user_msg.get("msg", "")
        assert "a.py" in msg
        assert "b.py" in msg
        assert "c.py" in msg
