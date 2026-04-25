"""Unit tests for the output-type resolver and subagent output_type enforcement.

Covers:
1. ``resolve_output_type`` returns the correct class for every registered name.
2. ``resolve_output_type(None)`` returns ``None``.
3. ``resolve_output_type`` raises ``ValueError`` for unknown names.
4. A subagent declared with ``output_type_name="verifier_verdict"`` actually
   receives a typed :class:`~sec_review_framework.data.verification.VerifierVerdict`
   instance when the LLM returns well-formed JSON.  (Uses a scripted provider.)
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

pydantic_ai = pytest.importorskip("pydantic_ai")

from sec_review_framework.agent.output_types import resolve_output_type  # noqa: E402
from sec_review_framework.data.classification import ClassifierJudgement  # noqa: E402
from sec_review_framework.data.findings import Finding  # noqa: E402
from sec_review_framework.data.taint import SanitizationVerdict, Source, TaintPath  # noqa: E402
from sec_review_framework.data.verification import VerifierVerdict  # noqa: E402

# ---------------------------------------------------------------------------
# 1. resolve_output_type — registry correctness
# ---------------------------------------------------------------------------


class TestResolveOutputType:
    """resolve_output_type returns the right class for each known name."""

    def test_none_returns_none(self) -> None:
        assert resolve_output_type(None) is None

    def test_finding_list(self) -> None:
        result = resolve_output_type("finding_list")
        # list[Finding] is a generic alias, not a plain class — check the origin
        import typing
        assert typing.get_origin(result) is list
        assert typing.get_args(result) == (Finding,)

    def test_verifier_verdict(self) -> None:
        result = resolve_output_type("verifier_verdict")
        assert result is VerifierVerdict

    def test_source_list(self) -> None:
        result = resolve_output_type("source_list")
        import typing
        assert typing.get_origin(result) is list
        assert typing.get_args(result) == (Source,)

    def test_taint_path_list(self) -> None:
        result = resolve_output_type("taint_path_list")
        import typing
        assert typing.get_origin(result) is list
        assert typing.get_args(result) == (TaintPath,)

    def test_sanitization_verdict(self) -> None:
        result = resolve_output_type("sanitization_verdict")
        assert result is SanitizationVerdict

    def test_classifier_judgement_list(self) -> None:
        result = resolve_output_type("classifier_judgement_list")
        import typing
        assert typing.get_origin(result) is list
        assert typing.get_args(result) == (ClassifierJudgement,)

    def test_unknown_name_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="unknown output_type_name"):
            resolve_output_type("nonexistent_type")

    def test_unknown_name_error_message_lists_valid_names(self) -> None:
        with pytest.raises(ValueError, match="finding_list"):
            resolve_output_type("bad_type")

    def test_all_registered_names_resolve_without_error(self) -> None:
        """All names that StrategyBundleDefault accepts must resolve cleanly."""
        valid_names = [
            "finding_list",
            "verifier_verdict",
            "source_list",
            "taint_path_list",
            "sanitization_verdict",
            "classifier_judgement_list",
        ]
        for name in valid_names:
            result = resolve_output_type(name)
            assert result is not None, f"resolve_output_type({name!r}) returned None"


# ---------------------------------------------------------------------------
# 2. StrategyBundleDefault validator for output_type_name
# ---------------------------------------------------------------------------


class TestStrategyBundleOutputTypeName:
    """output_type_name field validation on StrategyBundleDefault."""

    def _make_bundle(self, output_type_name: str | None) -> Any:
        from sec_review_framework.data.strategy_bundle import StrategyBundleDefault
        return StrategyBundleDefault(
            system_prompt="test",
            user_prompt_template="test",
            profile_modifier="",
            model_id="fake/test",
            tools=frozenset(),
            verification="none",
            max_turns=5,
            tool_extensions=frozenset(),
            output_type_name=output_type_name,
        )

    def test_none_is_valid(self) -> None:
        bundle = self._make_bundle(None)
        assert bundle.output_type_name is None

    def test_valid_names_accepted(self) -> None:
        for name in [
            "finding_list",
            "verifier_verdict",
            "source_list",
            "taint_path_list",
            "sanitization_verdict",
            "classifier_judgement_list",
        ]:
            bundle = self._make_bundle(name)
            assert bundle.output_type_name == name

    def test_invalid_name_raises_validation_error(self) -> None:
        with pytest.raises(Exception):
            self._make_bundle("totally_invalid")


# ---------------------------------------------------------------------------
# 3. Subagent output_type enforcement — scripted provider
#
# This test verifies that when a strategy declares output_type_name, the child
# Agent is constructed with output_type=... and pydantic-ai validates the LLM
# response into the correct Pydantic instance.
#
# We use a scripted provider that returns well-formed VerifierVerdict JSON so
# we can assert that SubagentOutput.output is a VerifierVerdict instance, not
# a raw string.
# ---------------------------------------------------------------------------


from sec_review_framework.models.base import Message, ToolDefinition  # noqa: E402
from sec_review_framework.models.base import ModelResponse as FrameworkModelResponse  # noqa: E402
from sec_review_framework.models.litellm_provider import LiteLLMProvider  # noqa: E402


class ScriptedProvider(LiteLLMProvider):
    """Pre-scripted provider that returns one canned response per call."""

    def __init__(self, responses: list[dict[str, Any]]) -> None:
        super().__init__(model_name="fake/test")
        self._responses = list(responses)

    def _do_complete(
        self,
        messages: list[Message],
        tools: list[ToolDefinition] | None,
        system_prompt: str | None,
        max_tokens: int,
        temperature: float,
    ) -> FrameworkModelResponse:
        if not self._responses:
            raise RuntimeError("ScriptedProvider: no more responses")
        data = self._responses.pop(0)
        return FrameworkModelResponse(
            content=data.get("content", ""),
            tool_calls=data.get("tool_calls", []),
            input_tokens=data.get("input_tokens", 10),
            output_tokens=data.get("output_tokens", 5),
            model_id=self.model_name,
            raw={},
        )


def _make_strategy(output_type_name: str | None) -> Any:
    """Build a minimal UserStrategy with the given output_type_name."""
    from datetime import datetime

    from sec_review_framework.data.strategy_bundle import (
        OrchestrationShape,
        StrategyBundleDefault,
        UserStrategy,
    )

    return UserStrategy(
        id=f"test.subagent_{uuid.uuid4().hex[:8]}",
        name="Test subagent",
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.SINGLE_AGENT,
        default=StrategyBundleDefault(
            system_prompt="You are a verifier.",
            user_prompt_template="Verify: {finding}",
            profile_modifier="",
            model_id="fake/test",
            tools=frozenset(),
            verification="none",
            max_turns=5,
            tool_extensions=frozenset(),
            output_type_name=output_type_name,
        ),
        overrides=[],
        created_at=datetime(2026, 1, 1),
        is_builtin=False,
    )


class TestSubagentOutputTypeEnforcement:
    """Subagent declared with output_type_name receives a typed Pydantic instance."""

    def test_verifier_verdict_output_is_typed_instance(self) -> None:
        """A subagent with output_type_name='verifier_verdict' yields a VerifierVerdict.

        We build a minimal parent SubagentDeps that holds a verifier child strategy,
        then call _run_child_sync directly with a scripted provider that returns a
        well-formed VerifierVerdict JSON payload via final_result.

        The assertion: SubagentOutput.output is a VerifierVerdict instance (not str).
        This confirms output_type= is correctly wired into the child Agent.
        """
        from unittest.mock import patch

        from sec_review_framework.agent.subagent import SubagentDeps, _run_child_sync
        from sec_review_framework.tools.registry import ToolRegistry

        child_strategy = _make_strategy("verifier_verdict")

        # Scripted response: well-formed VerifierVerdict JSON via final_result.
        # For a Pydantic BaseModel output_type (non-list), pydantic-ai passes the
        # model fields as the *direct* args to final_result — NOT wrapped in a
        # "response" key (that wrapper is only used for list[T] types).
        scripted = ScriptedProvider(
            responses=[
                {
                    "content": "",
                    "tool_calls": [
                        {
                            "name": "final_result",
                            "id": "tc_vv_1",
                            "input": {
                                "status": "confirmed",
                                "evidence": "Input reaches SQL exec at line 55 unescaped.",
                            },
                        }
                    ],
                    "input_tokens": 20,
                    "output_tokens": 10,
                }
            ]
        )

        parent_deps = SubagentDeps(
            depth=0,
            max_depth=3,
            invocations=0,
            max_invocations=100,
            max_batch_size=32,
            available_roles={child_strategy.id},
            subagent_strategies={child_strategy.id: child_strategy},
            tool_registry=ToolRegistry(),
        )

        # Patch LiteLLMProvider constructor inside _run_child_sync so it uses our
        # scripted provider instead of trying to connect to a real LLM endpoint.
        with patch(
            "sec_review_framework.agent.subagent.LiteLLMProvider",
            return_value=scripted,
        ):
            sub_output = _run_child_sync(
                child_strategy,
                {"finding": "SQL injection at views.py:42"},
                parent_deps,
            )

        assert isinstance(sub_output.output, VerifierVerdict), (
            f"Expected VerifierVerdict, got {type(sub_output.output)}: {sub_output.output!r}"
        )
        assert sub_output.output.status == "confirmed"
        assert "SQL exec" in sub_output.output.evidence

    def test_free_form_subagent_output_is_string_when_no_output_type(self) -> None:
        """A subagent with output_type_name=None returns str output (free-form text).

        Verifies the fallback path: when output_type_name is not set, no output_type
        is passed to the child Agent, and pydantic-ai returns plain text.
        """
        from unittest.mock import patch

        from sec_review_framework.agent.subagent import SubagentDeps, _run_child_sync
        from sec_review_framework.tools.registry import ToolRegistry

        child_strategy = _make_strategy(None)

        scripted = ScriptedProvider(
            responses=[
                {
                    "content": "No vulnerability found.",
                    "tool_calls": [],
                    "input_tokens": 10,
                    "output_tokens": 5,
                }
            ]
        )

        parent_deps = SubagentDeps(
            depth=0,
            max_depth=3,
            invocations=0,
            max_invocations=100,
            max_batch_size=32,
            available_roles={child_strategy.id},
            subagent_strategies={child_strategy.id: child_strategy},
            tool_registry=ToolRegistry(),
        )

        with patch(
            "sec_review_framework.agent.subagent.LiteLLMProvider",
            return_value=scripted,
        ):
            sub_output = _run_child_sync(
                child_strategy,
                {"finding": "Check this"},
                parent_deps,
            )

        # Free-form: output is a string (pydantic-ai default for text-only response)
        assert isinstance(sub_output.output, str)
