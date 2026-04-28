"""Tests for sec_review_framework.data.strategy_bundle."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from sec_review_framework.data.strategy_bundle import (
    OrchestrationShape,
    OverrideRule,
    StrategyBundleDefault,
    StrategyBundleOverride,
    UserStrategy,
    canonical_json,
    resolve_bundle,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2026, 1, 1, 0, 0, 0)

_DEFAULT_BUNDLE = StrategyBundleDefault(
    system_prompt="You are a security expert.",
    user_prompt_template="Review {repo_summary}.",
    profile_modifier="",
    model_id="claude-opus-4-5",
    tools=frozenset(["read_file", "grep"]),
    verification="none",
    max_turns=50,
    tool_extensions=frozenset(),
)


def _make_strategy(
    shape: OrchestrationShape,
    overrides: list[OverrideRule] | None = None,
) -> UserStrategy:
    return UserStrategy(
        id=f"test.{shape.value}",
        name=f"Test {shape.value}",
        parent_strategy_id=None,
        orchestration_shape=shape,
        default=_DEFAULT_BUNDLE,
        overrides=overrides or [],
        created_at=_NOW,
    )


# ---------------------------------------------------------------------------
# resolve_bundle — no overrides
# ---------------------------------------------------------------------------


def test_resolve_single_agent_no_key():
    strategy = _make_strategy(OrchestrationShape.SINGLE_AGENT)
    bundle = resolve_bundle(strategy, None)
    assert bundle == _DEFAULT_BUNDLE


def test_resolve_diff_review_no_key():
    strategy = _make_strategy(OrchestrationShape.DIFF_REVIEW)
    bundle = resolve_bundle(strategy, None)
    assert bundle == _DEFAULT_BUNDLE


def test_resolve_single_agent_with_key_raises():
    strategy = _make_strategy(OrchestrationShape.SINGLE_AGENT)
    with pytest.raises(ValueError, match="does not accept a key"):
        resolve_bundle(strategy, "some_key")


def test_resolve_per_vuln_class_none_key_raises():
    strategy = _make_strategy(OrchestrationShape.PER_VULN_CLASS)
    with pytest.raises(ValueError, match="requires a key"):
        resolve_bundle(strategy, None)


def test_resolve_per_vuln_class_no_overrides_returns_default():
    strategy = _make_strategy(OrchestrationShape.PER_VULN_CLASS)
    bundle = resolve_bundle(strategy, "sqli")
    assert bundle == _DEFAULT_BUNDLE


# ---------------------------------------------------------------------------
# resolve_bundle — per_vuln_class override merge
# ---------------------------------------------------------------------------


def test_resolve_per_vuln_class_matching_override():
    override = StrategyBundleOverride(
        system_prompt="You are a SQLi specialist.",
        max_turns=30,
    )
    strategy = _make_strategy(
        OrchestrationShape.PER_VULN_CLASS,
        overrides=[
            OverrideRule(key="sqli", override=override),
            OverrideRule(key="xss", override=StrategyBundleOverride(max_turns=25)),
        ],
    )
    bundle = resolve_bundle(strategy, "sqli")
    assert bundle.system_prompt == "You are a SQLi specialist."
    assert bundle.max_turns == 30
    # Unoverridden fields inherit from default
    assert bundle.model_id == _DEFAULT_BUNDLE.model_id
    assert bundle.tools == _DEFAULT_BUNDLE.tools
    assert bundle.verification == _DEFAULT_BUNDLE.verification


def test_resolve_per_vuln_class_no_matching_key_falls_back_to_default():
    strategy = _make_strategy(
        OrchestrationShape.PER_VULN_CLASS,
        overrides=[OverrideRule(key="sqli", override=StrategyBundleOverride(max_turns=10))],
    )
    bundle = resolve_bundle(strategy, "xss")
    assert bundle == _DEFAULT_BUNDLE


# ---------------------------------------------------------------------------
# resolve_bundle — per_file first-match-wins
# ---------------------------------------------------------------------------


def test_resolve_per_file_first_match_wins():
    strategy = _make_strategy(
        OrchestrationShape.PER_FILE,
        overrides=[
            OverrideRule(
                key="*.py",
                override=StrategyBundleOverride(max_turns=15),
            ),
            OverrideRule(
                key="myapp/*.py",
                override=StrategyBundleOverride(max_turns=5),
            ),
        ],
    )
    # *.py matches first — max_turns = 15
    bundle = resolve_bundle(strategy, "myapp/views.py")
    assert bundle.max_turns == 15


def test_resolve_per_file_second_pattern_if_first_not_matching():
    strategy = _make_strategy(
        OrchestrationShape.PER_FILE,
        overrides=[
            OverrideRule(
                key="tests/*.py",
                override=StrategyBundleOverride(max_turns=5),
            ),
            OverrideRule(
                key="*.py",
                override=StrategyBundleOverride(max_turns=15),
            ),
        ],
    )
    bundle = resolve_bundle(strategy, "src/app.py")
    assert bundle.max_turns == 15


def test_resolve_per_file_no_match_falls_through_to_default():
    strategy = _make_strategy(
        OrchestrationShape.PER_FILE,
        overrides=[
            OverrideRule(key="*.go", override=StrategyBundleOverride(max_turns=10)),
        ],
    )
    bundle = resolve_bundle(strategy, "myapp/views.py")
    assert bundle == _DEFAULT_BUNDLE


# ---------------------------------------------------------------------------
# Validators
# ---------------------------------------------------------------------------


def test_validator_rejects_overrides_on_single_agent():
    with pytest.raises(ValueError, match="must have no overrides"):
        _make_strategy(
            OrchestrationShape.SINGLE_AGENT,
            overrides=[OverrideRule(key="any", override=StrategyBundleOverride())],
        )


def test_validator_rejects_overrides_on_diff_review():
    with pytest.raises(ValueError, match="must have no overrides"):
        _make_strategy(
            OrchestrationShape.DIFF_REVIEW,
            overrides=[OverrideRule(key="any", override=StrategyBundleOverride())],
        )


def test_validator_rejects_invalid_vuln_class_key():
    with pytest.raises(ValueError, match="not a valid VulnClass name"):
        _make_strategy(
            OrchestrationShape.PER_VULN_CLASS,
            overrides=[
                OverrideRule(key="not_a_vuln_class", override=StrategyBundleOverride())
            ],
        )


def test_validator_accepts_valid_vuln_class_keys():
    strategy = _make_strategy(
        OrchestrationShape.PER_VULN_CLASS,
        overrides=[
            OverrideRule(key="sqli", override=StrategyBundleOverride()),
            OverrideRule(key="xss", override=StrategyBundleOverride()),
        ],
    )
    assert len(strategy.overrides) == 2


def test_validator_accepts_glob_patterns_for_per_file():
    strategy = _make_strategy(
        OrchestrationShape.PER_FILE,
        overrides=[
            OverrideRule(key="**/*.py", override=StrategyBundleOverride()),
            OverrideRule(key="src/[abc]*.go", override=StrategyBundleOverride()),
        ],
    )
    assert len(strategy.overrides) == 2


# ---------------------------------------------------------------------------
# canonical_json determinism
# ---------------------------------------------------------------------------


def test_canonical_json_is_deterministic():
    s = _make_strategy(OrchestrationShape.SINGLE_AGENT)
    j1 = canonical_json(s)
    j2 = canonical_json(s)
    assert j1 == j2


def test_canonical_json_frozenset_order_independent():
    """Two strategies differing only in frozenset iteration order produce equal JSON."""
    s1 = UserStrategy(
        id="test.sa",
        name="test",
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.SINGLE_AGENT,
        default=StrategyBundleDefault(
            system_prompt="p",
            user_prompt_template="u",
            model_id="m",
            tools=frozenset(["grep", "read_file"]),
            verification="none",
            max_turns=10,
            tool_extensions=frozenset(["lsp", "tree_sitter"]),
        ),
        overrides=[],
        created_at=_NOW,
    )
    s2 = UserStrategy(
        id="test.sa",
        name="test",
        parent_strategy_id=None,
        orchestration_shape=OrchestrationShape.SINGLE_AGENT,
        default=StrategyBundleDefault(
            system_prompt="p",
            user_prompt_template="u",
            model_id="m",
            tools=frozenset(["read_file", "grep"]),  # different iteration order
            verification="none",
            max_turns=10,
            tool_extensions=frozenset(["tree_sitter", "lsp"]),  # different order
        ),
        overrides=[],
        created_at=_NOW,
    )
    assert canonical_json(s1) == canonical_json(s2)


def test_canonical_json_is_valid_json():
    s = _make_strategy(OrchestrationShape.SINGLE_AGENT)
    parsed = json.loads(canonical_json(s))
    assert isinstance(parsed, dict)
    assert parsed["id"] == s.id


def test_canonical_json_sorted_keys():
    s = _make_strategy(OrchestrationShape.SINGLE_AGENT)
    j = canonical_json(s)
    parsed = json.loads(j)
    keys = list(parsed.keys())
    assert keys == sorted(keys)


def test_canonical_json_tools_sorted():
    s = _make_strategy(OrchestrationShape.SINGLE_AGENT)
    j = json.loads(canonical_json(s))
    tools = j["default"]["tools"]
    assert tools == sorted(tools)


# ---------------------------------------------------------------------------
# dispatch_fallback Literal type validation
# ---------------------------------------------------------------------------


def test_dispatch_fallback_valid_values_accepted():
    """All three Literal values are accepted without error."""
    for valid_value in ("reprompt", "programmatic", "none"):
        bundle = StrategyBundleDefault(
            system_prompt="p",
            user_prompt_template="u",
            model_id="m",
            tools=frozenset(),
            verification="none",
            max_turns=10,
            tool_extensions=frozenset(),
            dispatch_fallback=valid_value,  # type: ignore[arg-type]
        )
        assert bundle.dispatch_fallback == valid_value


def test_dispatch_fallback_invalid_value_raises_validation_error():
    """A non-Literal value for dispatch_fallback raises Pydantic ValidationError."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        StrategyBundleDefault(
            system_prompt="p",
            user_prompt_template="u",
            model_id="m",
            tools=frozenset(),
            verification="none",
            max_turns=10,
            tool_extensions=frozenset(),
            dispatch_fallback="invalid_fallback_value",  # type: ignore[arg-type]
        )
