"""Tests for BUILTIN_PROFILES lookup and modifier semantics."""

from __future__ import annotations

import pytest

from sec_review_framework.data.experiment import ReviewProfileName
from sec_review_framework.profiles.review_profiles import (
    BUILTIN_PROFILES,
    ProfileRegistry,
    ReviewProfile,
)

# ---------------------------------------------------------------------------
# BUILTIN_PROFILES coverage — every known profile resolves
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", list(ReviewProfileName))
def test_builtin_profiles_contains_every_enum_value(name: ReviewProfileName):
    assert name in BUILTIN_PROFILES


@pytest.mark.parametrize("name", list(ReviewProfileName))
def test_builtin_profiles_returns_review_profile_instance(name: ReviewProfileName):
    assert isinstance(BUILTIN_PROFILES[name], ReviewProfile)


@pytest.mark.parametrize("name", list(ReviewProfileName))
def test_builtin_profiles_name_field_matches_key(name: ReviewProfileName):
    profile = BUILTIN_PROFILES[name]
    assert profile.name == name


@pytest.mark.parametrize("name", list(ReviewProfileName))
def test_builtin_profiles_description_is_non_empty(name: ReviewProfileName):
    profile = BUILTIN_PROFILES[name]
    assert profile.description.strip()


# ---------------------------------------------------------------------------
# ProfileRegistry.get — happy path and error path
# ---------------------------------------------------------------------------


@pytest.fixture
def registry() -> ProfileRegistry:
    return ProfileRegistry()


@pytest.mark.parametrize("name", list(ReviewProfileName))
def test_registry_get_returns_correct_profile(registry: ProfileRegistry, name: ReviewProfileName):
    profile = registry.get(name)
    assert profile.name == name


def test_registry_get_unknown_raises_value_error(registry: ProfileRegistry):
    with pytest.raises(ValueError, match="Unknown review profile"):
        registry.get("nonexistent_profile")  # type: ignore[arg-type]


def test_registry_list_all_returns_all_profiles(registry: ProfileRegistry):
    profiles = registry.list_all()
    assert len(profiles) == len(ReviewProfileName)
    names = {p.name for p in profiles}
    assert names == set(ReviewProfileName)


# ---------------------------------------------------------------------------
# DEFAULT profile — empty modifier
# ---------------------------------------------------------------------------


def test_default_profile_has_empty_modifier():
    profile = BUILTIN_PROFILES[ReviewProfileName.DEFAULT]
    assert profile.system_prompt_modifier == ""


# ---------------------------------------------------------------------------
# STRICT profile — high-confidence, citation-required behaviour
# ---------------------------------------------------------------------------


def test_strict_profile_modifier_requires_high_confidence():
    modifier = BUILTIN_PROFILES[ReviewProfileName.STRICT].system_prompt_modifier
    assert "0.8" in modifier or "high confidence" in modifier.lower()


def test_strict_profile_modifier_requires_file_line_citation():
    modifier = BUILTIN_PROFILES[ReviewProfileName.STRICT].system_prompt_modifier
    assert "file" in modifier.lower() and "line" in modifier.lower()


def test_strict_profile_modifier_requires_exploitation_path():
    modifier = BUILTIN_PROFILES[ReviewProfileName.STRICT].system_prompt_modifier
    lower = modifier.lower()
    assert "exploit" in lower or "exploitation" in lower


def test_strict_profile_modifier_discourages_low_quality_findings():
    modifier = BUILTIN_PROFILES[ReviewProfileName.STRICT].system_prompt_modifier
    lower = modifier.lower()
    assert "fewer" in lower or "do not report" in lower or "not report" in lower


# ---------------------------------------------------------------------------
# COMPREHENSIVE profile — wide net, low threshold behaviour
# ---------------------------------------------------------------------------


def test_comprehensive_profile_modifier_lowers_confidence_threshold():
    modifier = BUILTIN_PROFILES[ReviewProfileName.COMPREHENSIVE].system_prompt_modifier
    assert "0.3" in modifier or "lower confidence" in modifier.lower()


