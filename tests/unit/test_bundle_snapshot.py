"""Tests for BundleSnapshot.capture().

Covers:
- Hash stability across semantically-identical strategies
- Different strategies → different snapshot_id
- Stability across Python runs (based on canonical JSON hash, not id())
- Full bundle capture: per-subagent overrides are reflected
- snapshot_id length and strategy_id field
"""

from __future__ import annotations

from datetime import datetime

from sec_review_framework.data.experiment import BundleSnapshot
from sec_review_framework.data.strategy_bundle import (
    OrchestrationShape,
    OverrideRule,
    StrategyBundleDefault,
    StrategyBundleOverride,
    UserStrategy,
    canonical_json,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CREATED_AT = datetime(2026, 1, 1, 0, 0, 0)


def _base_bundle(**kwargs) -> StrategyBundleDefault:
    defaults = dict(
        system_prompt="You are a security reviewer.",
        user_prompt_template="Review this code for {vuln_class} vulnerabilities.",
        profile_modifier="",
        model_id="claude-opus-4-5",
        tools=frozenset(["read_file", "grep"]),
        verification="none",
        max_turns=80,
        tool_extensions=frozenset(),
    )
    defaults.update(kwargs)
    return StrategyBundleDefault(**defaults)


def _make_strategy(
    sid: str = "test.strat",
    *,
    bundle: StrategyBundleDefault | None = None,
    overrides: list[OverrideRule] | None = None,
    shape: OrchestrationShape = OrchestrationShape.SINGLE_AGENT,
) -> UserStrategy:
    return UserStrategy(
        id=sid,
        name=sid,
        parent_strategy_id=None,
        orchestration_shape=shape,
        default=bundle or _base_bundle(),
        overrides=overrides or [],
        created_at=_CREATED_AT,
        is_builtin=False,
    )


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_capture_same_strategy_produces_same_snapshot_id():
    """Calling capture() twice on the same strategy object yields same snapshot_id."""
    strategy = _make_strategy()
    snap1 = BundleSnapshot.capture(strategy)
    snap2 = BundleSnapshot.capture(strategy)
    assert snap1.snapshot_id == snap2.snapshot_id


def test_capture_semantically_identical_strategies_produce_same_snapshot_id():
    """Two independently constructed strategies with the same content hash the same."""
    s1 = _make_strategy("strat-x")
    s2 = _make_strategy("strat-x")  # same id + same bundle
    snap1 = BundleSnapshot.capture(s1)
    snap2 = BundleSnapshot.capture(s2)
    assert snap1.snapshot_id == snap2.snapshot_id


def test_capture_different_system_prompts_produce_different_snapshot_ids():
    """Strategies differing only in system_prompt must produce different snapshot_ids."""
    s1 = _make_strategy(bundle=_base_bundle(system_prompt="Prompt A"))
    s2 = _make_strategy(bundle=_base_bundle(system_prompt="Prompt B"))
    snap1 = BundleSnapshot.capture(s1)
    snap2 = BundleSnapshot.capture(s2)
    assert snap1.snapshot_id != snap2.snapshot_id


def test_capture_different_model_ids_produce_different_snapshot_ids():
    """Strategies differing only in model_id must produce different snapshot_ids."""
    s1 = _make_strategy(bundle=_base_bundle(model_id="gpt-4o"))
    s2 = _make_strategy(bundle=_base_bundle(model_id="claude-opus-4-5"))
    snap1 = BundleSnapshot.capture(s1)
    snap2 = BundleSnapshot.capture(s2)
    assert snap1.snapshot_id != snap2.snapshot_id


def test_capture_different_strategy_ids_but_same_bundle_produce_different_snapshot_ids():
    """Strategies with different IDs but same bundle content hash differently.

    The strategy ID is part of the canonical JSON, so two strategies that are
    identical except for their ID must have different snapshot_ids.
    """
    s1 = _make_strategy("strat-alpha")
    s2 = _make_strategy("strat-beta")
    snap1 = BundleSnapshot.capture(s1)
    snap2 = BundleSnapshot.capture(s2)
    assert snap1.snapshot_id != snap2.snapshot_id


# ---------------------------------------------------------------------------
# Stability across Python runs (not based on id())
# ---------------------------------------------------------------------------


def test_snapshot_id_is_based_on_canonical_json_not_object_id():
    """snapshot_id must be derived from canonical_json(), not Python's id()."""
    strategy = _make_strategy()
    cjson = canonical_json(strategy)
    import hashlib
    expected_id = hashlib.sha256(cjson.encode()).hexdigest()[:16]
    snap = BundleSnapshot.capture(strategy)
    assert snap.snapshot_id == expected_id


def test_snapshot_id_is_stable_for_known_input():
    """SHA-256-based snapshot_id must be stable across interpreter restarts.

    This is a regression test: if the hash were based on Python's ``hash()``
    or ``id()``, it would differ across runs.  We verify by computing the
    expected value ourselves.
    """
    import hashlib

    strategy = _make_strategy("stable.test")
    cjson = canonical_json(strategy)
    expected = hashlib.sha256(cjson.encode()).hexdigest()[:16]

    snap = BundleSnapshot.capture(strategy)
    assert snap.snapshot_id == expected
    # Extra sanity: expected is not empty / not 'None'
    assert len(expected) == 16
    assert expected.isalnum() or any(c.isdigit() for c in expected)


# ---------------------------------------------------------------------------
# snapshot_id format
# ---------------------------------------------------------------------------


def test_snapshot_id_is_16_hex_chars():
    """snapshot_id must be exactly 16 lowercase hex characters."""
    snap = BundleSnapshot.capture(_make_strategy())
    assert len(snap.snapshot_id) == 16
    assert all(c in "0123456789abcdef" for c in snap.snapshot_id)


# ---------------------------------------------------------------------------
# strategy_id field
# ---------------------------------------------------------------------------


def test_snapshot_carries_correct_strategy_id():
    """BundleSnapshot.strategy_id must match the strategy's id."""
    strategy = _make_strategy("my-custom-strategy")
    snap = BundleSnapshot.capture(strategy)
    assert snap.strategy_id == "my-custom-strategy"


# ---------------------------------------------------------------------------
# bundle_json field — full bundle captured
# ---------------------------------------------------------------------------


def test_snapshot_bundle_json_is_canonical_json_of_strategy():
    """BundleSnapshot.bundle_json must equal canonical_json(strategy)."""
    strategy = _make_strategy()
    cjson = canonical_json(strategy)
    snap = BundleSnapshot.capture(strategy)
    assert snap.bundle_json == cjson


def test_snapshot_bundle_json_is_valid_json():
    """bundle_json must parse as valid JSON."""
    import json

    strategy = _make_strategy()
    snap = BundleSnapshot.capture(strategy)
    parsed = json.loads(snap.bundle_json)
    assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# Per-subagent override capture — regression test
#
# Old gap: PromptSnapshot only captured the first subagent's prompts for
# per_vuln_class runs.  BundleSnapshot hashes the ENTIRE canonical JSON of
# the strategy (including all overrides), so edits to any subagent's prompt
# are reflected in the snapshot_id.
# ---------------------------------------------------------------------------


def test_per_vuln_class_override_change_reflected_in_snapshot():
    """Editing a per_vuln_class override system_prompt changes snapshot_id.

    Regression test for the old per_vuln_class gap where only the first
    subagent's system_prompt was captured.  With BundleSnapshot the full
    canonical JSON is hashed, so any override change is detected.
    """
    base_override = OverrideRule(
        key="sqli",
        override=StrategyBundleOverride(system_prompt="Original SQLI prompt"),
    )
    modified_override = OverrideRule(
        key="sqli",
        override=StrategyBundleOverride(system_prompt="Modified SQLI prompt"),
    )

    s_original = _make_strategy(
        overrides=[base_override],
        shape=OrchestrationShape.PER_VULN_CLASS,
    )
    s_modified = _make_strategy(
        overrides=[modified_override],
        shape=OrchestrationShape.PER_VULN_CLASS,
    )

    snap_original = BundleSnapshot.capture(s_original)
    snap_modified = BundleSnapshot.capture(s_modified)

    assert snap_original.snapshot_id != snap_modified.snapshot_id


def test_adding_vuln_class_override_changes_snapshot():
    """Adding a new per_vuln_class override changes the snapshot_id."""
    s_no_override = _make_strategy(
        overrides=[],
        shape=OrchestrationShape.PER_VULN_CLASS,
    )
    extra_override = OverrideRule(
        key="xss",
        override=StrategyBundleOverride(system_prompt="XSS override"),
    )
    s_with_override = _make_strategy(
        overrides=[extra_override],
        shape=OrchestrationShape.PER_VULN_CLASS,
    )

    snap_none = BundleSnapshot.capture(s_no_override)
    snap_with = BundleSnapshot.capture(s_with_override)

    assert snap_none.snapshot_id != snap_with.snapshot_id


def test_bundle_json_contains_all_overrides():
    """bundle_json must serialise all overrides so downstream consumers have full context."""
    import json

    override = OverrideRule(
        key="sqli",
        override=StrategyBundleOverride(system_prompt="Custom SQLI prompt"),
    )
    strategy = _make_strategy(
        overrides=[override],
        shape=OrchestrationShape.PER_VULN_CLASS,
    )
    snap = BundleSnapshot.capture(strategy)
    parsed = json.loads(snap.bundle_json)

    # Overrides must be present and non-empty
    assert "overrides" in parsed
    assert len(parsed["overrides"]) == 1
    assert parsed["overrides"][0]["key"] == "sqli"
    assert parsed["overrides"][0]["override"]["system_prompt"] == "Custom SQLI prompt"