def test_comprehensive_profile_modifier_includes_false_positive_guidance():
    modifier = BUILTIN_PROFILES[ReviewProfileName.COMPREHENSIVE].system_prompt_modifier
    lower = modifier.lower()
    assert "false positive" in lower or "warrant" in lower


def test_comprehensive_profile_modifier_covers_defense_in_depth():
    modifier = BUILTIN_PROFILES[ReviewProfileName.COMPREHENSIVE].system_prompt_modifier
    lower = modifier.lower()
    assert "defense-in-depth" in lower or "defense in depth" in lower or "missing" in lower


# ---------------------------------------------------------------------------
# OWASP_FOCUSED profile — OWASP Top 10 2021 content
# ---------------------------------------------------------------------------


def test_owasp_focused_profile_modifier_references_owasp_top_10():
    modifier = BUILTIN_PROFILES[ReviewProfileName.OWASP_FOCUSED].system_prompt_modifier
    lower = modifier.lower()
    assert "owasp" in lower and "top 10" in lower


def test_owasp_focused_profile_modifier_references_2021_edition():
    modifier = BUILTIN_PROFILES[ReviewProfileName.OWASP_FOCUSED].system_prompt_modifier
    assert "2021" in modifier


def test_owasp_focused_profile_modifier_lists_all_ten_categories():
    modifier = BUILTIN_PROFILES[ReviewProfileName.OWASP_FOCUSED].system_prompt_modifier
    for i in range(1, 11):
        category_marker = f"A0{i}:2021" if i < 10 else "A10:2021"
        assert category_marker in modifier, f"Missing OWASP category {category_marker}"


def test_owasp_focused_profile_modifier_requires_category_mapping():
    modifier = BUILTIN_PROFILES[ReviewProfileName.OWASP_FOCUSED].system_prompt_modifier
    lower = modifier.lower()
    assert "map" in lower or "category" in lower


def test_owasp_focused_profile_modifier_restricts_to_owasp_only():
    modifier = BUILTIN_PROFILES[ReviewProfileName.OWASP_FOCUSED].system_prompt_modifier
    lower = modifier.lower()
    assert "do not report" in lower or "outside" in lower or "limit" in lower


# ---------------------------------------------------------------------------
# QUICK_SCAN profile — triage / critical+high only
# ---------------------------------------------------------------------------


def test_quick_scan_profile_modifier_focuses_on_high_severity():
    modifier = BUILTIN_PROFILES[ReviewProfileName.QUICK_SCAN].system_prompt_modifier
    lower = modifier.lower()
    assert "critical" in lower and "high" in lower


def test_quick_scan_profile_modifier_skips_lower_severities():
    modifier = BUILTIN_PROFILES[ReviewProfileName.QUICK_SCAN].system_prompt_modifier
    lower = modifier.lower()
    assert "medium" in lower or "low" in lower


# ---------------------------------------------------------------------------
# system_prompt_modifier formatting — no leading/trailing blank lines
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", [
    ReviewProfileName.STRICT,
    ReviewProfileName.COMPREHENSIVE,
    ReviewProfileName.OWASP_FOCUSED,
    ReviewProfileName.QUICK_SCAN,
])
def test_non_default_modifier_is_non_empty(name: ReviewProfileName):
    modifier = BUILTIN_PROFILES[name].system_prompt_modifier
    assert modifier.strip()


@pytest.mark.parametrize("name", [
    ReviewProfileName.STRICT,
    ReviewProfileName.COMPREHENSIVE,
    ReviewProfileName.OWASP_FOCUSED,
    ReviewProfileName.QUICK_SCAN,
])
def test_modifier_does_not_start_with_blank_line(name: ReviewProfileName):
    modifier = BUILTIN_PROFILES[name].system_prompt_modifier
    assert not modifier.startswith("\n")


@pytest.mark.parametrize("name", [
    ReviewProfileName.STRICT,
    ReviewProfileName.COMPREHENSIVE,
    ReviewProfileName.OWASP_FOCUSED,
    ReviewProfileName.QUICK_SCAN,
])
def test_modifier_does_not_end_with_blank_line(name: ReviewProfileName):
    modifier = BUILTIN_PROFILES[name].system_prompt_modifier
    assert not modifier.endswith("\n\n")
